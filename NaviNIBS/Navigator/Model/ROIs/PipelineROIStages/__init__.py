from __future__ import annotations

from abc import ABC
import logging
import typing as tp
from typing import ClassVar

import attrs

from NaviNIBS.Navigator.Model.GenericCollection import GenericListItem
from NaviNIBS.Navigator.Model.ROIs import ROI, SurfaceMeshROI
if tp.TYPE_CHECKING:
    from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.util.attrs import attrsAsDict


logger = logging.getLogger(__name__)


@attrs.define(eq=False)
class ROIStage(GenericListItem, ABC):
    """
    Base class for stages in an ROI assembly pipeline.
    """
    type: ClassVar[str]
    _label: str | None = None

    _session: Session | None = attrs.field(repr=False, default=None)

    def __attrs_post_init__(self):
        pass

    @property
    def label(self):
        return self._label if self._label is not None else self.type

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSession: Session | None):
        if self._session is newSession:
            return
        self.sigItemAboutToChange.emit(self, ['session'])
        self._session = newSession
        self.sigItemChanged.emit(self, ['session'])

    def _process(self, roiKey: str, inputROI: ROI | None) -> ROI:
        raise NotImplementedError('_process must be implemented in subclasses')

    def process(self, roiKey: str, inputROI: ROI | None) -> ROI | None:
        logger.debug(f'Starting ROIStage processing: {self}')
        ROI = self._process(roiKey=roiKey, inputROI=inputROI)
        logger.debug(f'Finished ROIStage processing: {self}')
        return ROI

    def asDict(self) -> dict[str, tp.Any]:
        d = attrsAsDict(self, exclude=('session',))
        d['type'] = self.type
        return d


@attrs.define(eq=False)
class PassthroughStage(ROIStage):
    type: ClassVar[str] = 'Passthrough'

    def _process(self, roiKey: str, inputROI: ROI | None) -> ROI | None:
        return inputROI


@attrs.define(eq=False)
class SelectSurfaceMesh(ROIStage):
    type: ClassVar[str] = 'SelectSurfaceMesh'
    _meshKey: str | None = None

    @property
    def meshKey(self):
        return self._meshKey

    @meshKey.setter
    def meshKey(self, newMeshKey: str | None):
        if self._meshKey == newMeshKey:
            return
        logger.info(f'Setting SelectSurfaceMesh meshKey to {newMeshKey}')
        self.sigItemAboutToChange.emit(self, ['meshKey'])
        self._meshKey = newMeshKey
        self.sigItemChanged.emit(self, ['meshKey'])

    def _process(self, roiKey: str, inputROI: ROI | None) -> SurfaceMeshROI:
        assert inputROI is None

        # create ROI with empty indices
        return SurfaceMeshROI(
            key=roiKey,
            meshKey=self._meshKey
        )


