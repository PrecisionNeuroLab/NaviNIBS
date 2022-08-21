from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import pyvista as pv
import pyvistaqt as pvqt
import pytransform3d.rotations as ptr
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp
from typing import ClassVar

from .TargetingCoordinator import TargetingCoordinator
from .ViewLayers import ViewLayer, PlotViewLayer
from .ViewLayers.MeshSurfaceLayer import MeshSurfaceLayer
from .ViewLayers.TargetingCrosshairsLayer import TargetingCoilCrosshairsLayer, TargetingTargetCrosshairsLayer
from .ViewLayers.TargetingPointLayer import TargetingCoilPointsLayer, TargetingTargetPointsLayer
from. ViewLayers.TargetingErrorLineLayer import TargetingErrorLineLayer

from RTNaBS.util.Transforms import invertTransform, concatenateTransforms, applyTransform, composeTransform
import RTNaBS.util.GUI.DockWidgets as dw

logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define
class NavigationView:
    _key: str
    _type: ClassVar[str]
    _coordinator: TargetingCoordinator

    _dockKeyPrefix: str = ''

    _dock: dw.DockWidget = attrs.field(init=False)
    _wdgt: QtWidgets.QWidget = attrs.field(init=False)

    _layers: tp.Dict[str, ViewLayer] = attrs.field(init=False, factory=dict)
    _layerLibrary: tp.Dict[str, tp.Callable[..., ViewLayer]] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        self._dock = dw.DockWidget(uniqueName=self._dockKeyPrefix + self._key, affinities=[self._dockKeyPrefix])

        self._wdgt = QtWidgets.QWidget()
        self._dock.setWidget(self._wdgt)

        self._wdgt.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.MinimumExpanding)

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
    _alignCameraTo: tp.Optional[str] = None
    """
    None to use default camera perspective; 'target' to align camera space to target space, etc.
    Can also specify 'target-Y' to look at from along typical coil handle axis, or 'TargetX' to look at from other direction
    """

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

        self._layerLibrary = {}
        for cls in (TargetingTargetCrosshairsLayer,
                    TargetingCoilCrosshairsLayer,
                    TargetingTargetPointsLayer,
                    TargetingCoilPointsLayer,
                    TargetingErrorLineLayer,
                    MeshSurfaceLayer):
            self._layerLibrary[cls.type] = cls

        self._coordinator.sigCurrentTargetChanged.connect(self._onCurrentTargetChanged)
        self._coordinator.sigCurrentCoilPositionChanged.connect(self._onCurrentCoilPositionChanged)

        self._redraw('all')

    def _onCurrentTargetChanged(self):
        if self._alignCameraTo.startswith('target'):
            self._alignCamera()

    def _onCurrentCoilPositionChanged(self):
        if self._alignCameraTo.startswith('coil'):
            self._alignCamera()

    @staticmethod
    def _getExtraRotationForToAlignCamera(rotSuffix: str) -> np.ndarray:
        # TODO: double check angles and signs
        match rotSuffix:
            case '':
                extraRot = np.eye(3)
            case 'X':
                extraRot = ptr.active_matrix_from_intrinsic_euler_yzy([np.pi / 2, np.pi/2, 0])
            case '-X':
                extraRot = ptr.active_matrix_from_intrinsic_euler_yzy([-np.pi / 2, -np.pi/2, 0])
            case 'Y':
                extraRot = ptr.active_matrix_from_extrinsic_euler_xyz([-np.pi / 2, 0, np.pi])
            case '-Y':
                extraRot = ptr.active_matrix_from_extrinsic_euler_xyz([np.pi / 2, 0, 0])
            case 'Z':
                extraRot = np.eye(3)
            case '-Z':
                extraRot = ptr.active_matrix_from_extrinsic_euler_xyz([np.pi, 0, 0])
            case _:
                raise NotImplementedError
        return extraRot

    def _alignCamera(self):
        class NoValidCameraPoseAvailable(Exception):
            pass

        try:
            cameraPts = np.asarray([[0, 0, 0], [0, 0, 100], [0, 1, 0]])  # focal point, position, and up respectively
            if self._alignCameraTo is None:
                pass

            elif self._alignCameraTo.startswith('target'):
                extraRot = self._getExtraRotationForToAlignCamera(self._alignCameraTo[len('target'):])
                extraTransf = composeTransform(extraRot)

                if self._plotInSpace == 'MRI':
                    if self._coordinator.currentTarget is not None and self._coordinator.currentTarget.coilToMRITransf is not None:
                        cameraPts = applyTransform(self._coordinator.currentTarget.coilToMRITransf @ extraTransf, cameraPts)
                    else:
                        raise NoValidCameraPoseAvailable()
                else:
                    raise NotImplementedError()

            elif self._alignCameraTo.startswith('coil'):
                extraRot = self._getExtraRotationForToAlignCamera(self._alignCameraTo[len('coil'):])
                extraTransf = composeTransform(extraRot)

                if self._plotInSpace == 'MRI':
                    if self._coordinator.currentCoilToMRITransform is not None:
                        cameraPts = applyTransform(self._coordinator.currentCoilToMRITransform @ extraTransf, cameraPts)
                    else:
                        raise NoValidCameraPoseAvailable()
                else:
                    raise NotImplementedError()

            else:
                 raise NotImplementedError()

        except NoValidCameraPoseAvailable:
            logger.debug('No camera pose available')
            return  # TODO: change display to indicate view is out-of-date / invalid

        self._plotter.camera.focal_point = cameraPts[0, :]
        self._plotter.camera.position = cameraPts[1, :]
        self._plotter.camera.up = cameraPts[2, :] - cameraPts[1, :]
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
                layer._redraw()

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
        self.addLayer(type='TargetingTargetPoints', key='TargetPoints')
        self.addLayer(type='TargetingCoilPoints', key='CoilPoints')
        self.addLayer(type='TargetingErrorLine', key='TargetError', targetDepth='target', coilDepth='target')
        self.addLayer(type='TargetingErrorLine', key='CoilError', targetDepth='coil', coilDepth='coil')
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
