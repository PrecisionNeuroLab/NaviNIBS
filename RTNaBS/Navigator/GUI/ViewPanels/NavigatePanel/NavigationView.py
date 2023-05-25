from __future__ import annotations

import attrs
import logging
import numpy as np
import pytransform3d.rotations as ptr
from qtpy import QtWidgets, QtCore
import typing as tp
from typing import ClassVar

from RTNaBS.Navigator.TargetingCoordinator import TargetingCoordinator
from .ViewLayers import ViewLayer, PlotViewLayer
from .ViewLayers.MeshSurfaceLayer import MeshSurfaceLayer
from .ViewLayers.OrientationsLayers import SampleOrientationsLayer, TargetOrientationsLayer
from .ViewLayers.SampleMetadataOrientationsLayer import SampleMetadataOrientationsLayer, SampleMetadataInterpolatedSurfaceLayer
from .ViewLayers.TargetingCrosshairsLayer import TargetingCoilCrosshairsLayer, TargetingTargetCrosshairsLayer
from .ViewLayers.TargetingAngleErrorLayer import TargetingAngleErrorLayer
from .ViewLayers.TargetingPointLayer import TargetingCoilPointsLayer, TargetingTargetPointsLayer
from. ViewLayers.TargetingErrorLineLayer import TargetingErrorLineLayer

from RTNaBS.util.Transforms import applyTransform, composeTransform
import RTNaBS.util.GUI.DockWidgets as dw
from RTNaBS.util.pyvista.plotting import PrimaryLayeredPlotter

logger = logging.getLogger(__name__)
#plotLogger.setLevel(logging.DEBUG)


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

        self._wdgt.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

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
    _plotter: PrimaryLayeredPlotter = attrs.field(init=False)
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

        self._plotter = PrimaryLayeredPlotter(
            show=False,
            app=QtWidgets.QApplication.instance()
        )
        self._wdgt.layout().addWidget(self._plotter.interactor)

        self._layerLibrary = {}
        for cls in (
                    MeshSurfaceLayer,
                    SampleOrientationsLayer,
                    SampleMetadataOrientationsLayer,
                    TargetOrientationsLayer,
                    TargetingTargetCrosshairsLayer,
                    TargetingCoilCrosshairsLayer,
                    TargetingTargetPointsLayer,
                    TargetingCoilPointsLayer,
                    TargetingErrorLineLayer,
                    TargetingAngleErrorLayer,
                    ):
            self._layerLibrary[cls.type] = cls

        self._coordinator.sigCurrentTargetChanged.connect(self._onCurrentTargetChanged)
        self._coordinator.sigCurrentCoilPositionChanged.connect(self._onCurrentCoilPositionChanged)

        self._redraw('all')

    @property
    def plotter(self):
        return self._plotter

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
            case '+X':
                extraRot = ptr.active_matrix_from_intrinsic_euler_yzy([np.pi / 2, np.pi/2, 0])
            case '-X':
                extraRot = ptr.active_matrix_from_intrinsic_euler_yzy([-np.pi / 2, -np.pi/2, 0])
            case '+Y':
                extraRot = ptr.active_matrix_from_extrinsic_euler_xyz([-np.pi / 2, 0, np.pi])
            case '-Y':
                extraRot = ptr.active_matrix_from_extrinsic_euler_xyz([np.pi / 2, 0, 0])
            case '+Z':
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
            cameraPts = np.asarray([[0, 0, 0], [0, 0, 200], [0, 1, 0]])  # focal point, position, and up respectively

            if self._alignCameraTo is None:
                pass

            elif self._alignCameraTo.startswith('target'):
                extraRot = self._getExtraRotationForToAlignCamera(self._alignCameraTo[len('target'):])
                extraTransf = composeTransform(extraRot)

                if self._alignCameraTo[-1] in 'XY':
                    # add negative z offset to reduce empty space above coil in camera view
                    extraTransf[2, 3] = -20

                    # reduce distance from camera to target to zoom in tighter
                    cameraPts[1, 2] = 100

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

                if self._alignCameraTo[-1] in 'XY':
                    # add negative z offset to reduce empty space above coil in camera view
                    extraTransf[2, 3] = -20

                    # reduce distance from camera to coil to zoom in tighter
                    cameraPts[1, 2] = 100

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
            #logger.debug('No camera pose available')
            self._plotter.reset_camera_clipping_range()
            self._plotter.render()
            return  # TODO: change display to indicate view is out-of-date / invalid

        self._plotter.camera.focal_point = cameraPts[0, :]
        self._plotter.camera.position = cameraPts[1, :]
        self._plotter.camera.up = cameraPts[2, :] - cameraPts[1, :]
        self._plotter.reset_camera_clipping_range()
        self._plotter.render()

    def addLayer(self, type: str, key: str, layeredPlotterKey: tp.Optional[str] = None,
                 layerPlotterLayer: tp.Optional[int] = None, **kwargs):
        cls = self._layerLibrary[type]
        assert key not in self._layers
        if layeredPlotterKey is None:
            plotter = self._plotter
        else:
            if layeredPlotterKey in self._plotter.secondaryPlotters:
                plotter = self._plotter.secondaryPlotters[layeredPlotterKey]
                if layerPlotterLayer is not None:
                    assert plotter.rendererLayer == layerPlotterLayer
            else:
                plotter = self._plotter.addLayeredPlotter(key=layeredPlotterKey, layer=layerPlotterLayer)
                logger.debug(f'Added renderer layer {layeredPlotterKey} #{plotter.rendererLayer}')

        self._layers[key] = cls(key=key, **kwargs,
                                coordinator=self._coordinator,
                                plotter=plotter,
                                plotInSpace=self._plotInSpace)

        plotter.render()

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
    _doParallelProjection: bool = False
    _doShowSkinSurf: bool = False
    _doShowHandleAngleError: bool = False
    _doShowTargetTangentialAngleError: bool = False
    _doShowScalpTangentialAngleError: bool = False

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        if self._doShowSkinSurf:
            self.addLayer(type='MeshSurface', key='Skin', surfKey='skinSimpleSurf',
                          color='#c9c5c2',
                          layeredPlotterKey='SkinMesh',
                          layerPlotterLayer=0)

            #self._plotter.secondaryPlotters['SkinMesh'].enable_depth_peeling(2)

        if True and self._alignCameraTo == 'target':
            self.addLayer(type='SampleMetadataInterpolatedSurface',
                          key='Brain',
                          surfKey='gmSurf',
                          scalarsOpacityKey=None,
                          metadataKey='Vpp_dBmV',
                          colorbarLabel='Vpp (dBmV)',
                          relevantSampleDepth='intersection')
        else:
            self.addLayer(type='MeshSurface', key='Brain', surfKey='gmSurf')
        self._plotter.setLayer(1 if self._doShowSkinSurf else 0)

        if False:
            self.addLayer(type='SampleMetadataInterpolatedSurface',
                          key='ScalpVpps',
                          surfKey='csfSurf',
                          opacity=0.5,
                          metadataKey='Vpp_dBmV',
                          colorbarLabel='Vpp (dBmV)',
                          relevantSampleDepth='intersection')

        if self._doParallelProjection:
            self._plotter.camera.enable_parallel_projection()

        #self._plotter.enable_depth_peeling(2)

        if True:
            self.addLayer(type='SampleOrientations', key='Samples', layeredPlotterKey='Orientations')
        elif False:
            self.addLayer(type='SampleMetadataOrientations',
                          key='SampleVpps',
                          layeredPlotterKey='Orientations',
                          metadataKey='Vpp',
                          metadataScaleFactor=1.e6,
                          colorbarLabel='Vpp (uV)',
                          lineWidth=6.)
        else:
            self.addLayer(type='SampleMetadataOrientations',
                          key='SampleVpps_dBmV',
                          layeredPlotterKey='Orientations',
                          metadataKey='Vpp_dBmV',
                          colorbarLabel='Vpp (dBmV)',
                          lineWidth=6.)

        self.addLayer(type='TargetOrientations', key='Targets', layeredPlotterKey='Orientations')
        self.addLayer(type='TargetingTargetPoints', key='TargetPoints', layeredPlotterKey='Orientations')
        self.addLayer(type='TargetingCoilPoints', key='CoilPoints', layeredPlotterKey='Orientations')

        self.addLayer(type='TargetingTargetCrosshairs', key='Target', layeredPlotterKey='Crosshairs')
        self.addLayer(type='TargetingCoilCrosshairs', key='Coil', layeredPlotterKey='Crosshairs')

        self.addLayer(type='TargetingErrorLine', key='TargetError', targetDepth='target', coilDepth='target', layeredPlotterKey='TargetingError')
        self.addLayer(type='TargetingErrorLine', key='CoilError', targetDepth='coil', coilDepth='coil', layeredPlotterKey='TargetingError')

        if self._doShowHandleAngleError:
            self.addLayer(type='TargetingAngleError', key='HandleAngleError',
                          angleMetric='Horiz angle error',
                          layeredPlotterKey='TargetingError')

        if self._doShowTargetTangentialAngleError:
            plotOn = self._alignCameraTo[:-2]
            if plotOn == 'coil':
                multiplier = 4.
            elif plotOn == 'target':
                multiplier = -4.
            else:
                raise NotImplementedError
            if self._alignCameraTo.endswith('X'):
                xyDims = (1, 2)
                angleMetric = f'Depth {plotOn} Y angle error'
            elif self._alignCameraTo.endswith('Y'):
                xyDims = (0, 2)
                plotOn = self._alignCameraTo[:-2]
                angleMetric = f'Depth {plotOn} X angle error'
            else:
                raise NotImplementedError
            self.addLayer(type='TargetingAngleError', key='TargetTangentialAngleError',
                          plotOnTargetOrCoil=plotOn,
                          angleMetric=angleMetric,
                          angleOffset=np.pi/2,
                          multiplier=multiplier,
                          radius=15,
                          xyDims=xyDims,
                          layeredPlotterKey='TargetingError')

        if self._doShowScalpTangentialAngleError:
            plotOn = self._alignCameraTo[:-2]
            if plotOn == 'coil':
                multiplier = 4.
            elif plotOn == 'target':
                multiplier = -4.
            else:
                raise NotImplementedError
            if self._alignCameraTo.endswith('X'):
                xyDims = (1, 2)
                angleMetric = f'Normal {plotOn} Y angle error'
            elif self._alignCameraTo.endswith('Y'):
                xyDims = (0, 2)
                plotOn = self._alignCameraTo[:-2]
                angleMetric = f'Normal {plotOn} X angle error'
            else:
                raise NotImplementedError
            self.addLayer(type='TargetingAngleError', key='ScalpTangentialAngleError',
                          color='#9fc58b',
                          plotOnTargetOrCoil=plotOn,
                          angleMetric=angleMetric,
                          angleOffset=np.pi/2,
                          multiplier=multiplier,
                          radius=12,
                          xyDims=xyDims,
                          layeredPlotterKey='TargetingError')


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
