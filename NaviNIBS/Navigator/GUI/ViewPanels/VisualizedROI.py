from __future__ import annotations

import asyncio
import logging
import os
import typing as tp

import attrs
import distinctipy
import numpy as np
import pyvista as pv
from qtpy import QtWidgets, QtGui, QtCore

from NaviNIBS.Navigator.GUI.Widgets.SurfViews import Surf3DView, defaultSurfColor
from NaviNIBS.util.pyvista import Actor, RemotePlotterProxy
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model import ROIs

if DefaultBackgroundPlotter is RemotePlotterProxy or tp.TYPE_CHECKING:
    from NaviNIBS.util.pyvista.RemotePlotting.RemotePlotterProxy import RemotePolyDataProxy, RemotePlotterProxyBase


logger = logging.getLogger(__name__)



@attrs.define
class VisualizedROI:
    _roi: ROIs.ROI
    _session: Session
    _linked3DView: Surf3DView
    _opacity: float = 0.85

    _meshKey: str | None = attrs.field(init=False, default=None)
    _mesh: pv.PolyData | None = attrs.field(init=False, default=None)
    _meshActor: Actor | None = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        self._initMesh()

        self._roi.sigItemChanged.connect(self._onROIChanged)

    @property
    def scalarsKey(self):
        return 'ROI_' + self._roi.key + '_rgba'

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

        elif isinstance(self._roi, ROIs.PipelineROI):
            if 'output' in changedAttrs or 'stages' in changedAttrs:
                newOutput = self._roi.getOutput()

                if newOutput is None:
                    # pipeline ROI has no output, clear existing visualization
                    self.clear()
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

        logger.info(f'Initializing mesh for visualizing ROI {self._roi.key}')

        if self._mesh is not None:
            # clear previous mesh, meshKey, meshActor
            self.clear()

        if not self._roi.isVisible:
            return  # nothing to do

        if isinstance(self._roi, ROIs.PipelineROI):
            roi = self._roi.getOutput()
        else:
            roi = self._roi

        if not isinstance(roi, ROIs.SurfaceMeshROI):
            raise NotImplementedError(f'Visualization for ROI type {type(roi)} not implemented')

        if roi.meshKey is None:
            logger.warning(f'ROI {roi.key} has no meshKey set, cannot visualize')
            return

        if roi.meshVertexIndices is None or len(roi.meshVertexIndices) == 0:
            # nothing to visualize
            return

        self._meshKey = roi.meshKey
        # TODO: subscribe to head model changes in this mesh

        self._mesh = pv.PolyData(getattr(self._session.headModel, roi.meshKey),
                                 deep=True)

        self._setMeshRGBA()

        if isinstance(self._linked3DView.plotter, RemotePlotterProxyBase):
            # wrap mesh as a RemotePolyDataProxy so that future updates
            # to mesh scalars are reflected in remote plotter
            self._mesh = self._linked3DView.plotter.registerPolyData(
                polyData=self._mesh,
                id=self._roi.key + '_VisualizedROIMesh',  # specify ID so that previous mesh gets overwritten on re-adding
            )

        self._meshActor = self._linked3DView.plotter.addMesh(
            self._mesh,
            scalars=self.scalarsKey,
            rgba=True,
            name=f'ROI_{self._roi.key}',
            pickable=False,
            specular=0.5,
            diffuse=0.5,
            ambient=0.5,
        )

    def _refreshMeshVisibility(self):
        logger.info(f'Refreshing mesh visibility for visualizing ROI {self._roi.key}')
        if self._meshActor is not None:
            with self._linked3DView.plotter.allowNonblockingCalls():
                self._meshActor.SetVisibility(self._roi.isVisible)
                self._linked3DView.plotter.render()
        elif self._roi.isVisible:
            self._initMesh()

    def _refreshMeshColor(self):
        logger.info(f'Refreshing mesh color for visualizing ROI {self._roi.key}')
        if self._meshActor is None:
            self._initMesh()
        else:
            self._setMeshRGBA()
            self._linked3DView.plotter.render()

    def _refreshMeshOpacity(self):
        logger.info(f'Refreshing mesh opacity for visualizing ROI {self._roi.key}')
        if self._meshActor is None:
            self._initMesh()
        else:
            self._setMeshRGBA()
            self._linked3DView.plotter.render()

    def _setMeshRGBA(self):
        if isinstance(self._roi, ROIs.PipelineROI):
            roi = self._roi.getOutput()
        else:
            assert isinstance(self._roi, ROIs.SurfaceMeshROI)
            roi = self._roi

        if roi.color is None:
            assert roi.autoColor is not None, 'autoColor should have been set before visualization'
            color = roi.autoColor
        else:
            color = roi.color[0:3]  # ignore any pre-set alpha
        color = list(color)

        # convert color from rgbf (0-1) to rgba (0-255 integer)
        rgbaColor = [round(255 * c) for c in color]

        logger.debug(f'rgbaColor: {rgbaColor}')

        # will store rgba value (0-255 uint8) per vertex
        # TODO: possibly cache this array so that it doesn't need to reallocated with each update
        newRGBA = np.full((self._mesh.n_points, 4), 0, dtype=np.uint8)

        if roi.meshVertexIndices is not None:
            newRGBA[:, 0:3] = rgbaColor[0:3]
            newRGBA[roi.meshVertexIndices, 3] = round(255 * self._opacity)  # set alpha for ROI vertices

        # do full assignment rather than subset above to trigger remote observer properly
        self._mesh[self.scalarsKey] = newRGBA

    def clear(self):
        if self._meshActor is not None:
            # clear previous mesh, meshKey, meshActor
            logger.info(f'Clearing visualized ROI {self._roi.key}')
            self._meshKey = None
            self._mesh = None
            self._linked3DView.plotter.remove_actor(self._meshActor)
            self._meshActor = None


def refreshROIAutoColors(session: Session):
    autoROIs = [roi for roi in session.ROIs.values() if roi.color is None and roi.isVisible]
    if len(autoROIs) == 0:
        return  # no need to update

    logger.info('Refreshing auto ROI colors')

    fixedROIs = [roi for roi in session.ROIs.values() if roi.color is not None and roi.isVisible]

    excludeColors = [[0., 0., 0.], [1., 1., 1.]]

    meshColors = [defaultSurfColor,]
    # could dynamically get current mesh colors, but then this may produce different
    # autocolor results in different plotting views

    # convert meshColor in form '#d9a5b2' into rgb 0-1
    meshColors = [QtGui.QColor(colorStr).getRgbF()[0:3] for colorStr in meshColors]
    excludeColors.extend(meshColors)

    fixedROIColors = [list(roi.color) for roi in fixedROIs]
    excludeColors.extend(fixedROIColors)

    autoROIColors = distinctipy.get_colors(len(autoROIs),
                                           exclude_colors=excludeColors,
                                           rng=0)
    for iROI, roi in enumerate(autoROIs):
        roi.autoColor = autoROIColors[iROI]
