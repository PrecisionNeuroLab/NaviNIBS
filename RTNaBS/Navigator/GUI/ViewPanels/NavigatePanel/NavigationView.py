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
from pyqtgraph.dockarea import DockArea, Dock
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp
from typing import ClassVar

from .TargetingCoordinator import TargetingCoordinator
from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Devices.IGTLinkToolPositionsServer import IGTLinkToolPositionsServer
from RTNaBS.Navigator.Model.Session import Session, Tool, CoilTool, SubjectTracker, Target
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget


logger = logging.getLogger(__name__)


Transform = np.ndarray



@attrs.define
class NavigationView:
    _key: str
    _type: ClassVar[str]
    _coordinator: TargetingCoordinator

    _dock: Dock = attrs.field(init=False)
    _wdgt: QtWidgets.QWidget = attrs.field(init=False)

    def __attrs_post_init__(self):
        self._dock = Dock(name=self._key,
                          autoOrientation=False,
                          closable=True)

        self._wdgt = QtWidgets.QWidget()
        self._dock.addWidget(self._wdgt)

        # TODO: add context menu to be able to change view type with right click on title bar (?)

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        pass

    @property
    def key(self):
        return self._key

    @property
    def type(self):
        return self._type

    @property
    def dock(self):
        return self._dock

    @property
    def wdgt(self):
        return self._wdgt


@attrs.define
class SinglePlotterNavigationView(NavigationView):
    _plotter: pvqt.QtInteractor = attrs.field(init=False)
    _actors: tp.Dict[str, tp.Optional[Actor]] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        self._plotter = pvqt.BackgroundPlotter(
            show=False,
            app=QtWidgets.QApplication.instance()
        )
        self._plotter.set_background('#FFFFFF')
        self._wdgt.layout().addWidget(self._plotter.interactor)

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        super()._redraw(which=which)


@attrs.define
class TargetingCrosshairsView(SinglePlotterNavigationView):
    _type: ClassVar[str] = 'TargetingCrosshairs'
    _viewRelativeTo: str = 'target'  # 'target' or 'coil'; to which to align camera view

    _targetColor: str = '#0000FF'
    _targetOpacity: float = 0.5
    _targetRadius: float = 5.
    _targetOffsetRadius: float = 10.
    _targetLineWidth: float = 2.

    _coilColor: str = '#00FF00F'
    _coilOpacity: float = 0.5
    _coilRadius: float = 10.
    _coilOffsetRadius: float = 5.
    _coilLineWidth: float = 4.

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.sigCurrentTargetChanged.connect(lambda: self._redraw(which='initTarget'))
        self._coordinator.sigCurrentCoilPositionChanged.connect(lambda: self._redraw(which='updatePositions'))

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        super()._redraw(which=which)

        logger.debug('redraw {}'.format(which))

        if which is None:
            which = 'all'

        if not isinstance(which, str):
            for subWhich in which:
                self._redraw(which=subWhich)
            return

        if which == 'all':
            which = ['initCamera', 'initTarget', 'initCoil']
            self._redraw(which=which)
            return

        elif which == 'initCamera':
            self._plotter.camera.focal_point = np.asarray([0, 0, 0])
            self._plotter.camera.position = np.asarray([0, 0, 2000])
            self._plotter.camera.clipping_range = [10, 3000]

        elif which == 'initTarget':

            actorKey = 'targetCrosshair'

            target = self._coordinator.currentTarget
            if target is None:
                return

            targetZOffset = np.linalg.norm(target.targetCoord - target.entryCoord) + target.depthOffset

            targetLines = self._getCrosshairLineSegments(radius=self._targetRadius,
                                                         zOffset=0)

            targetOffsetLines = self._getCrosshairLineSegments(radius=self._targetOffsetRadius,
                                                               zOffset=targetZOffset)

            targetDepthLine = pv.utilities.lines_from_points(np.asarray([[0, 0, 0], [0, 0, targetZOffset]]))

            self._actors[actorKey] = addLineSegments(self._plotter,
                                                     concatenateLineSegments([targetLines, targetDepthLine, targetOffsetLines]),
                                                     name=actorKey,
                                                     color=self._targetColor,
                                                     width=self._targetLineWidth,
                                                     opacity=self._targetOpacity)

            self._redraw(which='initCoil')  # coil z offset is dependent on target, so must also be updated
            self._redraw(which='updatePositions')

        elif which == 'initCoil':

            target = self._coordinator.currentTarget
            if target is None:
                return

            targetZOffset = np.linalg.norm(target.targetCoord - target.entryCoord) + target.depthOffset

            actorKey = 'coilCrosshair'
            coilLines = self._getCrosshairLineSegments(radius=self._coilRadius,
                                                       zOffset=0)

            coilOffsetLines = self._getCrosshairLineSegments(radius=self._coilOffsetRadius,
                                                               zOffset=-targetZOffset)

            coilDepthLine = pv.utilities.lines_from_points(np.asarray([[0, 0, 0], [0, 0, -targetZOffset]]))

            self._actors[actorKey] = addLineSegments(self._plotter,
                                                     concatenateLineSegments(
                                                         [coilLines, coilOffsetLines, coilDepthLine]),
                                                     name=actorKey,
                                                     color=self._coilColor,
                                                     width=self._coilLineWidth,
                                                     opacity=self._coilOpacity)

            self._redraw(which='updatePositions')

        elif which == 'updatePositions':

            if self._coordinator.currentTarget is None:
                return

            if 'targetCrosshair' not in self._actors:
                self._redraw(which='initTarget')
                return

            if 'coilCrosshair' not in self._actors:
                self._redraw(which='initCoil')
                return

            currentCoilToMRITransform = self._coordinator.currentCoilToMRITransform

            if self._viewRelativeTo == 'coil':
                viewNotRelativeTo = 'target'
            elif self._viewRelativeTo == 'target':
                viewNotRelativeTo = 'coil'
            else:
                raise NotImplementedError()

            if currentCoilToMRITransform is None:
                # no valid position available
                self._actors[viewNotRelativeTo + 'Crosshair'].VisibilityOff()
                return

            for coilOrTarget in ('coil', 'target'):
                self._actors[coilOrTarget + 'Crosshair'].VisibilityOn()

            if self._viewRelativeTo == 'coil':
                setActorUserTransform(self._actors['coilCrosshair'], np.eye(4))
                setActorUserTransform(self._actors['targetCrosshair'],
                                      invertTransform(currentCoilToMRITransform) @ self._coordinator.currentTarget.coilToMRITransf)
            elif self._viewRelativeTo == 'target':
                setActorUserTransform(self._actors['targetCrosshair'], np.eye(4))
                setActorUserTransform(self._actors['coilCrosshair'],
                                      invertTransform(self._coordinator.currentTarget.coilToMRITransf) @ currentCoilToMRITransform)
            else:
                raise NotImplementedError()

            self._plotter.show()
            self._plotter.render()

        else:
            raise NotImplementedError('Unexpected which: {}'.format(which))

    @classmethod
    def _getCircleLines(cls, radius: float, numPts: int = 300) -> pv.PolyData:
        points = np.zeros((numPts, 3))
        theta = np.linspace(0, 2*np.pi, numPts)
        points[:, 0] = radius * np.cos(theta)
        points[:, 1] = radius * np.sin(theta)
        return pv.utilities.lines_from_points(points)

    @classmethod
    def _getCrosshairLineSegments(cls,
                                  radius: float,
                                  numPtsInCircle: int = 300,
                                  zOffset: float = 0.,
                                  ) -> pv.PolyData:
        circle = cls._getCircleLines(radius=radius, numPts=numPtsInCircle)
        relNotchLength = 0.2
        topNotch = pv.utilities.lines_from_points(np.asarray([[0, radius, 0], [0, radius*(1-relNotchLength), 0]]))
        botNotch = pv.utilities.lines_from_points(np.asarray([[0, radius*(1+relNotchLength), 0], [0, radius * (1-relNotchLength), 0]]))
        leftNotch = pv.utilities.lines_from_points(np.asarray([[0, radius, 0], [0, radius * (1-relNotchLength), 0]]))
        rightNotch = pv.utilities.lines_from_points(np.asarray([[0, radius, 0], [0, radius * (1-relNotchLength), 0]]))

        lines = concatenateLineSegments([circle, topNotch, botNotch, leftNotch, rightNotch])
        lines.points += np.asarray([[0, 0, zOffset]])

        return lines