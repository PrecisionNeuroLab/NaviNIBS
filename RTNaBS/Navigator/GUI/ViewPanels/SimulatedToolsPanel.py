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
import pyvista._vtk as vtk
import pyvistaqt as pvqt
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from . import MainViewPanel
from RTNaBS.Devices.ToolPositionsServer import ToolPositionsServer
from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Devices.IGTLinkToolPositionsServer import IGTLinkToolPositionsServer
from RTNaBS.Devices.SimulatedToolPositionsServer import SimulatedToolPositionsServer
from RTNaBS.Devices.SimulatedToolPositionsClient import SimulatedToolPositionsClient
from RTNaBS.Navigator.Model.Session import Session, Tool, CoilTool, SubjectTracker
from RTNaBS.Navigator.GUI.Widgets.TrackingStatusWidget import TrackingStatusWidget
from RTNaBS.util.pyvista import Actor, setActorUserTransform
from RTNaBS.util.pyvista.PlotInteraction import pickActor, interactivelyMoveActor
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms
from RTNaBS.util.GUI import DockWidgets as dw
from RTNaBS.util.GUI.DockWidgets.DockWidgetsContainer import DockWidgetsContainer
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.util.pyvista.plotting import BackgroundPlotter


logger = logging.getLogger(__name__)


@attrs.define
class SimulatedToolsPanel(MainViewPanel):
    _wdgt: DockWidgetsContainer = attrs.field(init=False)
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.progress-wrench'))
    _dockWidgets: tp.Dict[str, dw.DockWidget] = attrs.field(init=False, factory=dict)
    _trackingStatusWdgt: TrackingStatusWidget = attrs.field(init=False)
    _plotter: BackgroundPlotter = attrs.field(init=False)
    _actors: tp.Dict[str, tp.Optional[Actor]] = attrs.field(init=False, factory=dict)

    _currentlyMovingActor: tp.Optional[str] = attrs.field(init=False, default=None)

    _positionsClient: SimulatedToolPositionsClient = attrs.field(init=False)

    def __attrs_post_init__(self):
        self._wdgt = DockWidgetsContainer(uniqueName=self._key)
        self._wdgt.setAffinities([self._key])

        super().__attrs_post_init__()

    def canBeEnabled(self):
        return self.session is not None

    def _finishInitialization(self):
        super()._finishInitialization()

        def createDockWidget(title: str,
                             widget: tp.Optional[QtWidgets.QWidget] = None,
                             layout: tp.Optional[QtWidgets.QLayout] = None):
            cdw = dw.DockWidget(
                uniqueName=self._key + title,
                options=dw.DockWidgetOptions(notClosable=True),
                title=title,
                affinities=[self._key]
            )
            if widget is None:
                widget = QtWidgets.QWidget()
            if layout is not None:
                widget.setLayout(layout)
            cdw.setWidget(widget)
            cdw.__childWidget = widget  # monkey-patch reference to child, since setWidget doesn't seem to claim ownernship
            self._dockWidgets[title] = cdw
            return cdw, widget

        cdw, container = createDockWidget(
            title='Tools tracking status',
        )
        container.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.MinimumExpanding)
        self._trackingStatusWdgt = TrackingStatusWidget(session=self.session,
                                                        wdgt=container)
        self._wdgt.addDockWidget(cdw, dw.DockWidgetLocation.OnLeft)

        cdw, container = createDockWidget(
            title='Simulated tool controls',
            layout=QtWidgets.QVBoxLayout()
        )
        container.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.MinimumExpanding)
        self._wdgt.addDockWidget(cdw, dw.DockWidgetLocation.OnBottom)

        btn = QtWidgets.QPushButton('Clear all positions')
        btn.clicked.connect(lambda checked: self.clearAllPositions())
        container.layout().addWidget(btn)

        btn = QtWidgets.QPushButton('Zero all positions')
        btn.clicked.connect(lambda checked: self.zeroAllPositions())
        container.layout().addWidget(btn)

        btn = QtWidgets.QPushButton('Move tool...')
        btn.clicked.connect(lambda checked: self.selectToolToMove())
        container.layout().addWidget(btn)

        container.layout().addStretch()

        self._plotter = BackgroundPlotter(
            show=False,
            app=QtWidgets.QApplication.instance())
        self._plotter.set_background('#FFFFFF')
        self._plotter.enable_depth_peeling(3)
        if False:
            # (disabled for now, breaks mesh picking)
            self._plotter.add_axes_at_origin(labels_off=True, line_width=4)
        cdw, container = createDockWidget(
            title='Simulated tools view',
            widget=self._plotter.interactor
        )
        self._wdgt.addDockWidget(cdw, dw.DockWidgetLocation.OnRight)

        self._positionsClient = SimulatedToolPositionsClient(
            serverHostname=self.session.tools.positionsServerInfo.hostname,
            serverPubPort=self.session.tools.positionsServerInfo.pubPort,
            serverCmdPort=self.session.tools.positionsServerInfo.cmdPort,
        )
        self._trackingStatusWdgt.session = self.session
        self._positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)
        self._onLatestPositionsChanged()

    def _onLatestPositionsChanged(self):
        if not self._hasInitialized and not self.isInitializing:
            return

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
                        if self._currentlyMovingActor is not None and actorKey == self._currentlyMovingActor:
                            continue  # don't update here since interactive update code will handle this actor position during move
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
                            self._plotter.render()

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
                        self._plotter.render()

                if actorKey in self._actors:
                    if canShow and not self._actors[actorKey].GetVisibility():
                        self._actors[actorKey].VisibilityOn()
                        self._plotter.render()
                    elif not canShow and self._actors[actorKey].GetVisibility():
                        self._actors[actorKey].VisibilityOff()
                        self._plotter.render()

    def clearAllPositions(self):
        raise NotImplementedError  # TODO

    def zeroAllPositions(self):
        for key, tool in self.session.tools.items():
            self._positionsClient.setNewPosition(key=key, transf=np.eye(4))

    async def selectAndMoveTool(self):
        # start by picking mesh to move
        pickedActor = await pickActor(self._plotter,
                                      show=True,
                                      show_message='Left click on mesh to move',
                                      style='wireframe',
                                      left_clicking=True)
        try:
            pickedKey = [actorKey for actorKey, actor in self._actors.items() if actor is pickedActor][0]
        except IndexError as e:
            logger.warning('Unrecognized actor picked. Cancelling select and move')
            return
        if pickedKey.endswith('_tracker'):
            pickedTool = self.session.tools[pickedKey[:-len('_tracker')]]
        elif pickedKey.endswith('_tool'):
            pickedTool = self.session.tools[pickedKey[:-len('_tool')]]
        else:
            raise NotImplementedError
        logger.info(f'Picked actor {pickedKey} ({pickedTool.key}) to move')

        self._currentlyMovingActor = pickedKey

        # move
        def onNewTransf(transf: vtk.vtkTransform):
            prevTransf = pickedActor.GetUserTransform()
            logger.debug('onNewTransf')
            # back out any tool-specific transforms and send updated transf to simulated tool position server
            transf = pv.array_from_vtkmatrix(transf.GetMatrix())
            if pickedKey.endswith('_tool'):
                # transf = trackerToWorldTransf @ toolToTrackerTransf @ toolStlToToolTransf
                newTrackerToWorldTransf = transf @ invertTransform(pickedTool.toolToTrackerTransf @ pickedTool.toolStlToToolTransf)
                logger.debug('Setting new simulated position')
                self._positionsClient.setNewPosition(key=pickedTool.key, transf=newTrackerToWorldTransf)
                logger.debug('Done setting new simulated position')
            else:
                raise NotImplementedError(f'Support for moving {pickedKey} not yet implemented')

        await interactivelyMoveActor(plotter=self._plotter, actor=pickedActor, onNewTransf=onNewTransf)

        # TODO: cleanup here


    def selectToolToMove(self):
        asyncio.create_task(self.selectAndMoveTool())

