from __future__ import annotations

import attrs
import contextlib
import datetime
import logging
import numpy as np
import pandas as pd
from pprint import pformat
import typing as tp

from RTNaBS.util.numpy import array_equalish, attrsWithNumpyAsDict, attrsWithNumpyFromDict

from RTNaBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem


logger = logging.getLogger(__name__)


Timestamp = pd.Timestamp


@attrs.define
class Sample(GenericCollectionDictItem[str]):
    """
    Represents a single recorded sample
    """
    _timestamp: Timestamp
    _coilToMRITransf: tp.Optional[np.ndarray] = None
    _targetKey: tp.Optional[str] = None
    """
    Key of target that was active at the time sample was collected. Note that if target info (e.g. coordinates) changed later,
    the actual target coordinates at the time of this sample may be lost (in the active model). However, these should be able
    to be recovered based on timestamps and previous target history.
    """
    _coilKey: tp.Optional[str] = None
    """
    Key of coil tool that was active at the time sample was collected.
    """

    _isVisible: bool = True
    _isSelected: bool = False
    _color: tp.Optional[str] = None

    _metadata: dict[str, tp.Any] = attrs.field(factory=dict)
    """
    For storing misc metadata, such as information about the trigger event that initiated this sample.
    
    Values should be JSON-serializable.
    """

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def timestamp(self):
        return self._timestamp

    @timestamp.setter
    def timestamp(self, newTimestamp: Timestamp):
        if self._timestamp == newTimestamp:
            return
        self.sigItemAboutToChange.emit(self.key, ['timestamp'])
        self._timestamp = newTimestamp
        self.sigItemChanged.emit(self.key, ['timestamp'])

    @property
    def coilToMRITransf(self):
        return self._coilToMRITransf

    @coilToMRITransf.setter
    def coilToMRITransf(self, newTransf: tp.Optional[np.ndarray]):
        if array_equalish(self._coilToMRITransf, newTransf):
            return
        self.sigItemAboutToChange.emit(self.key, ['coilToMRITransf'])
        self._coilToMRITransf = newTransf
        self.sigItemChanged.emit(self.key, ['coilToMRITransf'])

    @property
    def hasTransf(self):
        return self._coilToMRITransf is not None

    @property
    def targetKey(self):
        return self._targetKey

    @targetKey.setter
    def targetKey(self, newKey: tp.Optional[str]):
        if self._targetKey == newKey:
            return
        self.sigItemAboutToChange.emit(self.key, ['targetKey'])
        self._targetKey = newKey
        self.sigItemChanged.emit(self.key, ['targetKey'])

    @property
    def coilKey(self):
        return self._coilKey

    @coilKey.setter
    def coilKey(self, newKey: tp.Optional[str]):
        if self._coilKey == newKey:
            return
        self.sigItemAboutToChange.emit(self.key, ['coilKey'])
        self._coilKey = newKey
        self.sigItemChanged.emit(self.key, ['coilKey'])

    @property
    def isVisible(self):
        return self._isVisible

    @isVisible.setter
    def isVisible(self, isVisible: bool):
        if self._isVisible == isVisible:
            return
        self.sigItemAboutToChange.emit(self.key, ['isVisible'])
        self._isVisible = isVisible
        self.sigItemChanged.emit(self.key, ['isVisible'])

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
    def metadata(self):
        """
        Note: if needing to modify the result, make sure to do within the `changingMetada context manager, like:
            with sample.changingMetadata() as metadata:
                metadata['foo'] = 'bar'
        """
        return self._metadata

    @contextlib.contextmanager
    def changingMetadata(self):
        self.sigItemAboutToChange.emit(self.key, ['metadata'])
        yield self._metadata
        self.sigItemChanged.emit(self.key, ['metadata'])

    def __str__(self):
        return pformat(self.asDict())

    def asDict(self) -> tp.Dict[str, tp.Any]:
        d = attrsWithNumpyAsDict(self, npFields=('coilToMRITransf',))

        d['timestamp'] = d['timestamp'].isoformat(timespec='microseconds')  # default may include nanoseconds, which can break when calling with fromisoformat on later import

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]):

        d['timestamp'] = Timestamp.fromisoformat(d['timestamp'])

        return attrsWithNumpyFromDict(cls, d, npFields=('coilToMRITransf',))


@attrs.define
class Samples(GenericCollection[str, Sample]):
    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def getUniqueSampleKey(self,
                           baseStr: str = 'Sample ',
                           startAtIndex: tp.Optional[int] = None,
                           timestamp: tp.Optional[Timestamp] = None) -> str:
        """
        Get a key not already used by any current samples, presumably to use for a new sample.

        If a timestamp is specified, will format the key using the timestamp. Otherwise will be based on
        an index and length of samples.
        """

        key = baseStr
        if timestamp is None:
            if startAtIndex is None:
                index = len(self._items)
            else:
                index = startAtIndex
            while True:
                key = f'{baseStr}{index}'
                if key not in self.keys():
                    return key
                index += 1
        else:
            removeNChars = [7, 3, 0]
            iAttempt = 0
            while iAttempt < len(removeNChars):
                key = baseStr + timestamp.strftime('%y.%m.%d %H:%M:%S.%f')
                if removeNChars[iAttempt] > 0:
                    key = key[:-removeNChars[iAttempt]]
                if key.endswith('.'):
                    key = key[:-1]
                if key not in self.keys():
                    return key
                iAttempt += 1

            if startAtIndex is None:
                startAtIndex = 2
            return self.getUniqueSampleKey(baseStr=key, startAtIndex=startAtIndex)

    def setWhichSamplesVisible(self, visibleKeys: list[str]):
        self.setAttribForItems(self.keys(), dict(isVisible=[key in visibleKeys for key in self.keys()]))

    def setWhichSamplesSelected(self, selectedKeys: tp.List[str]):
        self.setAttribForItems(self.keys(), dict(isSelected=[key in selectedKeys for key in self.keys()]))

    @classmethod
    def fromList(cls, itemList: list[dict[str, tp.Any]]) -> Samples:
        items = {}
        for itemDict in itemList:
            items[itemDict['key']] = Sample.fromDict(itemDict)

        return cls(items=items)


def getSampleTimestampNow() -> Timestamp:
    return pd.Timestamp.now()
