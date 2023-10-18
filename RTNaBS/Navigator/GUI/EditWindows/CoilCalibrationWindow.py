import asyncio
import attrs
import inspect
import logging
import numpy as np
import pyvista as pv
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from RTNaBS.Navigator.GUI.EditWindows.ToolCalibrationWindow import ToolCalibrationWindow
from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms
from RTNaBS.util.pyvista import Actor, setActorUserTransform, concatenateLineSegments
from RTNaBS.util.pyvista import DefaultPrimaryLayeredPlotter, DefaultSecondaryLayeredPlotter, RemotePlotterProxy


logger = logging.getLogger(__name__)


@attrs.define
class CoilCalibrationWithPlateWindow(ToolCalibrationWindow):
    """
    Calibrate a coil by aligning to a calibration plate
    """

    _pendingNewTransf: tp.Optional[np.ndarray] = attrs.field(init=False, default=None)  # result of calibration, not yet saved to tool

    _instructions: QtWidgets.QTextEdit = attrs.field(init=False)

    _plotter: DefaultPrimaryLayeredPlotter = attrs.field(init=False)
    _plotterUpperLayer: DefaultSecondaryLayeredPlotter = attrs.field(init=False)
    _plotterLowerLayer: DefaultSecondaryLayeredPlotter = attrs.field(init=False)

    _coilToolActor: Actor | None = attrs.field(init=False, default=None)
    _coilTrackerActor: Actor | None = attrs.field(init=False, default=None)
    _calibrationPlateToolActor: Actor | None = attrs.field(init=False, default=None)
    _calibrationPlateTrackerActor: Actor | None = attrs.field(init=False, default=None)
    _coilAxesActor: Actor | None = attrs.field(init=False, default=None)
    _calibrationPlateAxesActor: Actor | None = attrs.field(init=False, default=None)

    _calibrateBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _revertBtn: QtWidgets.QPushButton = attrs.field(init=False)

    _finishedAsyncInit: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    # TODO: add error metrics

    # TODO: add checkboxes to optionally hide some actors

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        splitter = QtWidgets.QSplitter()
        self._wdgt.layout().addWidget(splitter)

        self._instructions = QtWidgets.QTextEdit('')
        self._instructions.setReadOnly(True)
        self._instructions.setMarkdown(inspect.cleandoc("""
                    # Coil calibration with calibration plate
                    ## Instructions
                    1. Hold calibration plate in location visible to camera.
                    2. Position coil on plate, with top face of plate touching bottom face of coil.
                    3. Align center of coil with center of plate, and align coil axes with plate axes.
                    4. Press "Calibrate coil".
                    5. Check calibration by removing coil, then repositioning in same place and evaluating visual alignment and error metrics.
                    6. Repeat calibration if necessary.
                    7. Close this window to accept the calibration. 
                """))
        splitter.addWidget(self._instructions)

        splitRight = QtWidgets.QWidget()
        splitRight.setLayout(QtWidgets.QVBoxLayout())
        splitter.addWidget(splitRight)

        self._plotter = DefaultPrimaryLayeredPlotter()
        splitRight.layout().addWidget(self._plotter)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QHBoxLayout())
        splitRight.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Calibrate coil')
        btn.clicked.connect(lambda checked: self.calibrate())
        btn.setEnabled(False)
        btnContainer.layout().addWidget(btn)
        self._calibrateBtn = btn

        btn = QtWidgets.QPushButton('Revert to original calibration')
        btn.clicked.connect(lambda checked: self.revert())
        btn.setEnabled(False)
        btnContainer.layout().addWidget(btn)
        self._revertBtn = btn

        # TODO: add error metrics (distance from previous calibration) for checking if need to recalibrate

        self.sigFinished.connect(self._onDialogFinished)

        self._wdgt.resize(QtCore.QSize(1000, 1200))

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._finishInitialization_async))

    async def _finishInitialization_async(self):

        if isinstance(self._plotter, RemotePlotterProxy):
            await self._plotter.isReadyEvent.wait()

        self._plotterUpperLayer = self._plotter.addLayeredPlotter(key='AxesIndicators', layer=1)
        self._plotterLowerLayer = self._plotter.addLayeredPlotter(key='Meshes', layer=0)

        self._finishedAsyncInit.set()

        self._onLatestPositionsChanged()

    def calibrate(self):
        # TODO: spin-wait positions client to make sure we have most up-to-date information
        coilTrackerToCameraTransf = self._positionsClient.getLatestTransf(self._toolKeyToCalibrate)
        calibrationPlateTrackerToCameraTransf = self._positionsClient.getLatestTransf(self._session.tools.calibrationPlate.key)
        calibrationPlateToTrackerTransf = self._session.tools.calibrationPlate.toolToTrackerTransf

        self._pendingNewTransf = concatenateTransforms([
            calibrationPlateToTrackerTransf,
            calibrationPlateTrackerToCameraTransf,
            invertTransform(coilTrackerToCameraTransf)
        ])

        logger.info('Calibrated {} transform: {}'.format(self._toolKeyToCalibrate, self._pendingNewTransf))

        self._revertBtn.setEnabled(True)

        self._onLatestPositionsChanged()

    def revert(self):
        self._pendingNewTransf = None
        self._revertBtn.setEnabled(False)
        self._onLatestPositionsChanged()

    def _onDialogFinished(self, wasAccepted: bool):
        if self._pendingNewTransf is not None:
            self._session.tools[self._toolKeyToCalibrate].toolToTrackerTransf = self._pendingNewTransf
            logger.info('Saved {} calibration: {}'.format(self._toolKeyToCalibrate, self.toolToCalibrate.toolToTrackerTransf))

        self._plotter.close()

    def _resetCamera(self):
        if isinstance(self._plotter, RemotePlotterProxy) and not self._plotter.isReadyEvent.is_set():
            # plotter not yet ready
            return

        self._plotter.camera.focal_point = (0, 0, 0)
        self._plotter.camera.position = (0, 0, 700)
        self._plotter.camera.up = (0, 1, 0)
        self._plotter.camera.view_angle = 30.
        self._plotter.camera.clipping_range = (1, 1400)
        self._plotter.render()

    def _onLatestPositionsChanged(self):

        if not self._finishedAsyncInit.is_set():
            # plotter not yet ready
            return

        needsRender = False
        doResetCamera = False

        super()._onLatestPositionsChanged()

        calibrationPlate = self._session.tools.calibrationPlate
        if self.toolToCalibrate.isActive and calibrationPlate is not None \
                and self._positionsClient.getLatestTransf(self._toolKeyToCalibrate, None) is not None \
                and self._positionsClient.getLatestTransf(calibrationPlate.key, None) is not None:
            self._calibrateBtn.setEnabled(True)
        else:
            self._calibrateBtn.setEnabled(False)

        calibPlate = self._session.tools.calibrationPlate
        if calibPlate is None:
            calibPlateTrackerToCameraTransf = None
        else:
            calibPlateTrackerToCameraTransf = self._positionsClient.getLatestTransf(calibPlate.key, None)

        if calibPlateTrackerToCameraTransf is None:
            # without calibration plate pose, can't show anything in view that is relative to calibration plate
            for actor in (
                self._calibrationPlateAxesActor,
                self._calibrationPlateToolActor,
                self._calibrationPlateTrackerActor,
                self._coilAxesActor,
                self._coilToolActor,
                self._coilTrackerActor,
            ):
                if actor is not None and actor.GetVisibility():
                    actor.VisibilityOff()
                    needsRender = True

            if needsRender:
                self._plotter.render()
            return

        else:
            for actor in (
                    self._calibrationPlateAxesActor,
                    self._calibrationPlateToolActor,
                    self._calibrationPlateTrackerActor
            ):
                if actor is not None and not actor.GetVisibility():
                    actor.VisibilityOn()
                    needsRender = True

        if self._calibrationPlateAxesActor is None:
            self._calibrationPlateAxesActor = self._plotterUpperLayer.add_axes_marker(
                xlabel='Ref X\n',
                ylabel='Ref Y\n',
                zlabel='',
                label_size=(0.1, 0.1),
                line_width=3,
                total_length=(50, 50, 50)
            )
            # no transform needed here
            needsRender = True
            doResetCamera = True

        if self._calibrationPlateToolActor is None:
            if calibPlate.toolSurf is not None:
                mesh = calibPlate.toolSurf
                meshColor = calibPlate.toolColor
                scalars = None
                if meshColor is None:
                    if len(mesh.array_names) > 0:
                        meshColor = None  # use color from surf file
                        scalars = mesh.array_names[-1]
                    else:
                        meshColor = '#2222ff'  # default color if nothing else provided
                self._calibrationPlateToolActor = self._plotterLowerLayer.add_mesh(
                    mesh=mesh,
                    color=meshColor,
                    scalars=scalars,
                    opacity=0.5,
                    rgb=meshColor is None
                )
                setActorUserTransform(self._calibrationPlateToolActor, calibPlate.toolStlToToolTransf)
                needsRender = True
                doResetCamera = True

        if self._calibrationPlateTrackerActor is None:
            if calibPlate.trackerSurf is not None:
                mesh = calibPlate.trackerSurf
                meshColor = calibPlate.trackerColor
                scalars = None
                if meshColor is None:
                    if len(mesh.array_names) > 0:
                        meshColor = None  # use color from surf file
                        scalars = mesh.array_names[-1]
                    else:
                        meshColor = '#2222ff'  # default color if nothing else provided
                self._calibrationPlateTrackerActor = self._plotterLowerLayer.add_mesh(
                    mesh=mesh,
                    color=meshColor,
                    scalars=scalars,
                    opacity=0.5,
                    rgb=meshColor is None
                )
                setActorUserTransform(self._calibrationPlateTrackerActor, concatenateTransforms([
                    calibPlate.trackerStlToTrackerTransf,
                    invertTransform(calibPlate.toolToTrackerTransf),
                ]))
                needsRender = True
                doResetCamera = True

        coil = self.toolToCalibrate
        if coil is None:
            coilTrackerToCameraTransf = None
        else:
            coilTrackerToCameraTransf = self._positionsClient.getLatestTransf(self._toolKeyToCalibrate, None)

        if coilTrackerToCameraTransf is None:
            # without coil pose, can't show any of the coil-related actors
            for actor in (
                    self._coilAxesActor,
                    self._coilToolActor,
                    self._coilTrackerActor,
            ):
                if actor is not None and actor.GetVisibility():
                    actor.VisibilityOff()
                    needsRender = True

            if doResetCamera:
                self._resetCamera()

            if needsRender:
                self._plotter.render()
            return

        else:
            for actor in (
                    self._coilAxesActor,
                    self._coilToolActor,
                    self._coilTrackerActor,
            ):
                if actor is not None and not actor.GetVisibility():
                    actor.VisibilityOn()

        if self._coilAxesActor is None:
            self._coilAxesActor = self._plotterUpperLayer.add_axes_marker(
                xlabel='\nCoil X',
                ylabel='\nCoil Y',
                zlabel='',
                label_size=(0.1, 0.1),
                total_length=(50, 50, 50)
            )
            doResetCamera = True

        if self._coilToolActor is None:
            if coil.toolSurf is not None:
                mesh = coil.toolSurf
                meshColor = coil.toolColor
                scalars = None
                if meshColor is None:
                    if len(mesh.array_names) > 0:
                        meshColor = None  # use color from surf file
                        scalars = mesh.array_names[-1]
                    else:
                        meshColor = '#2222ff'  # default color if nothing else provided
                self._coilToolActor = self._plotterLowerLayer.add_mesh(
                    mesh=mesh,
                    color=meshColor,
                    scalars=scalars,
                    opacity=0.5,
                    rgb=meshColor is None
                )
                doResetCamera = True

        if self._coilTrackerActor is None:
            if coil.trackerSurf is not None:
                mesh = coil.trackerSurf
                meshColor = coil.trackerColor
                scalars = None
                if meshColor is None:
                    if len(mesh.array_names) > 0:
                        meshColor = None  # use color from surf file
                        scalars = mesh.array_names[-1]
                    else:
                        meshColor = '#2222ff'  # default color if nothing else provided
                self._coilTrackerActor = self._plotterLowerLayer.add_mesh(
                    mesh=mesh,
                    color=meshColor,
                    scalars=scalars,
                    opacity=0.5,
                    rgb=meshColor is None
                )
                doResetCamera = True

        coilTrackerToCalibToolTransf = concatenateTransforms([
            coilTrackerToCameraTransf,
            invertTransform(calibPlateTrackerToCameraTransf),
            invertTransform(calibPlate.toolToTrackerTransf)
        ])
        if self._coilTrackerActor is not None:
            setActorUserTransform(self._coilTrackerActor, concatenateTransforms([
                coil.trackerStlToTrackerTransf,
                coilTrackerToCalibToolTransf
            ]))

        coilToolToCalibToolTransf = concatenateTransforms([
            coil.toolToTrackerTransf if self._pendingNewTransf is None else self._pendingNewTransf,
            coilTrackerToCalibToolTransf
        ])
        setActorUserTransform(self._coilAxesActor, coilToolToCalibToolTransf)
        if self._coilToolActor is not None:
            setActorUserTransform(self._coilToolActor, concatenateTransforms([
                coil.toolStlToToolTransf,
                coilToolToCalibToolTransf
            ]))

        if doResetCamera:
            self._resetCamera()

        self._plotter.render()


@attrs.define
class CoilCalibrationWithPointerWindow(ToolCalibrationWindow):
    """
    Calibrate a coil by pointing to known locations on the coil with pointer
    """
    def __attrs_post_init__(self):
        raise NotImplementedError  # TODO