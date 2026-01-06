from __future__ import annotations

import asyncio

import attrs
from datetime import datetime
import enum
import json
import logging
import numpy as np
import os
import pathlib
import pytransform3d.rotations as ptr
import pyvista as pv
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from NaviNIBS.Navigator.Model.TargetGrids import CartesianTargetGrid, DepthMethod, EntryAngleMethod, SpacingMethod
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.GUI.Dock import Dock, DockArea
from NaviNIBS.Navigator.GUI.Widgets.MRIViews import MRISliceView
from NaviNIBS.Navigator.GUI.Widgets.SurfViews import Surf3DView
from NaviNIBS.Navigator.GUI.Widgets.CollectionTableWidget import FullTargetsTableWidget
from NaviNIBS.Navigator.GUI.Widgets.CollectionTableWidget import TargetGridsTableWidget
from NaviNIBS.Navigator.GUI.Widgets.EditTargetWidget import EditTargetWidget
from NaviNIBS.Navigator.GUI.Widgets.EditGridWidget import EditGridWidget
from NaviNIBS.Navigator.GUI.ViewPanels.MainViewPanelWithDockWidgets import MainViewPanelWithDockWidgets
from NaviNIBS.Navigator.GUI.ViewPanels.VisualizedROI import VisualizedROI, refreshROIAutoColors
from NaviNIBS.util import makeStrUnique
from NaviNIBS.util.GUI.Icons import getIcon
from NaviNIBS.util.GUI.QCollapsibleSection import QCollapsibleSection
from NaviNIBS.util.GUI.QueuedRedrawMixin import QueuedRedrawMixin
from NaviNIBS.util.pyvista import Actor, RemotePlotterProxy
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import composeTransform, applyTransform, invertTransform, concatenateTransforms
from NaviNIBS.Navigator.Model.Session import Session, Target


logger = logging.getLogger(__name__)


class TargetDisplayStyle(enum.StrEnum):
    TARGET_COORD = 'Target coordinate'
    ENTRY_COORD = 'Entry coordinate'
    COIL_COORD = 'Coil coordinate'
    ENTRY = 'Entry line'
    EXTENDED_ENTRY = 'Extended entry line'
    HANDLE = 'Handle line'
    HANDLE_AND_ENTRY = 'Handle and entry lines'
    HANDLE_AND_EXTENDED_ENTRY = 'Handle and extended entry lines'
    MINICOIL_AND_ENTRY = 'Mini coil and entry lines'
    MINICOIL_AND_EXTENDED_ENTRY = 'Mini coil and extended entry lines'

@attrs.define
class VisualizedTarget:
    """
    Note: this doesn't connect to any change signals from `target`, instead assuming that caller will
    re-instantiate the `VisualizedTarget` for any target changes.
    """
    _target: Target
    _plotter: DefaultBackgroundPlotter = attrs.field(repr=False)
    _style: TargetDisplayStyle = attrs.field(converter=TargetDisplayStyle)
    _actorKeys: set[str] = attrs.field(init=False, factory=set, repr=False)
    _visible: bool = True  # track this separately from self._target.isVisible to allow temporarily overriding
    _fallbackColor: str = '#2222FF'

    def __attrs_post_init__(self):
        self.plot()

    @property
    def target(self):
        return self._target

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, isVisible: bool):
        if self._visible == isVisible:
            return

        with self._plotter.allowNonblockingCalls():
            self._plotter.set_actors_visibility(self._actorKeys, isVisible)

        self._visible = isVisible

        if len(self._actorKeys) == 0:
            if isVisible:
                # needs to be (re)plotted
                self.plot()
        else:
            # actor visibility was just changed
            with self._plotter.allowNonblockingCalls():
                self._plotter.render()

    @property
    def style(self):
        return self._style

    @style.setter
    def style(self, style: TargetDisplayStyle):
        if self._style == style:
            return

        self.clearActors()
        self._style = style
        self.plot()

    @property
    def color(self):
        return self._target.color if self._target.color is not None else self._fallbackColor

    def plot(self):
        if isinstance(self._plotter, RemotePlotterProxy) and not self._plotter.isReadyEvent.is_set():
            # plotter not ready yet
            return

        if not self.visible:
            return

        logger.debug(f'Plotting target {self._target.key} with style {self._style}')

        thinWidth = 3
        thickWidth = 6

        if self._style not in (
            TargetDisplayStyle.COIL_COORD,
            TargetDisplayStyle.ENTRY_COORD,
            TargetDisplayStyle.HANDLE
        ) and self._target.targetCoord is not None:
            # plot target coordinate as small sphere
            actorKey = self._target.key + 'target'
            self._actorKeys.add(actorKey)
            with self._plotter.allowNonblockingCalls():
                self._plotter.add_points(self._target.targetCoord,
                                         color=self.color,
                                         point_size=10.,
                                         render_points_as_spheres=True,
                                         label=self._target.key,
                                         name=actorKey,
                                         render=False,
                                         reset_camera=False)

        if self._style in (
            TargetDisplayStyle.ENTRY_COORD,
        ) and self._target.entryCoord is not None:
            # plot entry coordinate as small sphere
            actorKey = self._target.key + 'entry'
            self._actorKeys.add(actorKey)
            with self._plotter.allowNonblockingCalls():
                self._plotter.add_points(self._target.entryCoord,
                                         color=self.color,
                                         point_size=10.,
                                         render_points_as_spheres=True,
                                         label=self._target.key,
                                         name=actorKey,
                                         render=False,
                                         reset_camera=False)

        if self._style not in (
            TargetDisplayStyle.TARGET_COORD,
            TargetDisplayStyle.ENTRY_COORD,
        ) and self._target.entryCoordPlusDepthOffset is not None:
            # plot coil coordinate as small sphere
            actorKey = self._target.key + 'coilcoord'
            self._actorKeys.add(actorKey)
            with self._plotter.allowNonblockingCalls():
                self._plotter.add_points(self._target.entryCoordPlusDepthOffset,
                                         color=self.color,
                                         point_size=10.,
                                         render_points_as_spheres=True,
                                         label=self._target.key,
                                         name=actorKey,
                                         render=False,
                                         reset_camera=False)

        if self._style in (
            TargetDisplayStyle.ENTRY,
            TargetDisplayStyle.EXTENDED_ENTRY,
            TargetDisplayStyle.HANDLE_AND_ENTRY,
            TargetDisplayStyle.HANDLE_AND_EXTENDED_ENTRY,
            TargetDisplayStyle.MINICOIL_AND_ENTRY,
            TargetDisplayStyle.MINICOIL_AND_EXTENDED_ENTRY,
        ) and self._target.targetCoord is not None and self._target.entryCoordPlusDepthOffset is not None:
            # draw line between (offset) entry and target coord
            pts_line = np.vstack((self._target.entryCoordPlusDepthOffset, self._target.targetCoord))
            actorKey = self._target.key + 'entryline'
            self._actorKeys.add(actorKey)
            with self._plotter.allowNonblockingCalls():
                self._plotter.add_lines(pts_line,
                                        color=self.color,
                                        width=thickWidth,
                                        label=self._target.key,
                                        name=actorKey,
                                        )

        if self._style in (
            TargetDisplayStyle.HANDLE,
            TargetDisplayStyle.HANDLE_AND_ENTRY,
            TargetDisplayStyle.HANDLE_AND_EXTENDED_ENTRY,
            TargetDisplayStyle.MINICOIL_AND_ENTRY,
            TargetDisplayStyle.MINICOIL_AND_EXTENDED_ENTRY,
        ) and self._target.coilToMRITransf is not None:
            # draw line for coil handle
            coilHandleLength = 7
            pts_line = applyTransform(self._target.coilToMRITransf, np.asarray([[0, -coilHandleLength, 0], [0, 0, 0]]))
            actorKey = self._target.key + 'handleline'
            self._actorKeys.add(actorKey)
            with self._plotter.allowNonblockingCalls():
                self._plotter.add_lines(pts_line,
                                        color=self.color,
                                        width=thinWidth,
                                        label=self._target.key,
                                        name=actorKey,
                                        )

        if self._style in (
            TargetDisplayStyle.EXTENDED_ENTRY,
            TargetDisplayStyle.HANDLE_AND_EXTENDED_ENTRY,
            TargetDisplayStyle.MINICOIL_AND_EXTENDED_ENTRY,
        ) and self._target.coilToMRITransf is not None:
            # draw extended depth axis line
            pts_line = applyTransform(self._target.coilToMRITransf, np.asarray([[0, 0, -50], [0, 0, 10]]))
            actorKey = self._target.key + 'depthline'
            self._actorKeys.add(actorKey)
            with self._plotter.allowNonblockingCalls():
                self._plotter.add_lines(pts_line,
                                        color=self.color,
                                        width=thinWidth,
                                        label=self._target.key,
                                        name=actorKey,
                                        )

        if self._style in (
            TargetDisplayStyle.MINICOIL_AND_ENTRY,
            TargetDisplayStyle.MINICOIL_AND_EXTENDED_ENTRY,
        ):
            # draw mini coil figure 8
            coilDiameter = 10


            theta = np.linspace(0, 2*np.pi, 361)
            circlePts = np.column_stack((coilDiameter/2 * np.cos(theta), coilDiameter/2 * np.sin(theta), np.zeros(theta.shape)))

            with self._plotter.allowNonblockingCalls():
                for wing, dir in (('wing1', 1.), ('wing2', -1.)):
                    if self._target.coilToMRITransf is not None:
                        pts_wing = applyTransform(self._target.coilToMRITransf, circlePts + dir*np.asarray([coilDiameter/2, 0, 0]))
                        actorKey = self._target.key + wing
                        self._actorKeys.add(actorKey)
                        self._plotter.add_lines(pts_wing,
                                                connected=True,
                                                color=self.color,
                                                width=thinWidth,
                                                label=self._target.key,
                                                name=actorKey,
                                                )

        with self._plotter.allowNonblockingCalls():
            if not self.visible:
                self._plotter.set_actors_visibility(self._actorKeys, False)

            self._plotter.render()

    def clearActors(self):
        if len(self._actorKeys) == 0:
            return

        with self._plotter.allowNonblockingCalls():
            for actorKey in self._actorKeys:
                self._plotter.remove_actor(actorKey)
            self._actorKeys.clear()
            self._plotter.render()


@attrs.define
class TargetsPanel(MainViewPanelWithDockWidgets, QueuedRedrawMixin):
    _key: str = 'Set targets'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: getIcon('mdi6.head-flash-outline'))
    _tableWdgt: FullTargetsTableWidget = attrs.field(init=False)
    _gridTableWdgt: TargetGridsTableWidget = attrs.field(init=False)
    _views: dict[str, tp.Union[MRISliceView, Surf3DView]] = attrs.field(init=False, factory=dict)
    _3DView: Surf3DView = attrs.field(init=False)
    _targetActors: dict[str, VisualizedTarget] = attrs.field(init=False, factory=dict)
    _visualizedROIs: dict[str, VisualizedROI] = attrs.field(init=False, factory=dict)

    _targetDock: Dock = attrs.field(init=False)
    _gridDock: Dock = attrs.field(init=False)
    _editTargetWdgt: EditTargetWidget = attrs.field(init=False)
    _editGridWdgt: EditGridWidget = attrs.field(init=False)
    _addGridBtn: QtWidgets.QPushButton = attrs.field(init=False)

    _dispSection: QCollapsibleSection = attrs.field(init=False)
    _targetDispStyle_comboBox: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)
    _crosshairsDispCheckbox: QtWidgets.QCheckBox = attrs.field(init=False)
    _export3DBtn: QtWidgets.QPushButton = attrs.field(init=False)

    _surfKeys: tp.List[str] = attrs.field(factory=lambda: ['gmSurf', 'skinSurf'])

    _defaultGridKeyPrefix: str = attrs.field(default='<Target> grid')
    _defaultTargetColor: str = attrs.field(default='#2222FF')

    _enabledOnlyWhenTargetSelected: list[QtWidgets.QWidget] = attrs.field(init=False, factory=list)
    _enabledOnlyWhenTargetGridSelected: list[QtWidgets.QWidget] = attrs.field(init=False, factory=list)

    _redrawTargetKeys: set[str] = attrs.field(init=False, factory=set)
    _redrawROIKeys: set[str] = attrs.field(init=False, factory=set)

    finishedAsyncInit: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        MainViewPanelWithDockWidgets.__attrs_post_init__(self)
        QueuedRedrawMixin.__attrs_post_init__(self)

    def canBeEnabled(self) -> tuple[bool, str | None]:
        if self.session is None:
            return False, 'No session set'
        if not self.session.MRI.isSet:
            return False, 'No MRI set'
        if not self.session.headModel.skinSurfIsSet:
            return False, 'No skin surface set'
        if not self.session.headModel.gmSurfIsSet:
            return False, 'No gray matter surface set'
        return True, None

    @staticmethod
    def _getRotMatForCoilAxis(axis: str) -> np.ndarray:
        if axis == 'x':
            return ptr.active_matrix_from_extrinsic_euler_yxy([-np.pi/2, np.pi/2, 0])
        elif axis == 'y':
            return ptr.active_matrix_from_angle(0, np.pi/2)
        elif axis in ('z', '3D'):
            return np.eye(3)
        else:
            raise NotImplementedError()

    def _finishInitialization(self):
        # don't initialize computationally-demanding views until panel is activated (viewed)

        super()._finishInitialization()

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        dock, container = self._createDockWidget(
            title='Targets',
            layout=QtWidgets.QVBoxLayout()
        )
        dock.setStretch(1, 10)
        container.layout().setContentsMargins(0, 0, 0, 0)
        self._wdgt.addDock(dock, position='left')
        self._targetDock = dock

        splitContainer = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        container.layout().addWidget(splitContainer)
        splitContainer.setChildrenCollapsible(False)
        # TODO: override parent restoreLayoutIfAvailable to add restoration of splitter state

        upperContainer = QtWidgets.QWidget()
        upperContainer.setLayout(QtWidgets.QVBoxLayout())
        upperContainer.layout().setContentsMargins(0, 0, 0, 0)
        splitContainer.addWidget(upperContainer)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        upperContainer.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Import targets from file...')
        btn.clicked.connect(self._onImportTargetsBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 0, 1, 2)

        btn = QtWidgets.QPushButton('Add target')
        btn.clicked.connect(self._onAddBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 0)

        btn = QtWidgets.QPushButton('Delete target')
        btn.clicked.connect(self._onDeleteBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 1)
        self._enabledOnlyWhenTargetSelected.append(btn)

        btn = QtWidgets.QPushButton('Duplicate target')
        btn.clicked.connect(self._onDuplicateBtnClicked)
        btnContainer.layout().addWidget(btn, 2, 0)
        self._enabledOnlyWhenTargetSelected.append(btn)

        btn = QtWidgets.QPushButton('Goto target')
        btn.clicked.connect(self._onGotoBtnClicked)
        btnContainer.layout().addWidget(btn, 2, 1)
        self._enabledOnlyWhenTargetSelected.append(btn)

        self._tableWdgt = FullTargetsTableWidget(defaultTargetColor=self._defaultTargetColor)
        self._tableWdgt.sigCurrentItemChanged.connect(self._onCurrentTargetChanged)
        self._tableWdgt.sigSelectionChanged.connect(self._onSelectionChanged)
        self._tableWdgt.wdgt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Expanding)
        self._tableWdgt.wdgt.setMinimumHeight(100)
        upperContainer.layout().addWidget(self._tableWdgt.wdgt)

        self._dispSection = QCollapsibleSection('Display options', doStartCollapsed=True)
        fieldLayout = QtWidgets.QFormLayout()
        self._dispSection.innerWdgt.setLayout(fieldLayout)
        upperContainer.layout().addWidget(self._dispSection.outerWdgt)
        self._targetDispStyle_comboBox.addItem('Auto')
        self._targetDispStyle_comboBox.addItems([key.value for key in TargetDisplayStyle])
        self._targetDispStyle_comboBox.setCurrentIndex(0)  # auto
        self._targetDispStyle_comboBox.currentIndexChanged.connect(self._onTargetDispStyleChanged)
        fieldLayout.addRow('Target display style:', self._targetDispStyle_comboBox)

        self._crosshairsDispCheckbox = QtWidgets.QCheckBox()
        self._crosshairsDispCheckbox.setChecked(True)
        self._crosshairsDispCheckbox.stateChanged.connect(self._onCrosshairsDispCheckboxChanged)
        fieldLayout.addRow('Show cursor crosshairs', self._crosshairsDispCheckbox)

        self._export3DBtn = QtWidgets.QPushButton('Export 3D view...')
        self._export3DBtn.clicked.connect(self._onExport3DBtnClicked)
        fieldLayout.addRow(self._export3DBtn)

        self._editTargetWdgt = EditTargetWidget(session=self.session,
                                                wdgt=QtWidgets.QWidget(),
                                                getNewTargetCoord=self._getCrosshairCoord,
                                                setTargetCoordButtonLabel='Set from crosshair position',
                                                getNewEntryCoord=self._getCrosshairCoord,
                                                setEntryCoordButtonLabel='Set from crosshair position',
                                                )

        self._editTargetWdgt.wdgt.layout().setContentsMargins(0, 0, 0, 0)
        splitContainer.addWidget(self._editTargetWdgt.wdgt)

        dock, container = self._createDockWidget(
            title='Target grids',
            layout=QtWidgets.QVBoxLayout()
        )
        dock.setStretch(1, 10)
        container.layout().setContentsMargins(0, 0, 0, 0)
        self._wdgt.addDock(dock, position='below', relativeTo=self._targetDock)
        self._gridDock = dock

        splitContainer = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        container.layout().addWidget(splitContainer)
        splitContainer.setChildrenCollapsible(False)
        # TODO: override parent restoreLayoutIfAvailable to add restoration of splitter state

        upperContainer = QtWidgets.QWidget()
        upperContainer.setLayout(QtWidgets.QVBoxLayout())
        upperContainer.layout().setContentsMargins(0, 0, 0, 0)
        splitContainer.addWidget(upperContainer)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        upperContainer.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Import target grids from file...')
        btn.clicked.connect(self._onImportTargetGridsBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 0, 1, 2)

        btn = QtWidgets.QPushButton('Add grid')
        btn.clicked.connect(self._onAddTargetGridBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 0)
        self._addGridBtn = btn

        btn = QtWidgets.QPushButton('Delete grid')
        btn.clicked.connect(self._onDeleteTargetGridBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 1)
        self._enabledOnlyWhenTargetGridSelected.append(btn)

        btn = QtWidgets.QPushButton('Duplicate grid')
        btn.clicked.connect(self._onDuplicateTargetGridBtnClicked)
        btnContainer.layout().addWidget(btn, 2, 0)
        self._enabledOnlyWhenTargetGridSelected.append(btn)

        self._gridTableWdgt = TargetGridsTableWidget()
        self._gridTableWdgt.sigCurrentItemChanged.connect(self._onCurrentTargetGridChanged)
        self._gridTableWdgt.sigSelectionChanged.connect(self._onTargetGridSelectionChanged)
        self._tableWdgt.wdgt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                                           QtWidgets.QSizePolicy.Policy.Expanding)
        self._tableWdgt.wdgt.setMinimumHeight(100)
        upperContainer.layout().addWidget(self._gridTableWdgt.wdgt)

        self._editGridWdgt = EditGridWidget(session=self.session, wdgt=QtWidgets.QWidget())
        self._editGridWdgt.wdgt.layout().setContentsMargins(0, 0, 0, 0)
        splitContainer.addWidget(self._editGridWdgt.wdgt)

        dock, container = self._createDockWidget(
            title='Target views',
            layout=QtWidgets.QGridLayout()
        )
        container.layout().setContentsMargins(0, 0, 0, 0)
        self._wdgt.addDock(dock, position='right')

        # TODO: put each view in its own dockwidget instead of a grid layout so they can be moved around

        for iRow, iCol, key in ((0, 1, 'x'), (0, 0, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            if key in ('x', 'y', 'z'):
                self._views[key] = MRISliceView(label=key, normal=self._getRotMatForCoilAxis(key))
            elif key == '3D':
                self._views[key] = Surf3DView(label=key, normal=np.eye(3),
                                              activeSurf=self._surfKeys,
                                              pickableSurfs=[self._surfKeys[0]],  # assuming surfKeys are [gm, skin], make only gm pickable
                                              cameraOffsetDist=50,
                                              surfOpacity=[0.8, 0.5])
                self._3DView = self._views[key]
            else:
                raise NotImplementedError()

            self._views[key].sigSliceTransformChanged.connect(lambda key=key: self._onSliceTransformChanged(sourceKey=key))

            container.layout().addWidget(self._views[key].wdgt, iRow, iCol)

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

        for widget in self._enabledOnlyWhenTargetSelected:
            logger.debug(f'Disabling {widget}')
            widget.setEnabled(False)

        self._onTargetsChanged()

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._finishInitialization_async))

    async def _finishInitialization_async(self):
        for view in self._views.values():
            if isinstance(view.plotter, RemotePlotterProxy):
                await view.plotter.isReadyEvent.wait()

        self._onTargetsChanged()

        self._onROIsChanged()

        self.finishedAsyncInit.set()

    def _getCrosshairCoord(self) -> np.ndarray:
        """
        Used by CoordinateWidgets to set target or entry coord from current crosshair position
        """
        return self._views['3D'].sliceOrigin

    def _getDispStyle(self, forTargetKey: str) -> TargetDisplayStyle:
        dispStyle = self._targetDispStyle_comboBox.currentText()
        if dispStyle == 'Auto':
            if self.session is None:
                dispStyle = TargetDisplayStyle.TARGET_COORD
            else:
                if True:
                    # set style based on distance to other targets (can vary per target)
                    minDistToOtherTargets = np.inf
                    try:
                        target = self.session.targets[forTargetKey]
                    except KeyError:
                        return TargetDisplayStyle.TARGET_COORD
                    for otherTargetKey, otherTarget in self.session.targets.items():
                        if otherTargetKey == forTargetKey:
                            continue
                        if not otherTarget.isVisible:
                            continue
                        if otherTarget.entryCoordPlusDepthOffset is None or target.entryCoordPlusDepthOffset is None:
                            continue
                        dist = np.linalg.norm(otherTarget.entryCoordPlusDepthOffset - target.entryCoordPlusDepthOffset)
                        if dist < minDistToOtherTargets:
                            minDistToOtherTargets = dist

                    if minDistToOtherTargets > 15:
                        dispStyle = TargetDisplayStyle.MINICOIL_AND_EXTENDED_ENTRY
                    else:
                        dispStyle = TargetDisplayStyle.HANDLE_AND_EXTENDED_ENTRY
                else:
                    # set style based on total number of visible targets
                    numVisibleTargets = sum(1 for target in self.session.targets.values() if target.isVisible)
                    if numVisibleTargets <= 10:
                        dispStyle = TargetDisplayStyle.MINICOIL_AND_EXTENDED_ENTRY
                    else:
                        dispStyle = TargetDisplayStyle.HANDLE_AND_EXTENDED_ENTRY
        else:
            dispStyle = TargetDisplayStyle(dispStyle)
        return dispStyle

    def _onTargetDispStyleChanged(self, index: int | None = None):
        for visualizedTarget in self._targetActors.values():
            dispStyle = self._getDispStyle(forTargetKey=visualizedTarget.target.key)
            visualizedTarget.style = dispStyle

    def _onCrosshairsDispCheckboxChanged(self, state: int):
        showCrosshairs = (state == QtCore.Qt.CheckState.Checked.value)
        for view in self._views.values():
            view.doShowCrosshairs = showCrosshairs

    def _onExport3DBtnClicked(self):
        filepath, _ = QtWidgets.QFileDialog.getSaveFileName(self._wdgt,
                                                            'Export 3D view to file',
                                                            '',
                                                            'GLTF (*.gltf);;All Files (*)')
        if not filepath:
            logger.info('User cancelled 3D view export')
            return

        logger.debug(f'Exporting 3D view to file {filepath}')

        self._3DView.plotter.export_gltf(filepath)

    def _onSliceTransformChanged(self, sourceKey: str):
        logger.debug('Slice {} transform changed'.format(sourceKey))
        crossTransf = self._views[sourceKey].sliceTransform @ composeTransform(np.linalg.pinv(self._getRotMatForCoilAxis(axis=sourceKey)))  # TODO: double check order
        for key, view in self._views.items():
            if key == sourceKey:
                continue
            logger.debug('Updating {} slice transform'.format(key))
            view.sliceTransform = crossTransf @ composeTransform(self._getRotMatForCoilAxis(axis=key))

    def _onSessionSet(self):
        super()._onSessionSet()

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):
        self.session.targets.sigItemsChanged.connect(self._onTargetsChanged)
        self.session.ROIs.sigItemsChanged.connect(self._onROIsChanged)
        self._tableWdgt.session = self.session
        self._gridTableWdgt.session = self.session

        for key, view in self._views.items():
            view.session = self.session

    def _getCurrentTargetKey(self) -> tp.Optional[str]:
        return self._tableWdgt.currentCollectionItemKey

    def _onSelectionChanged(self, keys: list[str]):
        for widget in self._enabledOnlyWhenTargetSelected:
            logger.debug(f"{'Disabling' if len(keys)==0 else 'Enabling'} {widget}")
            widget.setEnabled(len(keys)>0)

    def _onCurrentTargetChanged(self, targetKey: str | None):
        if targetKey is not None:
            self._gotoTarget(targetKey=targetKey)    # go to target immediately whenever selection changes

    def _gotoTarget(self, targetKey: str):

        logger.debug('gotoTarget')

        plotter = self._views['3D'].plotter
        if isinstance(plotter, RemotePlotterProxy) and not plotter.isReadyEvent.is_set():
            return  # plotter not ready yet

        # change slice camera views to align with selected target
        target = self.session.targets[targetKey]
        extraTransf = np.eye(4)
        if True:
            # align at target depth
            extraTransf[:3, 3] = applyTransform(invertTransform(target.coilToMRITransf), target.targetCoord, doCheck=False)
        else:
            # align at coil depth
            pass

        self._views['3D'].sliceTransform = concatenateTransforms([
            composeTransform(self._getRotMatForCoilAxis(axis='3D')),
            extraTransf,
            target.coilToMRITransf])

        if True:
            # also set camera position for 3D view to align with target
            plotter = self._views['3D'].plotter
            with plotter.allowNonblockingCalls():
                plotter.camera.focal_point = self._views['3D'].sliceOrigin
                cameraPos = applyTransform(self._views['3D'].sliceTransform, np.asarray([0, 0, 200]))
                plotter.camera.position = cameraPos
                plotter.camera.up = applyTransform(self._views['3D'].sliceTransform,
                                                                 np.asarray([0, 100, 0])) - cameraPos
                plotter.render()

    def _createVisualForTarget(self, viewKey: str, target: Target):
        if False:
            logger.debug(f'Creating VisualizedTarget for target {target.asDict()} {target.coilToMRITransf}')
        else:
            logger.debug(f'Creating VisualizedTarget for target {target.key}')
        view = self._views[viewKey]
        style = self._getDispStyle(forTargetKey=target.key)
        self._targetActors[viewKey + target.key] = VisualizedTarget(target=target,
                                                                    fallbackColor=self._defaultTargetColor,
                                                                    plotter=view.plotter,
                                                                    style=style,
                                                                    visible=target.isVisible)

    def _onTargetsChanged(self, changedTargetKeys: tp.Optional[tp.List[str]] = None, changedTargetAttrs: tp.Optional[tp.List[str]] = None):
        if not self._hasInitialized and not self._isInitializing:
            return

        # TODO: move this below to only delay visualization initialization, not target table, etc.
        for view in self._views.values():
            if isinstance(view.plotter, RemotePlotterProxy) and not view.plotter.isReadyEvent.is_set():
                # plotter not ready yet
                return

        logger.debug(f'Targets changed. Updating tree view and plots.\nchangedTargetKeys={changedTargetKeys}, \nchangedTargetAttrs={changedTargetAttrs}')

        if changedTargetKeys is None:
            changedTargetKeys = self.session.targets.keys()

        if changedTargetAttrs == ['isVisible']:
            # only visibility changed

            for targetKey in changedTargetKeys:
                target = self._session.targets[targetKey]
                for viewKey in self._views:
                    actorKey = viewKey + targetKey
                    if actorKey in self._targetActors:
                        self._targetActors[actorKey].visible = target.isVisible
                    elif target.isVisible:
                        self._redrawTargetKeys.add(targetKey)
                        self._queueRedraw(which='targets')

            self._onTargetDispStyleChanged()  # if in auto mode and number of visible targets changed, style may have changed

        elif changedTargetAttrs == ['isSelected']:
            pass  # selection update will be handled separately

        else:
            # assume anything/everything changed, clear target and start over
            self._redrawTargetKeys.update(changedTargetKeys)
            self._queueRedraw(which='targets')

    def _onROIsChanged(self, changedKeys: list[str] | None = None, changedAttrs: list[str] | None = None):
        if not self._hasInitialized and not self._isInitializing:
            return

        for view in (self._3DView,):
            if isinstance(view.plotter, RemotePlotterProxy) and not view.plotter.isReadyEvent.is_set():
                # plotter not ready yet
                return

        logger.debug(f'onROIsChanged: {changedKeys}, {changedAttrs}')

        if changedAttrs is not None:
            # remove attrs that can be handled by existing visualizations or can be ignored
            changedAttrs = changedAttrs.copy()
            for changedAttr in ['output', 'autoColor', 'stages']:
                try:
                    changedAttrs.remove(changedAttr)
                except ValueError:
                    pass
            if len(changedAttrs) == 0:
                return

        if changedKeys is None:
            changedKeys = self.session.ROIs.keys()

        if changedAttrs is None or 'color' in changedAttrs:
            self._queueRedraw(which='ROIAutoColors')

        if changedAttrs == ['isVisible']:
            # only visibility changed
            self._queueRedraw(which='ROIAutoColors')
            for roiKey in changedKeys:
                roi = self.session.ROIs[roiKey]
                if roiKey in self._visualizedROIs:
                    pass  # let existing visualized ROI update itself
                elif roi.isVisible:
                    self._redrawROIKeys.add(roiKey)
                    self._queueRedraw(which='ROIs')

        elif changedAttrs == ['isSelected']:
            pass

        else:
            # assume anything/everything changed, clear ROI and start over
            if changedAttrs is not None:
                logger.debug(f'Other ROI attrs changed, completely redrawing ({changedAttrs})')
            self._redrawROIKeys.update(changedKeys)
            self._queueRedraw(which='ROIs')


    def _redraw(self, which: tp.Union[str | None, list[str]] = None, **kwargs):
        super()._redraw(which=which)

        if which is None:
            self._redraw(which='all', **kwargs)
            return

        if not isinstance(which, str):
            for subWhich in which:
                self._redraw(which=subWhich, **kwargs)
            return

        if which == 'all':
            self._redraw(which=['targets'])
            return

        if which == 'targets':
            changedTargetKeys = self._redrawTargetKeys.copy()
            logger.debug(f'Redrawing targets: {changedTargetKeys}')
            self._redrawTargetKeys.clear()
            if self._hasInitialized or self._isInitializing:
                # update views
                for viewKey, view in self._views.items():
                    for key in changedTargetKeys:
                        try:
                            target = self.session.targets[key]
                        except KeyError as e:
                            # previous key no longer in targets
                            if viewKey + key in self._targetActors:
                                visualizedTarget = self._targetActors.pop(viewKey + key)
                                visualizedTarget.clearActors()
                        else:
                            if viewKey + key in self._targetActors:
                                visualizedTarget = self._targetActors.pop(viewKey + key)
                                visualizedTarget.clearActors()
                            if target.isVisible:
                                self._createVisualForTarget(viewKey=viewKey, target=target)

                    view.updateView()

                currentTarget = self._getCurrentTargetKey()
                self._onCurrentTargetChanged(currentTarget)

                self._onTargetDispStyleChanged()  # if in auto mode and number of visible targets changed, style may have changed

        if which == 'ROIAutoColors':
            refreshROIAutoColors(self._session)

        if which == 'ROIs':
            changedROIKeys = self._redrawROIKeys.copy()
            self._redrawROIKeys.clear()
            if self._hasInitialized or self._isInitializing:
                # update view
                for key in changedROIKeys:
                    try:
                        roi = self.session.ROIs[key]
                    except KeyError:
                        # previous key no longer in ROIs
                        if key in self._visualizedROIs:
                            visualizedROI = self._visualizedROIs.pop(key)
                            visualizedROI.clear()
                    else:
                        if key in self._visualizedROIs:
                            visualizedROI = self._visualizedROIs.pop(key)
                            visualizedROI.clear()

                        if roi.isVisible:
                            # create new visualization
                            if True:  # TODO: debug, set to true / remove conditional
                                visualizedROI = VisualizedROI(roi=roi,
                                                              session=self.session,
                                                              linked3DView=self._3DView)
                                self._visualizedROIs[key] = visualizedROI

                self._3DView.updateView()

    def _onImportTargetsBtnClicked(self, checked: bool):
        newFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._wdgt,
                                                               'Select targets file to import',
                                                               os.path.dirname(self.session.filepath),
                                                               'json (*.json);; NaviNIBS (*.navinibs)')

        if len(newFilepath) == 0:
            logger.warning('Import cancelled')
            return

        self._importTargetsFromFile(newFilepath=newFilepath)

    def _importTargetsFromFile(self, newFilepath: str):

        self.session.mergeFromFile(filepath=newFilepath, sections=['targets'])

        self._tableWdgt.resizeColumnsToContents()  # resize immediately rather than waiting for delayed auto-resize

    def _onAddBtnClicked(self, checked: bool):
        # create new target at current crosshair location, and autoset entry
        targetCoord = self._getCrosshairCoord()
        targetKey = makeStrUnique('Target-1', existingStrs=self.session.targets.keys(), delimiter='-')
        currentTargetKey = self._tableWdgt.currentCollectionItemKey
        if currentTargetKey is not None:
            # match angle from midline of current target for target in new position
            nextTargetAngle = self.session.targets[currentTargetKey].calculatedAngle
        else:
            nextTargetAngle = 0
        target = Target(targetCoord=targetCoord,
                        angle=nextTargetAngle,
                        key=targetKey,
                        session=self.session)
        target.autosetEntryCoord()
        self.session.targets.addItem(target)
        self._tableWdgt.currentCollectionItemKey = targetKey  # select new target

    def _onDeleteBtnClicked(self, checked: bool):
        selectedTargetKeys = self._tableWdgt.selectedCollectionItemKeys
        self.session.targets.deleteItems(selectedTargetKeys)

    def _onDuplicateBtnClicked(self, checked: bool):
        currentTargetKey = self._tableWdgt.currentCollectionItemKey
        assert currentTargetKey is not None
        if '_' in currentTargetKey:
            delim = '_'
        elif ' ' in currentTargetKey:
            delim = ' '
        else:
            delim = '-'
        newTargetKey = makeStrUnique(f'{currentTargetKey}{delim}copy{delim}1', existingStrs=self.session.targets.keys(), delimiter=delim)
        logger.info(f'Duplicating target {currentTargetKey} to {newTargetKey}')
        currentTarget = self.session.targets[currentTargetKey]
        newTarget = currentTarget.asDict()
        newTarget['key'] = newTargetKey
        self.session.targets.addItem(Target.fromDict(newTarget))

    def _onGotoBtnClicked(self, checked: bool):
        targetKey = self._getCurrentTargetKey()
        if targetKey is not None:
            self._gotoTarget(self._getCurrentTargetKey())
        else:
            logger.warning('No target selected')

    def _onImportTargetGridsBtnClicked(self, checked: bool):
        raise NotImplementedError()  # TODO: implement import of target grids from file

    def _onAddTargetGridBtnClicked(self, checked: bool):
        DefaultGridCls = CartesianTargetGrid

        gridKey = self._defaultGridKeyPrefix

        # if a target is currently selected, use it as the seed for the new grid
        currentTargetKey = self._getCurrentTargetKey()
        if currentTargetKey is not None:
            gridKey = gridKey.replace('<Target>', currentTargetKey)

        gridKey = makeStrUnique(gridKey, existingStrs=self.session.targetGrids.keys(), delimiter=None)

        grid = DefaultGridCls(
            key=gridKey,
            seedTargetKey=currentTargetKey,
            session=self.session,
            pivotDepth=120,
        )
        logger.info(f'Adding target grid {grid}')
        self.session.targetGrids.addItem(grid)
        self._editGridWdgt.grid = grid

    def _onDeleteTargetGridBtnClicked(self, checked: bool):
        gridKeys = self._gridTableWdgt.selectedCollectionItemKeys
        for gridKey in gridKeys:
            grid = self.session.targetGrids[gridKey]
            grid.deleteAnyGeneratedTargets()  # clean up any generated targets associated with the grid
        self.session.targetGrids.deleteItems(gridKeys)

    def _onDuplicateTargetGridBtnClicked(self, checked: bool):
        currentGridKey = self._gridTableWdgt.currentCollectionItemKey
        assert currentGridKey is not None
        if '_' in currentGridKey:
            delim = '_'
        elif ' ' in currentGridKey:
            delim = ' '
        else:
            delim = '-'
        newGridKey = makeStrUnique(f'{currentGridKey}{delim}copy{delim}1',
                                     existingStrs=self.session.targetGrids.keys(),
                                     delimiter=delim)
        logger.info(f'Duplicating target grid {currentGridKey} to {newGridKey}')
        currentGrid = self.session.targetGrids[currentGridKey]
        newGrid = currentGrid.asDict()
        newGrid['key'] = newGridKey
        try:
            del newGrid['generatedTargetKeys']  # don't copy over generated targets
        except KeyError:
            pass

        self.session.targetGrids.addItem(self.session.targetGrids.gridFromDict(newGrid, session=self.session))

    def _onCurrentTargetGridChanged(self, gridKey: str | None):
        self._editGridWdgt.grid = self.session.targetGrids[gridKey] if gridKey is not None else None

    def _onTargetGridSelectionChanged(self, keys: list[str]):
        for widget in self._enabledOnlyWhenTargetGridSelected:
            logger.debug(f"{'Disabling' if len(keys) == 0 else 'Enabling'} {widget}")
            widget.setEnabled(len(keys) > 0)

