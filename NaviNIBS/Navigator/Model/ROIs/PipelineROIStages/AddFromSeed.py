from __future__ import annotations

import logging
from typing import ClassVar

import attrs
import numpy as np

from NaviNIBS.Navigator.Model.ROIs import ROI, SurfaceMeshROI
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages import ROIStage


logger = logging.getLogger(__name__)


@attrs.define(eq=False)
class AddFromSeedPoint(ROIStage):
    type: ClassVar[str] = 'AddFromSeedPoint'
    _seedPoint: tuple[float, float, float] | None = attrs.field(default=None,
                                                                converter=attrs.converters.optional(tuple))
    """
    Seed point in 3D space from which to generate the ROI.
    """
    @_seedPoint.validator
    def _check_seedPoint(self, attribute, value):
        assert value is None or len(value) == 3

    _radius: float | None = None
    """
    Radius around the seed point to include in the ROI.
    """
    _distanceMetric: str = 'euclidean'
    """
    euclidean or geodesic
    """

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def seedPoint(self):
        return self._seedPoint

    @seedPoint.setter
    def seedPoint(self, newSeedPoint: tuple[float, float, float] | None):
        if newSeedPoint is not None:
            if isinstance(newSeedPoint, np.ndarray):
                newSeedPoint = newSeedPoint.tolist()
            if not isinstance(newSeedPoint, tuple):
                newSeedPoint = tuple(newSeedPoint)

        if self._seedPoint == newSeedPoint:
            return

        logger.info(f'Setting AddFromSeedPoint seedPoint to {newSeedPoint}')
        self.sigItemAboutToChange.emit(self, ['seedPoint'])
        self._seedPoint = newSeedPoint
        self.sigItemChanged.emit(self, ['seedPoint'])

    @property
    def radius(self):
        return self._radius

    @radius.setter
    def radius(self, newRadius: float | None):
        if self._radius == newRadius:
            return

        logger.info(f'Setting AddFromSeedPoint radius to {newRadius}')
        self.sigItemAboutToChange.emit(self, ['radius'])
        self._radius = newRadius
        self.sigItemChanged.emit(self, ['radius'])

    @property
    def distanceMetric(self):
        return self._distanceMetric

    def _process(self, roiKey: str, inputROI: ROI | None) -> SurfaceMeshROI:
        # Placeholder for actual ROI generation logic
        logger.debug(f'Generating ROI from seed point: {self._seedPoint} with radius {self._radius} using {self._distanceMetric} metric')
        if self._seedPoint is None:
            logger.warning('No seed point specified, returning input ROI unchanged')
            return inputROI
        if self._radius is None:
            logger.warning('No radius specified, returning input ROI unchanged')
            return inputROI
        assert isinstance(inputROI, SurfaceMeshROI)
        assert inputROI.meshKey is not None
        if self._session is None:
            logger.warning('No session available, returning input ROI unchanged')
            return inputROI
        mesh = getattr(self._session.headModel, inputROI.meshKey)

        outputROI = inputROI.copy()
        outputROI.session = self._session
        if outputROI.seedCoord is None:
            outputROI.seedCoord = self._seedPoint

        match self._distanceMetric:
            case 'euclidean':
                # find vertex indices within radius of seed point using euclidean distance
                dists = np.linalg.norm(mesh.points - np.asarray(self._seedPoint), axis=1)
                newVertexIndices = np.where(dists <= self._radius)[0]
                if inputROI.meshVertexIndices is not None:
                    newVertexIndices = np.union1d(inputROI.meshVertexIndices, newVertexIndices)
                if len(newVertexIndices) == 0:
                    newVertexIndices = None
                outputROI.meshVertexIndices = newVertexIndices

            case _:
                raise NotImplementedError(f'Distance metric {self._distanceMetric} not implemented')

        return outputROI


@attrs.define(eq=False)
class AddFromSeedLine(ROIStage):
    type: ClassVar[str] = 'AddFromSeedLines'
    _seedLine: list[tuple[float, float, float]] = attrs.field(factory=list)
    """
    List of seed line segments points, with at least 2 points to define a line.
    3 or more points will define multiple connected line segments.
    """
    _radius: float | None = None
    """
    Radius around the seed lines to include in the ROI.
    """
    _distanceMetric: str = 'euclidean'
    """
    euclidean or geodesic
    """

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _process(self, roiKey: str, inputROI: ROI | None) -> SurfaceMeshROI:
        logger.debug(f'Generating ROI from seed line with {len(self._seedLine)} points, radius {self._radius} using {self._distanceMetric} metric')
        assert isinstance(inputROI, SurfaceMeshROI)
        assert inputROI.meshKey is not None
        mesh = getattr(self._session.headModel, inputROI.meshKey)

        raise NotImplementedError  # TODO
