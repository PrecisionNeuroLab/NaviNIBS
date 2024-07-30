from __future__ import annotations

import asyncio

import attrs
from datetime import datetime, timedelta
import inspect
import json
import logging
import numpy as np
import os
import pathlib
import pyvista as pv
import pyvistaqt as pvqt
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import re
import shutil
import typing as tp

from . import MainViewPanel
from NaviNIBS.Devices.ToolPositionsClient import ToolPositionsClient
from NaviNIBS.Navigator.GUI.Widgets.MRIViews import MRISliceView
from NaviNIBS.Navigator.GUI.Widgets.SurfViews import Surf3DView
from NaviNIBS.Navigator.GUI.Widgets.TrackingStatusWidget import TrackingStatusWidget
from NaviNIBS.Navigator.GUI.Widgets.CollectionTableWidget import HeadPointsTableWidget, RegistrationFiducialsTableWidget
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model.SubjectRegistration import Fiducial, HeadPoints
from NaviNIBS.Navigator.Model.Tools import CoilTool, CalibrationPlate
from NaviNIBS.util.pyvista import Actor, setActorUserTransform, RemotePlotterProxy
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import applyTransform, invertTransform, transformToString, stringToTransform, estimateAligningTransform, concatenateTransforms
from NaviNIBS.util import makeStrUnique
from NaviNIBS.util import exceptionToStr
from NaviNIBS.util.GUI.ErrorDialog import asyncTryAndRaiseDialogOnError
from NaviNIBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from NaviNIBS.util.GUI.QLineEdit import QLineEditWithValidationFeedback
from NaviNIBS.util.GUI.QTableWidgetDragRows import QTableWidgetDragRows
from NaviNIBS.util.numpy import array_equalish
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter
from NaviNIBS.util.pyvista.dataset import find_closest_point

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@attrs.define
class _PointerDistanceReadout:
    _label: str
    _parentLayout: QtWidgets.QFormLayout
    _units: str = ' mm'
    _value: float = np.nan
    _doHideWhenNaN: bool = False

    _wdgt: tp.Optional[QtWidgets.QLabel] = attrs.field(init=False, default=None)

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
    _session: Session = attrs.field(repr=False)
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
        pointerToCameraTransf = self._positionsClient.getLatestTransf(self.session.tools.pointer.trackerKey, None)
        subjectTrackerToCameraTransf = self._positionsClient.getLatestTransf(self.session.tools.subjectTracker.trackerKey, None)

        if pointerToCameraTransf is None or subjectTrackerToCameraTransf is None:
            # TODO: report NaNs for all distances
            return

        pointerCoord_relToSubTracker = applyTransform([self.session.tools.pointer.toolToTrackerTransf,
                                                       pointerToCameraTransf,
                                                       invertTransform(subjectTrackerToCameraTransf)
                                                       ], np.zeros((3,)), doCheck=False)

        subjectTrackerToMRITransf = self.session.subjectRegistration.trackerToMRITransf
        if subjectTrackerToMRITransf is None:
            # TODO: report NaNS for all distances
            return

        pointerCoord_MRISpace = applyTransform(subjectTrackerToMRITransf, pointerCoord_relToSubTracker, doCheck=False)

        # find distance to skin
        closestPtIndex = find_closest_point(self.session.headModel.skinSurf, pointerCoord_MRISpace)
        closestPt = self.session.headModel.skinSurf.points[closestPtIndex, :]
        dist = np.linalg.norm(closestPt - pointerCoord_MRISpace)
        self._distToSkinReadout.value = dist

        # determine which fiducial to compare to
        plannedFiducialCoords = self.session.subjectRegistration.fiducials.plannedFiducials
        sampledFiducialCoords = self.session.subjectRegistration.fiducials.sampledFiducials

        # look at both planned and sampled fiducials to choose single closest, then use that for both planned and sampled

        whichFidClosest: tp.Optional[str] = None
        closestDist = np.inf

        for whichType, coords in (('planned', plannedFiducialCoords), ('sampled', sampledFiducialCoords)):
            for whichFid, coord in coords.items():
                if coord is None:  # e.g. fiducial not yet sampled
                    continue
                if whichType == 'sampled':
                    # must do extra coordinate conversion from head tracker space to MRI space
                    coord = applyTransform(subjectTrackerToMRITransf, coord, doCheck=False)
                dist = np.linalg.norm(coord - pointerCoord_MRISpace)
                if dist < closestDist:
                    closestDist = dist
                    whichFidClosest = whichFid

        if False:
            # TODO: debug, delete
            whichFidClosest = list(sampledFiducialCoords.keys())[-1]

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
                coord = applyTransform(subjectTrackerToMRITransf, coord, doCheck=False)

                self._distToSampledFidReadout.value = np.linalg.norm(coord - pointerCoord_MRISpace)
                self._distToSampledFidReadout.label = f'Dist to sampled {whichFidClosest}'


@attrs.define
class SubjectRegistrationPanel(MainViewPanel):
    """
    SubjectRegistrationPanel

    Note: need to carefully manage sequencing / interaction between fiducials and head points.
    Example scenario:
    1. Plan fiducials
    2. Sample planned fiducials
    3. Align fiducials
    4. Sample head points
    5. Refine alignment with head points
    6. Convert sampled fiducials to planned
    7. Create another fiducial from pointer position (e.g. head ref)
    Some time later, tracker moves and we must reregister:
    8. Re-sample (sampled-converted-to-planned) fiducials and head ref
    9. Align fiducials
    10. Don't resample head points, but expect them to update their alignment
    This last point is what requires careful attention in sequencing.
    Head points are defined in tracker space. If the tracker moves, the old points
    are no longer valid (but we may want to update them for the new alignment for display purposes).

    Another example scenario, not using sample->planned fiducial conversion:
    1. Plan fiducials
    2. Sample planned fiducials
    3. Align fiducials
    4. Sample head points
    5. Refine alignment with head points
    Some time later, tracker moves and we must reregister:
    8. Re-sample planned fiducials
    9. Align fiducials
    10. Don't resample head points, but expect them to update their alignment(?)

    But also must allow this scenario:
    1. Plan fiducials
    2. Sample planned fiducials
    3. Align fiducials
    4. Sample head points
    5. Refine alignment with head points
    6. Align to fiducials again to "undo" head point refinement
    7. Change a refinement setting (e.g. weight) and refine again, without needing to resample head points

    Less common scenario that would also be nice to support:
    1. Plan fiducials
    2. Sample planned fiducials
    3. Align fiducials
    4. Sample head points
    5. Refine alignment with head points
    6. Edit a *planned* fiducial to make it align better with reality.
    7. Align fiducials.
    8. Refine alignment with head points, without needing to resample.

    So, if re-aligning to fiducials but *sampled* fiducials haven't actually changed, we should assume that tracker position hasn't changed, and so head points don't need to be adjusted.

    But if fiducials have changed, and then we re-align to those fiducials, then we should assume
        that the tracker position did change. If we do nothing, head points will then be
        incorrect/nonnsensical. We must either convert head points into the new tracker space,
        or delete them.

    To convert head points into new tracker space, can we assume that previous tracker to MRI
    transform was "correct", and go through that?

    TODO: maybe use fiducial history or add a "last edited" timestamp to fiducial fields and
    head points to not be responsible for tracking this sequencing here.
    """
    _key: str = 'Register'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.head-snowflake'))
    _surfKey: str = 'skinSurf'

    _trackingStatusWdgt: TrackingStatusWidget = attrs.field(init=False)
    _fidTblWdgt: RegistrationFiducialsTableWidget = attrs.field(init=False)
    _headPtsTblWdgt: HeadPointsTableWidget = attrs.field(init=False)
    _sampleFiducialBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _sampleFiducialMultipleBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _clearFiducialBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _newFidFromPointerBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _newFidFromSampleBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _alignToFiducialsBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _sampleHeadPtsBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _clearHeadPtsBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _refineWeightsField: QLineEditWithValidationFeedback = attrs.field(init=False)
    _refineWithHeadpointsBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _plotter: DefaultBackgroundPlotter = attrs.field(init=False)
    _actors: tp.Dict[str, tp.Optional[Actor]] = attrs.field(init=False, factory=dict)
    _pointerDistanceReadouts: _PointerDistanceReadouts = attrs.field(init=False)

    _positionsClient: tp.Optional[ToolPositionsClient] = attrs.field(init=False, default=None)

    finishedAsyncInitializationEvent: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def canBeEnabled(self) -> tuple[bool, str | None]:
        if self.session is None:
            return False, 'No session set'
        if not self.session.MRI.isSet:
            return False, 'No MRI set'
        if not self.session.headModel.isSet:
            return False, 'No head model set'
        if self.session.tools.subjectTracker is None:
            return False, 'No active subject tracker configured'
        if self.session.tools.pointer is None:
            return False, 'No active pointer tool configured'
        if not self.session.subjectRegistration.hasMinimumPlannedFiducials:
            return False, 'Missing planned fiducials'
        return True, None

    def _finishInitialization(self):
        super()._finishInitialization()

        self._positionsClient = ToolPositionsClient()
        self._positionsClient.sigLatestPositionsChanged.connect(lambda: self._redraw(which=['pointerPosition', 'sampleBtns']))

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())
        self._wdgt.layout().setContentsMargins(0, 0, 0, 0)

        sidebar = QtWidgets.QWidget()
        sidebar.setLayout(QtWidgets.QVBoxLayout())
        sidebar.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Expanding)
        self._wdgt.layout().addWidget(sidebar)

        self._trackingStatusWdgt = TrackingStatusWidget(session=self.session,
                                                        hideToolTypes=[CoilTool, CalibrationPlate])
        sidebar.layout().addWidget(self._trackingStatusWdgt.wdgt)

        fiducialsBox = QtWidgets.QGroupBox('Fiducials')
        fiducialsBox.setLayout(QtWidgets.QVBoxLayout())
        sidebar.layout().addWidget(fiducialsBox)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        btnContainer.layout().setContentsMargins(0, 0, 0, 0)
        fiducialsBox.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Sample fiducial (single)')
        btn.clicked.connect(self._onSampleFidBtnClicked)
        self._sampleFiducialBtn = btn
        btnContainer.layout().addWidget(btn, 0, 0, )

        btn = QtWidgets.QPushButton('Sample fiducial (multiple)')
        btn.clicked.connect(self._onSampleFidMultipleBtnClicked)
        self._sampleFiducialMultipleBtn = btn
        btnContainer.layout().addWidget(btn, 1, 0, )

        btn = QtWidgets.QPushButton('Clear fiducial')
        btn.clicked.connect(self._onClearFidBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 1)
        # TODO: change this to "clear fiducials" when multiple selected
        self._clearFiducialBtn = btn

        btn = QtWidgets.QPushButton('New planned fiducial from pointer')
        btn.clicked.connect(self._onNewFidFromPointerClicked)
        btnContainer.layout().addWidget(btn, 2, 0, 1, 2)
        btn.setEnabled(False)
        self._newFidFromPointerBtn = btn

        btn = QtWidgets.QPushButton('New planned fiducial from sample')
        btn.clicked.connect(self._onNewFidFromSampleClicked)
        btnContainer.layout().addWidget(btn, 3, 0, 1, 2)
        self._newFidFromSampleBtn = btn

        # TODO: add prev/next fiducial buttons for mapping to foot pedal actions
        # (i.e. without requiring click in fidTbl to select different fiducial)

        self._fidTblWdgt = RegistrationFiducialsTableWidget()
        self._fidTblWdgt.sigSelectionChanged.connect(self._onSelectedFiducialsChanged)
        self._fidTblWdgt.wdgt.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)
        fiducialsBox.layout().addWidget(self._fidTblWdgt.wdgt)

        # TODO: separate fiducials and samples into an adjustable splitter

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        btnContainer.layout().setContentsMargins(0, 0, 0, 0)
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
        btnContainer.layout().setContentsMargins(0, 0, 0, 0)
        headPtsBox.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Sample head point')
        btn.clicked.connect(self._onSampleHeadPtsBtnClicked)
        btn = self._sampleHeadPtsBtn = btn
        btnContainer.layout().addWidget(btn, 0, 0)

        btn = QtWidgets.QPushButton('Delete selected point')
        btn.clicked.connect(self._onClearHeadPtsBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 1)
        btn.setEnabled(False)
        self._clearHeadPtsBtn = btn

        self._headPtsTblWdgt = HeadPointsTableWidget(doAdjustSizeToContents=False)
        self._headPtsTblWdgt.sigSelectionChanged.connect(self._onSelectedHeadPointsChanged)
        self._headPtsTblWdgt.wdgt.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)

        headPtsBox.layout().addWidget(self._headPtsTblWdgt.wdgt)

        container = QtWidgets.QWidget()
        container.setLayout(QtWidgets.QFormLayout())
        container.layout().setContentsMargins(0, 0, 0, 0)
        headPtsBox.layout().addWidget(container)
        self._refineWeightsField = QLineEditWithValidationFeedback()
        container.layout().addRow('Refine weights', self._refineWeightsField)
        self._refineWeightsField.setToolTip(
            inspect.cleandoc(
                """
                Optional weights used for headpoint-based registration refinement, in format expected by 
                simpleicp's `rbp_observation_weights` argument (i.e. rot_x, rot_y, rot_z, t_x, t_y, t_z). 
                Can alternatively specify as a single scalar to apply the same weight to all terms, or as 
                two scalars to apply the first weight to all rotation term, the second weights to all
                translation terms.
                
                Note that values will be inverted (weightArg = 1 / weight) so that higher input values 
                correspond to greater weighting of head points, rather than greater weighting of fiducials.
                
                Note that these weights are different in definition and usage than `Fiducial.alignmentWeight`.
                """
            )
        )
        self._refineWeightsField.setPlaceholderText('Optional')
        self._refineWeightsField.editingFinished.connect(self._onHeadPointsWeightsFieldChanged)

        class RefineWeightsValidator(QtGui.QValidator):
            def __init__(self):
                super().__init__()
                self._invalidRegex = re.compile(r'(\[\[)|([a-zA-Z]+)|(\d+ +[\d.]+)|(]])|(,,)|(\.\.)|(] +\[)')

            def validate(self, inputStr: str, pos: int) -> QtGui.QValidator.State:
                if len(inputStr) == 0:
                    return self.State.Acceptable, inputStr, pos
                try:
                    listOrScalarVal = json.loads(inputStr)
                    if not isinstance(listOrScalarVal, (list, float, int)):
                        raise ValueError('Expected list or scalar')
                    arrayVal = np.asarray(listOrScalarVal, dtype=np.float64)  # raises value error if problem
                except ValueError as e:
                    if self._invalidRegex.search(inputStr):
                        return self.State.Invalid, inputStr, pos
                    else:
                        return self.State.Intermediate, inputStr, pos
                else:
                    return self.State.Acceptable, inputStr, pos

        self._refineWeightsField.setValidator(RefineWeightsValidator())

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        btnContainer.layout().setContentsMargins(0, 0, 0, 0)
        headPtsBox.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Refine with sampled head points')
        btn.clicked.connect(self._onAlignToHeadPtsBtnClicked)
        btn.setEnabled(False)
        self._refineWithHeadpointsBtn = btn
        btnContainer.layout().addWidget(btn, 0, 0, 1, 2)

        self._pointerDistanceReadouts = _PointerDistanceReadouts(
            session=self.session,
            positionsClient=self._positionsClient,
        )
        sidebar.layout().addWidget(self._pointerDistanceReadouts.wdgt)

        self._plotter = DefaultBackgroundPlotter()
        self._wdgt.layout().addWidget(self._plotter)

        asyncio.create_task(asyncTryAndRaiseDialogOnError(self._finishInitialization_async))

    async def _finishInitialization_async(self):
        if isinstance(self._plotter, RemotePlotterProxy):
            await self._plotter.isReadyEvent.wait()

        self._plotter.enable_depth_peeling(4)

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

        self.finishedAsyncInitializationEvent.set()

    def _onSessionSet(self):
        super()._onSessionSet()
        # TODO: connect relevant session changed signals to _redraw calls

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):
        self.session.headModel.sigDataChanged.connect(lambda which: self._redraw(which='initSurf'))
        self.session.subjectRegistration.fiducials.sigItemsChanged.connect(self._onFiducialsChanged)
        self.session.subjectRegistration.sampledHeadPoints.sigHeadpointsChanged.connect(self._onHeadpointsChanged)
        self.session.subjectRegistration.sampledHeadPoints.sigAttribsChanged.connect(self._onHeadPointAttribsChanged)
        self.session.subjectRegistration.sigTrackerToMRITransfChanged.connect(lambda: self._redraw(which=[
            'initSampledFids', 'initHeadPts', 'initSubjectTracker', 'initPointer', 'pointerPosition']))
        self._updateHeadPointsWeightsField()

        self._trackingStatusWdgt.session = self.session

        self._fidTblWdgt.session = self.session
        self._fidTblWdgt.currentRow = 0
        self._headPtsTblWdgt.session = self.session

        self._pointerDistanceReadouts.session = self.session

        self._redraw(which='all')

    def _onFiducialsChanged(self, fidKeys: list[str], attribs: tp.Optional[list[str]] = None):
        # TODO: pass fidKeys to redraw below to only redraw changed fiducials
        if attribs is None or 'plannedCoord' in attribs:
            self._redraw(which='initPlannedFids')
        if attribs is None or 'sampledCoord' in attribs or 'sampledCoords' in attribs:
            self._redraw(which=['initSampledFids', 'alignBtn'])

    def _onHeadpointsChanged(self, ptIndices: list[int], attribs: tp.Optional[list[str]] = None):
        self._redraw(which='initHeadPts')  # TODO: pass which indices to redraw to not entirely redraw each time there's a change
        self._onSelectedHeadPointsChanged(self._headPtsTblWdgt.selectedCollectionItemKeys)

    def _onHeadPointAttribsChanged(self, attribKeys: list[str]):
        """
        Called when an attribute of the headpoints instance changes other than the head points themselves.

        For now, this is just the alignmentWeights, but could be something else in the future.
        """
        if 'alignmentWeights' in attribKeys:
            self._updateHeadPointsWeightsField()

    def _updateHeadPointsWeightsField(self):
        if self.session is None:
            self._refineWeightsField.setText('')
        else:
            weights = self.session.subjectRegistration.sampledHeadPoints.alignmentWeights
            if weights is None:
                self._refineWeightsField.setText('')
            else:
                newText = json.dumps(weights.tolist())
                if self._refineWeightsField.text() != newText:
                    self._refineWeightsField.setText(newText)

    def _onHeadPointsWeightsFieldChanged(self):
        try:
            weightsStr = self._refineWeightsField.text()
            if len(weightsStr)==0:
                weights = None
            else:
                weights = json.loads(weightsStr)
                weights = np.atleast_1d(np.asarray(weights)).astype(np.float64)
            self.session.subjectRegistration.sampledHeadPoints.alignmentWeights = weights
        except Exception as e:
            # revert text
            logger.warning(f'Error trying to set weights from str {weightsStr}: {exceptionToStr(e)}')
            self._updateHeadPointsWeightsField()

    def _currentFidTblFidKey(self) -> tp.Optional[str]:
        return self._fidTblWdgt.currentCollectionItemKey

    def _getPointerCoordRelToSubTracker(self) -> tp.Optional[np.ndarray]:
        # TODO: spin-wait update positionsClient.latestPositions to make sure we have the most up to date position

        pointerToCameraTransf = self._positionsClient.getLatestTransf(self.session.tools.pointer.trackerKey, None)
        subjectTrackerToCameraTransf = self._positionsClient.getLatestTransf(self.session.tools.subjectTracker.trackerKey, None)

        if pointerToCameraTransf is None or subjectTrackerToCameraTransf is None:
            logger.warning('Tried to sample, but do not have valid positions. Returning.')
            return None

        #logger.info('Sampled fiducial:\npointer: {}\ntracker: {}'.format(pointerToCameraTransf, subjectTrackerToCameraTransf))

        pointerCoord_relToSubTracker = applyTransform([self.session.tools.pointer.toolToTrackerTransf,
                                                       pointerToCameraTransf,
                                                       invertTransform(subjectTrackerToCameraTransf)
                                                       ], np.zeros((3,)), doCheck=False)

        return pointerCoord_relToSubTracker

    def _onSampleFidBtnClicked(self):
        fidKey = self._currentFidTblFidKey()

        pointerCoord_relToSubTracker = self._getPointerCoordRelToSubTracker()
        if pointerCoord_relToSubTracker is None:
            return

        logger.info(f'Sampled fiducial relative to tracker: {pointerCoord_relToSubTracker}')

        self.session.subjectRegistration.fiducials[fidKey].sampledCoord = pointerCoord_relToSubTracker

        if True:
            currentRow = self._fidTblWdgt.currentRow
            if currentRow == self._fidTblWdgt.rowCount - 1:
                # already at end of table
                # TODO: auto-advance to prompt about aligning
                pass
            else:
                # advance to next fiducial in table
                self._fidTblWdgt.currentRow += 1

    def _onSampleFidMultipleBtnClicked(self):
        fidKey = self._currentFidTblFidKey()

        pointerCoord_relToSubTracker = self._getPointerCoordRelToSubTracker()
        if pointerCoord_relToSubTracker is None:
            return

        logger.info(f'Sampled fiducial relative to tracker: {pointerCoord_relToSubTracker}')

        coords = self.session.subjectRegistration.fiducials[fidKey].sampledCoords
        if coords is None:
            coords = pointerCoord_relToSubTracker[np.newaxis, :]
        else:
            coords = np.vstack((coords, pointerCoord_relToSubTracker[np.newaxis, :]))
        self.session.subjectRegistration.fiducials[fidKey].sampledCoords = coords

        # don't auto-advance to allow additional samples of this fiducial

    def _onClearFidBtnClicked(self):
        fidKeys = self._getSelectedFiducialKeys()
        logger.info(f'Clearing sampled fiducials {fidKeys}')
        for fidKey in fidKeys:
            self.session.subjectRegistration.fiducials[fidKey].sampledCoords = None

    def _onNewFidFromPointerClicked(self):
        sampledCoord = self._getPointerCoordRelToSubTracker()
        if sampledCoord is None:
            logger.warning('Cannot create fiducial from pointer when pointer is not visible')
            return

        if self.session.subjectRegistration.trackerToMRITransf is None:
            logger.warning('Cannot create fiducial from pointer when trackerToMRITransf is unknown')
            return

        plannedCoord = applyTransform(self.session.subjectRegistration.trackerToMRITransf, sampledCoord)

        newFidKey = makeStrUnique('SampledFiducial',
                                  existingStrs=self.session.subjectRegistration.fiducials.keys(),
                                  delimiter='')

        logger.info(f'Creating new fiducial {newFidKey} from current pointer position')

        self.session.subjectRegistration.fiducials.addItem(
            Fiducial(
                key=newFidKey,
                plannedCoord=plannedCoord,
                sampledCoords=sampledCoord
            )
        )

    def _onNewFidFromSampleClicked(self):

        if self.session.subjectRegistration.trackerToMRITransf is None:
            logger.warning('Cannot create fiducial from pointer when trackerToMRITransf is unknown')
            return

        selFidKeys = self._getSelectedFiducialKeys()

        for selFidKey in selFidKeys:
            sampledCoord = self.session.subjectRegistration.fiducials[selFidKey].sampledCoord
            if sampledCoord is None:
                # this fiducial doesn't have a sampled coordinate
                continue

            plannedCoord = applyTransform(self.session.subjectRegistration.trackerToMRITransf, sampledCoord)

            newFidKey = makeStrUnique(f'Sampled{selFidKey}',
                                      existingStrs=self.session.subjectRegistration.fiducials.keys(),
                                      delimiter='')

            logger.info(f'Creating new fiducial {newFidKey} from sampled fiducial {selFidKey} position')

            self.session.subjectRegistration.fiducials.addItem(
                Fiducial(
                    key=newFidKey,
                    plannedCoord=plannedCoord,
                    sampledCoords=sampledCoord
                )
            )

            # clear sampled positions of previously selected fiducials so that they're not automatically used for future alignments
            self.session.subjectRegistration.fiducials[selFidKey].sampledCoords = None


    def _onFidTblCurrentCellChanged(self, currentRow: int, currentCol: int, previousRow: int, previousCol: int):
        if previousRow == currentRow:
            return  # no change in row selection

        self._redraw(which=['sampleBtns'])

        fidKey = self._currentFidTblFidKey()

        if fidKey is None:
            # no selection (perhaps table was temporarily cleared)
            return

        subReg = self.session.subjectRegistration
        if fidKey in subReg.fiducials and subReg.fiducials[fidKey].plannedCoord is not None:
            lookAt = subReg.fiducials[fidKey].plannedCoord
        elif fidKey in subReg.fiducials and subReg.fiducials[fidKey].sampledCoord is not None and subReg.trackerToMRITransf is not None:
            lookAt = applyTransform(subReg.trackerToMRITransf, subReg.fiducials[fidKey].sampledCoord)
        else:
            lookAt = None
        if lookAt is not None:
            self._plotter.camera.focal_point = lookAt
            vec = lookAt - subReg.approxHeadCenter
            self._plotter.camera.position = lookAt + vec*10
            self._plotter.reset_camera()

    def _getSelectedFiducialKeys(self):
        return self._fidTblWdgt.selectedCollectionItemKeys

    def _onSelectedFiducialsChanged(self, selKeys: list[str]):
        if len(selKeys) == 0:
            self._clearFiducialBtn.setEnabled(False)
            self._clearFiducialBtn.setText('Clear fiducial')
            self._newFidFromSampleBtn.setEnabled(False)
            self._newFidFromSampleBtn.setText('New planned fiducial from sample')
        else:
            numSelFiducialsWithSamples = sum(self.session.subjectRegistration.fiducials[key].sampledCoord is not None for key in selKeys)

            if len(selKeys) == 1:
                self._clearFiducialBtn.setEnabled(True)
                self._clearFiducialBtn.setText('Clear fiducial')
            else:
                self._clearFiducialBtn.setEnabled(True)
                self._clearFiducialBtn.setText('Clear fiducials')

            if numSelFiducialsWithSamples == 0:
                self._newFidFromSampleBtn.setEnabled(False)
            elif numSelFiducialsWithSamples == 1:
                self._newFidFromSampleBtn.setEnabled(True)
                self._newFidFromSampleBtn.setText('New planned fiducial from sample')
            else:
                self._newFidFromSampleBtn.setEnabled(True)
                self._newFidFromSampleBtn.setText('New planned fiducials from samples')

    def _onFidTblCellDoubleClicked(self):
        raise NotImplementedError()  # TODO

    def _onAlignToFidBtnClicked(self):

        subReg = self.session.subjectRegistration
        assert subReg.hasMinimumSampledFiducials

        validPlannedFidKeys = {key for key, fid in subReg.fiducials.items() if fid.plannedCoord is not None}
        validSampledFidKeys = {key for key, fid in subReg.fiducials.items() if fid.sampledCoord is not None}
        commonKeys = validPlannedFidKeys & validSampledFidKeys
        assert len(commonKeys) >= 3

        plannedPts_mriSpace = np.vstack([subReg.fiducials[key].plannedCoord for key in commonKeys])
        sampledPts_subSpace = np.vstack([subReg.fiducials[key].sampledCoord for key in commonKeys])

        alignmentWeights = np.asarray([subReg.fiducials[key].alignmentWeight for key in commonKeys])

        if all(alignmentWeights == 1.):
            alignmentWeights = None

        logger.info('Estimating transform aligning sampled fiducials to planned fiducials')
        newTrackerToMRITransf = estimateAligningTransform(
            sampledPts_subSpace,
            plannedPts_mriSpace,
            weights=alignmentWeights)

        if len(subReg.sampledHeadPoints) > 0:
            # TODO: handle updating coordinate system of sampled head points if needed
            # did tracker move since last alignment?
            # if sampled fiducials were updated since trackerToMRITransf last updated,
            # then we should assume that the tracker moved and we need to update head points

            timeOfLastSampledFiducialChange = None
            # (assumes history is in chronological order, with most recent last)

            historyTimes = list(subReg.fiducialsHistory.keys())
            historyDatetimes = [subReg.getDatetimeFromTimestamp(t) for t in historyTimes]

            if len(subReg.fiducialsHistory) > 0:
                assert subReg.fiducialsHistory[historyTimes[-1]] == subReg.fiducials, \
                    'last item in history does not match current state'

            for iHist in range(len(historyTimes) - 2, -1, -1):
                timeDiff = historyDatetimes[iHist + 1] - historyDatetimes[iHist]
                assert timeDiff > timedelta(0), 'fiducial history times are not in chronological order'

                fids_i = subReg.fiducialsHistory[historyTimes[iHist]].sampledFiducials
                fids_j = subReg.fiducialsHistory[historyTimes[iHist + 1]].sampledFiducials

                fidKeys_i = set(fids_i.keys())
                for fidKey_j, fidCoord_j in fids_j.items():
                    if fidKey_j not in fids_i:
                        # new fiducial
                        timeOfLastSampledFiducialChange = historyDatetimes[iHist + 1]
                        break
                    else:
                        fidKeys_i.remove(fidKey_j)
                        if not array_equalish(fidCoord_j, fids_i[fidKey_j]):
                            # fiducial changed
                            timeOfLastSampledFiducialChange = historyDatetimes[iHist + 1]
                            break

                if timeOfLastSampledFiducialChange is None:
                    if len(fidKeys_i) > 0:
                        # previously-sampled fiducial is now removed
                        timeOfLastSampledFiducialChange = historyDatetimes[iHist + 1]
                        break

                if timeOfLastSampledFiducialChange is not None:
                    break

            if len(subReg.trackerToMRITransfHistory) > 0:
                timeOfLastTrackerToMRITransfChange = subReg.getDatetimeFromTimestamp(list(subReg.trackerToMRITransfHistory.keys())[-1])
            else:
                timeOfLastTrackerToMRITransfChange = None

            if timeOfLastSampledFiducialChange is None:
                trackerMoved = False
            elif timeOfLastTrackerToMRITransfChange is None:
                trackerMoved = True
            elif timeOfLastTrackerToMRITransfChange > timeOfLastSampledFiducialChange:
                trackerMoved = False
            else:
                trackerMoved = True

            if trackerMoved:

                headPtsTransf = concatenateTransforms([subReg.trackerToMRITransf, invertTransform(newTrackerToMRITransf)])

                logger.info(f'Applying transform to align previously sampled head points to new tracker position: {headPtsTransf}')

                subReg.sampledHeadPoints.replace(applyTransform(headPtsTransf, np.asarray(subReg.sampledHeadPoints)))

        subReg.trackerToMRITransf = newTrackerToMRITransf

    def _onSampleHeadPtsBtnClicked(self):

        pointerCoord_relToSubTracker = self._getPointerCoordRelToSubTracker()
        if pointerCoord_relToSubTracker is None:
            return

        logger.info(f'Sampled head pt relative to tracker: {pointerCoord_relToSubTracker}')

        self.session.subjectRegistration.sampledHeadPoints.append(pointerCoord_relToSubTracker)

    def _onClearHeadPtsBtnClicked(self):
        self.session.subjectRegistration.sampledHeadPoints.remove(self._headPtsTblWdgt.selectedCollectionItemKeys)

    def _onSelectedHeadPointsChanged(self, selectedIndices: list[int]):
        numSelHeadPts = len(self._headPtsTblWdgt.selectedCollectionItemKeys)
        if numSelHeadPts > 0:
            self._clearHeadPtsBtn.setEnabled(True)
            if numSelHeadPts == 1:
                self._clearHeadPtsBtn.setText('Delete selected point')
            else:
                self._clearHeadPtsBtn.setText('Delete selected points')
        else:
            self._clearHeadPtsBtn.setEnabled(False)

        self._redraw(which='initHeadPts')  # TODO: redraw just previously and currently selected points instead of all

    def _onAlignToHeadPtsBtnClicked(self):
        sampledHeadPts_trackerSpace = np.asarray(self.session.subjectRegistration.sampledHeadPoints)
        sampledHeadPts_MRISpace = applyTransform(self.session.subjectRegistration.trackerToMRITransf, sampledHeadPts_trackerSpace)

        meshHeadPts_MRISpace = self.session.headModel.skinSurf.points

        alignmentWeights = self.session.subjectRegistration.sampledHeadPoints.alignmentWeights
        if alignmentWeights is not None:
            match len(alignmentWeights):
                case 1:
                    alignmentWeights = np.tile(alignmentWeights, (6,))
                case 2:
                    assert alignmentWeights.ndim == 1
                    alignmentWeights = np.tile(alignmentWeights, (3, 1)).T.flatten()
                case 6:
                    pass  # no changes
                case _:
                    raise NotImplementedError('Unexpected size of alignmentWeights')

            # Invert weights so that larger input weights correspond to less trusting of initial values.
            #  Also, convert input zero values to inf values in output.
            icpObservationWeights = np.divide(1, alignmentWeights,
                                              out=np.full_like(alignmentWeights, np.inf),
                                              where=alignmentWeights != 0)

        else:
            icpObservationWeights = None

        # TODO: offload this to a separate thread/process so it doesn't block main GUI
        # (but then also need to implement progress bar, option to cancel, locking of other dependent GUI controls)
        extraTransf = estimateAligningTransform(sampledHeadPts_MRISpace, meshHeadPts_MRISpace,
                                                method='ICP',
                                                weights=icpObservationWeights)

        logger.info(f'Extra transf from refinining head points: {extraTransf}')

        self.session.subjectRegistration.trackerToMRITransf = concatenateTransforms([self.session.subjectRegistration.trackerToMRITransf, extraTransf])

    def _redraw(self, which: tp.Union[str, tp.List[str,...]]):

        logger.debug('redraw {}'.format(which))

        if isinstance(self._plotter, RemotePlotterProxy) and not self._plotter.isReadyEvent.is_set():
            # plotter not ready yet
            return

        if isinstance(which, list):
            for subWhich in which:
                self._redraw(which=subWhich)
            return

        if which == 'all':
            which = ['initSurf', 'initSubjectTracker', 'initPointer', 'initPlannedFids', 'initSampledFids', 'initHeadPts',
                     'sampleBtns', 'alignBtn']
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
                self._actors[actorKey] = self._plotter.addMesh(mesh=self.session.tools.subjectTracker.trackerSurf,
                                                               color=self.session.tools.subjectTracker.trackerColor,
                                                               defaultMeshColor='#aaaaaa',
                                                               opacity=0.6,
                                                               name=actorKey)
                self._redraw(which='subjectTrackerPosition')

        elif which in ('sampleBtns',):
            pointer = self.session.tools.pointer
            subjectTracker = self.session.tools.subjectTracker

            allowSampling = False
            if pointer is not None and subjectTracker is not None:
                if self._fidTblWdgt.currentCollectionItemKey is not None:
                    allowSampling = not any(self._positionsClient.getLatestTransf(key, None) is None for key in (pointer.trackerKey, subjectTracker.trackerKey))

            if self._sampleFiducialBtn.isEnabled() != allowSampling:
                self._sampleFiducialBtn.setEnabled(allowSampling)
            if self._sampleFiducialMultipleBtn.isEnabled() != allowSampling:
                self._sampleFiducialMultipleBtn.setEnabled(allowSampling)
            if self._sampleHeadPtsBtn.isEnabled() != allowSampling:
                self._sampleHeadPtsBtn.setEnabled(allowSampling)
            if self._newFidFromPointerBtn.isEnabled() != allowSampling:
                self._newFidFromPointerBtn.setEnabled(allowSampling)

        elif which in ('alignBtn',):
            self._alignToFiducialsBtn.setEnabled(self.session.subjectRegistration.hasMinimumSampledFiducials)

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
                    tool = self.session.tools.pointer
                    actorSurf = getattr(tool, toolOrTracker + 'Surf')
                    meshColor = getattr(tool, toolOrTracker + 'Color')
                    if actorSurf is None:
                        if actorKey in self._actors:
                            self._plotter.remove_actor(self._actors[actorKey])
                            self._actors.pop(actorKey)
                        continue
                    self._actors[actorKey] = self._plotter.addMesh(mesh=actorSurf,
                                                                   color=meshColor,
                                                                   defaultMeshColor='#999999',
                                                                   opacity=0.6,
                                                                   name=actorKey)
                self._redraw(which='pointerPosition')

            elif which == 'pointerPosition':
                if not self.isVisible:
                    return  # don't do frequent updates when not even visible

                for toolOrTracker in ('tool', 'tracker'):
                    actorKey = 'pointer' + '_' + toolOrTracker

                    if actorKey not in self._actors:
                        # assume this was because we don't have enough info to show
                        continue

                    pointerToCameraTransf = self._positionsClient.getLatestTransf(pointer.trackerKey, None)
                    subjectTrackerToCameraTransf = self._positionsClient.getLatestTransf(subjectTracker.trackerKey, None)

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

                    with self._plotter.allowNonblockingCalls():
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
            if not self.isVisible:
                return  # don't do frequent updates when not even visible

            actorKey = 'subjectTracker'
            if actorKey not in self._actors:
                # subject tracker hasn't been initialized, maybe due to missing information
                return

            with self._plotter.allowNonblockingCalls():
                setActorUserTransform(
                    self._actors[actorKey],
                    self.session.subjectRegistration.trackerToMRITransf @ self.session.tools.subjectTracker.trackerStlToTrackerTransf
                )
                self._plotter.render()

        elif which == 'initPlannedFids':

            actorKey = 'plannedFids'

            labels = []
            coords = np.full((len(self.session.subjectRegistration.fiducials), 3), np.nan)
            for iFid, (label, fid) in enumerate(self.session.subjectRegistration.fiducials.items()):
                labels.append(label)
                if fid.plannedCoord is not None:
                    coords[iFid, :] = fid.plannedCoord

            if actorKey in self._actors:
                self._plotter.remove_actor(self._actors[actorKey])
                self._actors.pop(actorKey)

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

            actorKeys = ('sampledFids', 'repeatedSampledFids')

            sampledFidKeys = [key for key, fid in self.session.subjectRegistration.fiducials.items() if fid.sampledCoord is not None]
            doShowSampledFids = len(sampledFidKeys) > 0 \
                                and self.session.subjectRegistration.trackerToMRITransf is not None

            if not doShowSampledFids:
                # no sampled fiducials or necessary transform for plotting (yet)
                for actorKey in actorKeys:
                    if actorKey in self._actors:
                        self._plotter.remove_actor(actorKey)
                        self._actors.pop(actorKey)
                return

            labels = []
            coords = np.full((len(self.session.subjectRegistration.fiducials), 3), np.nan)
            repeatedCoords = np.zeros((0, 3))

            for iFid, (label, fid) in enumerate(self.session.subjectRegistration.fiducials.items()):
                labels.append(label)
                if fid.sampledCoord is not None:
                    coords[iFid, :] = fid.sampledCoord
                if fid.sampledCoords is not None and fid.sampledCoords.shape[0] > 1:
                    repeatedCoords = np.vstack((repeatedCoords, fid.sampledCoords))

            coords = applyTransform(self.session.subjectRegistration.trackerToMRITransf, coords)
            if repeatedCoords.shape[0] > 0:
                repeatedCoords = applyTransform(self.session.subjectRegistration.trackerToMRITransf, repeatedCoords)

            self._actors[actorKeys[0]] = self._plotter.add_point_labels(
                name=actorKeys[0],
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

            if repeatedCoords.shape[0] > 0:
                self._actors[actorKeys[1]] = self._plotter.add_points(
                    name=actorKeys[1],
                    points=repeatedCoords,
                    color='green',
                    opacity=0.7,
                    point_size=10,
                    render_points_as_spheres=True,
                    reset_camera=False,
                    render=True
                )
            else:
                actorKey = actorKeys[1]
                if actorKey in self._actors:
                    self._plotter.remove_actor(actorKey)
                    self._actors.pop(actorKey)

        elif which == 'initHeadPts':

            self._refineWithHeadpointsBtn.setEnabled(len(self.session.subjectRegistration.sampledHeadPoints) > 4 \
                                                     and self.session.subjectRegistration.trackerToMRITransf is not None)

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

        else:
            raise NotImplementedError('Unexpected redraw key: {}'.format(which))

        with self._plotter.allowNonblockingCalls():
            self._plotter.render()
