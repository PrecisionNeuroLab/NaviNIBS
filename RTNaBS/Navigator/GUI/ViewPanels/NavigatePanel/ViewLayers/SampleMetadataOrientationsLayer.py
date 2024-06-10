from __future__ import annotations

import asyncio

import attrs
import logging
from math import nan, isnan
import numpy as np
import typing as tp
from typing import ClassVar
import pyvista as pv

from NaviNIBS.Navigator.GUI.ViewPanels.NavigatePanel.ViewLayers.OrientationsLayers import SampleOrientationsLayer, VisualizedOrientation
from NaviNIBS.Navigator.GUI.ViewPanels.NavigatePanel.ViewLayers.MeshSurfaceLayer import HeadMeshSurfaceLayer
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.Transforms import applyTransform, invertTransform, composeTransform, concatenateTransforms

logger = logging.getLogger(__name__)


@attrs.define(kw_only=True)
class SampleMetadataOrientationsLayer(SampleOrientationsLayer):

    _type: ClassVar[str] = 'SampleMetadataOrientations'

    _metadataKey: str
    _colorbarLabel: str | None = None
    _metadataScaleFactor: float = 1.0

    _colorDepthIndicator: str | None = None
    _colorHandleIndicator: str | None = None
    _colorDepthIndicatorSelected: str | None = None
    _colorHandleIndicatorSelected: str | None = None

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _createVisualizedOrientationForSample(self, key: str) -> VisualizedOrientation:

        isSelected = self.orientations[key].isSelected

        metadataVal = nan
        if self._metadataKey in self._coordinator.session.samples[key].metadata:
            metadataVal = self._coordinator.session.samples[key].metadata[self._metadataKey]

        logger.debug(f'metadataVal: {metadataVal}')

        if isnan(metadataVal):
            color = 'gray'
        else:
            if self._colorbarLabel is None:
                colorbarLabel = self._metadataKey
            else:
                colorbarLabel = self._colorbarLabel
            color = (metadataVal * self._metadataScaleFactor, colorbarLabel)

        lineWidth = self._lineWidth
        if isSelected:
            lineWidth *= 2  # increase line width to highlight selected samples

        return VisualizedOrientation(
            orientation=self.orientations[key],
            plotter=self._plotter,
            colorHandleIndicator=color,
            colorDepthIndicator=color,
            opacity=self._opacity,
            lineWidth=lineWidth,
            style=self._style,
            actorKeyPrefix=self._getActorKey(key)
        )


@attrs.define(kw_only=True)
class SampleMetadataInterpolatedSurfaceLayer(HeadMeshSurfaceLayer):
    _type: ClassVar[str] = 'SampleMetadataInterpolatedSurface'
    _metadataKey: str
    """
    Which value in sample metadata to plot
    """
    _colorbarLabel: str | None = None
    _relevantSampleDepth: str = 'target'
    """
    Which depth along sample entry vector to use for interpolation on surface.
    If interpolating onto cortical surface, probably want to use skin depth.
    If interpolating onto skin surface, probably want to use entry or coil depth.
    Or for any smooth-ish surface, can use "intersection" to autoset depth based on intersection with plotted surface.
    """
    _kernelSharpness: float = 0.5  # TODO: experiment to set more reasonable default
    _kernelRadius: float = 5.0

    _mesh: pv.PolyData | None = attrs.field(init=False, default=None)
    _scalarsKey: str = 'SampleMetadataInterpolated'
    """
    Where to save interpolated values within internal mesh object
    """
    _scalarsOpacityKey: str = 'SampleMetadataInterpolatedOpacity'

    _reinterpolationRateLimit: float = 10  # in Hz
    _needsReinterpolation: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    _cachedSampleIntersections: dict[str, np.ndarray] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.session.samples.sigItemsChanged.connect(self._onSamplesChanged)

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._loop_reinterpolate))

    async def _loop_reinterpolate(self):
        """
        Handle re-interpolation in an async loop to rate-limit (important when multiple samples are edited rapidly)
        """

        while True:
            await self._needsReinterpolation.wait()
            await asyncio.sleep(1/self._reinterpolationRateLimit)
            if not self._needsReinterpolation.is_set():
                continue
            self._redraw(which='interpolateValues')

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        logger.debug(f'redraw {which}')
        if which == 'initSurf':
            assert self._mesh is None

            # override parent method to use sample metadata to interpolate surface
            self._mesh = pv.PolyData(getattr(self._coordinator.session.headModel, self._surfKey),
                                     deep=True)

            if True:  # TODO: troubleshoot to be able to set initValue to nan instead of zero
                # this seems to cause issues with colorbar scaling
                initValue = np.nan
            else:
                initValue = 0.
            initValues = np.full((self._mesh.n_points,), initValue)
            initValues[0] = -40  # TODO: debug, delete
            self._mesh[self._scalarsKey] = initValues

            actorKey = self._getActorKey('surf')

            if self._colorbarLabel is None:
                colorbarLabel = self._metadataKey
            else:
                colorbarLabel = self._colorbarLabel
            scalar_bar_args = dict(
                title=colorbarLabel,
            )

            self._interpolateValuesOntoMesh()

            if self._scalarsOpacityKey is not None:
                opacity = self._scalarsOpacityKey
            else:
                opacity = self._opacity

            self._actors[actorKey] = self._plotter.addMesh(mesh=self._mesh,
                                                            color=self._color,
                                                            nan_color=self._color,
                                                            scalars=self._scalarsKey,
                                                            scalar_bar_args=scalar_bar_args,
                                                            opacity=opacity,
                                                            specular=0.5,
                                                            diffuse=0.5,
                                                            ambient=0.5,
                                                            #smooth_shading=True,  # disabled since this breaks scalar value updates later
                                                            split_sharp_edges=True,
                                                            name=actorKey)

            #self._plotter.reset_scalar_bar_ranges([self._colorbarLabel])

            self._plotter.reset_camera_clipping_range()

            if False:
                # if not immmediately interpolating values above, need to do afterwards
                self._redraw(['updatePosition', 'interpolateValues'])
            else:
                self._redraw(['updatePosition'])

        elif which == 'queueInterpolateValues':
            self._needsReinterpolation.set()

        elif which == 'interpolateValues':

            if False:

                self._interpolateValuesOntoMesh()

                # just call update rather than completely re-adding the mesh to plotter
                with self._plotter.allowNonblockingCalls():
                    self._plotter.update_scalars(self._scalarsKey, mesh=self._mesh, render=True)
                    # TODO: also update opacity field
                    #self._plotter.reset_scalar_bar_ranges(scalarBarTitles=[self._colorbarLabel])
                    self._plotter.update()
            else:
                # current pyvista doesn't support dynamically changing color when a custom opacity is specified, so
                # need to fully re-add the mesh to the plotter

                actorKey = self._getActorKey('surf')

                assert actorKey in self._actors

                with self._plotter.allowNonblockingCalls():
                    self._plotter.remove_actor(self._actors[actorKey])
                    self._actors.pop(actorKey)

                if self._colorbarLabel is None:
                    colorbarLabel = self._metadataKey
                else:
                    colorbarLabel = self._colorbarLabel
                scalar_bar_args = dict(
                    title=colorbarLabel,
                )

                self._interpolateValuesOntoMesh()

                if self._scalarsOpacityKey is not None:
                    opacity = self._scalarsOpacityKey
                else:
                    opacity = self._opacity

                self._actors[actorKey] = self._plotter.addMesh(mesh=self._mesh,
                                                               color=self._color,
                                                               nan_color=self._color,
                                                               scalars=self._scalarsKey,
                                                               scalar_bar_args=scalar_bar_args,
                                                               opacity=opacity,
                                                               specular=0.5,
                                                               diffuse=0.5,
                                                               ambient=0.5,
                                                               # smooth_shading=True,  # disabled since this breaks scalar value updates later
                                                               split_sharp_edges=True,
                                                               name=actorKey)

                with self._plotter.allowNonblockingCalls():
                    # self._plotter.reset_scalar_bar_ranges([self._colorbarLabel])

                    self._plotter.reset_camera_clipping_range()

        else:
            super()._redraw(which=which)

    def _interpolateValuesOntoMesh(self):
        self._needsReinterpolation.clear()

        allSamples = self._coordinator.session.samples.values()
        includeSamples = [sample for sample in allSamples if sample.isVisible]  # include only samples marked as visible

        coords = np.full((len(includeSamples), 3), np.nan)

        match self._relevantSampleDepth:
            case 'coil':
                for iSample, sample in enumerate(includeSamples):
                    if sample.coilToMRITransf is None:
                        continue
                    coords[iSample, :] = sample.coilToMRITransf[:3, 3].T

            case 'entry':
                for iSample, sample in enumerate(includeSamples):
                    if sample.coilToMRITransf is None:
                        continue
                    if sample.targetKey is None:
                        continue
                    target = self._coordinator.session.targets[sample.targetKey]
                    if target.entryCoordPlusDepthOffset is None or target.entryCoord is None:
                        continue
                    coilToEntryDepth = np.linalg.norm(target.entryCoordPlusDepthOffset - target.entryCoord)
                    coords[iSample, :] = applyTransform(sample.coilToMRITransf, np.asarray([0, 0, -coilToEntryDepth]), doCheck=False)

            case 'intersection':
                for iSample, sample in enumerate(includeSamples):
                    # ray tracing takes a non-negligible amount of time, and results don't change for most samples between updates, so cache results
                    try:
                        coords[iSample, :] = self._cachedSampleIntersections[sample.key]
                    except KeyError:
                        pass  # no cached value available
                    else:
                        continue

                    if sample.coilToMRITransf is None:
                        continue
                    rayPoints_coilSpace = np.asarray([[0, 0, 0,], [0, 0, -100]])
                    rayPoints_mriSpace = applyTransform(sample.coilToMRITransf, rayPoints_coilSpace, doCheck=False)

                    intersectionPoints, intersectionCells = self._mesh.ray_trace(
                        origin=rayPoints_mriSpace[0, :],
                        end_point=rayPoints_mriSpace[1, :],
                        first_point=True)
                    if len(intersectionPoints) == 0:
                        continue  # no intersection
                    coords[iSample, :] = intersectionPoints

                    self._cachedSampleIntersections[sample.key] = coords[iSample, :]

            case 'target':
                for iSample, sample in enumerate(includeSamples):
                    if sample.coilToMRITransf is None:
                        continue
                    if sample.targetKey is None:
                        continue
                    target = self._coordinator.session.targets[sample.targetKey]
                    if target.entryCoordPlusDepthOffset is None or target.targetCoord is None:
                        continue
                    coilToTargetDepth = np.linalg.norm(target.entryCoordPlusDepthOffset - target.targetCoord)
                    coords[iSample, :] = applyTransform(sample.coilToMRITransf, np.asarray([0, 0, -coilToTargetDepth]), doCheck=False)

            case _:
                raise NotImplementedError

        # TODO: cache this point cloud between calls and just update iteratively on changes

        values = np.full((coords.shape[0],), np.nan)
        for iSample, sample in enumerate(includeSamples):
            values[iSample] = sample.metadata.get(self._metadataKey, np.nan)

        if True:
            # remove samples with nan values
            indices = np.where(np.isnan(values))
            if len(indices) > 0:
                coords = np.delete(coords, indices, axis=0)
                values = np.delete(values, indices, axis=0)

        if len(coords) == 0:
            # nothing to interpolate
            newVals = np.full((self._mesh.n_points,), np.nan)
        else:

            pointCloud = pv.PointSet(coords)
            pointCloud[self._scalarsKey] = values

            self._mesh.clear_data()  # not sure why this is necessary, but otherwise the interpolation doesn't work
            logger.debug(f'Interpolating point cloud values {self._metadataKey} onto surface {self._surfKey}')
            tmpMesh = self._mesh.interpolate(pointCloud,
                                             null_value=np.nan,
                                             sharpness=self._kernelSharpness,
                                             radius=self._kernelRadius
                                             )
            logger.debug(f'Done interpolating')

            if self._scalarsKey not in tmpMesh.point_data:
                # this can happen if all interpolated values were nan
                newVals = np.full((self._mesh.n_points,), np.nan)
            else:
                newVals = tmpMesh[self._scalarsKey]

        if self._scalarsKey not in self._mesh.point_data:
            self._mesh[self._scalarsKey] = newVals
        else:
            self._mesh[self._scalarsKey][:] = newVals  # update existing array

        if self._scalarsOpacityKey is not None:
            # set opacity to 0 for nan values
            opacityVals = np.where(np.isnan(self._mesh[self._scalarsKey]), 0, self._opacity)
            if self._scalarsOpacityKey not in self._mesh.point_data:
                self._mesh[self._scalarsOpacityKey] = opacityVals
            else:
                self._mesh[self._scalarsOpacityKey][:] = opacityVals  # update existing array

        #logger.debug(f'{np.isnan(self._mesh[self._scalarsKey]).sum()} / {self._mesh.n_points} points are nan')

    def _onSamplesChanged(self, changedKeys: tp.List[str], changedAttrs: tp.Optional[tp.List[str]]):
        if changedAttrs is not None:
            if 'isVisible' not in changedAttrs:
                if any(x in changedAttrs for x in ('coilToMRITransf', 'targetKey', 'metadata')):
                    anyIncludedSampleChanged = False
                    for sampleKey in changedKeys:
                        sample = self._coordinator.session.samples.get(sampleKey, None)
                        if sample is None:
                            continue
                        if sample.isVisible:
                            anyIncludedSampleChanged = True
                            break
                    if not anyIncludedSampleChanged:
                        # no attributes changed that would affect metadata interpolation
                        return
                else:
                    # no attributes changed that would affect metadata interpolation
                    return
        if len(self._cachedSampleIntersections) > 0:
            # clear relevant entries in cache
            for key in changedKeys:
                try:
                    self._cachedSampleIntersections.pop(key)
                except KeyError:
                    pass
        self._redraw(which='queueInterpolateValues')
