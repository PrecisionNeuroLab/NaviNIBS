from __future__ import annotations

from abc import ABC
import logging
import typing as tp
from typing import ClassVar

import attrs
import numpy as np
if tp.TYPE_CHECKING:
    from numpy import typing as npt

from NaviNIBS.Navigator.Model.GenericCollection import GenericCollectionDictItem, GenericCollection
if tp.TYPE_CHECKING:
    from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.util.attrs import attrsAsDict
from NaviNIBS.util.numpy import array_equalish, attrsWithNumpyAsDict


logger = logging.getLogger(__name__)


@attrs.define(kw_only=True)
class ROI(GenericCollectionDictItem[str], ABC):
    """
    Base class for regions of interest (ROIs).
    """
    type: ClassVar[str]
    _color: tuple[float, float, float] | tuple[float, float, float, float] | None = \
        attrs.field(default=None, converter=attrs.converters.optional(tuple))
    """
    RGB or RGBA color for displaying the ROI, with values in range [0, 1]
    """
    _autoColor: tuple[float, float, float] | None = attrs.field(init=False, default=None, repr=False)
    """
    If no color is specified, this field will be populated with an automatically chosen color.
    """
    _isVisible: bool = True

    _session: Session | None = attrs.field(repr=False, default=None)

    def __attrs_post_init__(self):
        pass

    @property
    def isVisible(self):
        return self._isVisible

    @isVisible.setter
    def isVisible(self, isVisible: bool):
        if self._isVisible == isVisible:
            return
        self.sigItemAboutToChange.emit(self._key, ['isVisible'])
        self._isVisible = isVisible
        self.sigItemChanged.emit(self._key, ['isVisible'])

    @property
    def color(self):
        return self._color

    @color.setter
    def color(self, newColor: tuple[float, float, float] | tuple[float, float, float, float] | None):
        if newColor is not None:
            if len(newColor) not in (3, 4):
                raise ValueError('Color must be a tuple of 3 (RGB) or 4 (RGBA) floats')
            for c in newColor:
                if not (0.0 <= c <= 1.0):
                    raise ValueError('Color values must be in range [0, 1]')

        if self._color == newColor:
            return

        logger.info(f'Setting ROI color to {newColor}')
        self.sigItemAboutToChange.emit(self._key, ['color'])
        self._color = newColor
        self.sigItemChanged.emit(self._key, ['color'])

    @property
    def autoColor(self):
        return self._autoColor

    @autoColor.setter
    def autoColor(self, newAutoColor: tuple[float, float, float] | None):
        if newAutoColor is not None:
            if len(newAutoColor) != 3:
                raise ValueError('Auto color must be a tuple of 3 (RGB) floats')
            for c in newAutoColor:
                if not (0.0 <= c <= 1.0):
                    raise ValueError('Auto color values must be in range [0, 1]')

        if self._autoColor == newAutoColor:
            return

        logger.info(f'Setting ROI autoColor to {newAutoColor}')
        self.sigItemAboutToChange.emit(self._key, ['autoColor'])
        self._autoColor = newAutoColor
        self.sigItemChanged.emit(self._key, ['autoColor'])

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, session: Session):
        if self._session is not session:
            self.sigItemAboutToChange.emit(self._key, ['session'])
            self._session = session
            self.sigItemChanged.emit(self._key, ['session'])

    def copy(self):
        d = self.asDict()
        d.pop('type')
        d['session'] = self._session
        return type(self).fromDict(d)

    def asDict(self) -> dict[str, tp.Any]:
        d = attrsAsDict(self, exclude=['session'])
        d['type'] = self.type
        return d


@attrs.define(kw_only=True)
class SurfaceMeshROI(ROI):
    type: ClassVar[str] = 'SurfaceMeshROI'
    _meshKey: str | None = None
    """
    Key identifying surface mesh in the session's head model on which this ROI is defined.
    """
    _meshVertexIndices: npt.NDArray[np.int64] | None = attrs.field(
        default=None,
        converter=lambda v: np.asarray(v, dtype=np.int64) if v is not None else v
    )
    """
    Indices of the vertices in the mesh identified by ``meshKey`` that belong to this ROI.
    """
    _seedCoord: tuple[float, float, float] | None = attrs.field(
        default=None,
        converter=attrs.converters.optional(tuple)
    )
    """
    Optional coordinate, typically representing approximate center or meaningfully representative 
    point of the ROI. For now, assumed to be defined in native (MRI) space.
    """

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def meshKey(self):
        return self._meshKey

    @meshKey.setter
    def meshKey(self, newMeshKey: str | None):
        if self._meshKey == newMeshKey:
            return

        logger.info(f'Setting SurfaceMeshROI meshKey to {newMeshKey}')
        self.sigItemAboutToChange.emit(self.key, ['meshKey'])
        self._meshKey = newMeshKey
        self.sigItemChanged.emit(self.key, ['meshKey'])

    @property
    def meshVertexIndices(self) -> npt.NDArray[np.int64] | None:
        return self._meshVertexIndices

    @meshVertexIndices.setter
    def meshVertexIndices(self, newMeshVertexIndices: npt.NDArray[np.int64] | None):
        if newMeshVertexIndices is not None:
            if len(newMeshVertexIndices) == 0:
                newMeshVertexIndices = None
            else:
                newMeshVertexIndices = np.asarray(newMeshVertexIndices, dtype=np.int64)

        if array_equalish(self._meshVertexIndices, newMeshVertexIndices):
            return

        logger.info(f'Setting SurfaceMeshROI meshVertexIndices to len({len(newMeshVertexIndices) if newMeshVertexIndices is not None else 0})')
        self.sigItemAboutToChange.emit(self.key, ['meshVertexIndices'])
        self._meshVertexIndices = newMeshVertexIndices
        self.sigItemChanged.emit(self.key, ['meshVertexIndices'])

    @property
    def seedCoord(self):
        return self._seedCoord

    @seedCoord.setter
    def seedCoord(self, newSeedCoord: tuple[float, float, float] | None):
        if self._seedCoord == newSeedCoord:
            return

        logger.info(f'Setting SurfaceMeshROI seedCoord to {newSeedCoord}')
        self.sigItemAboutToChange.emit(self.key, ['seedCoord'])
        self._seedCoord = newSeedCoord
        self.sigItemChanged.emit(self.key, ['seedCoord'])

    def asDict(self) -> dict[str, tp.Any]:
        d = attrsWithNumpyAsDict(self, npFields=['meshVertexIndices'], exclude=['session'])
        d['type'] = self.type
        return d


@attrs.define
class ROIs(GenericCollection[str, ROI]):
    _session: Session | None = attrs.field(default=None, repr=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self.sigItemsChanged.connect(self._setSessionOnItemsChanged)

    def _setSessionOnItemsChanged(self, keys: list[str], attribNames: list[str] | None = None):
        logger.debug(f'_setSessionOnItemsChanged called with keys: {keys}, attribNames: {attribNames}')
        if attribNames is None:
            for key in keys:
                if key in self:
                    self[key].session = self._session

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSession: Session | None):
        if self._session is newSession:
            return
        self._session = newSession
        self._setSessionOnItemsChanged(self.keys())

    @classmethod
    def fromList(cls, itemList: list[dict[str, tp.Any]], session: Session | None = None) -> ROIs:
        items = {}
        for itemDict in itemList:
            key = itemDict['key']
            itemDict['session'] = session
            items[key] = cls.roiFromDict(itemDict)

        return cls(items=items, session=session)

    @classmethod
    def roiFromDict(cls, roiDict: dict[str, tp.Any]) -> ROI:
        roiType = roiDict.pop('type')
        match roiType:
            case 'SurfaceMeshROI':
                ROICls = SurfaceMeshROI
            case 'AtlasSurfaceParcel':
                from NaviNIBS.Navigator.Model.ROIs.AtlasSurfaceParcel import AtlasSurfaceParcel
                ROICls = AtlasSurfaceParcel
            case 'PipelineROI':
                from NaviNIBS.Navigator.Model.ROIs.PipelineROI import PipelineROI
                ROICls = PipelineROI
            case _:
                raise ValueError(f'Unknown ROI type: {roiType}')
        return ROICls.fromDict(roiDict)
