import asyncio
import asyncio
import attrs
import inspect
import logging
import numpy as np
import pyvista as pv
from scipy.stats import trim_mean
from scipy.optimize import least_squares
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from RTNaBS.Navigator.GUI.EditWindows.ToolCalibrationWindow import ToolCalibrationWindow
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms, applyTransform
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments
from RTNaBS.util.pyvista.plotting import BackgroundPlotter


logger = logging.getLogger(__name__)


@attrs.define(kw_only=True)
class VisualizedOrientation:
    """
    Note: this doesn't connect to any change signals from underlying sample, instead assuming that caller will
    re-instantiate for any changes as needed
    """
    _transf: np.ndarray
    _plotter: pv.Plotter
    _colorDepthIndicator: str = '#ba55d3'
    _colorHandleIndicator: str = '#9360db'
    _colorEndpointIndicator: str = '#7340bb'
    _opacity: float = 1.
    _lineWidth: float = 3.
    _style: str = 'linesPlusEndpoint'
    _actorKeyPrefix: str

    _actors: tp.Dict[str, Actor] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        match self._style:
            case 'linesPlusEndpoint':
                zOffset = 20
                handleLength = 2
                depthLine = pv.utilities.lines_from_points(np.asarray([[0, 0, 0], [0, 0, zOffset]]))
                handleLine = pv.utilities.lines_from_points(np.asarray([[0, 0, zOffset], [0, -handleLength, zOffset]]))
                actorKey = self._actorKeyPrefix + 'depthLine'
                self._actors[actorKey] = addLineSegments(self._plotter,
                                                         depthLine,
                                                         name=actorKey,
                                                         color=self._colorDepthIndicator,
                                                         width=self._lineWidth,
                                                         opacity=self._opacity)

                actorKey = self._actorKeyPrefix + 'handleLine'
                self._actors[actorKey] = addLineSegments(self._plotter,
                                                         handleLine,
                                                         name=actorKey,
                                                         color=self._colorHandleIndicator,
                                                         width=self._lineWidth,
                                                         opacity=self._opacity)

                actorKey = self._actorKeyPrefix + 'endpoint'
                self._actors[actorKey] = self._plotter.add_points(
                    np.asarray([[0, 0, 0]]),
                    color=self._colorEndpointIndicator,
                    name=actorKey,
                    opacity=self._opacity,
                    reset_camera=False,
                    point_size=8.
                )

            case _:
                raise NotImplementedError(f'Unexpected style: {self._style}')

        for actor in self._actors.values():
            setActorUserTransform(actor, self._transf)

        self._plotter.render()

    @property
    def actors(self):
        return self._actors

    @property
    def transf(self):
        return self._transf

    @transf.setter
    def transf(self, newTransf: np.ndarray):
        self._transf = newTransf
        for actor in self._actors.values():
            setActorUserTransform(actor, self._transf)


@attrs.define
class PointerCalibrationWindow(ToolCalibrationWindow):

    _samples: list[np.ndarray] = attrs.field(factory=list)  # list of pointer poses with common endpoint location
    """
    Note that these are orientations BEFORE applying any previous toolToTrackerTransf, even though visualization
    will include this previous toolToTrackerTransf
    """

    _pendingNewTransf: tp.Optional[np.ndarray] = attrs.field(init=False, default=None)  # result of calibration, not yet saved to tool

    _plotter: BackgroundPlotter = attrs.field(init=False)
    _liveVisual: tp.Optional[VisualizedOrientation] = attrs.field(init=False, default=None)
    _sampleVisuals_orig: list[VisualizedOrientation] = attrs.field(init=False, factory=list)
    _sampleVisuals_pending: list[VisualizedOrientation] = attrs.field(init=False, factory=list)

    _visualColors_orig: tuple[str, str, str] = ('#ba55d3', '#9360db', '#7340bb')
    _visualColors_pending: tuple[str, str, str] = ('#bad355', '#93db60', '#73bb40')
    _visualColors_live: tuple[str, str, str] = ('#d3ba55', '#db9360', '#bb7340')

    _instructions: QtWidgets.QTextEdit = attrs.field(init=False)
    _deleteLastSampleBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _acquireSampleBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _calibrateBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _revertBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _showOrigVisuals: QtWidgets.QCheckBox = attrs.field(init=False)
    _showPendingVisuals: QtWidgets.QCheckBox = attrs.field(init=False)
    _showLiveVisual: QtWidgets.QCheckBox = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        splitter = QtWidgets.QSplitter()
        self._wdgt.layout().addWidget(splitter)

        self._instructions = QtWidgets.QTextEdit('')
        self._instructions.setReadOnly(True)
        self._instructions.setMarkdown(inspect.cleandoc("""
            # Pointer endpoint calibration
            ## Instructions
            1. Choose a rigid, fixed reference point on a solid object in the room. The camera should not move relative to this fixed reference point throughout calibration.
            2. Position the pointer to be visible to the camera, with its endpoint precisely placed on the reference point.
            3. Press "Acquire sample" to record the stylus pose while the stylus tip is touching the reference point.
            4. Rotate the stylus while holding the endpoint fixed at the reference point, and then acquire another sample.
            5. Repeat rotate + acquire process for a number of samples. 
                - The more samples the better, as long as you maintain consistent contact with the reference point whenever sampling.
                - Try to get a broad range of angles sampled, including pivoting the stylus along ~150 degrees along the left-right axis, ~150 degrees along the front-back axis, and ~180 degrees rotating around the stylus shaft.
            6. When done acquiring samples, click "Process samples" to estimate a new endpoint.
            7. Double check that the samples with new transform show endpoints tightly clustered around a single point. If you accidentally deviated from the reference point during a small number of samples, those should be visible as outliers here.
            8. Close this window to accept the calibration.
        """))
        splitter.addWidget(self._instructions)

        splitRight = QtWidgets.QWidget()
        splitRight.setLayout(QtWidgets.QVBoxLayout())
        splitter.addWidget(splitRight)

        self._plotter = BackgroundPlotter(
            show=False,
            app=QtWidgets.QApplication.instance()
        )
        splitRight.layout().addWidget(self._plotter)

        if len(self._samples) > 0:
            raise NotImplementedError  # TODO: initialize corresponding sample actors

        # TODO: initialize actor to visualize live position of pointer

        btnContainer = QtWidgets.QWidget()
        btnCntLayout = QtWidgets.QGridLayout()
        btnContainer.setLayout(btnCntLayout)
        splitRight.layout().layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Delete last sample')
        btn.clicked.connect(lambda checked: self._onDeleteLastSampleClicked())
        btn.setEnabled(len(self._samples) > 0)
        btnCntLayout.addWidget(btn, 1, 0)
        self._deleteLastSampleBtn = btn

        btn = QtWidgets.QPushButton('Acquire sample')
        btn.clicked.connect(lambda checked: self._onAcquireSampleClicked())
        btn.setEnabled(False)
        btnCntLayout.addWidget(btn, 0, 0)
        self._acquireSampleBtn = btn

        btn = QtWidgets.QPushButton('Process samples')
        btn.clicked.connect(lambda checked: self._onCalibrateClicked())
        btn.setEnabled(len(self._samples) > 2)
        btnCntLayout.addWidget(btn, 0, 1)
        self._calibrateBtn = btn

        btn = QtWidgets.QPushButton('Revert calibration')
        btn.clicked.connect(lambda checked: self._onRevertClicked())
        btn.setEnabled(self._pendingNewTransf is not None)
        btnCntLayout.addWidget(btn, 1, 1)
        self._revertBtn = btn

        groupBox = QtWidgets.QGroupBox('Render')
        groupBox.setLayout(QtWidgets.QVBoxLayout())
        splitRight.layout().addWidget(groupBox)

        cb = QtWidgets.QCheckBox('Samples with original transform')
        cb.setStyleSheet(f'QCheckBox {{color: {self._visualColors_orig[2]} }}')
        cb.setChecked(True)
        cb.stateChanged.connect(self._onShowOrigVisualsChanged)
        groupBox.layout().addWidget(cb)
        self._showOrigVisuals = cb

        cb = QtWidgets.QCheckBox('Samples with new transform')
        cb.setStyleSheet(f'QCheckBox {{color: {self._visualColors_pending[2]} }}')
        cb.setChecked(True)
        cb.setEnabled(self._pendingNewTransf is not None)
        cb.stateChanged.connect(self._onShowPendingVisualsChanged)
        groupBox.layout().addWidget(cb)
        self._showPendingVisuals = cb

        cb = QtWidgets.QCheckBox('Live position')
        cb.setStyleSheet(f'QCheckBox {{color: {self._visualColors_live[2]} }}')
        cb.setChecked(True)
        cb.stateChanged.connect(self._onShowLiveVisualChanged)
        groupBox.layout().addWidget(cb)
        self._showLiveVisual = cb

        self.sigFinished.connect(self._onDialogFinished)

        self._wdgt.resize(QtCore.QSize(1000, 800))

    def _onDeleteLastSampleClicked(self):
        if len(self._samples) > 0:
            logger.info(f'Deleting sample {len(self._samples)}')

            assert len(self._samples) == len(self._sampleVisuals_orig)
            if self._pendingNewTransf is not None:
                assert len(self._samples) == len(self._sampleVisuals_pending)

            self._samples.pop()

            visual = self._sampleVisuals_orig.pop()
            for actor in visual.actors.values():
                self._plotter.remove_actor(actor)

            if self._pendingNewTransf is not None:
                visual = self._sampleVisuals_pending.pop()
                for actor in visual.actors.values():
                    self._plotter.remove_actor(actor)

        if len(self._samples) == 0:
            self._deleteLastSampleBtn.setEnabled(False)

        if len(self._samples) < 3:
            self._calibrateBtn.setEnabled(False)

    def _onAcquireSampleClicked(self):
        toolTrackerToCameraTransf = self._positionsClient.getLatestTransf(self._toolKeyToCalibrate)
        self._samples.append(toolTrackerToCameraTransf)

        # draw new visual for sample
        self._sampleVisuals_orig.append(VisualizedOrientation(
            transf=concatenateTransforms([
                self.toolToCalibrate.toolToTrackerTransf,
                toolTrackerToCameraTransf]),
            colorDepthIndicator=self._visualColors_orig[0],
            colorHandleIndicator=self._visualColors_orig[1],
            colorEndpointIndicator=self._visualColors_orig[2],
            plotter=self._plotter,
            actorKeyPrefix=f'OrigSample{len(self._samples)}'
        ))

        if not self._showOrigVisuals.isChecked():
            for actor in self._sampleVisuals_orig[-1].actors.values():
                actor.SetVisibility(False)

        if self._pendingNewTransf is not None:
            self._sampleVisuals_pending.append(VisualizedOrientation(
                transf=concatenateTransforms([
                    self._pendingNewTransf,
                    toolTrackerToCameraTransf]),
                colorDepthIndicator=self._visualColors_pending[0],
                colorHandleIndicator=self._visualColors_pending[1],
                colorEndpointIndicator=self._visualColors_pending[2],
                plotter=self._plotter,
                actorKeyPrefix=f'PendingSample{len(self._samples)}'
            ))
            if not self._showPendingVisuals.isChecked():
                for actor in self._sampleVisuals_pending[-1].actors.values():
                    actor.SetVisibility(False)

        self._deleteLastSampleBtn.setEnabled(True)
        if len(self._samples) > 2:
            self._calibrateBtn.setEnabled(True)

        self._plotter.reset_camera()

    def _onCalibrateClicked(self):

        # set up optimization problem
        def costFn(offset: np.ndarray) -> float:
            uncalibratedToCalibratedTransf = self.toolToCalibrate.toolToTrackerTransf.copy()
            uncalibratedToCalibratedTransf[0:3, -1] = offset

            sampledEndpoints = np.full((len(self._samples), 3), np.nan)
            for iS, sample in enumerate(self._samples):
                sampledEndpoints[iS, :] = applyTransform([uncalibratedToCalibratedTransf, sample], np.asarray([0, 0, 0]))

            meanEndpoint = trim_mean(sampledEndpoints, 0.1, axis=0)

            dists = np.linalg.norm(sampledEndpoints - meanEndpoint, axis=1)**2

            return np.percentile(dists, 80)

        initialOffset = self.toolToCalibrate.toolToTrackerTransf[0:3, -1].T

        maxOffset = 500  # in mm
        bounds = (np.ones((3,))*-maxOffset/2, np.ones((3,))*maxOffset/2)

        logger.debug('Running endpoint calibration optimization')
        result = least_squares(
            fun=costFn,
            x0=initialOffset,
            bounds=bounds
            )
        logger.info(f'Optimization results: {result}')
        optimizedOffset = result.x

        if self._pendingNewTransf is not None:
            # destroy any visuals associated with previous pending transform
            for visual in self._sampleVisuals_pending:
                for actor in visual.actors.values():
                    self._plotter.remove_actor(actor)
            self._sampleVisuals_pending.clear()

        self._pendingNewTransf = self.toolToCalibrate.toolToTrackerTransf.copy()
        self._pendingNewTransf[0:3, -1] = optimizedOffset

        self._revertBtn.setEnabled(True)

        assert len(self._sampleVisuals_pending) == 0
        for iS, sample in enumerate(self._samples):
            toolTrackerToCameraTransf = sample
            self._sampleVisuals_pending.append(VisualizedOrientation(
                transf=concatenateTransforms([
                    self._pendingNewTransf,
                    toolTrackerToCameraTransf]),
                colorDepthIndicator=self._visualColors_pending[0],
                colorHandleIndicator=self._visualColors_pending[1],
                colorEndpointIndicator=self._visualColors_pending[2],
                plotter=self._plotter,
                actorKeyPrefix=f'PendingSample{iS}'
            ))

            if not self._showPendingVisuals.isChecked():
                for actor in self._sampleVisuals_pending[-1].actors.values():
                    actor.SetVisibility(False)

        self._plotter.render()

        self._showPendingVisuals.setEnabled(True)

        self._plotter.reset_camera()

    def _onRevertClicked(self):

        if self._pendingNewTransf is not None:
            for visual in self._sampleVisuals_pending:
                for actor in visual.actors.values():
                    self._plotter.remove_actor(actor)
            self._sampleVisuals_pending.clear()

        self._pendingNewTransf = None
        self._revertBtn.setEnabled(False)

    def _onShowOrigVisualsChanged(self, state: int):
        doShow = state > 0
        for visual in self._sampleVisuals_orig:
            for actor in visual.actors.values():
                actor.SetVisibility(doShow)

    def _onShowPendingVisualsChanged(self, state: int):
        doShow = state > 0
        for visual in self._sampleVisuals_pending:
            for actor in visual.actors.values():
                actor.SetVisibility(doShow)

    def _onShowLiveVisualChanged(self, state: int):
        doShow = state > 0
        if self._liveVisual is not None:
            for actor in self._liveVisual.actors.values():
                actor.SetVisibility(doShow)

    def _onDialogFinished(self, wasAccepted: bool):
        if self._pendingNewTransf is not None:
            self._session.tools[self._toolKeyToCalibrate].toolToTrackerTransf = self._pendingNewTransf
            logger.info('Saved {} calibration: {}'.format(self._toolKeyToCalibrate, self.toolToCalibrate.toolToTrackerTransf))

        self._plotter.close()

    def _onLatestPositionsChanged(self):
        super()._onLatestPositionsChanged()
        if self.toolToCalibrate.isActive and self._positionsClient.getLatestTransf(self._toolKeyToCalibrate, None) is not None:
            self._acquireSampleBtn.setEnabled(True)

            if self._pendingNewTransf is None:
                transfToolToTracker = self.toolToCalibrate.toolToTrackerTransf
            else:
                transfToolToTracker = self._pendingNewTransf
            transfToolTrackerToCamera = self._positionsClient.getLatestTransf(self._toolKeyToCalibrate, None)
            transfToolToCamera = concatenateTransforms([
                        transfToolToTracker,
                        transfToolTrackerToCamera])
            if self._liveVisual is None:
                self._liveVisual = VisualizedOrientation(
                    transf=transfToolToCamera,
                    colorDepthIndicator=self._visualColors_live[0],
                    colorHandleIndicator=self._visualColors_live[1],
                    colorEndpointIndicator=self._visualColors_live[2],
                    plotter=self._plotter,
                    actorKeyPrefix=f'LivePose'
                )
            else:
                self._liveVisual.transf = transfToolToCamera
                for actor in self._liveVisual.actors.values():
                    actor.SetVisibility(self._showLiveVisual.isChecked())
            self._plotter.render()
        else:
            self._acquireSampleBtn.setEnabled(False)
            if self._liveVisual is not None:
                for actor in self._liveVisual.actors.values():
                    actor.SetVisibility(False)
                self._plotter.render()

