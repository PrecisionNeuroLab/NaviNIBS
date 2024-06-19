from __future__ import annotations

import asyncio

import attrs
import logging
import multiprocessing as mp
import numpy as np
import qtawesome as qta
from qtpy import QtWidgets, QtGui
import typing as tp

from NaviNIBS.Devices.ToolPositionsServer import ToolPositionsServer
from NaviNIBS.Devices.ToolPositionsClient import ToolPositionsClient
from NaviNIBS.Devices.IGTLinkToolPositionsServer import IGTLinkToolPositionsServer
from NaviNIBS.Navigator.Model.Session import Session, SubjectTracker
from NaviNIBS.Navigator.GUI.ViewPanels.MainViewPanelWithDockWidgets import MainViewPanelWithDockWidgets
from NaviNIBS.Navigator.GUI.Widgets.TrackingStatusWidget import TrackingStatusWidget
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.pyvista import Actor, setActorUserTransform, RemotePlotterProxy
from NaviNIBS.util.GUI.Dock import Dock
from NaviNIBS.util.Transforms import invertTransform, concatenateTransforms, applyTransform
from NaviNIBS.util.GUI.QueuedRedrawMixin import QueuedRedrawMixin
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@attrs.define
class CameraObjectsView(QueuedRedrawMixin):
    _positionsClient: ToolPositionsClient
    _session: Session

    _plotter: DefaultBackgroundPlotter = attrs.field(init=False)

    _actors: tp.Dict[str, tp.Optional[Actor]] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        QueuedRedrawMixin.__attrs_post_init__(self)

        self._plotter = DefaultBackgroundPlotter()

        self._session.tools.sigItemsChanged.connect(lambda *args: self._queueRedraw('afterToolsChanged'))

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._finishInitialization_async))

    @property
    def plotter(self):
        return self._plotter

    @property
    def session(self):
        return self._session

    async def _finishInitialization_async(self):
        if isinstance(self._plotter, RemotePlotterProxy):
            await self._plotter.isReadyEvent.wait()

        if isinstance(self._plotter, RemotePlotterProxy):
            self._plotter.enable_depth_peeling(4)

        self._plotter.add_axes_at_origin(labels_off=True, line_width=4)

        self._positionsClient.sigLatestPositionsChanged.connect(lambda: self._queueRedraw('toolPositions'))

        self._redraw('all')

        # if necessary info available, reorient camera view based on subject location
        if not self.session.subjectRegistration.isRegistered:
            logger.info('Not setting camera due lack of subject registration')
        else:
            subTracker = self.session.tools.subjectTracker
            if subTracker is None:
                logger.info('Not setting camera due lack of defined subject tracker')
            else:
                transf_subTrackToWorld = self._positionsClient.getLatestTransf(subTracker.key, None)
                if transf_subTrackToWorld is None:
                    logger.info('Not setting camera due to lack of valid subject tracker position')
                else:
                    transf_MRIToWorld = concatenateTransforms([
                        invertTransform(self.session.subjectRegistration.trackerToMRITransf),
                        transf_subTrackToWorld,
                    ])

                    # for now, assume MRI direction is consistent
                    # could alternatively look for "standard" fiducial names and define relative to those here
                    # or use MNI space
                    cameraPts_MRI = np.asarray([
                        [0, 0, 0],  # focal point
                        np.asarray([-0.8, 1, 0.3]) * 1000,  # position
                        [0, 0, 1]])  # up

                    cameraPts_world = applyTransform(transf_MRIToWorld, cameraPts_MRI)

                    plotter = self._plotter
                    with plotter.allowNonblockingCalls():
                        plotter.camera.focal_point = cameraPts_world[0, :]
                        plotter.camera.position = cameraPts_world[1, :]
                        plotter.camera.up = cameraPts_world[2, :] - cameraPts_world[1, :]

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str]] = None, **kwargs):
        super()._redraw(which=which, **kwargs)

        if isinstance(self._plotter, RemotePlotterProxy) and not self._plotter.isReadyEvent.is_set():
            # remote plotter not ready yet
            return

        if which is None:
            which = 'all'
            self._redraw(which=which, **kwargs)
            return

        if not isinstance(which, str):
            for subWhich in which:
                self._redraw(which=subWhich, **kwargs)
            return

        if which == 'all':
            self._redraw(which=['toolPositions'])
            return

        if which == 'toolPositions':
            doResetCamera = False

            for key, tool in self.session.tools.items():
                actorKeysForTool = [key + '_tracker', key + '_tool']
                if isinstance(tool, SubjectTracker):
                    actorKeysForTool.append(key + '_subject')

                if not tool.isActive or self._positionsClient.getLatestTransf(tool.trackerKey, None) is None:
                    # no valid position available
                    with self._plotter.allowNonblockingCalls():
                        for actorKey in actorKeysForTool:
                            if actorKey in self._actors and self._actors[actorKey].GetVisibility():
                                self._actors[actorKey].VisibilityOff()
                    continue

                for actorKey in actorKeysForTool:
                    logger.debug(f'actorKey: {actorKey}')

                    doShow = False
                    for toolOrTracker in ('tracker', 'tool'):
                        if actorKey == (key + '_' + toolOrTracker):
                            if getattr(tool, 'doRender' + toolOrTracker.capitalize()) is False:
                                doShow = False
                            else:
                                if getattr(tool, toolOrTracker + 'StlFilepath') is not None:
                                    if toolOrTracker == 'tool':
                                        if tool.toolToTrackerTransf is None:
                                            toolOrTrackerStlToTrackerTransf = None
                                        else:
                                            toolOrTrackerStlToTrackerTransf = tool.toolToTrackerTransf @ tool.toolStlToToolTransf
                                    elif toolOrTracker == 'tracker':
                                        toolOrTrackerStlToTrackerTransf = tool.trackerStlToTrackerTransf
                                    else:
                                        raise NotImplementedError()
                                    if toolOrTrackerStlToTrackerTransf is not None:
                                        doShow = True

                                        if actorKey not in self._actors:
                                            # initialize graphic
                                            mesh = getattr(tool, toolOrTracker + 'Surf')
                                            meshColor = tool.trackerColor if toolOrTracker == 'tracker' else tool.toolColor
                                            meshOpacity = tool.trackerOpacity if toolOrTracker == 'tracker' else tool.toolOpacity

                                            self._actors[actorKey] = self._plotter.addMesh(mesh=mesh,
                                                                                           color=meshColor,
                                                                                           defaultMeshColor='#2222ff',
                                                                                           opacity=1.0 if meshOpacity is None else meshOpacity,
                                                                                           name=actorKey)

                                            doResetCamera = True

                                    if doShow:
                                        with self._plotter.allowNonblockingCalls():
                                            # apply transform to existing actor
                                            setActorUserTransform(self._actors[actorKey],
                                                                  concatenateTransforms([
                                                                      toolOrTrackerStlToTrackerTransf,
                                                                      self._positionsClient.getLatestTransf(tool.trackerKey)
                                                                  ]))
                                            self._plotter.render()
                                else:
                                    # TODO: show some generic graphic to indicate tool position, even when we don't have an stl for the tool
                                    doShow = False

                    if isinstance(tool, SubjectTracker) and actorKey == tool.key + '_subject':
                        if self.session.subjectRegistration.trackerToMRITransf is not None and self.session.headModel.skinSurf is not None:
                            doShow = True
                            if actorKey not in self._actors:
                                self._actors[actorKey] = self._plotter.add_mesh(mesh=self.session.headModel.skinSurf,
                                                                                color='#d9a5b2',
                                                                                # opacity=0.8,
                                                                                name=actorKey)
                                doResetCamera = True

                            with self._plotter.allowNonblockingCalls():
                                setActorUserTransform(self._actors[actorKey],
                                                      self._positionsClient.getLatestTransf(tool.trackerKey) @ invertTransform(self.session.subjectRegistration.trackerToMRITransf))
                                self._plotter.render()

                    if actorKey in self._actors:
                        with self._plotter.allowNonblockingCalls():
                            if doShow and not self._actors[actorKey].GetVisibility():
                                self._actors[actorKey].VisibilityOn()
                                self._plotter.render()
                            elif not doShow and self._actors[actorKey].GetVisibility():
                                self._actors[actorKey].VisibilityOff()
                                self._plotter.render()

            if doResetCamera:
                if False:
                    with self._plotter.allowNonblockingCalls():
                        self._plotter.reset_camera()

        elif which == 'afterToolsChanged':
            # remove any tool actors, to be regenerated later
            didRemove = False
            for key, tool in self.session.tools.items():
                actorKeysForTool = [key + '_tracker', key + '_tool']
                with self._plotter.allowNonblockingCalls():
                    for actorKey in actorKeysForTool:
                        if actorKey in self._actors:
                            self._plotter.remove_actor(self._actors.pop(actorKey))
                            didRemove = True

            if didRemove:
                self._redraw('toolPositions')

        else:
            raise NotImplementedError  # TODO


@attrs.define
class CameraPanel(MainViewPanelWithDockWidgets):
    """
    For now, assume this will always be connecting to an NDI Polaris camera with PyIGTLink.

    In the future, can update to have a more device-agnostic base class that is subclassed for specific localization systems
    """
    _key: str = 'Camera'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.cctv'))

    _trackingStatusWdgt: TrackingStatusWidget = attrs.field(init=False)
    _positionsServerProc: tp.Optional[mp.Process] = attrs.field(init=False, default=None)
    _positionsClient: ToolPositionsClient = attrs.field(init=False)

    _serverTypeComboBox: QtWidgets.QComboBox = attrs.field(init=False)
    _serverAddressEdit: QtWidgets.QLineEdit = attrs.field(init=False)
    _serverStartStopBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _serverGUIContainer: QtWidgets.QGroupBox = attrs.field(init=False)

    _mainCameraView: CameraObjectsView = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def canBeEnabled(self) -> tuple[bool, str | None]:
        if self.session is None:
            return False, 'No session set'
        return True, None

    def _finishInitialization(self):
        super()._finishInitialization()

        self._trackingStatusWdgt = TrackingStatusWidget(session=self._session, wdgt=QtWidgets.QWidget())
        dock, _ = self._createDockWidget(
            title='Tracking status',
            widget=self._trackingStatusWdgt.wdgt)
        dock.setStretch(1, 10)
        dock.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        self._wdgt.addDock(dock, position='left')

        dock, container = self._createDockWidget(
            title='Camera connection',
            layout=QtWidgets.QVBoxLayout(),
        )
        dock.setStretch(1, 10)
        self._wdgt.addDock(dock, position='bottom')

        self._serverGUIContainer = container

        # TODO: add GUI controls for configuring, launching, stopping Plus Server
        # for now, assume plus server is launched separately with appropriate tool configs

        subContainer = QtWidgets.QGroupBox('Tool positions server')
        formLayout = QtWidgets.QFormLayout()
        subContainer.setLayout(formLayout)
        container.layout().addWidget(subContainer)

        self._serverTypeComboBox = QtWidgets.QComboBox()
        self._serverTypeComboBox.addItems(['IGTLink', 'Generic'])
        formLayout.addRow('Server type', self._serverTypeComboBox)

        self._serverAddressEdit = QtWidgets.QLineEdit()
        formLayout.addRow('Server addr', self._serverAddressEdit)
        self._serverAddressEdit.textChanged.connect(self._onServerAddressTextChanged)

        btn = QtWidgets.QPushButton('Start server' if self._positionsServerProc is not None else 'Stop server')
        btn.clicked.connect(self._onStartStopServerClicked)
        subContainer.layout().addWidget(btn)
        self._serverStartStopBtn = btn

        container.layout().addStretch()

        self._positionsClient = ToolPositionsClient()
        self._positionsClient.sigIsConnectedChanged.connect(self._onClientIsConnectedChanged)

        self._mainCameraView = CameraObjectsView(
            positionsClient=self._positionsClient,
            session=self.session
        )

        # TODO: create other camera views as defined in session config

        dock, _ = self._createDockWidget(
            title='Tracked objects',
            widget=self._mainCameraView.plotter)
        self._wdgt.addDock(dock, position='right')

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._finishInitialization_async))

    async def _finishInitialization_async(self):

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

        self._onClientIsConnectedChanged()

    def _onSessionSet(self):
        super()._onSessionSet()

        if self._positionsServerProc is not None:
            # kill previous server
            self._stopPositionsServer()

        if self.session.tools.positionsServerInfo.doAutostart:
            self._startPositionsServer()

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):

        assert self._mainCameraView.session is self.session
        # TODO: assert for other views too

        self._trackingStatusWdgt.session = self.session

        info = self.session.tools.positionsServerInfo
        if info.type is None:
            self._serverTypeComboBox.setCurrentIndex(-1)
        else:
            self._serverTypeComboBox.setCurrentText(info.type)
        self._serverAddressEdit.setText(f'{info.hostname}:{info.pubPort},{info.cmdPort}')

    def _startPositionsServer(self):
        logger.info('Starting Positions server process')
        match(self.session.tools.positionsServerInfo.type):
            case 'IGTLink':
                Server = IGTLinkToolPositionsServer
            case 'Generic':
                Server = ToolPositionsServer
            case _:
                raise NotImplementedError(f'Unexpected positionsServerInfo type: {self.session.tools.positionsServerInfo.type}')
        self._positionsServerProc = mp.Process(target=Server.createAndRun,
                                               daemon=True,
                                               kwargs=self.session.tools.positionsServerInfo.initKwargs)
        self._positionsServerProc.start()
        if self._hasInitialized:
            self._serverStartStopBtn.setText('Stop server')

    def _stopPositionsServer(self):
        logger.info('Stopping Positions server process')
        self._positionsServerProc.kill()
        self._positionsServerProc = None
        if self._hasInitialized:
            self._serverStartStopBtn.setText('Start server')

    def _onStartStopServerClicked(self, checked: bool):
        if self._positionsServerProc is None:
            # start server
            self._startPositionsServer()
        else:
            # stop server
            self._stopPositionsServer()

    def _onServerAddressTextChanged(self, newVal: str):
        pass  # TODO: if changed, apply edits to ToolPositionsServerInfo

    def _onClientIsConnectedChanged(self):
        if not self._hasInitialized and not self.isInitializing:
            return

        if self._positionsClient.isConnected:
            self._serverStartStopBtn.setText('Stop server')

            serverType = self._positionsClient.getServerType()

            # TODO: maybe pop up a warning dialog here if old type does not match new server type,
            #  since this will overwrite setting in session file
            self.session.tools.positionsServerInfo.type = serverType
        else:
            self._serverStartStopBtn.setText('Start server')

    def _onLatestPositionsChanged(self):
        if not self._hasInitialized and not self.isInitializing:
            return

    def close(self):
        if self._positionsServerProc is not None:
            self._stopPositionsServer()
        super().close()
