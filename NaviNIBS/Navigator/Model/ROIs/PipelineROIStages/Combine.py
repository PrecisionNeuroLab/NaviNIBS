from __future__ import annotations

from abc import ABC
import logging
from typing import ClassVar

import attrs
import numpy as np

from NaviNIBS.Navigator.Model.GenericCollection import listItemAttrSetter
from NaviNIBS.Navigator.Model.ROIs import ROI, SurfaceMeshROI
from NaviNIBS.Navigator.Model.ROIs.PipelineROI import PipelineROI
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages import ROIStage


logger = logging.getLogger(__name__)


@attrs.define(eq=False)
class Combine(ROIStage, ABC):
    type: ClassVar[str] = 'Combine'

    _roiKeys: list[str | None] = attrs.field(factory=list)
    """
    Keys of the ROIs to combine. If an entry is None, the input to this ROIStage will be used instead.
    If no entries are None, the inputROI to this stage will be ignored.
    """

    _minNumROIs: ClassVar[int | None] = None
    _maxNumROIs: ClassVar[int | None] = None

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def roiKeys(self) -> list[str | None]:
        return list(self._roiKeys)

    @roiKeys.setter
    @listItemAttrSetter()
    def roiKeys(self, newRoiKeys: list[str | None]):
        if self._minNumROIs is not None and len(newRoiKeys) < self._minNumROIs:
            raise ValueError(f'{self.type} requires at least {self._minNumROIs} ROI keys, got {len(newRoiKeys)}')
        if self._maxNumROIs is not None and len(newRoiKeys) > self._maxNumROIs:
            raise ValueError(f'{self.type} allows at most {self._maxNumROIs} ROI keys, got {len(newRoiKeys)}')

    def _resolveROIs(self, inputROI: ROI | None) -> list[SurfaceMeshROI]:
        if self._session is None:
            logger.warning('No session available, cannot resolve roiKeys')
        resolved = []
        for key in self._roiKeys:
            if key is None:
                if inputROI is not None:
                    assert isinstance(inputROI, SurfaceMeshROI)
                    resolved.append(inputROI)
            else:
                if self._session is not None and key in self._session.ROIs:
                    roi = self._session.ROIs[key]
                    if isinstance(roi, PipelineROI):
                        # resolve pipeline ROI to its output
                        roi = roi.getOutput()
                    
                    assert isinstance(roi, SurfaceMeshROI), \
                            f'ROI {key!r} must be a SurfaceMeshROI for Combine stages'
                    resolved.append(roi)
                else:
                    logger.warning(f'ROI key {key!r} not found in session, skipping')
        # All resolved ROIs must share the same meshKey (output is on a single surface)
        meshKeys = {roi.meshKey for roi in resolved}
        assert len(meshKeys) <= 1, \
            f'All combined ROIs must be on the same surface mesh, got meshKeys: {meshKeys}'
        return resolved

    def _process(self, roiKey: str, inputROI: ROI | None) -> ROI | None:
        raise NotImplementedError('_process must be implemented in subclasses')


@attrs.define(eq=False)
class Intersect(Combine):
    type: ClassVar[str] = 'Intersect'

    _minNumROIs: ClassVar[int | None] = 0
    _maxNumROIs: ClassVar[int | None] = None

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _process(self, roiKey: str, inputROI: ROI | None) -> SurfaceMeshROI | None:
        rois = self._resolveROIs(inputROI)
        if not rois:
            if inputROI is not None:
                assert isinstance(inputROI, SurfaceMeshROI)
            return inputROI
        outputROI = rois[0].copy()
        if self._session is not None:
            outputROI.session = self._session
        indices = rois[0].meshVertexIndices
        for roi in rois[1:]:
            if indices is None or roi.meshVertexIndices is None:
                indices = None
                break
            indices = np.intersect1d(indices, roi.meshVertexIndices)
            if len(indices) == 0:
                indices = None
                break
        outputROI.meshVertexIndices = indices
        return outputROI


@attrs.define(eq=False)
class Union(Combine):
    type: ClassVar[str] = 'Union'

    _minNumROIs: ClassVar[int | None] = 0
    _maxNumROIs: ClassVar[int | None] = None

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _process(self, roiKey: str, inputROI: ROI | None) -> SurfaceMeshROI | None:
        rois = self._resolveROIs(inputROI)
        if not rois:
            if inputROI is not None:
                assert isinstance(inputROI, SurfaceMeshROI)
            return inputROI
        outputROI = rois[0].copy()
        if self._session is not None:
            outputROI.session = self._session
        all_indices = [roi.meshVertexIndices for roi in rois if roi.meshVertexIndices is not None]
        if not all_indices:
            indices = None
        else:
            indices = all_indices[0]
            for idx in all_indices[1:]:
                indices = np.union1d(indices, idx)
            if len(indices) == 0:
                indices = None
        outputROI.meshVertexIndices = indices
        return outputROI


@attrs.define(eq=False)
class Difference(Combine):
    type: ClassVar[str] = 'Difference'

    _minNumROIs: ClassVar[int | None] = 0
    _maxNumROIs: ClassVar[int | None] = 2

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _process(self, roiKey: str, inputROI: ROI | None) -> SurfaceMeshROI | None:
        rois = self._resolveROIs(inputROI)
        if not rois:
            if inputROI is not None:
                assert isinstance(inputROI, SurfaceMeshROI)
            return inputROI
        outputROI = rois[0].copy()
        if self._session is not None:
            outputROI.session = self._session
        if len(rois) < 2 or rois[0].meshVertexIndices is None:
            outputROI.meshVertexIndices = rois[0].meshVertexIndices
            return outputROI
        if rois[1].meshVertexIndices is None:
            outputROI.meshVertexIndices = rois[0].meshVertexIndices.copy()
        else:
            indices = np.setdiff1d(rois[0].meshVertexIndices, rois[1].meshVertexIndices)
            outputROI.meshVertexIndices = indices if len(indices) > 0 else None
        return outputROI
