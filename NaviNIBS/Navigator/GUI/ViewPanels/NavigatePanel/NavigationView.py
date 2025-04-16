from __future__ import annotations

import asyncio
import attrs
import logging
import numpy as np
import pyvista as pv
import pytransform3d.rotations as ptr
from qtpy import QtWidgets, QtCore
import typing as tp
from typing import ClassVar

from NaviNIBS.Navigator.TargetingCoordinator import TargetingCoordinator
from .ViewLayers import ViewLayer, PlotViewLayer, LegendEntry
from .ViewLayers.MeshSurfaceLayer import HeadMeshSurfaceLayer, ToolMeshSurfaceLayer
from .ViewLayers.OrientationsLayers import SampleOrientationsLayer, TargetOrientationsLayer
from .ViewLayers.SampleMetadataOrientationsLayer import SampleMetadataOrientationsLayer, SampleMetadataInterpolatedSurfaceLayer
from .ViewLayers.TargetingCrosshairsLayer import TargetingCoilCrosshairsLayer, TargetingTargetCrosshairsLayer
from .ViewLayers.TargetingAngleErrorLayer import TargetingAngleErrorLayer
from .ViewLayers.TargetingPointLayer import TargetingCoilPointsLayer, TargetingTargetPointsLayer
from. ViewLayers.TargetingErrorLineLayer import TargetingErrorLineLayer

from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.Transforms import applyTransform, composeTransform, concatenateTransforms
from NaviNIBS.util.GUI.Dock import Dock
from NaviNIBS.util.GUI.QueuedRedrawMixin import QueuedRedrawMixin
from NaviNIBS.util.numpy import array_equalish
from NaviNIBS.util.pyvista import DefaultPrimaryLayeredPlotter, RemotePlotterProxy

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


Transform = np.ndarray


@attrs.define
class NavigationView(QueuedRedrawMixin):
    _key: str
    _type: ClassVar[str]
    _coordinator: TargetingCoordinator

    _dockKeyPrefix: str = ''
    _title: str | None = None

    _dock: Dock = attrs.field(init=False)
    _wdgt: QtWidgets.QWidget = attrs.field(init=False)
    _contextMenu: QtWidgets.QMenu = attrs.field(init=False)

    _layers: tp.Dict[str, ViewLayer] = attrs.field(init=False, factory=dict)
    _layerLibrary: tp.Dict[str, tp.Callable[..., ViewLayer]] = attrs.field(init=False, factory=dict, repr=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._dock = Dock(
            name=self._dockKeyPrefix + self._key,
            title=self._title,
            affinities=[self._dockKeyPrefix])

        self._wdgt = QtWidgets.QWidget()
        self._wdgt.setContentsMargins(0, 0, 0, 0)
        self._dock.addWidget(self._wdgt)

        self._wdgt.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        # TODO: add context menu to be able to change view type with right click on title bar (?)

    def addLayer(self, key: str, type: str, **kwargs):
        raise NotImplementedError()  # should be implemented by subclass

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        #logger.debug(f'redraw {which}')

        super()._redraw(which=which)

        if which is None:
            which = 'all'
            self._redraw(which=which)
            return

        if not isinstance(which, str):
            for subWhich in which:
                self._redraw(which=subWhich)
            return

        # subclass should handle the rest

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
    _plotter: DefaultPrimaryLayeredPlotter = attrs.field(init=False, repr=False)
    _plotInSpace: str = 'MRI'
    _alignCameraTo: str | None = None
    _alignCameraOffset: tuple[float, float, float] | None = None
    _cameraDist: float = 100
    """
    None to use default camera perspective; 'target' to align camera space to target space, etc.
    Can also specify 'target-Y' to look at from along typical coil handle axis, or 'Target+X' to look at from other direction
    Can also specify something like 'tool-<toolKey>+X' to look at from along tool X axis
    """
    _doParallelProjection: bool = False

    _doShowLegend: bool = False

    _layers: tp.Dict[str, PlotViewLayer] = attrs.field(init=False, factory=dict)
    _layerLibrary: tp.Dict[str, tp.Callable[..., PlotViewLayer]] = attrs.field(init=False, factory=dict, repr=False)
    _lastAlignedToolPose: Transform | None = attrs.field(init=False, default=None, repr=False)

    finishedAsyncInit: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QVBoxLayout())
        self._wdgt.layout().setContentsMargins(0, 0, 0, 0)

        self._plotter = DefaultPrimaryLayeredPlotter()
        self._wdgt.layout().addWidget(self._plotter)

        self._layerLibrary = {}
        for cls in (
                    HeadMeshSurfaceLayer,
                    ToolMeshSurfaceLayer,
                    SampleOrientationsLayer,
                    SampleMetadataOrientationsLayer,
                    SampleMetadataInterpolatedSurfaceLayer,
                    TargetOrientationsLayer,
                    TargetingTargetCrosshairsLayer,
                    TargetingCoilCrosshairsLayer,
                    TargetingTargetPointsLayer,
                    TargetingCoilPointsLayer,
                    TargetingErrorLineLayer,
                    TargetingAngleErrorLayer,
                    ):
            self._layerLibrary[cls.type] = cls

        asyncio.create_task(asyncTryAndLogExceptionOnError(self.__finishInitialization_async))

    async def __finishInitialization_async(self):
        # call _finishInitialization_async() here to allow extending in subclass while still setting event only after fully initialized
        await self._finishInitialization_async()
        self.finishedAsyncInit.set()

    async def _finishInitialization_async(self):
        if isinstance(self._plotter, RemotePlotterProxy):
            await self._plotter.isReadyEvent.wait()

        self._coordinator.sigCurrentTargetChanged.connect(self._onCurrentTargetChanged)
        self._coordinator.sigCurrentCoilPositionChanged.connect(self._onCurrentCoilPositionChanged)
        self._coordinator.positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)

        if self._doParallelProjection:
            self._plotter.camera.enable_parallel_projection()

        self._redraw('all')

    @property
    def plotter(self):
        return self._plotter

    def _onCurrentTargetChanged(self):
        if self._alignCameraTo.startswith('target'):
            self._queueRedraw(which='camera')

    def _onCurrentCoilPositionChanged(self):
        if self._alignCameraTo.startswith('coil'):
            self._queueRedraw(which='camera')

    def _onLatestPositionsChanged(self):
        if self._alignCameraTo.startswith('tool-'):
            if self._alignCameraTo[-2:] in ('+X', '-X', '+Y', '-Y', '+Z', '-Z'):
                toolKey = self._alignCameraTo[len('tool-'):-2]
            else:
                toolKey = self._alignCameraTo[len('tool-'):]

            tool = self._coordinator.session.tools[toolKey]
            trackerKey = tool.trackerKey

            newTrackerPose = self._coordinator.positionsClient.getLatestTransf(trackerKey, None)
            if newTrackerPose is None:
                newToolPose = None
            else:
                if tool.toolToTrackerTransf is None:
                    newToolPose = None
                else:
                    # TODO: do some extra caching to not recalculate this with every position change
                    # (since the tool we align to may not actually be changing frequently)
                    # but then would also need to connect to toolTrackerTransf changed signal.
                    newToolPose = concatenateTransforms((tool.toolToTrackerTransf, newTrackerPose))

            if not array_equalish(newToolPose, self._lastAlignedToolPose):
                self._lastAlignedToolPose = newToolPose  # not actually aligned yet due to queueing, but should be okay unless there
                # are rapid bursts of switching between a small set of discrete poses
                self._queueRedraw(which='camera')

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
            cameraPts = np.asarray([[0, 0, 0], [0, 0, self._cameraDist], [0, 1, 0]])  # focal point, position, and up respectively

            if self._alignCameraTo is None:
                with self._plotter.allowNonblockingCalls:
                    self._plotter.reset_camera()
                    self._plotter.render()

            elif self._alignCameraTo.startswith('target'):
                extraRot = self._getExtraRotationForToAlignCamera(self._alignCameraTo[len('target'):])
                extraTransf = composeTransform(extraRot)

                if self._alignCameraOffset is not None:
                    extraTransf[0:3, 3] = np.asarray(self._alignCameraOffset)

                if self._plotInSpace == 'MRI':
                    if self._coordinator.currentTarget is not None and self._coordinator.currentTarget.coilToMRITransf is not None:
                        cameraPts = applyTransform(self._coordinator.currentTarget.coilToMRITransf @ extraTransf, cameraPts,
                                                   doCheck=False)
                    else:
                        raise NoValidCameraPoseAvailable()
                else:
                    raise NotImplementedError()

            elif self._alignCameraTo.startswith('coil'):
                extraRot = self._getExtraRotationForToAlignCamera(self._alignCameraTo[len('coil'):])
                extraTransf = composeTransform(extraRot)

                if self._alignCameraOffset is not None:
                    extraTransf[0:3, 3] = np.asarray(self._alignCameraOffset)

                if self._plotInSpace == 'MRI':
                    if self._coordinator.currentCoilToMRITransform is not None:
                        cameraPts = applyTransform(self._coordinator.currentCoilToMRITransform @ extraTransf, cameraPts, doCheck=False)
                    else:
                        raise NoValidCameraPoseAvailable()
                else:
                    raise NotImplementedError()

            elif self._alignCameraTo.startswith('tool-'):
                if self._alignCameraTo[-2:] in ('+X', '-X', '+Y', '-Y', '+Z', '-Z'):
                    extraRot = self._getExtraRotationForToAlignCamera(self._alignCameraTo[-2:])
                    extraTransf = composeTransform(extraRot)
                    toolKey = self._alignCameraTo[len('tool-'):-2]
                else:
                    extraTransf = np.eye(4)
                    toolKey = self._alignCameraTo[len('tool-'):]

                if self._alignCameraOffset is not None:
                    extraTransf[0:3, 3] = np.asarray(self._alignCameraOffset)

                trackerKey = self._coordinator.session.tools[toolKey].trackerKey
                trackerToWorldTransf = self._coordinator.positionsClient.getLatestTransf(trackerKey, None)

                if trackerToWorldTransf is None:
                    # missing information
                    raise NoValidCameraPoseAvailable

                if self._plotInSpace == 'MRI':
                    raise NotImplementedError  # TODO

                elif self._plotInSpace == 'World':
                    toolToTrackerTransf = self._coordinator.session.tools[toolKey].toolToTrackerTransf

                    if toolToTrackerTransf is None:
                        # missing information
                        raise NoValidCameraPoseAvailable

                    cameraPts = applyTransform(trackerToWorldTransf @ toolToTrackerTransf @ extraTransf, cameraPts, doCheck=False)

            else:
                 raise NotImplementedError()

        except NoValidCameraPoseAvailable:
            #logger.debug('No camera pose available')
            if False:
                with self._plotter.allowNonblockingCalls():
                    self._plotter.reset_camera()
                    self._plotter.reset_camera_clipping_range()
                    self._plotter.render()
                return  # TODO: change display to indicate view is out-of-date / invalid
            else:
                if False:
                    # continue plotting, but with generic position
                    cameraPts = np.asarray([[0, 0, 0], [0, 0, self._cameraDist * 3], [0, 1, 0]])  # focal point, position, and up respectively
                else:
                    # hide plot until we have necessary info
                    self._wdgt.setVisible(False)
                    return

        with self._plotter.allowNonblockingCalls():
            with self._plotter.renderingPaused():
                self._plotter.camera.focal_point = cameraPts[0, :]
                self._plotter.camera.position = cameraPts[1, :]
                self._plotter.camera.up = cameraPts[2, :] - cameraPts[1, :]
                if True:
                    # force fixed zoom in parallel camera views
                    self.plotter.camera.parallel_scale = self._cameraDist / 2

                if False:
                    # force fixed zoom in perspective camera views
                    self.plotter.camera.view_angle = 60.

                if self._alignCameraTo[-1] in 'XY' and False:
                    # orthogonal view, clip camera
                    self._plotter.camera.clipping_range = (self._cameraDist-0.1, self._cameraDist+0.1)
                else:
                    self._plotter.reset_camera_clipping_range()
            self._plotter.render()

        if not self._wdgt.isVisible():
            self._wdgt.setVisible(True)

    def addLayer(self, type: str, key: str, layeredPlotterKey: tp.Optional[str] = None,
                 plotterLayer: tp.Optional[int] = None, **kwargs):
        """
        Note: layer is added aynchronously later, to support delayed plotter initialization
        """
        asyncio.create_task(asyncTryAndLogExceptionOnError(self._addLayer_async,
                                                           type=type,
                                                           key=key,
                                                           layeredPlotterKey=layeredPlotterKey,
                                                           plotterLayer=plotterLayer,
                                                           **kwargs
                                                           ))

    async def _addLayer_async(self, type: str, key: str, layeredPlotterKey: tp.Optional[str] = None,
                              plotterLayer: tp.Optional[int] = None, **kwargs):

        if isinstance(self._plotter, RemotePlotterProxy):
            await self._plotter.isReadyEvent.wait()

        cls = self._layerLibrary[type]
        assert key not in self._layers
        if layeredPlotterKey is None:
            plotter = self._plotter
            if plotterLayer is not None:
                plotter.setLayer(plotterLayer)
        else:
            if layeredPlotterKey in self._plotter.secondaryPlotters:
                plotter = self._plotter.secondaryPlotters[layeredPlotterKey]
                if plotterLayer is not None:
                    assert plotter.rendererLayer == plotterLayer
            else:
                plotter = self._plotter.addLayeredPlotter(key=layeredPlotterKey, layer=plotterLayer)
                logger.debug(f'Added renderer layer {layeredPlotterKey} #{plotter.rendererLayer}')

        self._layers[key] = cls(key=key, **kwargs,
                                coordinator=self._coordinator,
                                plotter=plotter,
                                plotInSpace=self._plotInSpace)

        plotter.render()

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str]] = None):

        if isinstance(self._plotter, RemotePlotterProxy) and not self._plotter.isReadyEvent.is_set():
            # plotter not yet ready
            return

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
                layer._queueRedraw(which='all')

        else:
            raise NotImplementedError


@attrs.define
class TargetingCrosshairsView(SinglePlotterNavigationView):
    _type: ClassVar[str] = 'TargetingCrosshairs'
    _alignCameraTo: str = 'target'
    _doShowSkinSurf: bool = False
    _doShowHandleAngleError: bool = False
    _doShowTargetTangentialAngleError: bool = False
    _doShowScalpTangentialAngleError: bool = False

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        plotLayer = 0

        if self._doShowSkinSurf:
            self.addLayer(type='HeadMeshSurface', key='Skin', surfKey='skinSimpleSurf',
                          color='#c9c5c2',
                          layeredPlotterKey='SkinMesh',
                          plotterLayer=plotLayer)

            plotLayer += 1

            #self._plotter.secondaryPlotters['SkinMesh'].enable_depth_peeling(2)


        if True and self._alignCameraTo == 'target':
            self.addLayer(type='SampleMetadataInterpolatedSurface',
                          key='Brain',
                          surfKey='gmSurf',
                          metadataKey='Vpp_dBmV',
                          colorbarLabel='Vpp (dBmV)',
                          scalarAnnotations={
                              20*np.log10(50e-3): '50 uV',
                              20*np.log10(1): '1 mV',
                          },
                          relevantSampleDepth='intersection',
                          kernelRadius=8,
                          layeredPlotterKey='Brain',
                          plotterLayer=plotLayer)
        else:
            self.addLayer(type='HeadMeshSurface', key='Brain', surfKey='gmSurf',
                          layeredPlotterKey='Brain',
                          plotterLayer=plotLayer)

        plotLayer += 1

        if False and self._alignCameraTo == 'target':
            self.addLayer(type='SampleMetadataInterpolatedSurface',
                          key='ScalpVpps',
                          surfKey='csfSurf',
                          opacity=0.5,
                          meshOpacityOutsideInterpolatedRegion=0.,
                          metadataKey='Vpp_dBmV',
                          colorbarLabel='Vpp (dBmV)',
                          scalarAnnotations={
                              20 * np.log10(50e-3): '50 uV',
                              20 * np.log10(1): '1 mV',
                          },
                          relevantSampleDepth='intersection',
                          layeredPlotterKey='ScalpVpps',
                          plotterLayer=plotLayer)
            plotLayer += 1

        if True:
            self.addLayer(type='SampleOrientations', key='Samples',
                          layeredPlotterKey='Orientations',
                          plotterLayer=plotLayer)
        elif False:
            self.addLayer(type='SampleMetadataOrientations',
                          key='SampleVpps',
                          layeredPlotterKey='Orientations',
                          plotterLayer=plotLayer,
                          metadataKey='Vpp',
                          metadataScaleFactor=1.e6,
                          colorbarLabel='Vpp (uV)',
                          lineWidth=6.)
        else:
            self.addLayer(type='SampleMetadataOrientations',
                          key='SampleVpps_dBmV',
                          layeredPlotterKey='Orientations',
                          plotterLayer=plotLayer,
                          metadataKey='Vpp_dBmV',
                          colorbarLabel='Vpp (dBmV)',
                          lineWidth=6.)

        self.addLayer(type='TargetOrientations', key='Targets', layeredPlotterKey='Orientations')
        self.addLayer(type='TargetingTargetPoints', key='TargetPoints', layeredPlotterKey='Orientations')
        self.addLayer(type='TargetingCoilPoints', key='CoilPoints', layeredPlotterKey='Orientations')

        plotLayer += 1

        self.addLayer(type='TargetingTargetCrosshairs', key='Target',
                      layeredPlotterKey='Crosshairs',
                      plotterLayer=plotLayer)
        self.addLayer(type='TargetingCoilCrosshairs', key='Coil', layeredPlotterKey='Crosshairs')
        plotLayer += 1

        self.addLayer(type='TargetingErrorLine', key='TargetError', targetDepth='target', coilDepth='target',
                      layeredPlotterKey='TargetingError',
                      plotterLayer=plotLayer)
        self.addLayer(type='TargetingErrorLine', key='CoilError', targetDepth='coil', coilDepth='coil',
                      layeredPlotterKey='TargetingError')

        if self._doShowHandleAngleError:
            self.addLayer(type='TargetingAngleError', key='HandleAngleError',
                          angleMetric='Horiz angle error',
                          layeredPlotterKey='TargetingError')

        if self._doShowTargetTangentialAngleError:
            plotOn = self._alignCameraTo[:-2]
            if plotOn == 'coil':
                multiplier = -4.
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
                multiplier = -4.
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

            plotLayer += 1

    async def _finishInitialization_async(self):
        await super()._finishInitialization_async()

        #self._plotter.enable_depth_peeling(2)



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
