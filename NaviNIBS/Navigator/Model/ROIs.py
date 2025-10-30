from __future__ import annotations

from abc import ABC
import enum
import logging
import typing as tp
from typing import TYPE_CHECKING, ClassVar

import attrs
import numpy as np

if TYPE_CHECKING:
    from NaviNIBS.Navigator.Model.Session import Session
    import numpy.typing as npt

from NaviNIBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem, GenericListItem, GenericList
from NaviNIBS.util.attrs import attrsAsDict
from NaviNIBS.util.numpy import attrsWithNumpyAsDict, array_equalish

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



@attrs.define(kw_only=True)
class ROI(GenericCollectionDictItem[str], ABC):
    """
    Base class for regions of interest (ROIs).
    """
    type: ClassVar[str]
    _color: tuple[float, float, float] | tuple[float, float, float, float] | None = \
        attrs.field(init=False, default=None, converter=attrs.converters.optional(tuple))
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

class _EmptyCache:
    pass

_emptyCache = _EmptyCache()


@attrs.define(kw_only=True)
class PipelineROI(ROI):

    @attrs.define
    class PipelineStages(GenericList[ROIStage]):
        _stageLibrary: dict[str, type[ROIStage]] = attrs.field(init=False, factory=dict)

        _session: Session | None = attrs.field(default=None, repr=False)

        def __attrs_post_init__(self):
            super().__attrs_post_init__()
            self.sigItemsChanged.connect(self._setSessionOnItemsChanged)

            for cls in (
                    PassthroughStage,
                    SelectSurfaceMesh,
                    AddFromSeedPoint,
                    AddFromSeedLine,
            ):
                self._stageLibrary[cls.type] = cls

        @property
        def stageLibrary(self):
            return self._stageLibrary

        @property
        def session(self):
            return self._session

        @session.setter
        def session(self, session: Session):
            if self._session is not session:
                self._session = session
                self._setSessionOnItemsChanged(self._items)

        def _setSessionOnItemsChanged(self, items: list[ROIStage], attribNames: list[str] | None = None):
            logger.debug(f'_setSessionOnItemsChanged called with items: {items}, attribNames: {attribNames}')
            if attribNames is None:
                for item in items:
                    if item in self:
                        item.session = self._session

        @classmethod
        def fromList(cls, itemList: list[dict[str, tp.Any]], session: Session | None = None) -> PipelineROI.PipelineStages:
            items = []
            for itemDict in itemList:
                try:
                    stageType = itemDict.pop('type')
                except KeyError:
                    raise ValueError('ROIStage dict missing "type" field')
                if stageType not in cls().stageLibrary:
                    raise ValueError(f'Unknown ROIStage type: {stageType}')
                StageCls = cls().stageLibrary[stageType]
                assert issubclass(StageCls, ROIStage)
                itemDict['session'] = session
                items.append(StageCls.fromDict(itemDict))

            return cls(items=items, session=session)

    type: ClassVar[str] = 'PipelineROI'

    _cachedOutput: ROI | _EmptyCache = attrs.field(init=False, default=_emptyCache)

    _stages: PipelineStages = attrs.field(factory=PipelineStages)

    _session: Session | None = attrs.field(repr=False, default=None)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._stages.sigItemsAboutToChange.connect(lambda stageKeys, whichAttrs=None: \
                self.sigItemAboutToChange.emit(self.key, ['stages']))

        self._stages.sigItemsChanged.connect(lambda stageKeys, whichAttrs=None: \
                self.sigItemChanged.emit(self.key, ['stages']))

        self._stages.sigItemsChanged.connect(lambda *args: self.clearCache(), priority=1)

        self.sigItemChanged.connect(self._onSelfChanged, priority=2)

    @property
    def stages(self):
        return self._stages

    @property
    def stageLibrary(self):
        return self._stages.stageLibrary

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSession: Session | None):
        if self._session is newSession:
            return
        self.sigItemAboutToChange.emit(self.key, ['session'])
        self._session = newSession
        self._stages.session = newSession
        self.sigItemChanged.emit(self.key, ['session'])

    def process(self, upThroughStage: int | None = None):
        logger.debug(f'Processing PipelineROI {self.key}{f" up through stage {upThroughStage}" if upThroughStage is not None else ""}')

        if upThroughStage is None:
            self.sigItemAboutToChange.emit(self.key, ['output'])

        if len(self._stages) == 0:
            logger.info(f'PipelineROI {self.key} has no stages')
            ROI = SurfaceMeshROI(key=self.key)
        else:
            ROI = None
            for iStage, stage in enumerate(self._stages):
                logger.debug(f'Processing ROI stage {iStage}: {stage}')
                ROI = stage.process(roiKey=f'{iStage}', inputROI=ROI)
                if upThroughStage is not None and iStage == upThroughStage:
                    return ROI

        if ROI is not None:
            # apply some fields from parent to child if not set
            vars = ['color', 'autoColor']
            for var in vars:
                if getattr(ROI, var) is None:
                    setattr(ROI, var, getattr(self, var))
        else:
            pass

        self._cachedOutput = ROI

        self.sigItemChanged.emit(self.key, ['output'])

    def _onSelfChanged(self, key: str, changedAttrs: list[str] | None = None):
        if changedAttrs is None \
                or 'color' in changedAttrs \
                or 'autoColor' in changedAttrs:
            # since colors are copied to output during processing, need to refresh if they change
            self.clearCache()

    def getOutput(self) -> ROI | None:
        if self._cachedOutput is _emptyCache:
            self.process()
        assert not self._cachedOutput is _emptyCache
        return self._cachedOutput

    def clearCache(self):
        logger.debug(f'Clearing cached output for PipelineROI {self.key}')
        self._cachedOutput = _emptyCache

    def asDict(self) -> dict[str, tp.Any]:
        d = attrsAsDict(self, exclude=('stages', 'session'))
        d['type'] = self.type
        d['stages'] = self._stages.asList()
        return d

    @classmethod
    def fromDict(cls, d: dict[str, tp.Any]):
        stagesList = d.pop('stages')
        session = d.get('session', None)
        stages = cls.PipelineStages.fromList(stagesList, session=session)
        d['stages'] = stages
        roi = cls(**d)
        return roi


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
            case 'PipelineROI':
                ROICls = PipelineROI
            case _:
                raise ValueError(f'Unknown ROI type: {roiType}')
        return ROICls.fromDict(roiDict)
