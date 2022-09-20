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

from RTNaBS.util.Signaler import Signal
from RTNaBS.util.numpy import array_equalish
from RTNaBS.util.attrs import attrsAsDict


logger = logging.getLogger(__name__)


@attrs.define
class Target:
    """
    Can specify (targetCoord, entryCoord, angle, [depthOffset]) to autogenerate coilToMRITransf,
    or (coilToMRITransf, [targetCoord]) to use transform directly.
    """
    _key: str
    _targetCoord: tp.Optional[np.ndarray] = None
    _entryCoord: tp.Optional[np.ndarray] = None
    _angle: tp.Optional[float] = None  # typical coil handle angle, in coil's horizontal plane
    _depthOffset: tp.Optional[float] = None  # offset beyond entryCoord, e.g. due to EEG electrode thickness, coil foam
    _coilToMRITransf: tp.Optional[np.ndarray] = None  # uses convention when -y axis is along handle of typical coil and -z axis is pointing down into the head; origin is bottom face center of coil

    _isVisible: bool = True
    _isSelected: bool = False
    _color: str = '#0000FF'

    _cachedCoilToMRITransf: tp.Optional[np.ndarray] = attrs.field(init=False, default=None)

    sigKeyAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((str, str)))  # includes old key, new key
    sigKeyChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str, str)))  # includes old key, new key

    sigTargetAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((str, tp.Optional[tp.List[str]])))
    """
    This signal includes the key of the target, and optionally a list of keys of attributes about to change;
    if second arg is None, all attributes should be assumed to be about to change.
    
    Not emitted when key changed (use sigKeyAboutToChange instead!)
    """
    sigTargetChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str, tp.Optional[tp.List[str]])))
    """
    This signal includes the key of the target, and optionally a list of keys of changed attributes;  
    if second arg is None, all attributes should be assumed to have changed.
    
    Not emitted when key changed (use sigKeyChanged instead!)
    """

    @property
    def key(self):
        return self._key

    @key.setter
    def key(self, newKey: str):
        if self._key == newKey:
            return
        prevKey = self._key
        self.sigKeyAboutToChange.emit(prevKey, newKey)
        self._key = newKey
        self.sigKeyChanged.emit(prevKey, newKey)

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
        self.sigTargetAboutToChange.emit(self._key, ['isVisible'])
        self._isVisible = isVisible
        self.sigTargetChanged.emit(self._key, ['isVisible'])

    @property
    def isSelected(self):
        return self._isSelected

    @isSelected.setter
    def isSelected(self, isSelected: bool):
        if self._isSelected == isSelected:
            return
        self.sigTargetAboutToChange.emit(self.key, ['isSelected'])
        self._isSelected = isSelected
        self.sigTargetChanged.emit(self.key, ['isSelected'])

    def asDict(self) -> tp.Dict[str, tp.Any]:

        npFields = ('targetCoord', 'entryCoord', 'coilToMRITransf')

        d = attrsAsDict(self, eqs={field: array_equalish for field in npFields})

        for key in npFields:
            if key in d and d[key] is not None:
                d[key] = d[key].tolist()

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]):

        def convertOptionalNDArray(val: tp.Optional[tp.List[tp.Any]]) -> tp.Optional[np.ndarray]:
            if val is None:
                return None
            else:
                return np.asarray(val)

        for attrKey in ('targetCoord', 'entryCoord', 'coilToMRITransf'):
            if attrKey in d:
                d[attrKey] = convertOptionalNDArray(d[attrKey])

        return cls(**d)


@attrs.define
class Targets:
    _targets: tp.Dict[str, Target] = attrs.field(factory=dict)

    sigTargetsAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((tp.List[str], tp.Optional[tp.List[str]])))
    """
    This signal includes list of keys of targets about to change, and optionally a list of keys of attributes about to change;  
    if second arg is None, all attributes should be assumed to be about to change.
    """
    sigTargetsChanged: Signal = attrs.field(init=False, factory=lambda: Signal((tp.List[str], tp.Optional[tp.List[str]])))
    """
    This signal includes list of keys of changed targets, and optionally a list of keys of changed attributes;  
    if second arg is None, all attributes should be assumed to have changed.
    """

    sigTargetKeyAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((str, str)))
    sigTargetKeyChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str, str)))
    """
    Emitted in addition to sigTargetsAboutToChange and sigTargetsChanged specifically when target key changes.
    
    This is because when a key does change, everything else about a target may have changed, so these other signals don't include a list of attributes indicating the source of the change.
    """

    def __attrs_post_init__(self):
        for key, target in self._targets.items():
            assert target.key == key
            target.sigTargetAboutToChange.connect(self._onTargetAboutToChange)
            target.sigTargetChanged.connect(self._onTargetChanged)
            target.sigKeyAboutToChange.connect(self._onTargetKeyAboutToChange)
            target.sigKeyChanged.connect(self._onTargetKeyChanged)

    def addTarget(self, target: Target):
        assert target.key not in self._targets
        return self.setTarget(target=target)

    def deleteTarget(self, key: str):
        raise NotImplementedError()  # TODO

    def setTarget(self, target: Target):
        self.sigTargetsAboutToChange.emit([target.key], None)
        if target.key in self._targets:
            self._targets[target.key].sigTargetAboutToChange.disconnect(self._onTargetAboutToChange)
            self._targets[target.key].sigKeyAboutToChange.disconnect(self._onTargetKeyAboutToChange)
            self._targets[target.key].sigKeyChanged.disconnect(self._onTargetKeyChanged)
            self._targets[target.key].sigTargetChanged.disconnect(self._onTargetChanged)
        self._targets[target.key] = target

        target.sigTargetAboutToChange.connect(self._onTargetAboutToChange)
        target.sigKeyAboutToChange.connect(self._onTargetKeyAboutToChange)
        target.sigKeyChanged.connect(self._onTargetKeyChanged)
        target.sigTargetChanged.connect(self._onTargetChanged)

        self.sigTargetsChanged.emit([target.key], None)

    def setTargets(self, targets: tp.List[Target]):
        # assume all keys are changing, though we could do comparisons to find subset changed
        oldKeys = list(self.targets.keys())
        newKeys = [target.key for target in targets]
        combinedKeys = list(set(oldKeys) | set(newKeys))
        self.sigTargetsAboutToChange.emit(combinedKeys, None)
        for key in oldKeys:
            self._targets[key].sigTargetAboutToChange.disconnect(self._onTargetAboutToChange)
            self._targets[key].sigKeyAboutToChange.disconnect(self._onTargetKeyAboutToChange)
            self._targets[key].sigKeyChanged.disconnect(self._onTargetKeyChanged)
            self._targets[key].sigTargetChanged.disconnect(self._onTargetChanged)
        self._targets = {target.key: target for target in targets}
        for key, target in self._targets.items():
            self._targets[key].sigTargetAboutToChange.connect(self._onTargetAboutToChange)
            self._targets[key].sigKeyAboutToChange.connect(self._onTargetKeyAboutToChange)
            self._targets[key].sigKeyChanged.connect(self._onTargetKeyChanged)
            self._targets[key].sigTargetChanged.connect(self._onTargetChanged)
        self.sigTargetsChanged.emit(combinedKeys, None)

    def _onTargetAboutToChange(self, key: str, attribKeys: tp.Optional[tp.List[str]] = None):
        self.sigTargetsAboutToChange.emit([key], attribKeys)

    def _onTargetKeyAboutToChange(self, fromKey: str, toKey: str):
        assert toKey not in self._targets
        self.sigTargetKeyAboutToChange.emit(fromKey, toKey)
        self.sigTargetsAboutToChange.emit([fromKey, toKey], None)

    def _onTargetKeyChanged(self, fromKey: str, toKey: str):
        assert toKey not in self._targets
        self._targets = {(toKey if key == fromKey else key): val for key, val in self._targets.items()}
        self.sigTargetsChanged.emit([fromKey, toKey], None)
        self.sigTargetKeyChanged.emit(fromKey, toKey)

    def _onTargetChanged(self, key: str, attribKeys: tp.Optional[tp.List[str]] = None):
        self.sigTargetsChanged.emit([key], attribKeys)

    def setWhichTargetsVisible(self, visibleKeys: tp.List[str]):
        changingKeys = [key for key, target in self.targets.items() if target.isVisible != (key in visibleKeys)]
        if len(changingKeys) == 0:
            return
        self.sigTargetsAboutToChange.emit(changingKeys, ['isVisible'])
        with self.sigTargetsAboutToChange.blocked(), self.sigTargetsChanged.blocked():
            for key in changingKeys:
                self.targets[key].isVisible = key in visibleKeys
        self.sigTargetsChanged.emit(changingKeys, ['isVisible'])

    def setWhichTargetsSelected(self, selectedKeys: tp.List[str]):
        changingKeys = [key for key, target in self.targets.items() if target.isSelected != (key in selectedKeys)]
        if len(changingKeys) == 0:
            return
        self.sigTargetsAboutToChange.emit(changingKeys, ['isSelected'])
        with self.sigTargetsAboutToChange.blocked(), self.sigTargetsChanged.blocked():
            for key in changingKeys:
                self.targets[key].isSelected = key in selectedKeys
        self.sigTargetsChanged.emit(changingKeys, ['isSelected'])

    def __getitem__(self, key):
        return self._targets[key]

    def __setitem__(self, key, target: Target):
        assert key == target.key
        self.setTarget(target=target)

    def __iter__(self):
        return iter(self._targets)

    def __len__(self):
        return len(self._targets)

    def keys(self):
        return self._targets.keys()

    def items(self):
        return self._targets.items()

    def values(self):
        return self._targets.values()

    def merge(self, otherTargets: Targets):

        self.sigTargetsAboutToChange.emit(list(otherTargets.keys()), None)

        with self.sigTargetsAboutToChange.blocked(), self.sigTargetsChanged.blocked():
            for target in otherTargets.values():
                self.setTarget(target)

        self.sigTargetsChanged.emit(list(otherTargets.keys()), None)

    @property
    def targets(self):
        return self._targets  # note: result should not be modified directly

    def asList(self) -> tp.List[tp.Dict[str, tp.Any]]:
        return [target.asDict() for target in self._targets.values()]

    @classmethod
    def fromList(cls, targetList: tp.List[tp.Dict[str, tp.Any]]) -> Targets:

        targets = {}
        for targetDict in targetList:
            targets[targetDict['key']] = Target.fromDict(targetDict)

        return cls(targets=targets)
