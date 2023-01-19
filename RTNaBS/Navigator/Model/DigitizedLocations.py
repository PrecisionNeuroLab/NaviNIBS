from __future__ import annotations

import attrs
import copy
from datetime import datetime
import nibabel as nib
import json
import logging
import numpy as np
import os
import pandas as pd
import pyvista as pv
import shutil
import tempfile
import typing as tp
from typing import ClassVar

from RTNaBS.util.attrs import attrsAsDict
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.numpy import array_equalish, attrsWithNumpyAsDict, attrsWithNumpyFromDict

from RTNaBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem


logger = logging.getLogger(__name__)


@attrs.define
class DigitizedLocation(GenericCollectionDictItem[str]):
    _plannedCoord: tp.Optional[np.ndarray] = None
    _plannedCoordSpace: tp.Optional[str] = None  # str key referring to a CoordinateSystem (e.g. MNI)
    _sampledCoord: tp.Optional[np.ndarray] = None  # in MRI space
    _type: tp.Optional[str] = None

    _color: str = '#6f2da8'

    @property
    def plannedCoord(self):
        return self._plannedCoord

    @property
    def plannedCoordSpace(self):
        return self._plannedCoordSpace

    @property
    def sampledCoord(self):
        return self._sampledCoord

    @sampledCoord.setter
    def sampledCoord(self, newCoord: tp.Optional[np.ndarray]):
        if array_equalish(newCoord, self._sampledCoord):
            return
        self.sigItemAboutToChange.emit(self.key, ['sampledCoord'])
        self._sampledCoord = newCoord
        self.sigItemChanged.emit(self.key, ['sampledCoord'])

    @property
    def type(self):
        return self._type

    @property
    def color(self):
        return self._color

    def asDict(self) -> dict[str, tp.Any]:
        return attrsWithNumpyAsDict(self, npFields=('plannedCoord', 'sampledCoord'))

    @classmethod
    def fromDict(cls, d: dict[str, tp.Any]):
        return attrsWithNumpyFromDict(cls, d, npFields=('plannedCoord', 'sampledCoord'))


@attrs.define
class DigitizedLocations(GenericCollection[str, DigitizedLocation]):
    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def loadFromXML(self, xmlPath: str):
        import xmltodict
        with open(xmlPath, 'r') as f:
            montage = xmltodict.parse(f.read())

        for marker in montage['PresetEEG']['Marker']:
            self.addItem(DigitizedLocation(
                key=marker['@name'],
                color=marker['@color']
            ))

    @classmethod
    def fromList(cls, itemList: list[dict[str, tp.Any]]) -> DigitizedLocations:
        items = {}
        for itemDict in itemList:
            items[itemDict['key']] = DigitizedLocation.fromDict(itemDict)

        return cls(items=items)