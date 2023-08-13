from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import logging
import multiprocessing as mp
import numpy as np
import os
import pathlib
import pyvista as pv
import pyvistaqt as pvqt
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from RTNaBS.Devices.ToolPositionsServer import ToolPositionsServer
from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Devices.IGTLinkToolPositionsServer import IGTLinkToolPositionsServer
from RTNaBS.Devices.SimulatedToolPositionsServer import SimulatedToolPositionsServer
from RTNaBS.Devices.SimulatedToolPositionsClient import SimulatedToolPositionsClient
from RTNaBS.Navigator.Model.Session import Session, Tool, CoilTool, SubjectTracker
from RTNaBS.Navigator.GUI.ViewPanels.MainViewPanelWithDockWidgets import MainViewPanelWithDockWidgets
from RTNaBS.Navigator.GUI.Widgets.TrackingStatusWidget import TrackingStatusWidget
from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError
from RTNaBS.util.pyvista import Actor, setActorUserTransform
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.util.pyvista import DefaultBackgroundPlotter, EmbeddedRemotePlotter


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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

    _plotter: DefaultBackgroundPlotter = attrs.field(init=False)
    _actors: tp.Dict[str, tp.Optional[Actor]] = attrs.field(init=False, factory=dict)

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
        self._serverTypeComboBox.addItems(['IGTLink', 'Simulated'])
        formLayout.addRow('Server type', self._serverTypeComboBox)

        self._serverAddressEdit = QtWidgets.QLineEdit()
        formLayout.addRow('Server addr', self._serverAddressEdit)
        self._serverAddressEdit.textChanged.connect(self._onServerAddressTextChanged)

        btn = QtWidgets.QPushButton('Start server' if self._positionsServerProc is not None else 'Stop server')
        btn.clicked.connect(self._onStartStopServerClicked)
        subContainer.layout().addWidget(btn)
        self._serverStartStopBtn = btn

        container.layout().addStretch()

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._finishInitialization_async))

    async def _finishInitialization_async(self):
        self._plotter = DefaultBackgroundPlotter()

        dock, _ = self._createDockWidget(
            title='Tracked objects',
            widget=self._plotter)
        self._wdgt.addDock(dock, position='right')

        if isinstance(self._plotter, EmbeddedRemotePlotter):
            await self._plotter.isReadyEvent.wait()

        self._positionsClient = ToolPositionsClient()
        self._positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)
        self._positionsClient.sigIsConnectedChanged.connect(self._onClientIsConnectedChanged)

        if isinstance(DefaultBackgroundPlotter, EmbeddedRemotePlotter):
            self._plotter.enable_depth_peeling(4)

        self._plotter.add_axes_at_origin(labels_off=True, line_width=4)

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

        self._onLatestPositionsChanged()
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
        self._session.tools.sigItemsChanged.connect(self._onToolsChanged)
        self._trackingStatusWdgt.session = self.session

        info = self.session.tools.positionsServerInfo
        if info.type is None:
            self._serverTypeComboBox.setCurrentIndex(-1)
        else:
            self._serverTypeComboBox.setCurrentText(info.type)
        self._serverAddressEdit.setText(f'{info.hostname}:{info.pubPort},{info.cmdPort}')

    def _onToolsChanged(self, toolKeysChanged: tp.List[str], changedAttribs: tp.Optional[list[str]] = None):
        didRemove = False
        for key, tool in self.session.tools.items():
            actorKeysForTool = [key + '_tracker', key + '_tool']
            for actorKey in actorKeysForTool:
                if actorKey in self._actors:
                    self._plotter.remove_actor(self._actors[actorKey])
                    self._actors.pop(actorKey)
                    didRemove = True

        if didRemove:
            self._onLatestPositionsChanged()

    def _startPositionsServer(self):
        logger.info('Starting Positions server process')
        match(self.session.tools.positionsServerInfo.type):
            case 'IGTLink':
                Server = IGTLinkToolPositionsServer
            case 'Simulated':
                Server = SimulatedToolPositionsServer
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

        doResetCamera = False

        for key, tool in self.session.tools.items():
            if True == False:
                logger.debug('TODO: delete')
            actorKeysForTool = [key + '_tracker', key + '_tool']
            if isinstance(tool, SubjectTracker):
                actorKeysForTool.append(key + '_subject')

            if not tool.isActive or self._positionsClient.getLatestTransf(key, None) is None:
                # no valid position available
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
                                    if meshColor is None:
                                        if len(mesh.array_names) > 0:
                                            meshColor = None  # use color from surf file
                                        else:
                                            meshColor = '#2222ff'  # default color if nothing else provided
                                    self._actors[actorKey] = self._plotter.add_mesh(mesh=mesh,
                                                                                    color=meshColor,
                                                                                    opacity=1.0 if meshOpacity is None else meshOpacity,
                                                                                    rgb=meshColor is None,
                                                                                    name=actorKey)
                                    doResetCamera = True

                                # apply transform to existing actor
                                setActorUserTransform(self._actors[actorKey],
                                                      concatenateTransforms([
                                                          toolOrTrackerStlToTrackerTransf,
                                                          self._positionsClient.getLatestTransf(key)
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
                                                                            opacity=0.8,
                                                                            name=actorKey)
                            doResetCamera = True

                        setActorUserTransform(self._actors[actorKey],
                                              self._positionsClient.getLatestTransf(key) @ invertTransform(self.session.subjectRegistration.trackerToMRITransf))
                        self._plotter.render()

                if actorKey in self._actors:
                    if doShow and not self._actors[actorKey].GetVisibility():
                        self._actors[actorKey].VisibilityOn()
                        self._plotter.render()
                    elif not doShow and self._actors[actorKey].GetVisibility():
                        self._actors[actorKey].VisibilityOff()
                        self._plotter.render()

        if doResetCamera:
            pass #self._plotter.reset_camera()

    def close(self):
        if self._positionsServerProc is not None:
            self._stopPositionsServer()
        super().close()
