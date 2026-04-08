from __future__ import annotations

import asyncio
import functools
import logging
import typing as tp

import attrs
import distinctipy
import numpy as np
import pyvista as pv
from qtpy import QtWidgets, QtGui, QtCore

from NaviNIBS.Navigator.GUI.Widgets.SurfViews import Surf3DView, defaultSurfColor
if tp.TYPE_CHECKING:
    from NaviNIBS.util.pyvista import Actor
from NaviNIBS.util.pyvista import RemotePlotterProxy
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter
from NaviNIBS.util.GUI.QueuedRedrawMixin import QueuedRedrawMixin
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model import ROIs
from NaviNIBS.Navigator.Model.ROIs.PipelineROI import PipelineROI

if DefaultBackgroundPlotter is RemotePlotterProxy or tp.TYPE_CHECKING:
    from NaviNIBS.util.pyvista.RemotePlotting.RemotePlotterProxy import RemotePolyDataProxy, RemotePlotterProxyBase


logger = logging.getLogger(__name__)


@attrs.define
class VisualizedROIsMesh(QueuedRedrawMixin):
    """
    Shared mesh visualization for one or more ROIs on the same meshKey.
    Owns a single mesh copy and actor. Renders background brain color for non-ROI vertices
    and per-ROI colors (averaged where overlapping) for ROI vertices in one RGBA scalar array.
    Hides the corresponding Surf3DView actor while active to avoid z-fighting.
    """
    _meshKey: str
    _session: Session
    _linked3DView: Surf3DView
    _opacity: float = 0.85
    _backgroundColor: tuple[int, int, int, int] = attrs.field(init=False)

    _mesh: pv.PolyData | None = attrs.field(init=False, default=None)
    _meshActor: Actor | None = attrs.field(init=False, default=None)
    _registeredROIs: list[VisualizedROI] = attrs.field(init=False, factory=list)

    scalarsKey: tp.ClassVar[str] = 'ROIs_combined_rgba'

    def __attrs_post_init__(self):
        QueuedRedrawMixin.__attrs_post_init__(self)
        bgColor, bgOpacity = self._linked3DView.getSurfColorAndOpacity(self._meshKey)
        c = QtGui.QColor(bgColor)
        self._backgroundColor = (c.red(), c.green(), c.blue(), round(255 * bgOpacity))
        self._initMesh()

    def _initMesh(self):
        logger.info(f'Initializing shared mesh for ROIs on {self._meshKey}')

        srcMesh = getattr(self._session.headModel, self._meshKey)
        self._mesh = pv.PolyData(srcMesh, deep=True)

        # Initialize all vertices to background color
        self._mesh[self.scalarsKey] = np.tile(self._backgroundColor,
                                              (self._mesh.n_points, 1)).astype(np.uint8)

        if isinstance(self._linked3DView.plotter, RemotePlotterProxyBase):
            self._mesh = self._linked3DView.plotter.registerPolyData(
                polyData=self._mesh,
                id=self._meshKey + '_VisualizedROIsMesh',
            )

        self._meshActor = self._linked3DView.plotter.addMesh(
            self._mesh,
            scalars=self.scalarsKey,
            rgba=True,
            name=f'ROIs_{self._meshKey}',
            pickable=False,
            specular=0.5,
            diffuse=0.5,
            ambient=0.5,
        )

        # Hide the Surf3DView's own actor for this surface to avoid duplication/z-fighting
        self._linked3DView.setSurfaceVisibility(self._meshKey, visible=False)

    @property
    def isEmpty(self) -> bool:
        return len(self._registeredROIs) == 0

    def _redraw(self, which: str | None = None, **kwargs):
        super()._redraw(which=which, **kwargs)
        match which:
            case 'mesh':
                if self._mesh is None:
                    return

                n = self._mesh.n_points
                buf = np.zeros((n, 5), dtype=np.float64)  # [R_sum, G_sum, B_sum, alpha_sum, count]

                for vROI in self._registeredROIs:
                    if not vROI.isVisible:
                        continue
                    roi = vROI.effectiveROI
                    if roi is None:
                        continue
                    if roi.meshVertexIndices is None or len(roi.meshVertexIndices) == 0:
                        continue
                    buf[roi.meshVertexIndices, :4] += vROI.effectiveColor  # RGBA 0-1
                    buf[roi.meshVertexIndices, 4] += 1

                roiMask = buf[:, 4] > 0

                if np.any(roiMask):
                    buf[roiMask, :4] /= buf[roiMask, 4:5]  # average to 0-1
                    buf[roiMask, :4] *= 255
                    buf[roiMask, 4] *= self._opacity

                buf[~roiMask, :4] = self._backgroundColor  # broadcast single background RGBA tuple

                np.clip(buf, 0, 255, out=buf)
                newRGBA = buf[:, :4].astype(np.uint8)

                with self._linked3DView.plotter.allowNonblockingCalls():
                    self._mesh[self.scalarsKey] = newRGBA
                    self._linked3DView.plotter.render()
            case _:
                raise NotImplementedError

    def refresh(self):
        self._queueRedraw('mesh')

    def addROI(self, visualizedROI: VisualizedROI):
        if visualizedROI not in self._registeredROIs:
            self._registeredROIs.append(visualizedROI)
        self._queueRedraw('mesh')

    def removeROI(self, visualizedROI: VisualizedROI):
        if visualizedROI in self._registeredROIs:
            self._registeredROIs.remove(visualizedROI)
        if self.isEmpty:
            self.clear()
        else:
            self._queueRedraw('mesh')

    def clear(self):
        if self._meshActor is not None:
            logger.info(f'Clearing shared mesh for ROIs on {self._meshKey}')

            self._linked3DView.plotter.remove_actor(self._meshActor)
            self._meshActor = None
            self._mesh = None
            # Restore the Surf3DView actor
            self._linked3DView.setSurfaceVisibility(self._meshKey, visible=True)


@attrs.define(eq=False)
class VisualizedROI:
    _roi: ROIs.ROI
    _session: Session
    _linked3DView: Surf3DView
    _opacity: float = 0.85
    _meshRegistry: dict[str, VisualizedROIsMesh] = attrs.field(factory=dict)

    _meshKey: str | None = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        self._initMesh()

        self._roi.sigItemChanged.connect(self._onROIChanged)

    @property
    def isVisible(self) -> bool:
        return self._roi.isVisible

    @property
    def effectiveROI(self) -> ROIs.SurfaceMeshROI | None:
        if isinstance(self._roi, PipelineROI):
            out = self._roi.getOutput()
            return out if isinstance(out, ROIs.SurfaceMeshROI) else None
        elif isinstance(self._roi, ROIs.SurfaceMeshROI):
            return self._roi
        return None

    @property
    def effectiveColor(self) -> np.ndarray:
        roi = self.effectiveROI
        if roi is None:
            return np.array([0.5, 0.5, 0.5, 1.0])
        if roi.color is not None:
            if len(roi.color) == 3:
                return np.asarray(list(roi.color[0:3]) + [1.], dtype=np.float64)
            else:
                assert len(roi.color) == 4
                return np.asarray(roi.color, dtype=np.float64)
        if roi.autoColor is not None:
            return np.asarray(list(roi.autoColor) + [1.], dtype=np.float64)
        return np.array([0.5, 0.5, 0.5, 1.0])

    def _onROIChanged(self, key: str, changedAttrs: list[str] | None = None):
        if changedAttrs is None:
            # assume everything changed
            self._initMesh()
            return

        if 'isVisible' in changedAttrs:
            self._refreshMeshVisibility()

        if 'color' in changedAttrs or 'autoColor' in changedAttrs:
            self._refreshMeshColor()

        if isinstance(self._roi, ROIs.SurfaceMeshROI):
            if 'meshKey' in changedAttrs:
                self._initMesh()
            else:
                if 'meshVertexIndices' in changedAttrs:
                    self._refreshMeshOpacity()

        elif isinstance(self._roi, PipelineROI):
            if 'output' in changedAttrs or 'stages' in changedAttrs:
                newOutput = self._roi.getOutput()

                if newOutput is None:
                    # pipeline ROI has no output, clear existing visualization
                    self._unregisterFromMesh()
                    return

                if not isinstance(newOutput, ROIs.SurfaceMeshROI):
                    raise NotImplementedError(f'Visualization for ROI type {type(newOutput)} not implemented')
                if newOutput.meshKey != self._meshKey:
                    self._initMesh()
                else:
                    # assume only thing that changed was vertex membership within existing mesh
                    self._refreshMeshOpacity()

        else:
            raise NotImplementedError(f'Visualization for ROI type {type(self._roi)} not implemented')

    def _initMesh(self):
        logger.info(f'Initializing mesh registration for visualizing ROI {self._roi.key}')

        self._unregisterFromMesh()

        if not self._roi.isVisible:
            return

        if isinstance(self._roi, PipelineROI):
            roi = self._roi.getOutput()
        else:
            roi = self._roi

        if not isinstance(roi, ROIs.SurfaceMeshROI):
            raise NotImplementedError(f'Visualization for ROI type {type(roi)} not implemented')

        if roi.meshKey is None:
            logger.warning(f'ROI {roi.key} has no meshKey set, cannot visualize')
            return

        if roi.meshVertexIndices is None or len(roi.meshVertexIndices) == 0:
            return

        self._meshKey = roi.meshKey
        # TODO: subscribe to head model changes in this mesh

        if self._meshKey not in self._meshRegistry:
            self._meshRegistry[self._meshKey] = VisualizedROIsMesh(
                meshKey=self._meshKey,
                session=self._session,
                linked3DView=self._linked3DView,
                opacity=self._opacity,
            )
        self._meshRegistry[self._meshKey].addROI(self)

    def _unregisterFromMesh(self):
        if self._meshKey is not None:
            viz = self._meshRegistry.get(self._meshKey)
            if viz is not None:
                viz.removeROI(self)
                if viz.isEmpty:
                    del self._meshRegistry[self._meshKey]
            self._meshKey = None

    def _refreshMeshVisibility(self):
        logger.info(f'Refreshing mesh visibility for visualizing ROI {self._roi.key}')
        if self._meshKey is None:
            if self._roi.isVisible:
                self._initMesh()
        else:
            viz = self._meshRegistry.get(self._meshKey)
            if viz is not None:
                viz.refresh()

    def _refreshMeshColor(self):
        logger.info(f'Refreshing mesh color for visualizing ROI {self._roi.key}')
        if self._meshKey is None:
            self._initMesh()
        else:
            viz = self._meshRegistry.get(self._meshKey)
            if viz is not None:
                viz.refresh()

    def _refreshMeshOpacity(self):
        logger.info(f'Refreshing mesh opacity for visualizing ROI {self._roi.key}')
        if self._meshKey is None:
            self._initMesh()
        else:
            viz = self._meshRegistry.get(self._meshKey)
            if viz is not None:
                viz.refresh()

    def clear(self):
        logger.info(f'Clearing visualized ROI {self._roi.key}')
        self._unregisterFromMesh()


def refreshROIAutoColors(session: Session):
    autoROIs = [roi for roi in session.ROIs.values() if roi.color is None and roi.isVisible]
    if len(autoROIs) == 0:
        return  # no need to update

    logger.info('Refreshing auto ROI colors')

    fixedROIs = [roi for roi in session.ROIs.values() if roi.color is not None and roi.isVisible]

    excludeColors = [(0., 0., 0.), (1., 1., 1.)]

    meshColors = [defaultSurfColor,]
    # could dynamically get current mesh colors, but then this may produce different
    # autocolor results in different plotting views

    # convert meshColor in form '#d9a5b2' into rgb 0-1
    meshColors = [QtGui.QColor(colorStr).getRgbF()[0:3] for colorStr in meshColors]
    excludeColors.extend(meshColors)

    fixedROIColors = [tuple(roi.color[0:3]) for roi in fixedROIs]
    excludeColors.extend(fixedROIColors)

    autoROIColors = _getDistinctColors(len(autoROIs),
                                       exclude_colors=tuple(excludeColors),
                                       rng=0)
    for iROI, roi in enumerate(autoROIs):
        roi.autoColor = autoROIColors[iROI]


@functools.cache
def _getDistinctColors(n_colors: int, exclude_colors: tuple[tuple[float, float, float], ...] | None = None, rng: int | None = None) -> list[tp.Any]:
    """
    distinctipy.get_colors is slow, so cache results
    """
    return distinctipy.get_colors(n_colors, exclude_colors=list(exclude_colors), rng=rng)
