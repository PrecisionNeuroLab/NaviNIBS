from __future__ import annotations

import logging
import typing as tp
from typing import ClassVar

import attrs

from NaviNIBS.Navigator.Model.GenericCollection import GenericList
from NaviNIBS.Navigator.Model.ROIs import ROI
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages import ROIStage, PassthroughStage, SelectSurfaceMesh, SurfaceMeshROI
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages.AddFromSeed import AddFromSeedPoint, AddFromSeedLine
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages.AddFromTarget import AddFromTarget
if tp.TYPE_CHECKING:
    from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.util.attrs import attrsAsDict


logger = logging.getLogger(__name__)


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
                    AddFromTarget,
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

        self._stages.sigItemsChanged.connect(self._onStagesChanged)

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

    def _onStagesChanged(self, stageKeys: list[str], whichAttrs: list[str] | None):
        if whichAttrs is not None and len(whichAttrs) == 1 and 'session' in whichAttrs:
            # don't need to signal about this change
            return
        self.sigItemChanged.emit(self.key, ['stages'])

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

