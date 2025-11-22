from __future__ import annotations

import shutil

import attrs
from datetime import datetime
import nibabel as nib
import json
import logging
import numpy as np
import os
import pandas as pd
import pyvista as pv
import tempfile
import typing as tp
from typing import ClassVar, TYPE_CHECKING

from NaviNIBS.util.attrs import attrsAsDict
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.numpy import array_equalish, attrsWithNumpyAsDict, attrsWithNumpyFromDict
if TYPE_CHECKING:
    from NaviNIBS.Navigator.Model.Session import Session

from NaviNIBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem
from NaviNIBS.Navigator.Model.Calculations import calculateAngleFromMidlineFromCoilToMRITransf, calculateCoilToMRITransfFromTargetEntryAngle, getClosestPointToPointOnMesh


logger = logging.getLogger(__name__)


@attrs.define
class Target(GenericCollectionDictItem[str]):
    """
    Can specify (targetCoord, entryCoord, angle, [depthOffset]) to autogenerate coilToMRITransf,
    or (coilToMRITransf, [targetCoord]) to use transform directly.
    """
    _targetCoord: tp.Optional[np.ndarray] = None
    _entryCoord: tp.Optional[np.ndarray] = None
    _angle: tp.Optional[float] = None
    """
    Typical coil handle angle, defined relative to "midline"
    """
    _depthOffset: tp.Optional[float] = None
    """
    Offset beyond entryCoord, e.g. due to EEG electrode thickness, coil foam
    """
    _coilToMRITransf: tp.Optional[np.ndarray] = None
    """
    Uses convention where -y axis is along handle of typical coil and -z axis is pointing down into the head; origin is bottom face center of coil
    """

    _isVisible: bool = True
    """
    Whether currently visible in views that render subsets of targets
    """
    _isHistorical: bool = False
    """
    If True, will be hidden in almost all views, but may be referenced by samples (e.g. used when a target with associated samples is edited with a new orientation, but the associated samples should still be connected to the old version of the target)
    """
    _mayBeADependency: bool = False
    """
    Can be used to mark (e.g. when an associated sample is created) that this target is a dependency for other data elements, and a copy should be created and stored in history if this is edited
    """
    _isSelected: bool = False
    _color: str = '#0000FF'

    _cachedCoilToMRITransf: tp.Optional[np.ndarray] = attrs.field(init=False, default=None)
    """
    If coilToMRITransf isn't manually set, it might be auto-calculated from other target parameters.
    """
    _cachedCoilAngle: tp.Optional[float] = attrs.field(init=False, default=None)
    """
    If angle isn't manually set, it might be auto-calculated from other target parameters.
    """

    _session: tp.Optional[Session] = attrs.field(default=None, repr=False)
    """
    Used to access head model for determining angle from coilToMRITransf and vice versa
    """

    @property
    def targetCoord(self):
        return self._targetCoord

    @targetCoord.setter
    def targetCoord(self, newTargetCoord: tp.Optional[np.ndarray]):
        if array_equalish(self._targetCoord, newTargetCoord):
            return

        attribsChanging = ['targetCoord', 'coilToMRITransf']
        newCoilToMRITransf = calculateCoilToMRITransfFromTargetEntryAngle(
            session=self.session,
            targetCoord=newTargetCoord,
            entryCoord=self._entryCoord,
            angle=self.angle,  # note this may be autocalculated based on previous coilToMRITransf
            depthOffset=self._depthOffset
        )
        self.sigItemAboutToChange.emit(self.key, attribsChanging)
        self._targetCoord = newTargetCoord
        self._coilToMRITransf = None  # previous transform is invalid with new target
        self._cachedCoilToMRITransf = newCoilToMRITransf
        self.sigItemChanged.emit(self.key, attribsChanging)

    @property
    def entryCoord(self):
        return self._entryCoord

    @entryCoord.setter
    def entryCoord(self, newEntryCoord: tp.Optional[np.ndarray]):
        if array_equalish(self._entryCoord, newEntryCoord):
            return

        attribsChanging = ['entryCoord', 'coilToMRITransf']
        newCoilToMRITransf = calculateCoilToMRITransfFromTargetEntryAngle(
            session=self.session,
            targetCoord=self._targetCoord,
            entryCoord=newEntryCoord,
            angle=self.angle,  # note this may be autocalculated based on previous coilToMRITransf
            depthOffset=self._depthOffset
        )
        self.sigItemAboutToChange.emit(self.key, attribsChanging)
        self._entryCoord = newEntryCoord
        self._coilToMRITransf = None  # previous transform is invalid with new entry
        self._cachedCoilToMRITransf = newCoilToMRITransf
        self.sigItemChanged.emit(self.key, attribsChanging)

    @property
    def angle(self):
        """
        Angle from midline, in degrees.

        Can be manually specified. If not specified, will be autocalculated
        based on coilToMRITransf (if available)
        """
        if self._angle is not None:
            return self._angle
        else:
            return self.calculatedAngle

    @angle.setter
    def angle(self, newAngle: tp.Optional[float]):
        if self._angle == newAngle:
            return

        logger.debug(f'Changing angle from {self._angle} to {newAngle}')

        attribsChanging = ['angle', 'coilToMRITransf']
        newCoilToMRITransf = calculateCoilToMRITransfFromTargetEntryAngle(
            session=self.session,
            targetCoord=self._targetCoord,
            entryCoord=self._entryCoord,
            angle=newAngle,
            depthOffset=self._depthOffset
        )
        logger.debug(f'newCoilToMRITransf: {newCoilToMRITransf}')

        self.sigItemAboutToChange.emit(self.key, attribsChanging)
        self._angle = newAngle
        self._coilToMRITransf = None  # previous transform is invalid with new angle
        self._cachedCoilToMRITransf = newCoilToMRITransf
        self.sigItemChanged.emit(self.key, attribsChanging)

    @property
    def calculatedAngle(self) -> float:
        """
        Angle from midline (in degrees), calculated from coilToMRITransf (if available)
        """
        if self._cachedCoilAngle is None:
            if self._coilToMRITransf is not None:
                self._cachedCoilAngle = calculateAngleFromMidlineFromCoilToMRITransf(self.session, self._coilToMRITransf)
            elif self._cachedCoilToMRITransf is not None:
                self._cachedCoilAngle = calculateAngleFromMidlineFromCoilToMRITransf(self.session, self._cachedCoilToMRITransf)
            if self._cachedCoilAngle is None:
                if self._angle is not None:
                    return self._angle
                else:
                    return 0

        return self._cachedCoilAngle

    @property
    def depthOffset(self) -> float:
        return self._depthOffset if self._depthOffset is not None else 0.

    @depthOffset.setter
    def depthOffset(self, newDepthOffset: tp.Optional[float]):
        if self._depthOffset == newDepthOffset:
            return

        attribsChanging = ['depthOffset', 'coilToMRITransf']
        newCoilToMRITransf = calculateCoilToMRITransfFromTargetEntryAngle(
            session=self.session,
            targetCoord=self._targetCoord,
            entryCoord=self._entryCoord,
            angle=self.angle,  # note this may be autocalculated based on previous coilToMRITransf
            depthOffset=newDepthOffset
        )

        self.sigItemAboutToChange.emit(self.key, attribsChanging)
        self._depthOffset = newDepthOffset
        self._coilToMRITransf = None  # previous transform is invalid with new depth offset
        self._cachedCoilToMRITransf = newCoilToMRITransf
        self.sigItemChanged.emit(self.key, attribsChanging)

    @property
    def entryCoordPlusDepthOffset(self) -> tp.Optional[np.ndarray]:
        if self._entryCoord is None or self._targetCoord is None:
            if self._coilToMRITransf is not None:
                return self._coilToMRITransf[:3, 3].T
            return None
        if self._depthOffset is None or self._depthOffset == 0:
            return self._entryCoord

        entryVec = self._entryCoord - self._targetCoord
        entryVec /= np.linalg.norm(entryVec)

        offsetVec = entryVec * self._depthOffset

        return self._entryCoord + offsetVec

    @property
    def coilToMRITransf(self):
        if self._coilToMRITransf is not None:
            return self._coilToMRITransf
        else:
            if self._cachedCoilToMRITransf is None:
                self._cachedCoilToMRITransf = calculateCoilToMRITransfFromTargetEntryAngle(
                    session=self._session,
                    targetCoord=self.targetCoord,
                    entryCoord=self.entryCoord,
                    angle=self.angle,
                    depthOffset=self.depthOffset)
            return self._cachedCoilToMRITransf

    @coilToMRITransf.setter
    def coilToMRITransf(self, newCoilToMRITransf: tp.Optional[np.ndarray]):
        """
        Note that this may be set in a way that conflicts with targetCoord, entryCoord, angle, and depthOffset.
        However, if any of those are changed after setting this, this transf will be reset and be replaced
        with a cached auto-calculated version.
        """
        if array_equalish(self._coilToMRITransf, newCoilToMRITransf):
            return
        self.sigItemAboutToChange.emit(self.key, ['coilToMRITransf'])
        self._coilToMRITransf = newCoilToMRITransf
        self.sigItemChanged.emit(self.key, ['coilToMRITransf'])

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
    def isHistorical(self):
        return self._isHistorical

    @isHistorical.setter
    def isHistorical(self, isHistorical: bool):
        if self._isHistorical == isHistorical:
            return
        self.sigItemAboutToChange.emit(self._key, ['isHistorical'])
        self._isHistorical = isHistorical
        self.sigItemChanged.emit(self._key, ['isHistorical'])

    @property
    def mayBeADependency(self):
        return self._mayBeADependency

    @mayBeADependency.setter
    def mayBeADependency(self, mayBeADependency: bool):
        if self._mayBeADependency == mayBeADependency:
            return
        self.sigItemAboutToChange.emit(self._key, ['mayBeADependency'])
        self._mayBeADependency = mayBeADependency
        self.sigItemChanged.emit(self._key, ['mayBeADependency'])

    @property
    def isSelected(self):
        return self._isSelected

    @isSelected.setter
    def isSelected(self, isSelected: bool):
        if self._isSelected == isSelected:
            return
        self.sigItemAboutToChange.emit(self.key, ['isSelected'])
        self._isSelected = isSelected
        self.sigItemChanged.emit(self.key, ['isSelected'])

    @property
    def color(self):
        return self._color

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, session: Session):
        if self._session is not session:
            prevSessionWasNone = self._session is None
            self._session = session
            if prevSessionWasNone and self.targetCoord is not None \
                    and self.entryCoord is None and self._coilToMRITransf is None:
                self.autosetEntryCoord()

    def autosetEntryCoord(self, offsetFromSkin: float | None = None):
        """
        Note: this assumes the target coordinate is far inside the scalp (e.g. on cortical surface, not on the scalp itself)
        """
        closestPt_skin = getClosestPointToPointOnMesh(
            session=self._session,
            whichMesh='skinConvexSurf',
            point_MRISpace=self.targetCoord)
        if closestPt_skin is None:
            raise ValueError('Missing information, cannot autoset entry coord')

        if offsetFromSkin is not None:
            directionVec = closestPt_skin - self.targetCoord
            directionVec /= np.linalg.norm(directionVec)
            closestPt_skin = closestPt_skin + directionVec * offsetFromSkin
            logger.debug(f'Adjusted entry coord by offset {offsetFromSkin}')

        logger.info(f'Autosetting entry info to {closestPt_skin}')

        self.entryCoord = closestPt_skin

    def asDict(self) -> tp.Dict[str, tp.Any]:
        return attrsWithNumpyAsDict(self, npFields=('targetCoord', 'entryCoord', 'coilToMRITransf'), exclude=('session',))

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any], session: Session | None = None):
        o = attrsWithNumpyFromDict(cls, d, npFields=('targetCoord', 'entryCoord', 'coilToMRITransf'))
        o.session = session

        if True:
            # auto-set entry coordinate based on target if entryCoord or coilToMRITransf not specified
            if o.targetCoord is not None and o.entryCoord is None and o._coilToMRITransf is None:
                if session is not None:
                    o.autosetEntryCoord()
                else:
                    logger.warning(f'No entry info for target {o.key}, no session info with which to autoset entry coord')

        return o


@attrs.define
class Targets(GenericCollection[str, Target]):
    _session: Session | None = attrs.field(default=None, repr=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self.sigItemsChanged.connect(self._setSessionOnItemsChanged)

    def setWhichTargetsVisible(self, visibleKeys: tp.List[str]):
        self.setAttribForItems(self.keys(), dict(isVisible=[key in visibleKeys for key in self.keys()]))

    def setWhichTargetsSelected(self, selectedKeys: tp.List[str]):
        logger.debug(f'setWhichTargetsSelected: {selectedKeys}')
        self.setAttribForItems(self.keys(), dict(isSelected=[key in selectedKeys for key in self.keys()]))

    def _setSessionOnItemsChanged(self, keys: list[str], attribNames: list[str] | None = None):
        if attribNames is None:
            for key in keys:
                if key in self:
                    self[key].session = self._session

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, session: Session):
        if self._session is not session:
            self._session = session
            self._setSessionOnItemsChanged(self.keys())

    @classmethod
    def fromList(cls, itemList: list[dict[str, tp.Any]], session: Session | None = None) -> Targets:
        items = {}
        for itemDict in itemList:
            items[itemDict['key']] = Target.fromDict(itemDict, session=session)

        return cls(items=items, session=session)

