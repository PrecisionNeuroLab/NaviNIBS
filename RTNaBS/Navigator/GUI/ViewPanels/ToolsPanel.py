from __future__ import annotations

import asyncio
from datetime import datetime

import appdirs
import attrs
import json
import logging
import numpy as np
import os
import pathlib
import pyvista as pv
import pyvistaqt as pvqt
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from . import MainViewPanel
from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient, TimestampedToolPosition
from RTNaBS.Navigator.GUI.EditWindows.CoilCalibrationWindow import CoilCalibrationWithPlateWindow
from RTNaBS.Navigator.GUI.EditWindows.PointerCalibrationWindow import PointerCalibrationWindow
from RTNaBS.Navigator.GUI.Widgets.TrackingStatusWidget import TrackingStatusWidget
from RTNaBS.Navigator.GUI.Widgets.CollectionTableWidget import ToolsTableWidget
from RTNaBS.Navigator.Model.Session import Session, Tools, Tool, CoilTool, Pointer
from RTNaBS.util import makeStrUnique
from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError
from RTNaBS.util.pyvista import setActorUserTransform, Actor, RemotePlotterProxy
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import transformToString, stringToTransform, concatenateTransforms, invertTransform
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.util.GUI.QLineEdit import QLineEditWithValidationFeedback
from RTNaBS.util.GUI.QTableWidgetDragRows import QTableWidgetDragRows
from RTNaBS.util.GUI.QValidators import OptionalTransformValidator
from RTNaBS.util.pyvista import DefaultBackgroundPlotter
from RTNaBS.util.pyvista.plotting import BackgroundPlotter


logger = logging.getLogger(__name__)


@attrs.define
class ToolWidget:
    _tool: Tool
    _session: Session = attrs.field(repr=False)  # only used for cross-tool references like coil calibration

    _wdgt: QtWidgets.QWidget = attrs.field(init=False)
    _formLayout: QtWidgets.QFormLayout = attrs.field(init=False)
    _key: QtWidgets.QLineEdit = attrs.field(init=False)
    _trackerKey: QtWidgets.QLineEdit = attrs.field(init=False)
    _label: QtWidgets.QLineEdit = attrs.field(init=False)
    _usedFor: QtWidgets.QComboBox = attrs.field(init=False)
    _isActive: QtWidgets.QCheckBox = attrs.field(init=False)
    _romFilepath: QFileSelectWidget = attrs.field(init=False)
    _trackerStlFilepath: QFileSelectWidget = attrs.field(init=False)
    _toolStlFilepath: QFileSelectWidget = attrs.field(init=False)
    _toolToTrackerTransf: QtWidgets.QLineEdit = attrs.field(init=False)
    _toolStlToToolTransf: QtWidgets.QLineEdit = attrs.field(init=False)
    _trackerStlToTrackerTransf: QtWidgets.QLineEdit = attrs.field(init=False)
    _toolSpacePlotter: DefaultBackgroundPlotter = attrs.field(init=False)
    _trackerSpacePlotter: DefaultBackgroundPlotter = attrs.field(init=False)

    _asyncInitTask: asyncio.Task = attrs.field(init=False)
    _finishedAsyncInit: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    _toolSpaceActors: dict[str, Actor] = attrs.field(init=False, factory=dict)
    _trackerSpaceActors: dict[str, Actor] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        self._wdgt = QtWidgets.QGroupBox('Selected tool: {}'.format(self._tool.key))
        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        formContainer = QtWidgets.QWidget()
        self._formLayout = QtWidgets.QFormLayout()
        formContainer.setLayout(self._formLayout)
        self._wdgt.layout().addWidget(formContainer)

        self._tool.sigItemChanged.connect(self._onToolChanged)

        self._key = QtWidgets.QLineEdit()
        self._key.editingFinished.connect(self._onKeyEdited)
        formContainer.layout().addRow('Key', self._key)

        self._trackerKey = QtWidgets.QLineEdit()
        self._trackerKey.editingFinished.connect(self._onTrackerKeyEdited)
        formContainer.layout().addRow('TrackerKey', self._trackerKey)

        self._label = QtWidgets.QLineEdit()
        self._label.editingFinished.connect(self._onLabelEdited)
        formContainer.layout().addRow('Label', self._label)

        self._usedFor = QtWidgets.QComboBox()
        self._usedFor.insertItems(0, ['coil', 'subject', 'pointer', 'calibration', 'visualization'])
        if len(self._tool.usedFor) > 0:
            index = self._usedFor.findText(self._tool.usedFor)
            assert index != -1, 'Unexpected tool type: {}'.format(self._tool.usedFor)
        else:
            index = -1
        self._usedFor.setCurrentIndex(index)
        self._usedFor.currentIndexChanged.connect(lambda index: self._onUsedForEdited())
        formContainer.layout().addRow('Type', self._usedFor)

        self._isActive = QtWidgets.QCheckBox('')
        self._isActive.setChecked(self._tool.isActive)
        self._isActive.stateChanged.connect(lambda state: self._onIsActiveEdited())
        formContainer.layout().addRow('Is active', self._isActive)

        self._romFilepath = QFileSelectWidget(
            browseMode='getOpenFilename',
            filepath=self._tool.romFilepath,
            showRelativeTo=self._tool.filepathsRelTo,
            showRelativePrefix=self._tool.filepathsRelToKey,
            extFilters='ROM (*.rom)',
            browseCaption='Choose tracker definition file',
        )
        self._romFilepath.sigFilepathChanged.connect(lambda filepath: self._onRomFilepathEdited())
        formContainer.layout().addRow('ROM filepath', self._romFilepath)

        self._trackerStlFilepath = QFileSelectWidget(
            browseMode='getOpenFilename',
            filepath=self._tool.trackerStlFilepath,
            showRelativeTo=self._tool.filepathsRelTo,
            showRelativePrefix=self._tool.filepathsRelToKey,
            extFilters='STL (*.stl); PLY (*.ply)',
            browseCaption='Choose 3D model for tracker visualization'
        )
        self._trackerStlFilepath.sigFilepathChanged.connect(lambda filepath: self._onTrackerStlFilepathEdited())
        formContainer.layout().addRow('Tracker STL filepath', self._trackerStlFilepath)

        self._toolStlFilepath = QFileSelectWidget(
            browseMode='getOpenFilename',
            filepath=self._tool.toolStlFilepath,
            showRelativeTo=self._tool.filepathsRelTo,
            showRelativePrefix=self._tool.filepathsRelToKey,
            extFilters='STL (*.stl); PLY (*.ply)',
            browseCaption='Choose 3D model for tool visualization'
        )
        self._toolStlFilepath.sigFilepathChanged.connect(lambda filepath: self._onToolStlFilepathEdited())
        formContainer.layout().addRow('Tool STL filepath', self._toolStlFilepath)

        self._toolStlToToolTransf = QLineEditWithValidationFeedback(self._transfToStr(self._tool.toolStlToToolTransf))
        self._toolStlToToolTransf.setValidator(OptionalTransformValidator())
        self._toolStlToToolTransf.editingFinished.connect(self._onToolStlToToolTransfEdited)
        formContainer.layout().addRow('Tool STL to tool transf', self._toolStlToToolTransf)

        self._trackerStlToTrackerTransf = QLineEditWithValidationFeedback(self._transfToStr(self._tool.trackerStlToTrackerTransf))
        self._trackerStlToTrackerTransf.setValidator(OptionalTransformValidator())
        self._trackerStlToTrackerTransf.editingFinished.connect(self._onTrackerStlToTrackerTransfEdited)
        formContainer.layout().addRow('Tracker STL to tracker transf', self._trackerStlToTrackerTransf)

        self._toolToTrackerTransf = QLineEditWithValidationFeedback(self._transfToStr(self._tool.toolToTrackerTransf))
        self._toolToTrackerTransf.setValidator(OptionalTransformValidator())
        self._toolToTrackerTransf.editingFinished.connect(self._onToolToTrackerTransfEdited)
        formContainer.layout().addRow('Tool to tracker transf', self._toolToTrackerTransf)

        plotContainer = QtWidgets.QWidget()
        plotContainer.setLayout(QtWidgets.QHBoxLayout())
        self._wdgt.layout().addWidget(plotContainer)

        self._toolSpacePlotter = DefaultBackgroundPlotter(parent=self._wdgt)
        # TODO: make sure these plotters (when RemotePlotterProxies) are appropriately terminated when switching between tools
        # TODO: ideally actually repurpose previous RemotePlotter when switching between tools to avoid unnecessary process init times

        plotterContainer = QtWidgets.QGroupBox('Tool-space')
        plotterContainer.setLayout(QtWidgets.QVBoxLayout())
        plotterContainer.layout().addWidget(self._toolSpacePlotter)
        plotContainer.layout().addWidget(plotterContainer)

        self._trackerSpacePlotter = DefaultBackgroundPlotter(parent=self._wdgt)

        plotterContainer = QtWidgets.QGroupBox('Tracker-space')
        plotterContainer.setLayout(QtWidgets.QVBoxLayout())
        plotterContainer.layout().addWidget(self._trackerSpacePlotter)
        plotContainer.layout().addWidget(plotterContainer)

        self._asyncInitTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._finishInitialization_async))

        QtCore.QTimer.singleShot(0, lambda: self._onToolChanged(self._tool.key, attribsChanged=None))

        self.redraw()

    async def _finishInitialization_async(self):
        if isinstance(self._toolSpacePlotter, RemotePlotterProxy):
            await self._toolSpacePlotter.isReadyEvent.wait()

        if isinstance(self._trackerSpacePlotter, RemotePlotterProxy):
            await self._trackerSpacePlotter.isReadyEvent.wait()

        for plotter in (self._toolSpacePlotter, self._trackerSpacePlotter):
            with plotter.allowNonblockingCalls():
                plotter.enable_parallel_projection()
                plotter.enable_depth_peeling(2)

        self._finishedAsyncInit.set()

        self.redraw()

    def redraw(self, whatToRedraw: tp.Iterable[str] | None = None):

        if not self._finishedAsyncInit.is_set():
            return

        if whatToRedraw is None or 'tool' in whatToRedraw:
            actorKey = 'tool'
            if actorKey in self._toolSpaceActors:
                self._toolSpacePlotter.remove_actor(self._toolSpaceActors.pop(actorKey))
            if actorKey in self._trackerSpaceActors:
                self._trackerSpacePlotter.remove_actor(self._trackerSpaceActors.pop(actorKey))

            defaultGridKwargs = dict(
                bold=False,
            )

            if self._tool.toolSurf is not None:
                meshColor = self._tool.toolColor
                scalars = None
                if meshColor is None:
                    if len(self._tool.toolSurf.array_names) > 0:
                        meshColor = None  # use color from mesh file
                        scalars = self._tool.toolSurf.array_names[0]
                    else:
                        meshColor = '#2222ff'
                meshColor_tool = meshColor
                actor = self._toolSpacePlotter.add_mesh(
                    name=actorKey,
                    mesh=self._tool.toolSurf,
                    color=meshColor,
                    scalars=scalars,
                    rgb=meshColor is None,
                    opacity=0.8
                )
                self._toolSpaceActors[actorKey] = actor
                setActorUserTransform(actor, self._tool.toolStlToToolTransf)
                self._toolSpacePlotter.show_grid(
                    color=self._toolSpacePlotter.palette().color(QtGui.QPalette.Text).name(),
                    **defaultGridKwargs)

            if self._tool.toolToTrackerTransf is not None and self._tool.toolSurf is not None:
                actor = self._trackerSpacePlotter.add_mesh(
                    mesh=self._tool.toolSurf,
                    color=meshColor_tool,  # noqa
                    rgb=meshColor_tool is None,
                    scalars=scalars,
                    opacity=0.8,
                    name=actorKey
                )
                self._trackerSpaceActors[actorKey] = actor
                setActorUserTransform(actor, self._tool.toolToTrackerTransf @ self._tool.toolStlToToolTransf)
                self._trackerSpacePlotter.show_grid(
                    color=self._trackerSpacePlotter.palette().color(QtGui.QPalette.Text).name(),
                    **defaultGridKwargs)

            self._toolSpacePlotter.reset_camera()

        if whatToRedraw is None or 'tracker' in whatToRedraw:
            actorKey = 'tracker'
            if actorKey in self._toolSpaceActors:
                self._toolSpacePlotter.remove_actor(self._toolSpaceActors.pop(actorKey))
            if actorKey in self._trackerSpaceActors:
                self._trackerSpacePlotter.remove_actor(self._trackerSpaceActors.pop(actorKey))

            if self._tool.trackerStlToTrackerTransf is not None and self._tool.trackerSurf is not None:

                defaultGridKwargs = dict(
                    bold=False,
                )

                meshColor = self._tool.trackerColor
                scalars = None
                if meshColor is None:
                    if len(self._tool.trackerSurf.array_names) > 0:
                        meshColor = None  # use color from mesh file
                        scalars = self._tool.trackerSurf.array_names[0]
                    else:
                        meshColor = '#2222ff'
                actor = self._trackerSpacePlotter.add_mesh(
                    mesh=self._tool.trackerSurf,
                    color=meshColor,
                    scalars=scalars,
                    rgb=meshColor is None,
                    opacity=0.8,
                    name=actorKey
                )
                self._trackerSpaceActors[actorKey] = actor
                setActorUserTransform(actor, self._tool.trackerStlToTrackerTransf)
                self._trackerSpacePlotter.show_grid(
                    color=self._trackerSpacePlotter.palette().color(QtGui.QPalette.Text).name(),
                    **defaultGridKwargs)

                if self._tool.toolToTrackerTransf is not None:
                    actor = self._toolSpacePlotter.add_mesh(
                        mesh=self._tool.trackerSurf,
                        color=meshColor,
                        scalars=scalars,
                        rgb=meshColor is None,
                        opacity=0.8,
                        name=actorKey
                    )
                    self._toolSpaceActors[actorKey] = actor
                    setActorUserTransform(actor, concatenateTransforms([self._tool.trackerStlToTrackerTransf, invertTransform(self._tool.toolToTrackerTransf)]))
                    self._toolSpacePlotter.show_grid(
                        color=self._toolSpacePlotter.palette().color(QtGui.QPalette.Text).name(),
                        **defaultGridKwargs)

            self._trackerSpacePlotter.reset_camera()

    @property
    def wdgt(self):
        return self._wdgt

    def _onKeyEdited(self):
        self._tool.key = self._key.text()

    def _onTrackerKeyEdited(self):
        self._tool.trackerKey = self._trackerKey.text()

    def _onLabelEdited(self):
        newLabel = self._label.text().strip()
        if len(newLabel) == 0:
            newLabel = None
        self._tool.label = self._tool.label = newLabel

    def _onUsedForEdited(self):
        self._tool.usedFor = self._usedFor.currentText()

    def _onIsActiveEdited(self):
        self._tool.isActive = self._isActive.isChecked()

    def _onRomFilepathEdited(self):
        self._tool.romFilepath = self._romFilepath.filepath

    def _onTrackerStlFilepathEdited(self):
        self._tool.trackerStlFilepath = self._trackerStlFilepath.filepath

    def _onToolStlFilepathEdited(self):
        self._tool.toolStlFilepath = self._toolStlFilepath.filepath

    def _onToolStlToToolTransfEdited(self):
        newTransf = self._strToTransf(self._toolStlToToolTransf.text())
        if self._transfToStr(newTransf) == self._transfToStr(self._tool.toolStlToToolTransf):
            # no change
            return
        logger.info('User edited {} toolStlToToolTransf: {}'.format(self._tool.key, newTransf))
        self._tool.toolStlToToolTransf = newTransf

    def _onTrackerStlToTrackerTransfEdited(self):
        newTransf = self._strToTransf(self._trackerStlToTrackerTransf.text())
        if self._transfToStr(newTransf) == self._transfToStr(self._tool.trackerStlToTrackerTransf):
            # no change
            return
        logger.info('User edited {} trackerStlToTrackerTransf: {}'.format(self._tool.key, newTransf))
        self._tool.trackerStlToTrackerTransf = newTransf

    def _onToolToTrackerTransfEdited(self):
        newTransf = self._strToTransf(self._toolToTrackerTransf.text())
        if self._transfToStr(newTransf) == self._transfToStr(self._tool.toolToTrackerTransf):
            # no change
            return
        logger.info('User edited {} toolToTrackerTransf: {}'.format(self._tool.key, newTransf))
        self._tool.toolToTrackerTransf = newTransf

    def _onToolChanged(self, toolKey: str, attribsChanged: list[str] | None = None):
        toRedraw = set()
        if attribsChanged is None or 'key' in attribsChanged:
            self._key.setText(self._tool.key)
            self._trackerKey.setPlaceholderText(self._tool.key)
            self._label.setPlaceholderText(self._tool.key)
        if attribsChanged is None or 'trackerKey' in attribsChanged:
              if self._tool.trackerKeyIsSet:
                  self._trackerKey.setText(self._tool.trackerKey)
              else:
                  self._trackerKey.setText('')
        if attribsChanged is None or 'label' in attribsChanged:
            if self._tool.labelIsSet:
                self._label.setText(self._tool.label)
            else:
                self._label.setText('')
        if attribsChanged is None or 'usedFor' in attribsChanged:
            self._usedFor.setCurrentIndex(self._usedFor.findText(self._tool.usedFor) if self._tool.usedFor is not None else -1)  # TODO: check for change in type that we can't handle without reinstantiating
        if attribsChanged is None or 'isActive' in attribsChanged:
            self._isActive.setChecked(self._tool.isActive)
        if attribsChanged is None or 'romFilepath' in attribsChanged:
            self._romFilepath.filepath = self._tool.romFilepath
        if attribsChanged is None or 'trackerStlFilepath' in attribsChanged:
            self._trackerStlFilepath.filepath = self._tool.trackerStlFilepath
            toRedraw.add('tracker')
        if attribsChanged is None or 'toolStlFilepath' in attribsChanged:
            self._toolStlFilepath.filepath = self._tool.toolStlFilepath
            toRedraw.add('tool')
        if attribsChanged is None or 'filepathsRelToKey' in attribsChanged:
            for wdgt in (self._romFilepath, self._trackerStlFilepath, self._toolStlFilepath):
                wdgt.showRelativePrefix = self._tool.filepathsRelToKey
        if attribsChanged is None or 'toolStlToToolTransf' in attribsChanged:
            self._toolStlToToolTransf.setText(self._transfToStr(self._tool.toolStlToToolTransf))
            toRedraw.add('tool')
        if attribsChanged is None or 'trackerStlToTrackerTransf' in attribsChanged:
            self._trackerStlToTrackerTransf.setText(self._transfToStr(self._tool.trackerStlToTrackerTransf))
            toRedraw.add('tracker')
        if attribsChanged is None or 'toolToTrackerTransf' in attribsChanged:
            self._toolToTrackerTransf.setText(self._transfToStr(self._tool.toolToTrackerTransf))
            toRedraw |= {'tracker', 'tool'}

        if attribsChanged is None:
            self.redraw()
        else:
            self.redraw(toRedraw)

    def close(self):
        if not self._finishedAsyncInit.is_set():
            self._asyncInitTask.cancel()
        self._tool.sigItemChanged.disconnect(self._onToolChanged)
        self._toolSpacePlotter.close()
        self._trackerSpacePlotter.close()
        self._wdgt.deleteLater()  # TODO: verify this is correct way to remove from layout and also delete children

    @staticmethod
    def _transfToStr(transf: tp.Optional[np.ndarray]) -> str:
        if transf is None:
            return ''
        else:
            return transformToString(transf, precision=6)

    @staticmethod
    def _strToTransf(inputStr: str) -> tp.Optional[np.ndarray]:
        if len(inputStr.strip()) == 0:
            return None
        else:
            return stringToTransform(inputStr)


@attrs.define
class CoilToolWidget(ToolWidget):
    _tool: CoilTool

    _lastCalibratedAtLabel: QtWidgets.QLabel = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._lastCalibratedAtLabel = QtWidgets.QLabel('')
        self._formLayout.addRow('Last calibrated at', self._lastCalibratedAtLabel)
        self._updateLastCalibratedAt()

        btn = QtWidgets.QPushButton('Calibrate coil...')
        self._formLayout.addRow('', btn)
        btn.clicked.connect(lambda _: self._calibrate())

    def _updateLastCalibratedAt(self):
        historyTimes = [datetime.strptime(x, '%y%m%d%H%M%S.%f') for x in self._tool.toolToTrackerTransfHistory.keys()]
        if len(historyTimes) == 0:
            self._lastCalibratedAtLabel.setText('Never')
        else:
            lastCalibratedAt = historyTimes[-1]  # assume last in list is most recent
            self._lastCalibratedAtLabel.setText(lastCalibratedAt.strftime('%Y-%m-%d %H:%M:%S'))

    def _calibrate(self):
        CoilCalibrationWithPlateWindow(
            parent=self._wdgt,
            toolKeyToCalibrate=self._tool.key,
            session=self._session
        ).show()

    def _onToolChanged(self, toolKey: str, attribsChanged: list[str] | None = None):
        super()._onToolChanged(toolKey=toolKey, attribsChanged=attribsChanged)
        self._updateLastCalibratedAt()


@attrs.define
class PointerToolWidget(ToolWidget):
    _tool: Pointer

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        btn = QtWidgets.QPushButton('Calibrate pointer by multiple endpoint samples...')
        self._formLayout.addRow('', btn)
        btn.clicked.connect(lambda _: self._calibrateByEndpoint())

        btn = QtWidgets.QPushButton('Calibrate pointer with calibration plate...')
        self._formLayout.addRow('', btn)
        btn.clicked.connect(lambda _: self._calibrateWithPlate())

    def _calibrateWithPlate(self):
        # TODO: add extra arg to specify that pointer will be rotated 90 deg (tangential to calibration plate instead of perpendicular)
        CoilCalibrationWithPlateWindow(
            parent=self._wdgt,
            toolKeyToCalibrate=self._tool.key,
            session=self._session
        ).show()

    def _calibrateByEndpoint(self):
        PointerCalibrationWindow(
            parent=self._wdgt,
            toolKeyToCalibrate=self._tool.key,
            session=self._session
        ).show()


@attrs.define
class ToolsPanel(MainViewPanel):
    _key: str = 'Tools'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.hammer-screwdriver'))
    _trackingStatusWdgt: TrackingStatusWidget = attrs.field(init=False)
    _tblWdgt: ToolsTableWidget = attrs.field(init=False)
    _toolWdgt: tp.Optional[ToolWidget] = attrs.field(init=False, default=None)
    _wdgts: tp.Dict[str, QtWidgets.QWidget] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def canBeEnabled(self) -> tuple[bool, str | None]:
        if self.session is None:
            return False, 'No session set'
        return True, None

    def _finishInitialization(self):
        super()._finishInitialization()

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        sidebar = QtWidgets.QWidget()
        sidebar.setLayout(QtWidgets.QVBoxLayout())
        self._wdgt.layout().addWidget(sidebar)
        self._wdgt.layout().setAlignment(sidebar, QtCore.Qt.AlignLeft)
        sidebar.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.MinimumExpanding)

        self._trackingStatusWdgt = TrackingStatusWidget(session=self.session, hideInactiveTools=False)
        sidebar.layout().addWidget(self._trackingStatusWdgt.wdgt)

        container = QtWidgets.QGroupBox('Tools')
        container.setLayout(QtWidgets.QVBoxLayout())
        sidebar.layout().addWidget(container)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        container.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Import tool settings from file...')
        btn.clicked.connect(self._onImportToolsBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 0, 1, 3)

        btn = QtWidgets.QPushButton('Add')
        btn.clicked.connect(self._onAddBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 0)

        btn = QtWidgets.QPushButton('Duplicate')
        btn.clicked.connect(self._onDuplicateBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 1)

        btn = QtWidgets.QPushButton('Delete')
        btn.clicked.connect(self._onDeleteBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 2)

        self._tblWdgt = ToolsTableWidget(session=self.session)
        self._tblWdgt.sigCurrentItemChanged.connect(lambda *args: self._updateSelectedToolWdgt())
        container.layout().addWidget(self._tblWdgt.wdgt)

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

        self._updateSelectedToolWdgt()

    def _onSessionSet(self):
        super()._onSessionSet()

        if any(tool.initialTrackerPose is not None for tool in self.session.tools.values()):
            asyncio.create_task(asyncTryAndLogExceptionOnError(self._recordInitialToolPoses))

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    async def _recordInitialToolPoses(self):
        positionsClient = ToolPositionsClient()
        for tool in self.session.tools.values():
            if tool.initialTrackerPose is not None:
                await positionsClient.recordNewPosition(
                    tool.trackerKey,
                    position=TimestampedToolPosition(
                        time=0.,
                        transf=tool.initialTrackerPose,
                        relativeTo=tool.initialTrackerPoseRelativeTo
                    ))

    def _onPanelInitializedAndSessionSet(self):
        self._trackingStatusWdgt.session = self.session
        self._tblWdgt.session = self.session
        self._updateSelectedToolWdgt()

    def _onTblCurrentCellChanged(self, key: str):
        self._updateSelectedToolWdgt()

    def _onImportToolsBtnClicked(self, checked: bool):
        newFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._wdgt,
                                                               'Select tools file to import',
                                                               os.path.dirname(self.session.filepath),
                                                               'json (*.json);; NaviNIBS (*.navinibs)')

        if len(newFilepath) == 0:
            logger.warning('Import cancelled')
            return

        self.session.mergeFromFile(filepath=newFilepath, sections=['tools'])

    def _updateSelectedToolWdgt(self):
        logger.debug('Updating selected tool widget')
        currentToolKey = self._tblWdgt.currentCollectionItemKey
        # TODO: if possible, only update specific fields rather than fully recreating widget
        if self._toolWdgt is not None:
            self._toolWdgt.close()
            self._toolWdgt = None

        if currentToolKey is None:
            return

        if isinstance(self.session.tools[currentToolKey], CoilTool):
            ToolWidgetCls = CoilToolWidget
        elif isinstance(self.session.tools[currentToolKey], Pointer):
            ToolWidgetCls = PointerToolWidget
        else:
            ToolWidgetCls = ToolWidget

        self._toolWdgt = ToolWidgetCls(tool=self.session.tools[currentToolKey], session=self.session)
        self._wdgt.layout().addWidget(self._toolWdgt.wdgt)

    def _onAddBtnClicked(self, checked: bool):
        logger.info('Add tool btn clicked')
        self.session.tools.addItemFromDict(dict(key=makeStrUnique('Tool', self.session.tools.keys()), usedFor='coil'))

    def _onDuplicateBtnClicked(self, checked: bool):
        logger.info('Duplicate tool btn clicked')
        toolDict = self._tblWdgt.currentCollectionItem.asDict().copy()
        toolDict['key'] = makeStrUnique(toolDict['key'], self.session.tools.keys())
        self.session.tools.addToolFromDict(toolDict)

    def _onDeleteBtnClicked(self, checked: bool):
        keysToDelete = [key for key in self._tblWdgt.selectedCollectionItemKeys if key is not None]
        if len(keysToDelete) > 0:
            self.session.tools.deleteItems(keysToDelete)











