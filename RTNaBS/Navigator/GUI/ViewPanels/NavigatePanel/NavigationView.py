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
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms, applyTransform
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

    _layers: tp.Dict[str, ViewLayer] = attrs.field(init=False, factory=dict)
    _layerLibrary: tp.Dict[str, tp.Callable[..., ViewLayer]] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        self._dock = Dock(name=self._key,
                          autoOrientation=False,
                          closable=True)

        self._wdgt = QtWidgets.QWidget()
        self._dock.addWidget(self._wdgt)

        # TODO: add context menu to be able to change view type with right click on title bar (?)

    def addLayer(self, key: str, type: str, **kwargs):
        raise NotImplementedError()  # should be implemented by subclass

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        logger.debug('redraw {}'.format(which))

        if which is None:
            which = 'all'

        if not isinstance(which, str):
            for subWhich in which:
                self._redraw(which=subWhich)
            return

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
    _plotInSpace: str = 'MRI'
    _alignCameraTo: tp.Optional[str] = None  # None to use default camera perspective; 'target' to align camera space to target space, etc.

    _layers: tp.Dict[str, PlotViewLayer] = attrs.field(init=False, factory=dict)
    _layerLibrary: tp.Dict[str, tp.Callable[..., PlotViewLayer]] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        self._plotter = pvqt.BackgroundPlotter(
            show=False,
            app=QtWidgets.QApplication.instance()
        )
        self._plotter.set_background('#FFFFFF')
        self._plotter.enable_depth_peeling(10)
        self._wdgt.layout().addWidget(self._plotter.interactor)

        self._layerLibrary = dict(
            TargetingTargetCrosshairs=TargetingTargetCrosshairsLayer,
            TargetingCoilCrosshairs=TargetingCoilCrosshairsLayer,
            MeshSurface=MeshSurfaceLayer
        )

        self._coordinator.sigCurrentTargetChanged.connect(self._onCurrentTargetChanged)
        self._coordinator.sigCurrentCoilPositionChanged.connect(self._onCurrentCoilPositionChanged)

        self._redraw('all')

    def _onCurrentTargetChanged(self):
        if self._alignCameraTo == 'target':
            self._alignCamera()

    def _onCurrentCoilPositionChanged(self):
        if self._alignCameraTo == 'coil':
            self._alignCamera()

    def _alignCamera(self):
        class NoValidCameraPoseAvailable(Exception):
            pass

        try:
            cameraPts = np.asarray([[0, 0, 0], [0, 0, 100], [0, 1, 0]])  # focal point, position, and up respectively
            match self._alignCameraTo:
                case None:
                    pass

                case 'target':
                    if self._plotInSpace == 'MRI':
                        if self._coordinator.currentTarget is not None and self._coordinator.currentTarget.coilToMRITransf is not None:
                            cameraPts = applyTransform(self._coordinator.currentTarget.coilToMRITransf, cameraPts)
                        else:
                            raise NoValidCameraPoseAvailable()
                    else:
                        raise NotImplementedError()

                case 'coil':
                    if self._plotInSpace == 'MRI':
                        if self._coordinator.currentCoilToMRITransform is not None:
                            cameraPts = applyTransform(self._coordinator.currentCoilToMRITransform, cameraPts)
                        else:
                            raise NoValidCameraPoseAvailable()
                    else:
                        raise NotImplementedError()

                case _:
                    raise NotImplementedError()

        except NoValidCameraPoseAvailable:
            logger.debug('No camera pose available')
            return  # TODO: change display to indicate view is out-of-date / invalid

        self._plotter.camera.focal_point = cameraPts[0, :]
        self._plotter.camera.position = cameraPts[1, :]
        self._plotter.camera.up = cameraPts[2, :]
        self._plotter.camera.reset_clipping_range()

    def addLayer(self, type: str, key: str, **kwargs):
        cls = self._layerLibrary[type]
        assert key not in self._layers
        self._layers[key] = cls(key=key, **kwargs,
                                coordinator=self._coordinator,
                                plotter=self._plotter,
                                plotInSpace=self._plotInSpace)

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        super()._redraw(which=which)

        if not isinstance(which, str):
            # assume parent call above triggered appropriate redraws
            return

        if which == 'all':
            which = ['layers', 'camera']
            self._redraw(which=which)
            return

        if which == 'camera':
            self._alignCamera()

        elif which == 'layers':
            for layer in self._layers.values():
                layer._redraw(which=which)

        else:
            raise NotImplementedError


@attrs.define
class ViewLayer:
    _key: str
    _type: ClassVar[str]
    _coordinator: TargetingCoordinator

    def __attrs_post_init__(self):
        pass


@attrs.define
class PlotViewLayer(ViewLayer):
    _plotter: pvqt.QtInteractor  # note this this one plotter may be shared between multiple ViewLayers
    _plotInSpace: str = 'MRI'

    _actors: tp.Dict[str, tp.Optional[Actor]] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self._redraw('all')

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):

        #logger.debug('redraw {}'.format(which))

        if which is None:
            which = 'all'

        if not isinstance(which, str):
            for subWhich in which:
                self._redraw(which=subWhich)
            return

    def _getActorKey(self, subKey: str) -> str:
        return self._key + '_' + subKey  # make actor keys unique across multiple layers in the same plotter


@attrs.define
class MeshSurfaceLayer(PlotViewLayer):
    _type: ClassVar[str] = 'MeshSurface'
    _color: str = '#d9a5b2'
    _opacity: float = 0.7
    _surfKey: str = 'gmSurf'

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        super()._redraw(which=which)

        if not isinstance(which, str):
            # assume parent call above triggered appropriate redraws
            return

        if which == 'all':
            which = ['initSurf']
            self._redraw(which=which)
            return

        if which == 'initSurf':
            mesh = getattr(self._coordinator.session.headModel, self._surfKey)

            actorKey = self._getActorKey('surf')

            self._actors[actorKey] = self._plotter.add_mesh(mesh=mesh,
                                                            color=self._color,
                                                            opacity=self._opacity,
                                                            specular=0.5,
                                                            diffuse=0.5,
                                                            ambient=0.5,
                                                            smooth_shading=True,
                                                            split_sharp_edges=True,
                                                            name=actorKey)

            self._redraw('updatePosition')

        elif which == 'updatePosition':
            if self._plotInSpace == 'MRI':
                if False:
                    actorKey = self._getActorKey('surf')
                    transf = np.eye(4)
                    setActorUserTransform(self._actors[actorKey], transf)
                else:
                    pass  # assume since plotInSpace is always MRI (for now) that we don't need to update anything
        else:
            raise NotImplementedError


@attrs.define
class TargetingCrosshairsLayer(PlotViewLayer):
    _type: ClassVar[str]

    _targetOrCoil: str = 'target'

    _color: str = '#0000ff'
    _opacity: float = 0.5
    _radius: float = 10.
    _offsetRadius: float = 5.
    _lineWidth: float = 4.

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.sigCurrentTargetChanged.connect(lambda: self._redraw(which='initCrosshair'))
        self._coordinator.sigCurrentCoilPositionChanged.connect(lambda: self._redraw(which=['updatePositions', 'crosshairVisibility']))

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        super()._redraw(which=which)

        if not isinstance(which, str):
            # assume parent call above triggered appropriate redraws
            return

        if which == 'all':
            which = ['initCrosshair']
            self._redraw(which=which)
            return

        elif which == 'clearCrosshair':
            actorKey = self._getActorKey('crosshair')
            if actorKey in self._actors:
                actor = self._actors.pop(actorKey)
                self._plotter.remove_actor(actor)

        elif which == 'crosshairVisibility':
            actorKey = self._getActorKey('crosshair')
            if actorKey in self._actors:
                actor = self._actors[actorKey]

                doShow = self._canShow()

                if actor.GetVisibility() != doShow:
                    actor.SetVisibility(doShow)

        elif which == 'initCrosshair':
            actorKey = self._getActorKey('crosshair')

            target = self._coordinator.currentTarget
            if target is None:
                if self._targetOrCoil == 'target':
                    # no active target, cannot plot
                    self._redraw(which='clearCrosshair')
                    return
                elif self._targetOrCoil == 'coil':
                    # use an estimated zOffset until we have a target
                    zOffset = -10.
                else:
                    raise NotImplementedError()
            else:
                # distance from bottom of coil to target (presumably in brain)
                zOffset = -1 * np.linalg.norm(target.targetCoord - target.entryCoord) + target.depthOffset

            lines = self._getCrosshairLineSegments(radius=self._radius)

            offsetLines = self._getCrosshairLineSegments(radius=self._offsetRadius, zOffset=zOffset)

            depthLine = pv.utilities.lines_from_points(np.asarray([[0, 0, 0], [0, 0, zOffset]]))

            self._actors[actorKey] = addLineSegments(self._plotter,
                                                     concatenateLineSegments([lines, offsetLines, depthLine]),
                                                     name=actorKey,
                                                     color=self._color,
                                                     width=self._lineWidth,
                                                     opacity=self._opacity)

            self._redraw(which=['updatePositions', 'crosshairVisibility'])

        elif which == 'updatePositions':
            if not self._canShow():
                return

            actorKey = self._getActorKey('crosshair')
            if actorKey not in self._actors:
                self._redraw(which='initCrosshair')
                return

            actor = self._actors[actorKey]

            if self._plotInSpace != 'MRI':
                raise NotImplementedError()  # TODO: add necessary transforms for plotting in other spaces below

            if self._targetOrCoil == 'target':
                currentTargetToMRITransform = self._coordinator.currentTarget.coilToMRITransf
                setActorUserTransform(actor, currentTargetToMRITransform)

            elif self._targetOrCoil == 'coil':
                currentCoilToMRITransform = self._coordinator.currentCoilToMRITransform
                setActorUserTransform(actor, currentCoilToMRITransform)

            else:
                raise NotImplementedError()

        else:
            raise NotImplementedError('Unexpected redraw which: {}'.format(which))

    def _canShow(self) -> bool:
        canShow = True
        hasTarget = self._coordinator.currentTarget
        hasCoil = self._coordinator.currentCoilToMRITransform is not None

        if self._plotInSpace != 'MRI':
            raise NotImplementedError()  # TODO: check that we have necessary info to convert to other coordinate spaces

        if self._targetOrCoil == 'target':
            if not hasTarget:
                canShow = False
        elif self._targetOrCoil == 'coil':
            if not hasCoil:
                canShow = False
        else:
            raise NotImplementedError()
        return canShow

    @classmethod
    def _getCircleLines(cls, radius: float, numPts: int = 300) -> pv.PolyData:
        points = np.zeros((numPts, 3))
        theta = np.linspace(0, 2 * np.pi, numPts)
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
        # TODO: check signs and directions
        topNotch = pv.utilities.lines_from_points(np.asarray([[0, radius, 0], [0, radius * (1 - relNotchLength), 0]]))
        botNotch = pv.utilities.lines_from_points(
            np.asarray([[0, -radius * (1 + relNotchLength), 0], [0, -radius * (1 - relNotchLength), 0]]))
        leftNotch = pv.utilities.lines_from_points(
            np.asarray([[-radius, 0, 0], [-radius * (1 - relNotchLength), 0, 0]]))
        rightNotch = pv.utilities.lines_from_points(np.asarray([[radius, 0, 0], [radius * (1 - relNotchLength), 0, 0]]))

        lines = concatenateLineSegments([circle, botNotch, topNotch, leftNotch, rightNotch])

        lines.points += np.asarray([[0, 0, zOffset]])

        return lines


@attrs.define
class TargetingTargetCrosshairsLayer(TargetingCrosshairsLayer):
    _type: ClassVar[str] = 'TargetingTargetCrosshairs'
    _targetOrCoil: str = 'target'

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class TargetingCoilCrosshairsLayer(TargetingCrosshairsLayer):
    _type: ClassVar[str] = 'TargetingCoilCrosshairs'
    _targetOrCoil: str = 'coil'

    _color: str = '#00ff00'
    _radius: float = 10.
    _offsetRadius: float = 5.
    _lineWidth: float = 8.

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class TargetingCrosshairsView(SinglePlotterNavigationView):
    _type: ClassVar[str] = 'TargetingCrosshairs'
    _alignCameraTo: str = 'target'

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self.addLayer(type='TargetingTargetCrosshairs', key='Target')
        self.addLayer(type='TargetingCoilCrosshairs', key='Coil')
        self.addLayer(type='MeshSurface', key='Brain', surfKey='gmSurf')


@attrs.define
class TargetingOverMeshSurfacesView(SinglePlotterNavigationView):
    _type: ClassVar[str] = 'TargetingOverMeshSurfaces'
    _viewRelativeTo: str = 'perspective'

    # TODO


@attrs.define
class TargetingSliceView(SinglePlotterNavigationView):
    _type: ClassVar[str] = 'TargetingSlice'
    _viewRelativeTo: str = 'coil'
    _normal: tp.Union[str, np.ndarray] = 'x'  # axis in space defined by self.viewRelativeTo

    _backgroundColor: str = '#000000'

    # TODO
