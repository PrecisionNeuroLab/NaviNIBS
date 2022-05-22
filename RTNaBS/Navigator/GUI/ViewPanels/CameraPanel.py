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
from RTNaBS.Navigator.Model.Session import Session, Tool, CoilTool
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget


logger = logging.getLogger(__name__)


Actor = pv._vtk.vtkActor

@attrs.define
class CameraPanel(MainViewPanel):
    """
    For now, assume this will always be connecting to an NDI Polaris camera with PyIGTLink.

    In the future, can update to have a more device-agnostic base class that is subclassed for specific localization systems
    """

    _positionsServerProc: tp.Optional[mp.Process] = attrs.field(init=False, default=None)
    _positionsClient: ToolPositionsClient = attrs.field(init=False)

    _btn_startStopServer: QtWidgets.QPushButton = attrs.field(init=False)

    _plotter: pvqt.QtInteractor = attrs.field(init=False)
    _actors: tp.Dict[str, tp.Optional[Actor]] = attrs.field(init=False, factory=dict)
    _ignoredKeys: tp.List[str] = attrs.field(init=False, factory=list)

    _hasBeenActivated: bool = attrs.field(init=False, default=False)

    def __attrs_post_init__(self):
        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        container = QtWidgets.QGroupBox('Camera connection')
        container.setLayout(QtWidgets.QVBoxLayout())
        self._wdgt.layout().addWidget(container)

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
        self._wdgt.layout().addWidget(self._plotter.interactor)

        self.sigPanelActivated.connect(self._onPanelActivated)

    def _onPanelActivated(self):
        self._hasBeenActivated = True
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
        if not self._hasBeenActivated:
            return

        for longKey, pos in self._positionsClient.latestPositions.items():
            if not longKey.endswith('ToTracker'):
                if longKey not in self._ignoredKeys:
                    logger.warning('Unexpected transform: {}. Ignoring'.format(longKey))
                    self._ignoredKeys.append(longKey)
                continue
            key = longKey[:-len('ToTracker')]
            if key not in self._actors:
                if key not in self.session.tools:
                    logger.warning('Position key {} has no match in session tool keys. Ignoring'.format(key))
                    self._actors[key] = None
                    continue

                tool = self.session.tools[key]

                # initialize graphic

                if tool.stlFilepath is not None:
                    self._actors[key] = self._plotter.add_mesh(mesh=tool.trackerSurf,
                                           color='#2222FF',
                                           opacity=0.8,
                                           name=tool.key)
                elif isinstance(tool, CoilTool) and tool.coilStlFilepath is not None:
                    # TODO: this should only be plotted if tool.trackerToToolTransf is set, but for now include anyways
                    self._actors[key] = self._plotter.add_mesh(mesh=tool.coilSurf,
                                           color='#FF2222',
                                           opacity=0.8,
                                           name=tool.key)

                else:
                    logger.warning('No mesh available for {}, not plotting'.format(key))
                    # TODO: plot some generate shape (e.g. small crosshairs) for object
                    continue

            # TODO: apply transform to existing actor
            t = pv._vtk.vtkTransform()
            t.SetMatrix(pv.vtkmatrix_from_array(pos.transf))
            self._actors[key].SetUserTransform(t)







