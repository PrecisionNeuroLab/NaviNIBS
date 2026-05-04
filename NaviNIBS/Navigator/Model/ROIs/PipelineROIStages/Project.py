from __future__ import annotations

import logging
from typing import ClassVar

import attrs
import numpy as np

from NaviNIBS.Navigator.Model.GenericCollection import listItemAttrSetter
from NaviNIBS.Navigator.Model.ROIs import ROI, SurfaceMeshROI
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages import ROIStage


logger = logging.getLogger(__name__)


@attrs.define(eq=False)
class ProjectBetweenSurfaces(ROIStage):
    type: ClassVar[str] = 'ProjectBetweenSurfaces'

    _toSurfaceKey: str | None = None
    
    def __attrs_post_init__(self):
        super().__attrs_post_init__()
    
    @property
    def toSurfaceKey(self) -> str | None:
        return self._toSurfaceKey

    @toSurfaceKey.setter
    @listItemAttrSetter()
    def toSurfaceKey(self, newToSurfaceKey: str | None):
        pass

    def _process(self, roiKey: str, inputROI: ROI | None) -> ROI | None:
        from scipy.spatial import cKDTree  # type: ignore[import-untyped]

        if self._toSurfaceKey is None:
            logger.warning('No toSurfaceKey specified, returning input ROI unchanged')
            return inputROI
        if self._session is None:
            logger.warning('No session available, returning input ROI unchanged')
            return inputROI
        assert isinstance(inputROI, SurfaceMeshROI)
        assert inputROI.meshKey is not None

        if inputROI.meshKey == self._toSurfaceKey:
            logger.info('Input ROI is already on the target surface, skipping projection')
            return inputROI

        originMesh = getattr(self._session.headModel, inputROI.meshKey)
        destMesh = getattr(self._session.headModel, self._toSurfaceKey)

        outputROI = inputROI.copy()
        outputROI.session = self._session
        outputROI.meshKey = self._toSurfaceKey
        outputROI.seedCoord = None  # seedCoord was on origin surface; clear it

        if inputROI.meshVertexIndices is None or len(inputROI.meshVertexIndices) == 0:
            outputROI.meshVertexIndices = None
            return outputROI

        # For each dest vertex, find nearest origin vertex and inherit membership
        tree = cKDTree(originMesh.points)
        _, nearestOriginIndices = tree.query(destMesh.points, workers=-1)
        inROI = np.isin(nearestOriginIndices, inputROI.meshVertexIndices)
        destVertexIndices = np.where(inROI)[0].astype(np.int64)

        outputROI.meshVertexIndices = destVertexIndices if len(destVertexIndices) > 0 else None
        return outputROI

    