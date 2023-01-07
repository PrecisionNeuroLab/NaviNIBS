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
from typing import ClassVar

from RTNaBS.util.attrs import attrsAsDict
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.numpy import array_equalish, attrsWithNumpyAsDict, attrsWithNumpyFromDict

from RTNaBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem


logger = logging.getLogger(__name__)


@attrs.define
class Target(GenericCollectionDictItem[str]):
    """
    Can specify (targetCoord, entryCoord, angle, [depthOffset]) to autogenerate coilToMRITransf,
    or (coilToMRITransf, [targetCoord]) to use transform directly.
    """
    _targetCoord: tp.Optional[np.ndarray] = None
    _entryCoord: tp.Optional[np.ndarray] = None
    _angle: tp.Optional[float] = None  # typical coil handle angle, in coil's horizontal plane
    _depthOffset: tp.Optional[float] = None  # offset beyond entryCoord, e.g. due to EEG electrode thickness, coil foam
    _coilToMRITransf: tp.Optional[np.ndarray] = None  # uses convention when -y axis is along handle of typical coil and -z axis is pointing down into the head; origin is bottom face center of coil

    _isVisible: bool = True
    _isSelected: bool = False
    _color: str = '#0000FF'

    _cachedCoilToMRITransf: tp.Optional[np.ndarray] = attrs.field(init=False, default=None)

    @property
    def targetCoord(self):
        return self._targetCoord

    @property
    def entryCoord(self):
        return self._entryCoord

    @property
    def angle(self):
        return self._angle if self._angle is not None else 0

    @property
    def depthOffset(self) -> float:
        return self._depthOffset if self._depthOffset is not None else 0.

    @property
    def entryCoordPlusDepthOffset(self) -> tp.Optional[np.ndarray]:
        if self._entryCoord is None or self._targetCoord is None:
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
                raise NotImplementedError()  # TODO: generate transform from target, entry, angle, offset
                self._cachedCoilToMRITransf = 'todo'
            return self._cachedCoilToMRITransf

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
    def isSelected(self):
        return self._isSelected

    @isSelected.setter
    def isSelected(self, isSelected: bool):
        if self._isSelected == isSelected:
            return
        self.sigItemAboutToChange.emit(self.key, ['isSelected'])
        self._isSelected = isSelected
        self.sigItemChanged.emit(self.key, ['isSelected'])

    def asDict(self) -> tp.Dict[str, tp.Any]:
        return attrsWithNumpyAsDict(self, npFields=('targetCoord', 'entryCoord', 'coilToMRITransf'))

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]):
        return attrsWithNumpyFromDict(cls, d, npFields=('targetCoord', 'entryCoord', 'coilToMRITransf'))


@attrs.define
class Targets(GenericCollection[str, Target]):
    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def setWhichTargetsVisible(self, visibleKeys: tp.List[str]):
        self.setAttribForItems(self.keys(), dict(isVisible=[key in visibleKeys for key in self.keys()]))

    def setWhichTargetsSelected(self, selectedKeys: tp.List[str]):
        self.setAttribForItems(self.keys(), dict(isSelected=[key in selectedKeys for key in self.keys()]))

    @classmethod
    def fromList(cls, itemList: list[dict[str, tp.Any]]) -> Targets:
        items = {}
        for itemDict in itemList:
            items[itemDict['key']] = Target.fromDict(itemDict)

        return cls(items=items)
