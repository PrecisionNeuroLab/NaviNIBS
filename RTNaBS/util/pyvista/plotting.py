from __future__ import annotations
import asyncio
import logging
import numpy as np
import pyvista as pv
import pyvistaqt as pvqt
from qtpy import QtGui, QtCore
import typing as tp


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class _DelayedPlotter:
    _needsRender: asyncio.Event
    _renderTask: asyncio.Task
    _renderingNotPaused: asyncio.Event
    minRenderPeriod: float

    def __init__(self, minRenderPeriod: float = 0.05):
        self._needsRender = asyncio.Event()
        self._renderingNotPaused = asyncio.Event()
        self._renderingNotPaused.set()

        self.minRenderPeriod = minRenderPeriod

        self._renderTask = asyncio.create_task(self._renderLoop())

    def pauseRendering(self):
        self._renderingNotPaused.clear()

    def resumeRendering(self):
        self._renderingNotPaused.set()

    def _renderNow(self):
        self._needsRender.clear()
        logger.debug('Rendering')
        super().render()
        logger.debug('Done rendering')

    async def _renderLoop(self):
        while True:
            await asyncio.sleep(self.minRenderPeriod)
            await self._needsRender.wait()
            await self._renderingNotPaused.wait()
            self._renderNow()

    def render(self, doRenderImmediately: bool = False):
        if doRenderImmediately:
            logger.debug('Rendering immediately')
            self._renderNow()
            logger.debug('Done rendering immediately')
        else:
            logger.debug('Setting render flag')
            self._needsRender.set()


class BackgroundPlotter(_DelayedPlotter, pvqt.plotting.BackgroundPlotter):
    """
    Same as inherited pvqt.BackgroundPlotter, but batches multiple render calls together with an async coroutine

    Also set default background color based on app palette
    """

    def __init__(self, *args, auto_update: float = 0.01, **kwargs):
        _DelayedPlotter.__init__(self, **{key: val for key, val in kwargs.items() if key in ('minRenderPeriod',)})
        try:
            kwargs.pop('minRenderPeriod')
        except KeyError:
            pass
        pvqt.plotting.BackgroundPlotter.__init__(self, *args, auto_update=auto_update, **kwargs)

        self.set_background(self.palette().color(QtGui.QPalette.Base).name())


class SecondaryLayeredPlotter(_DelayedPlotter, pv.BasePlotter):
    _mainPlotter: PrimaryLayeredPlotter
    _rendererLayer: int

    def __init__(self, mainPlotter: PrimaryLayeredPlotter, rendererLayer: tp.Optional[int] = None, **kwargs):
        _DelayedPlotter.__init__(self, **{key: val for key, val in kwargs if key in ('minRenderPeriod',)})
        try:
            kwargs.pop('minRenderPeriod')
        except KeyError:
            pass

        pv.BasePlotter.__init__(self, **kwargs)

        if rendererLayer is None:
            rendererLayer = mainPlotter.ren_win.GetNumberOfLayers()
            logger.info('Renderer layer: {rendererLayer}')

        self._rendererLayer = rendererLayer

        self._mainPlotter = mainPlotter

        mainPlotter.ren_win.SetNumberOfLayers(mainPlotter.ren_win.GetNumberOfLayers()+1)
        for renderer in self.renderers:
            mainPlotter.ren_win.AddRenderer(renderer)
            renderer.SetLayer(rendererLayer)
        mainPlotter.link_views_across_plotters(self)

        self.renderer.set_background(mainPlotter.background_color)

        for renderer in self.renderers:
            renderer.SetLayer(rendererLayer)

    @property
    def rendererLayer(self):
        return self._rendererLayer

    def enable_depth_peeling(self, *args, **kwargs):
        result = self.renderer.enable_depth_peeling(*args, **kwargs)
        if result:
            self._mainPlotter.ren_win.AlphaBitPlanesOn()
        return result

    def render(self, doRenderPrimaryPlotter: bool = True, doRenderImmediately: bool = False):
        super().render(doRenderImmediately=doRenderImmediately)
        if doRenderPrimaryPlotter:
            self._mainPlotter.render(doRenderSecondaryPlotters=False,
                                     doRenderImmediately=doRenderImmediately)  # TODO: determine if this is necessary

    def reset_camera_clipping_range(self, doIncludeMainPlotter: bool = True):
        if doIncludeMainPlotter:
            self._mainPlotter.reset_camera_clipping_range()
        else:
            super().reset_camera_clipping_range()


class PrimaryLayeredPlotter(BackgroundPlotter):

    _secondaryPlotters: dict[str, SecondaryLayeredPlotter]

    def __init__(self, *args, rendererLayer: int = 0, **kwargs):
        self._secondaryPlotters = dict()

        BackgroundPlotter.__init__(self, *args, **kwargs)

        for renderer in self.renderers:
            renderer.SetLayer(rendererLayer)

        # disable auto-adjust of camera clipping since it fails with multiple renderers
        self.iren.interactor.GetInteractorStyle().AutoAdjustCameraClippingRangeOff()

    @property
    def secondaryPlotters(self):
        return self._secondaryPlotters

    def setLayer(self, layer: int):
        for renderer in self.renderers:
            renderer.SetLayer(layer)
        # TODO: do validation to make sure ren_win.NumberLayers etc. is set as needed

    def addLayeredPlotter(self, key: str, layer: tp.Optional[int] = None) -> SecondaryLayeredPlotter:
        assert key not in self._secondaryPlotters
        self._secondaryPlotters[key] = SecondaryLayeredPlotter(mainPlotter=self, rendererLayer=layer)
        return self._secondaryPlotters[key]

    def pauseRendering(self):
        super().pauseRendering()
        for plotter in self._secondaryPlotters.values():
            plotter.pauseRendering()

    def resumeRendering(self):
        super().resumeRendering()
        for plotter in self._secondaryPlotters.values():
            plotter.resumeRendering()

    def _renderNow(self):
        logger.debug('Setting camera clipping range')
        self.reset_camera_clipping_range()
        super()._renderNow()

    def render(self, doRenderSecondaryPlotters: bool = True, doRenderImmediately: bool = False):
        super().render(doRenderImmediately=doRenderImmediately)
        if doRenderSecondaryPlotters:
            for plotter in self._secondaryPlotters.values():
                plotter.render(doRenderPrimaryPlotter=False, doRenderImmediately=doRenderImmediately)

    def set_background(self, *args, **kwargs):
        super().set_background(*args, **kwargs)
        for plotter in self._secondaryPlotters.values():
            plotter.set_background(*args, **kwargs)

    def enable_depth_peeling(self, *args, **kwargs):
        super().enable_depth_peeling(*args, **kwargs)
        # NOTE: apply this to primary plotter only; if want to enable depth peeling in
        #  secondary plotters, then need to call enable_depth_peeling for each separately

    def reset_camera_clipping_range(self):
        if False:
            super().reset_camera_clipping_range()
            for plotter in self.secondaryPlotters.values():
                plotter.reset_camera_clipping_range(doIncludeMainPlotter=False)
        else:
            # manually calculate bounds collectively from primary and secondary plotters
            bounds = np.asarray(self.bounds).reshape((3,2)).T
            for plotter in self.secondaryPlotters.values():
                thisBounds = np.asarray(plotter.bounds).reshape((3,2)).T
                bounds[0, :] = np.minimum(bounds[0, :], thisBounds[0, :])
                bounds[1, :] = np.maximum(bounds[1, :], thisBounds[1, :])

            bounds[0, :] = bounds[0, :] - (bounds[1, :] - bounds[0, :])
            bounds[1, :] = bounds[1, :] + (bounds[1, :] - bounds[0, :])

            self.renderer.ResetCameraClippingRange(*bounds.T.reshape((6,)).tolist())

            # for plotter in self.secondaryPlotters.values():
            #     plotter.renderer.ResetCameraClippingRange(*bounds.T.reshape((6,)).tolist())

            logger.debug(f'Camera clipping range: {self.renderer.camera.clipping_range}')
