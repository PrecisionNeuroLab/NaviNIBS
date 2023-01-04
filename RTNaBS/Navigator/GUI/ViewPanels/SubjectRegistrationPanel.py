from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
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
from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Navigator.GUI.Widgets.MRIViews import MRISliceView
from RTNaBS.Navigator.GUI.Widgets.SurfViews import Surf3DView
from RTNaBS.Navigator.GUI.Widgets.TrackingStatusWidget import TrackingStatusWidget
from RTNaBS.Navigator.GUI.Widgets.CollectionTableWidget import HeadPointsTableWidget
from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.Tools import CoilTool, CalibrationPlate
from RTNaBS.util.pyvista import Actor, setActorUserTransform
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import applyTransform, invertTransform, transformToString, stringToTransform, estimateAligningTransform, concatenateTransforms
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.util.GUI.QTableWidgetDragRows import QTableWidgetDragRows
from RTNaBS.util.pyvista.plotting import BackgroundPlotter


logger = logging.getLogger(__name__)


@attrs.define
class _PointerDistanceReadout:
    _label: str
    _parentLayout: QtWidgets.QFormLayout
    _units: str = ' mm'
    _value: float = np.nan
    _doHideWhenNaN: bool = False

    _wdgt: QtWidgets.QLabel | None= attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        self.update()

    def update(self):
        if self._doHideWhenNaN and np.isnan(self._value):
            if self._wdgt is not None:
                labelWdgt = self._parentLayout.labelForField(self._wdgt)
                assert isinstance(labelWdgt, QtWidgets.QLabel)
                labelWdgt.setText('')
                self._wdgt.setText('')
            return

        text = f"{self._value:.1f}{self._units}"
        if self._wdgt is None:
            self._wdgt = QtWidgets.QLabel(text)
            self._parentLayout.addRow(self._label, self._wdgt)
        else:
            self._wdgt.setText(text)

    @property
    def label(self):
        return self._label

    @label.setter
    def label(self, newLabel: str):
        self._label = newLabel
        if self._wdgt is not None:
            labelWdgt = self._parentLayout.labelForField(self._wdgt)
            assert isinstance(labelWdgt, QtWidgets.QLabel)
            labelWdgt.setText(self._label)

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, newVal: float):
        self._value = newVal
        self.update()


@attrs.define
class _PointerDistanceReadouts:
    _session: Session
    _positionsClient: ToolPositionsClient
    _title: str = 'Current pointer position'

    _wdgt: QtWidgets.QGroupBox = attrs.field(init=False)
    _layout: QtWidgets.QFormLayout = attrs.field(init=False)

    _distToSkinReadout: _PointerDistanceReadout = attrs.field(init=False)
    _distToPlannedFidReadout: _PointerDistanceReadout = attrs.field(init=False)
    _distToSampledFidReadout: _PointerDistanceReadout = attrs.field(init=False)

    def __attrs_post_init__(self):
        self._wdgt = QtWidgets.QGroupBox(self._title)
        self._layout = QtWidgets.QFormLayout()
        self._wdgt.setLayout(self._layout)

        self._distToSkinReadout = _PointerDistanceReadout(
            label='Dist to skin',
            parentLayout=self._layout,
            doHideWhenNaN=False
        )

        self._distToPlannedFidReadout = _PointerDistanceReadout(
            label='Dist to planned fid',
            parentLayout=self._layout,
            doHideWhenNaN=True
        )

        self._distToSampledFidReadout = _PointerDistanceReadout(
            label='Dist to sampled fid',
            parentLayout=self._layout,
            doHideWhenNaN=True
        )

        self._positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)

        # note: could subscribe to fiducial location and head model mesh change signal here, but instead
        # assume that pointer position changes often enough to catch up on any such changes anyway

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSes: Session):
        self._session = newSes

    def _onLatestPositionsChanged(self):
        self._update()

    def _update(self):
        pointerToCameraTransf = self._positionsClient.getLatestTransf(self.session.tools.pointer.key, None)
        subjectTrackerToCameraTransf = self._positionsClient.getLatestTransf(self.session.tools.subjectTracker.key, None)

        if pointerToCameraTransf is None or subjectTrackerToCameraTransf is None:
            # TODO: report NaNs for all distances
            return

        pointerCoord_relToSubTracker = applyTransform([self.session.tools.pointer.toolToTrackerTransf,
                                                       pointerToCameraTransf,
                                                       invertTransform(subjectTrackerToCameraTransf)
                                                       ], np.zeros((3,)))

        subjectTrackerToMRITransf = self.session.subjectRegistration.trackerToMRITransf
        if subjectTrackerToMRITransf is None:
            # TODO: report NaNS for all distances
            return

        pointerCoord_MRISpace = applyTransform(subjectTrackerToMRITransf, pointerCoord_relToSubTracker)

        # find distance to skin
        closestPtIndex = self.session.headModel.skinSurf.find_closest_point(pointerCoord_MRISpace)
        closestPt = self.session.headModel.skinSurf.points[closestPtIndex, :]
        dist = np.linalg.norm(closestPt - pointerCoord_MRISpace)
        self._distToSkinReadout.value = dist

        # determine which fiducial to compare to
        plannedFiducialCoords = self.session.subjectRegistration.plannedFiducials
        sampledFiducialCoords = self.session.subjectRegistration.sampledFiducials

        # look at both planned and sampled fiducials to choose single closest, then use that for both planned and sampled

        whichFidClosest: str | None = None
        closestDist = np.inf

        for whichType, coords in (('planned', plannedFiducialCoords), ('sampled', sampledFiducialCoords)):
            for whichFid, coord in coords.items():
                if coord is None:  # e.g. fiducial not yet sampled
                    continue
                if whichType == 'sampled':
                    # must do extra coordinate conversion from head tracker space to MRI space
                    coord = applyTransform(subjectTrackerToMRITransf, coord)
                dist = np.linalg.norm(coord - pointerCoord_MRISpace)
                if dist < closestDist:
                    closestDist = dist
                    whichFidClosest = whichFid

        if closestDist > 50:
            # don't show any fiducial distance feedback if not close to any fiducials
            whichFidClosest = None

        if whichFidClosest is None:
            # hide fiducial distance readouts
            self._distToPlannedFidReadout.value = np.nan
            self._distToSampledFidReadout.value = np.nan

        else:
            coord = plannedFiducialCoords[whichFidClosest]
            if coord is None:
                self._distToPlannedFidReadout.value = np.nan
            else:
                self._distToPlannedFidReadout.value = np.linalg.norm(coord - pointerCoord_MRISpace)
                self._distToPlannedFidReadout.label = f'Dist to planned {whichFidClosest}'

            coord = sampledFiducialCoords.get(whichFidClosest, None)
            if coord is None:
                self._distToSampledFidReadout.value = np.nan
            else:
                # must do extra coordinate conversion from head tracker space to MRI space
                coord = applyTransform(subjectTrackerToMRITransf, coord)

                self._distToSampledFidReadout.value = np.linalg.norm(coord - pointerCoord_MRISpace)
                self._distToSampledFidReadout.label = f'Dist to sampled {whichFidClosest}'



@attrs.define
class SubjectRegistrationPanel(MainViewPanel):
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.head-snowflake'))
    _surfKey: str = 'skinSurf'

    _trackingStatusWdgt: TrackingStatusWidget = attrs.field(init=False)
    _fidTblWdgt: QtWidgets.QTableWidget = attrs.field(init=False)
    _headPtsTblWdgt: HeadPointsTableWidget = attrs.field(init=False)
    _sampleFiducialBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _alignToFiducialsBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _sampleHeadPtsBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _plotter: BackgroundPlotter = attrs.field(init=False)
    _actors: tp.Dict[str, tp.Optional[Actor]] = attrs.field(init=False, factory=dict)
    _pointerDistanceReadouts: _PointerDistanceReadouts = attrs.field(init=False)

    _positionsClient: ToolPositionsClient | None = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def canBeEnabled(self) -> bool:
        return self.session is not None and self.session.MRI.isSet and self.session.headModel.isSet \
            and self.session.tools.subjectTracker is not None and self.session.tools.pointer is not None \
            and self.session.subjectRegistration.hasMinimumPlannedFiducials

    def _finishInitialization(self):
        super()._finishInitialization()

        self._positionsClient = ToolPositionsClient()
        self._positionsClient.sigLatestPositionsChanged.connect(lambda: self._redraw(which=['pointerPosition', 'sampleBtns']))

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        sidebar = QtWidgets.QWidget()
        sidebar.setLayout(QtWidgets.QVBoxLayout())
        sidebar.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.MinimumExpanding)
        self._wdgt.layout().addWidget(sidebar)

        self._trackingStatusWdgt = TrackingStatusWidget(session=self.session,
                                                        hideToolTypes=[CoilTool, CalibrationPlate])
        sidebar.layout().addWidget(self._trackingStatusWdgt.wdgt)

        fiducialsBox = QtWidgets.QGroupBox('Fiducials')
        fiducialsBox.setLayout(QtWidgets.QVBoxLayout())
        sidebar.layout().addWidget(fiducialsBox)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        fiducialsBox.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Sample fiducial')
        btn.clicked.connect(self._onSampleFidBtnClicked)
        self._sampleFiducialBtn = btn
        btnContainer.layout().addWidget(btn, 0, 0, )

        btn = QtWidgets.QPushButton('Clear fiducial')
        btn.clicked.connect(self._onClearFidBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 1)
        # TODO: change this to "clear fiducials" when multiple selected

        # TODO: add prev/next fiducial buttons for mapping to foot pedal actions
        # (i.e. without requiring click in fidTbl to select different fiducial)

        self._fidTblWdgt = QtWidgets.QTableWidget(0, 3)
        self._fidTblWdgt.setHorizontalHeaderLabels(['Fiducial', 'Planned', 'Sampled'])
        self._fidTblWdgt.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self._fidTblWdgt.currentCellChanged.connect(self._onFidTblCurrentCellChanged)
        self._fidTblWdgt.cellDoubleClicked.connect(self._onFidTblCellDoubleClicked)
        fiducialsBox.layout().addWidget(self._fidTblWdgt)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        fiducialsBox.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Align to sampled fiducials')
        btn.clicked.connect(self._onAlignToFidBtnClicked)
        self._alignToFiducialsBtn = btn
        btnContainer.layout().addWidget(btn, 0, 0, 1, 2)

        headPtsBox = QtWidgets.QGroupBox('Head shape points')
        headPtsBox.setLayout(QtWidgets.QVBoxLayout())
        sidebar.layout().addWidget(headPtsBox)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        headPtsBox.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Sample head point')
        btn.clicked.connect(self._onSampleHeadPtsBtnClicked)
        btn = self._sampleHeadPtsBtn = btn
        btnContainer.layout().addWidget(btn, 0, 0)

        btn = QtWidgets.QPushButton('Clear head point')
        btn.clicked.connect(self._onClearHeadPtsBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 1)
        # TODO: change this to "clear head points" when multiple selected

        self._headPtsTblWdgt = HeadPointsTableWidget()
        self._headPtsTblWdgt.sigSelectionChanged.connect(self._onSelectedHeadPointsChanged)

        headPtsBox.layout().addWidget(self._headPtsTblWdgt.wdgt)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        headPtsBox.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Refine with sampled head points')
        btn.clicked.connect(self._onAlignToHeadPtsBtnClicked)
        # TODO: set this to only be enabled when have already aligned to fiducials and when sufficient # head points have been sampled
        btnContainer.layout().addWidget(btn, 0, 0, 1, 2)

        self._pointerDistanceReadouts = _PointerDistanceReadouts(
            session=self.session,
            positionsClient=self._positionsClient,

        )
        sidebar.layout().addWidget(self._pointerDistanceReadouts.wdgt)

        sidebar.layout().addStretch()

        self._plotter = BackgroundPlotter(
            show=False,
            app=QtWidgets.QApplication.instance()
        )
        self._plotter.enable_depth_peeling(4)
        self._wdgt.layout().addWidget(self._plotter.interactor)

        self._redraw(which='all')

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

    def _onSessionSet(self):
        super()._onSessionSet()
        # TODO: connect relevant session changed signals to _redraw calls

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):
        self.session.headModel.sigDataChanged.connect(lambda which: self._redraw(which='initSurf'))
        self.session.subjectRegistration.sigPlannedFiducialsChanged.connect(lambda: self._redraw(which='initPlannedFids'))
        self.session.subjectRegistration.sigSampledFiducialsChanged.connect(lambda: self._redraw(which='initSampledFids'))
        self.session.subjectRegistration.sampledHeadPoints.sigHeadpointsChanged.connect(lambda *args: self._redraw(which='initHeadPts'))  # TODO: pass which indices to redraw to not entirely redraw each time there's a change
        self.session.subjectRegistration.sigTrackerToMRITransfChanged.connect(lambda: self._redraw(which=[
            'initSampledFids', 'initHeadPts', 'initSubjectTracker', 'pointerPosition']))

        self._trackingStatusWdgt.session = self.session

        self._headPtsTblWdgt.session = self.session

        self._pointerDistanceReadouts.session = self.session

    def _currentFidTblFidKey(self) -> tp.Optional[str]:
        if self._fidTblWdgt.currentItem() is None:
            return None
        currentRow = self._fidTblWdgt.currentRow()
        return self._fidTblWdgt.item(currentRow, 0).text()

    def _getPointerCoordRelToSubTracker(self) -> tp.Optional[np.ndarray]:
        # TODO: spin-wait update positionsClient.latestPositions to make sure we have the most up to date position

        pointerToCameraTransf = self._positionsClient.getLatestTransf(self.session.tools.pointer.key, None)
        subjectTrackerToCameraTransf = self._positionsClient.getLatestTransf(self.session.tools.subjectTracker.key, None)

        if pointerToCameraTransf is None or subjectTrackerToCameraTransf is None:
            logger.warning('Tried to sample, but do not have valid positions. Returning.')
            return None

        #logger.info('Sampled fiducial:\npointer: {}\ntracker: {}'.format(pointerToCameraTransf, subjectTrackerToCameraTransf))

        pointerCoord_relToSubTracker = applyTransform([self.session.tools.pointer.toolToTrackerTransf,
                                                       pointerToCameraTransf,
                                                       invertTransform(subjectTrackerToCameraTransf)
                                                       ], np.zeros((3,)))

        return pointerCoord_relToSubTracker

    def _onSampleFidBtnClicked(self):
        fidKey = self._currentFidTblFidKey()

        pointerCoord_relToSubTracker = self._getPointerCoordRelToSubTracker()
        if pointerCoord_relToSubTracker is None:
            return

        logger.info(f'Sampled fiducial relative to tracker: {pointerCoord_relToSubTracker}')

        self.session.subjectRegistration.setFiducial(whichSet='sampled', whichFiducial=fidKey, coord=pointerCoord_relToSubTracker)

        if True:
            currentRow = self._fidTblWdgt.currentRow()
            if currentRow == self._fidTblWdgt.rowCount() - 1:
                # already at end of table
                # TODO: auto-advance to prompt about aligning
                pass
            else:
                # advance to next fiducial in table
                self._fidTblWdgt.setCurrentCell(currentRow + 1, 0)

    def _onClearFidBtnClicked(self):
        raise NotImplementedError()  # TODO

    def _onFidTblCurrentCellChanged(self, currentRow: int, currentCol: int, previousRow: int, previousCol: int):
        if previousRow == currentRow:
            return  # no change in row selection

        self._redraw(which=['sampleBtns'])

        fidKey = self._currentFidTblFidKey()

        if fidKey is None:
            # no selection (perhaps table was temporarily cleared)
            return

        subReg = self.session.subjectRegistration
        if fidKey in subReg.plannedFiducials and subReg.plannedFiducials[fidKey] is not None:
            lookAt = subReg.plannedFiducials[fidKey]
        elif fidKey in subReg.sampledFiducials and subReg.sampledFiducials[fidKey] is not None and subReg.trackerToMRITransf is not None:
            lookAt = applyTransform(subReg.trackerToMRITransf, subReg.sampledFiducials[fidKey])
        else:
            lookAt = None
        if lookAt is not None:
            self._plotter.camera.focal_point = lookAt
            vec = lookAt - subReg.approxHeadCenter
            self._plotter.camera.position = lookAt + vec*10
            self._plotter.reset_camera()

    def _onFidTblCellDoubleClicked(self):
        raise NotImplementedError()  # TODO

    def _onAlignToFidBtnClicked(self):

        subReg = self.session.subjectRegistration
        assert subReg.hasMinimumSampledFiducials

        validPlannedFidKeys = {key for key, coord in subReg.plannedFiducials.items() if coord is not None}
        validSampledFidKeys = {key for key, coord in subReg.sampledFiducials.items() if coord is not None}
        commonKeys = validPlannedFidKeys & validSampledFidKeys
        assert len(commonKeys) >= 3

        plannedPts_mriSpace = np.vstack(subReg.plannedFiducials[key] for key in commonKeys)
        sampledPts_subSpace = np.vstack(subReg.sampledFiducials[key] for key in commonKeys)

        logger.info('Estimating transform aligning sampled fiducials to planned fiducials')
        subReg.trackerToMRITransf = estimateAligningTransform(sampledPts_subSpace, plannedPts_mriSpace)

    def _onSampleHeadPtsBtnClicked(self):

        pointerCoord_relToSubTracker = self._getPointerCoordRelToSubTracker()
        if pointerCoord_relToSubTracker is None:
            return

        logger.info(f'Sampled head pt relative to tracker: {pointerCoord_relToSubTracker}')

        self.session.subjectRegistration.sampledHeadPoints.append(pointerCoord_relToSubTracker)

    def _onClearHeadPtsBtnClicked(self):
        raise NotImplementedError()  # TODO

    def _onSelectedHeadPointsChanged(self, selectedIndices: list[int]):
        self._redraw(which='initHeadPts')  # TODO: redraw just previously and currently selected points instead of all

    def _onAlignToHeadPtsBtnClicked(self):
        sampledHeadPts_trackerSpace = np.asarray(self.session.subjectRegistration.sampledHeadPoints)
        sampledHeadPts_MRISpace = applyTransform(self.session.subjectRegistration.trackerToMRITransf, sampledHeadPts_trackerSpace)

        meshHeadPts_MRISpace = self.session.headModel.skinSurf.points

        extraTransf = estimateAligningTransform(sampledHeadPts_MRISpace, meshHeadPts_MRISpace, method='ICP')

        logger.info(f'Extra transf from refinining head points: {extraTransf}')

        self.session.subjectRegistration.trackerToMRITransf = concatenateTransforms([self.session.subjectRegistration.trackerToMRITransf, extraTransf])

    def _redraw(self, which: tp.Union[str, tp.List[str,...]]):

        if not self.isVisible:
            return

        logger.debug('redraw {}'.format(which))

        if isinstance(which, list):
            for subWhich in which:
                self._redraw(which=subWhich)
            return

        if which == 'all':
            which = ['initSurf', 'initSubjectTracker', 'initPointer', 'initPlannedFids', 'initSampledFids', 'initHeadPts',
                     'sampleBtns']
            self._redraw(which=which)
            return

        elif which == 'initSurf':
            actorKey = 'surf'
            self._actors[actorKey] = self._plotter.add_mesh(mesh=getattr(self.session.headModel, self._surfKey),
                                                          color='#d9a5b2',
                                                          opacity=0.8,  # TODO: make GUI-configurable
                                                          name=actorKey)

        elif which == 'initSubjectTracker':

            subjectTracker = self.session.tools.subjectTracker
            doShowSubjectTracker = self.session.subjectRegistration.trackerToMRITransf is not None \
                                and subjectTracker is not None \
                                and subjectTracker.trackerSurf is not None
            actorKey = 'subjectTracker'
            if not doShowSubjectTracker:
                if actorKey in self._actors:
                    self._plotter.remove_actor(self._actors[actorKey])
                    self._actors.pop(actorKey)

            else:
                self._actors[actorKey] = self._plotter.add_mesh(mesh=self.session.tools.subjectTracker.trackerSurf,
                                                                color='#aaaaaa',
                                                                opacity=0.6,
                                                                name=actorKey)
                self._redraw(which='subjectTrackerPosition')

        elif which in ('sampleBtns',):
            pointer = self.session.tools.pointer
            subjectTracker = self.session.tools.subjectTracker

            allowSampling = False
            if pointer is not None and subjectTracker is not None:
                if self._fidTblWdgt.currentItem() is not None:
                    allowSampling = not any(self._positionsClient.getLatestTransf(key, None) is None for key in (pointer.key, subjectTracker.key))

            if self._sampleFiducialBtn.isEnabled() != allowSampling:
                self._sampleFiducialBtn.setEnabled(allowSampling)
            if self._sampleHeadPtsBtn.isEnabled() != allowSampling:
                self._sampleHeadPtsBtn.setEnabled(allowSampling)

        elif which in ('initPointer', 'pointerPosition'):
            pointer = self.session.tools.pointer
            subjectTracker = self.session.tools.subjectTracker

            doShowPointer = self.session.subjectRegistration.trackerToMRITransf is not None \
                            and pointer is not None \
                            and (pointer.trackerSurf is not None or pointer.toolSurf is not None) \
                            and subjectTracker is not None

            if not doShowPointer:
                for toolOrTracker in ('tool', 'tracker'):
                    actorKey = 'pointer' + '_' + toolOrTracker
                    if actorKey in self._actors:
                        self._plotter.remove_actor(self._actors[actorKey])
                        self._actors.pop(actorKey)
                return

            if which == 'initPointer':
                for toolOrTracker in ('tool', 'tracker'):
                    actorKey = 'pointer' + '_' + toolOrTracker
                    actorSurf = getattr(self.session.tools.pointer, toolOrTracker + 'Surf')
                    if actorSurf is None:
                        if actorKey in self._actors:
                            self._plotter.remove_actor(self._actors[actorKey])
                            self._actors.pop(actorKey)
                        continue
                    self._actors[actorKey] = self._plotter.add_mesh(mesh=actorSurf,
                                                                    color='#999999',
                                                                    opacity=0.6,
                                                                    name=actorKey)
                self._redraw(which='pointerPosition')

            elif which == 'pointerPosition':
                for toolOrTracker in ('tool', 'tracker'):
                    actorKey = 'pointer' + '_' + toolOrTracker

                    if actorKey not in self._actors:
                        # assume this was because we don't have enough info to show
                        continue

                    pointerToCameraTransf = self._positionsClient.getLatestTransf(pointer.key, None)
                    subjectTrackerToCameraTransf = self._positionsClient.getLatestTransf(subjectTracker.key, None)

                    if pointerToCameraTransf is None or subjectTrackerToCameraTransf is None:
                        # don't have valid info for determining pointer position relative to head tracker
                        if self._actors[actorKey].GetVisibility():
                            self._actors[actorKey].VisibilityOff()
                        continue

                    if not self._actors[actorKey].GetVisibility():
                        self._actors[actorKey].VisibilityOn()

                    if toolOrTracker == 'tool':
                        pointerStlToSubjectTrackerTransf = concatenateTransforms([
                            pointer.toolStlToToolTransf,
                            pointer.toolToTrackerTransf,
                            pointerToCameraTransf,
                            invertTransform(subjectTrackerToCameraTransf)
                        ])
                    elif toolOrTracker == 'tracker':
                        pointerStlToSubjectTrackerTransf = concatenateTransforms([
                            pointer.trackerStlToTrackerTransf,
                            pointerToCameraTransf,
                            invertTransform(subjectTrackerToCameraTransf)
                        ])
                    else:
                        raise NotImplementedError()

                    setActorUserTransform(
                        self._actors[actorKey],
                        concatenateTransforms([
                            pointerStlToSubjectTrackerTransf,
                            self.session.subjectRegistration.trackerToMRITransf
                        ])
                    )
                    self._plotter.render()

            else:
                raise NotImplementedError()

        elif which == 'subjectTrackerPosition':
            actorKey = 'subjectTracker'
            if actorKey not in self._actors:
                # subject tracker hasn't been initialized, maybe due to missing information
                return

            setActorUserTransform(
                self._actors[actorKey],
                self.session.subjectRegistration.trackerToMRITransf @ self.session.tools.subjectTracker.trackerStlToTrackerTransf
            )
            self._plotter.render()

        elif which == 'initPlannedFids':

            actorKey = 'plannedFids'

            # also update table
            self._redraw(which='fidTbl')

            labels = []
            coords = np.full((len(self.session.subjectRegistration.plannedFiducials), 3), np.nan)
            for iFid, (label, coord) in enumerate(self.session.subjectRegistration.plannedFiducials.items()):
                labels.append(label)
                if coord is not None:
                    coords[iFid, :] = coord

            self._actors[actorKey] = self._plotter.add_point_labels(
                name=actorKey,
                points=coords,
                labels=labels,
                point_color='blue',
                text_color='blue',
                point_size=12,
                shape=None,
                render_points_as_spheres=True,
                reset_camera=False,
                render=True
            )

        elif which == 'initSampledFids':

            actorKey = 'sampledFids'

            # also update table
            self._redraw(which='fidTbl')

            doShowSampledFids = len(self.session.subjectRegistration.sampledFiducials) > 0 \
                                and self.session.subjectRegistration.trackerToMRITransf is not None

            if not doShowSampledFids:
                # no sampled fiducials or necessary transform for plotting (yet)
                if actorKey in self._actors:
                    self._plotter.remove_actor(actorKey)
                    self._actors.pop(actorKey)
                return

            labels = []
            coords = np.full((len(self.session.subjectRegistration.sampledFiducials), 3), np.nan)
            for iFid, (label, coord) in enumerate(self.session.subjectRegistration.sampledFiducials.items()):
                labels.append(label)
                if coord is not None:
                    coords[iFid, :] = coord

            coords = applyTransform(self.session.subjectRegistration.trackerToMRITransf, coords)

            self._actors[actorKey] = self._plotter.add_point_labels(
                name=actorKey,
                points=coords,
                labels=labels,
                point_color='green',
                text_color='green',
                point_size=15,
                shape=None,
                render_points_as_spheres=True,
                reset_camera=False,
                render=True
            )

        elif which == 'initHeadPts':

            actorKeys = ['headPts', 'headPts_selected']

            doShowHeadPts = len(self.session.subjectRegistration.sampledHeadPoints) > 0 \
                            and self.session.subjectRegistration.trackerToMRITransf is not None

            if not doShowHeadPts:
                # no sampled head points or necessary transform (yet)
                for actorKey in actorKeys:
                    if actorKey in self._actors:
                        self._plotter.remove_actor(actorKey)
                        self._actors.pop(actorKey)
                return

            coords = applyTransform(self.session.subjectRegistration.trackerToMRITransf, np.asarray(self.session.subjectRegistration.sampledHeadPoints))

            # color selected points differently
            actorKey = actorKeys[1]
            selectedIndices = self._headPtsTblWdgt.selectedCollectionItemKeys

            if len(selectedIndices) > 0:
                selectedCoords = coords[selectedIndices, :]

                self._actors[actorKey] = self._plotter.add_points(
                    name=actorKey,
                    points=selectedCoords,
                    color='orange',
                    point_size=15,
                    render_points_as_spheres=True,
                    reset_camera=False,
                    render=False
                )

                unselectedIndices = list(range(coords.shape[0]))
                for index in reversed(sorted(selectedIndices)):
                    del unselectedIndices[index]
                coords = coords[unselectedIndices, :]
            else:
                if actorKey in self._actors:
                    self._plotter.remove_actor(actorKey)
                    self._actors.pop(actorKey)

            actorKey = actorKeys[0]
            if coords.shape[0] > 0:
                self._actors[actorKey] = self._plotter.add_points(
                    name=actorKey,
                    points=coords,
                    color='red',
                    point_size=10,
                    render_points_as_spheres=True,
                    reset_camera=False,
                    render=False
                )
            else:
                if actorKey in self._actors:
                    self._plotter.remove_actor(actorKey)
                    self._actors.pop(actorKey)

            self._plotter.render()

        elif which == 'fidTbl':
            # TODO: do iterative partial updates rather than entirely clearing and repopulating table with every change
            prevKey = self._currentFidTblFidKey()
            subReg = self.session.subjectRegistration
            self._fidTblWdgt.clearContents()
            allFidKeys = list(subReg.plannedFiducials.keys()) + [key for key in subReg.sampledFiducials if key not in subReg.plannedFiducials]
            self._fidTblWdgt.setRowCount(len(allFidKeys))

            checkIcon_planned = qta.icon('mdi6.checkbox-marked-circle', color='blue')
            checkIcon_sampled = qta.icon('mdi6.checkbox-marked-circle', color='green')
            neutralIcon = qta.icon('mdi6.circle-medium', color='gray')
            xIcon = qta.icon('mdi6.close-circle-outline', color='red')

            for iFid, key in enumerate(allFidKeys):
                item = QtWidgets.QTableWidgetItem(key)
                self._fidTblWdgt.setItem(iFid, 0, item)

                if key in subReg.plannedFiducials:
                    if subReg.plannedFiducials[key] is not None:
                        icon = checkIcon_planned
                    else:
                        icon = xIcon
                else:
                    icon = xIcon
                item = QtWidgets.QTableWidgetItem(icon, '')
                self._fidTblWdgt.setItem(iFid, 1, item)

                if key in subReg.sampledFiducials:
                    if subReg.sampledFiducials[key] is not None:
                        icon = checkIcon_sampled
                    else:
                        icon = xIcon
                else:
                    icon = xIcon
                item = QtWidgets.QTableWidgetItem(icon, '')
                self._fidTblWdgt.setItem(iFid, 2, item)

            self._alignToFiducialsBtn.setEnabled(subReg.hasMinimumSampledFiducials)

            if prevKey is not None:
                if prevKey in allFidKeys:
                    row = allFidKeys.index(prevKey)
                else:
                    # no match, reset to beginning
                    row = 0
            else:
                row = 0
            self._fidTblWdgt.setCurrentCell(row, 0)

        else:
            raise NotImplementedError('Unexpected redraw key: {}'.format(which))

