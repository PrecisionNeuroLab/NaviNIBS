from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import pyvista as pv
import pyvistaqt as pvqt
from pyqtgraph.dockarea import DockArea, Dock
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp
from typing import ClassVar

from .TargetingCoordinator import TargetingCoordinator
from .ViewLayers import ViewLayer, PlotViewLayer
from .ViewLayers.MeshSurfaceLayer import MeshSurfaceLayer
from .ViewLayers.TargetingCrosshairsLayer import TargetingCoilCrosshairsLayer, TargetingTargetCrosshairsLayer
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms, applyTransform


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