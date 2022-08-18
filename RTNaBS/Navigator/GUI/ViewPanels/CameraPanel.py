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

from . import MainViewPanel
from RTNaBS.Devices.ToolPositionsServer import ToolPositionsServer
from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Devices.IGTLinkToolPositionsServer import IGTLinkToolPositionsServer
from RTNaBS.Navigator.Model.Session import Session, Tool, CoilTool, SubjectTracker
from RTNaBS.Navigator.GUI.Widgets.TrackingStatusWidget import TrackingStatusWidget
from RTNaBS.util.pyvista import Actor, setActorUserTransform
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget


logger = logging.getLogger(__name__)


@attrs.define
class CameraPanel(MainViewPanel):
    """
    For now, assume this will always be connecting to an NDI Polaris camera with PyIGTLink.

    In the future, can update to have a more device-agnostic base class that is subclassed for specific localization systems
    """

    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.cctv'))

    _cameraFOVSTLPath: str = None

    _trackingStatusWdgt: TrackingStatusWidget = attrs.field(init=False)
    _positionsServerProc: tp.Optional[mp.Process] = attrs.field(init=False, default=None)
    _positionsClient: ToolPositionsClient = attrs.field(init=False)

    _btn_startStopServer: QtWidgets.QPushButton = attrs.field(init=False)

    _plotter: pvqt.QtInteractor = attrs.field(init=False)
    _actors: tp.Dict[str, tp.Optional[Actor]] = attrs.field(init=False, factory=dict)
    _ignoredKeys: tp.List[str] = attrs.field(init=False, factory=list)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        if self._cameraFOVSTLPath is None:
            self._cameraFOVSTLPath = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'data', 'tools', 'PolarisVegaFOV.stl')

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        sidebar = QtWidgets.QWidget()
        sidebar.setLayout(QtWidgets.QVBoxLayout())
        self._wdgt.layout().addWidget(sidebar)

        self._trackingStatusWdgt = TrackingStatusWidget(session=self._session)
        sidebar.layout().addWidget(self._trackingStatusWdgt.wdgt)

        container = QtWidgets.QGroupBox('Camera connection')
        container.setLayout(QtWidgets.QVBoxLayout())
        sidebar.layout().addWidget(container)

        # TODO: add GUI controls for configuring, launching, stopping Plus Server
        # for now, assume plus server is launched separately with appropriate tool configs

        subContainer = QtWidgets.QGroupBox('Tool positions server')
        subContainer.setLayout(QtWidgets.QVBoxLayout())
        container.layout().addWidget(subContainer)

        btn = QtWidgets.QPushButton('Start server')
        btn.clicked.connect(self._onStartStopServerClicked)
        subContainer.layout().addWidget(btn)
        self._btn_startStopServer = btn

        container.layout().addStretch()

        self._positionsClient = ToolPositionsClient()
        self._positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)

        self._plotter = pvqt.BackgroundPlotter(
            show=False,
            app=QtWidgets.QApplication.instance()
        )
        self._plotter.set_background('#FFFFFF')
        self._plotter.enable_depth_peeling(4)

        self._plotter.add_axes_at_origin(labels_off=True, line_width=4)

        self._wdgt.layout().addWidget(self._plotter.interactor)

    def canBeEnabled(self) -> bool:
        return self.session is not None

    def _finishInitialization(self):
        super()._finishInitialization()
        self._onLatestPositionsChanged()

    def _onSessionSet(self):
        super()._onSessionSet()
        self._session.tools.sigToolsChanged.connect(self._onToolsChanged)
        self._trackingStatusWdgt.session = self.session

    def _onToolsChanged(self, toolKeysChanged: tp.List[str]):
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

    def _onStartStopServerClicked(self, checked: bool):
        if self._positionsServerProc is None:
            # start server
            logger.info('Starting Positions server process')
            self._positionsServerProc = mp.Process(target=IGTLinkToolPositionsServer.createAndRun)
            self._positionsServerProc.start()
            self._btn_startStopServer.setText('Stop server')
        else:
            # stop server
            logger.info('Stopping Positions server process')
            self._positionsServerProc.kill()
            self._positionsServerProc = None
            self._btn_startStopServer.setText('Start server')

    def _onLatestPositionsChanged(self):
        if not self._hasInitialized:
            return

        actorKey = 'cameraFOV'
        if actorKey not in self._actors:
            logger.debug('Loading cameraFOV mesh from {}'.format(self._cameraFOVSTLPath))
            cameraFOVMesh = pv.read(self._cameraFOVSTLPath)
            self._actors[actorKey] = self._plotter.add_mesh(mesh=cameraFOVMesh,
                                                            color='#222222',
                                                            opacity=0.1,
                                                            show_edges=True,
                                                            name=actorKey)
            setActorUserTransform(self._actors[actorKey], np.asarray(
                [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, -1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]))

        for key, tool in self.session.tools.items():
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
                canShow = False
                for toolOrTracker in ('tracker', 'tool'):
                    if actorKey == key + '_' + toolOrTracker:
                        if getattr(tool, toolOrTracker + 'StlFilepath') is not None:
                            if toolOrTracker == 'tool':
                                toolOrTrackerStlToTrackerTransf = tool.toolToTrackerTransf @ tool.toolStlToToolTransf
                            elif toolOrTracker == 'tracker':
                                toolOrTrackerStlToTrackerTransf = tool.trackerStlToTrackerTransf
                            else:
                                raise NotImplementedError()
                            if toolOrTrackerStlToTrackerTransf is not None:
                                canShow = True
                        else:
                            # TODO: show some generic graphic to indicate tool position, even when we don't have an stl for the tool
                            canShow = False

                        if canShow:
                            if actorKey not in self._actors:
                                # initialize graphic
                                self._actors[actorKey] = self._plotter.add_mesh(mesh=getattr(tool, toolOrTracker + 'Surf'),
                                                       color='#2222FF',
                                                       opacity=0.8,
                                                       name=actorKey)

                            # apply transform to existing actor
                            setActorUserTransform(self._actors[actorKey],
                                                  concatenateTransforms([
                                                      toolOrTrackerStlToTrackerTransf,
                                                      self._positionsClient.getLatestTransf(key)
                                                  ]))

                if isinstance(tool, SubjectTracker) and actorKey == tool.key + '_subject':
                    if self.session.subjectRegistration.trackerToMRITransf is not None and self.session.headModel.skinSurf is not None:
                        canShow = True
                        if actorKey not in self._actors:
                            self._actors[actorKey] = self._plotter.add_mesh(mesh=self.session.headModel.skinSurf,
                                                                            color='#d9a5b2',
                                                                            opacity=0.8,
                                                                            name=actorKey)

                        setActorUserTransform(self._actors[actorKey],
                                              self._positionsClient.getLatestTransf(key) @ invertTransform(self.session.subjectRegistration.trackerToMRITransf))

                if actorKey in self._actors:
                    if canShow and not self._actors[actorKey].GetVisibility():
                        self._actors[actorKey].VisibilityOn()
                    elif not canShow and self._actors[actorKey].GetVisibility():
                        self._actors[actorKey].VisibilityOff()

