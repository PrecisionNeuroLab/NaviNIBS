from __future__ import annotations

import attrs
import datetime
import logging
import numpy as np
import pandas as pd
from pprint import pformat
import typing as tp

from RTNaBS.util.Signaler import Signal
from RTNaBS.util.numpy import array_equalish
from RTNaBS.util.attrs import attrsAsDict


logger = logging.getLogger(__name__)


Timestamp = pd.Timestamp


@attrs.define
class Sample:
    """
    Represents a single recorded sample
    """
    _key: str
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

    sigSampleAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((str, tp.Optional[tp.List[str]])))
    """
    This signal includes the key of the sample, and optionally a list of keys of attributes about to change;
    if second arg is None, all attributes should be assumed to be about to change.
    """

    sigSampleChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str, tp.Optional[tp.List[str]])))
    """
    This signal includes the key of the sample, and optionally a list of keys of changed attributes;  
    if second arg is None, all attributes should be assumed to have changed.
    """

    @property
    def key(self):
        return self._key

    @property
    def timestamp(self):
        return self._timestamp

    @timestamp.setter
    def timestamp(self, newTimestamp: Timestamp):
        if self._timestamp == newTimestamp:
            return
        self.sigSampleAboutToChange.emit(self.key, ['timestamp'])
        self._timestamp = newTimestamp
        self.sigSampleChanged.emit(self.key, ['timestamp'])

    @property
    def coilToMRITransf(self):
        return self._coilToMRITransf

    @coilToMRITransf.setter
    def coilToMRITransf(self, newTransf: tp.Optional[np.ndarray]):
        if array_equalish(self._coilToMRITransf, newTransf):
            return
        self.sigSampleAboutToChange.emit(self.key, ['coilToMRITransf'])
        self._coilToMRITransf = newTransf
        self.sigSampleChanged.emit(self.key, ['coilToMRITransf'])

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
        self.sigSampleAboutToChange.emit(self.key, ['targetKey'])
        self._targetKey = newKey
        self.sigSampleChanged.emit(self.key, ['targetKey'])

    @property
    def coilKey(self):
        return self._coilKey

    @coilKey.setter
    def coilKey(self, newKey: tp.Optional[str]):
        if self._coilKey == newKey:
            return
        self.sigSampleAboutToChange.emit(self.key, ['coilKey'])
        self._coilKey = newKey
        self.sigSampleChanged.emit(self.key, ['coilKey'])

    @property
    def isVisible(self):
        return self._isVisible

    @isVisible.setter
    def isVisible(self, isVisible: bool):
        if self._isVisible == isVisible:
            return
        self.sigSampleAboutToChange.emit(self.key, ['isVisible'])
        self._isVisible = isVisible
        self.sigSampleChanged.emit(self.key, ['isVisible'])

    @property
    def isSelected(self):
        return self._isSelected

    @isSelected.setter
    def isSelected(self, isSelected: bool):
        if self._isSelected == isSelected:
            return
        self.sigSampleAboutToChange.emit(self.key, ['isSelected'])
        self._isSelected = isSelected
        self.sigSampleChanged.emit(self.key, ['isSelected'])

    @property
    def color(self):
        return self._color

    def __str__(self):
        return pformat(self.asDict())

    def asDict(self) -> tp.Dict[str, tp.Any]:
        npFields = ('coilToMRITransf',)

        d = attrsAsDict(self, eqs={field: array_equalish for field in npFields})

        for key in npFields:
            if key in d and d[key] is not None:
                d[key] = d[key].tolist()

        d['timestamp'] = d['timestamp'].isoformat(timespec='microseconds')  # default may include nanoseconds, which can break when calling with fromisoformat on later import

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]):
        def convertOptionalNDArray(val: tp.Optional[tp.List[tp.Any]]) -> tp.Optional[np.ndarray]:
            if val is None:
                return None
            else:
                return np.asarray(val)

        for attrKey in ('coilToMRITransf',):
            if attrKey in d:
                d[attrKey] = convertOptionalNDArray(d[attrKey])

        d['timestamp'] = Timestamp.fromisoformat(d['timestamp'])

        return cls(**d)


@attrs.define
class Samples:
    _samples: tp.Dict[str, Sample] = attrs.field(factory=dict)

    sigSamplesAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((tp.List[str], tp.Optional[tp.List[str]])))
    """
    This signal includes list of keys of samples about to change, and optionally a list of keys of attributes about to change;  
    if second arg is None, all attributes should be assumed to be about to change.
    """
    sigSamplesChanged: Signal = attrs.field(init=False, factory=lambda: Signal((tp.List[str], tp.Optional[tp.List[str]])))
    """
    This signal includes list of keys of changed samples, and optionally a list of keys of changed attributes;  
    if second arg is None, all attributes should be assumed to have changed.
    """

    def __attrs_post_init__(self):
        for key, sample in self._samples.items():
            assert sample.key == key
            sample.sigSampleAboutToChange.connect(self._onSampleAboutToChange)
            sample.sigSampleChanged.connect(self._onSampleChanged)

    def addSample(self, sample: Sample):
        assert sample.key not in self._samples
        return self.setSample(sample=sample)

    def deleteSample(self, key: str):
        raise NotImplementedError  # TODO

    def setSample(self, sample: Sample):
        self.sigSamplesAboutToChange.emit([sample.key], None)
        if sample.key in self._samples:
            self._samples[sample.key].sigSampleAboutToChange.disconnect(self._onSampleAboutToChange)
            self._samples[sample.key].sigSampleChanged.disconnect(self._onSampleChanged)
        self._samples[sample.key] = sample

        sample.sigSampleAboutToChange.connect(self._onSampleAboutToChange)
        sample.sigSampleChanged.connect(self._onSampleChanged)

        self.sigSamplesChanged.emit([sample.key], None)

    def setSamples(self, samples: tp.List[Sample]):
        # assume all keys are changing, though we could do comparisons to find subset changed
        oldKeys = list(self.samples.keys())
        newKeys = [sample.key for sample in samples]
        combinedKeys = list(set(oldKeys) | set(newKeys))
        self.sigSamplesAboutToChange.emit(combinedKeys, None)
        for key in oldKeys:
            self._samples[key].sigSampleAboutToChange.disconnect(self._onSampleAboutToChange)
            self._samples[key].sigSampleChanged.disconnect(self._onSampleChanged)
        self._samples = {sample.key: sample for sample in samples}
        for key, sample in self._samples.items():
            self._samples[key].sigSampleAboutToChange.connect(self._onSampleAboutToChange)
            self._samples[key].sigSampleChanged.connect(self._onSampleChanged)
        self.sigSamplesChanged.emit(combinedKeys, None)

    def getUniqueSampleKey(self,
                           baseStr: str = 'Sample ',
                           startAtIndex: tp.Optional[int] = None,
                           timestamp: tp.Optional[Timestamp] = None) -> str:
        """
        Get a key not already used by any current samples, presumably to use for a new sample.

        If a timestamp is specified, will format the key using the timestamp. Otherwise will be based on
        an index and length of samples.
        """

        if timestamp is None:
            if startAtIndex is None:
                index = len(self.samples)
            else:
                index = startAtIndex
            while True:
                key = f'{baseStr}{index}'
                if key not in self._samples:
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
                if key not in self._samples:
                    return key
                iAttempt += 1

            if startAtIndex is None:
                startAtIndex = 2
            return self.getUniqueSampleKey(baseStr=key, startAtIndex=startAtIndex)

    def _onSampleAboutToChange(self, key: str, attribKeys: tp.Optional[tp.List[str]]):
        self.sigSamplesAboutToChange.emit([key], attribKeys)

    def _onSampleChanged(self, key: str, attribKeys: tp.Optional[tp.List[str]]):
        self.sigSamplesChanged.emit([key], attribKeys)

    def setWhichSamplesVisible(self, visibleKeys: list[str]):
        changingKeys = [key for key, sample in self.samples.items() if sample.isVisible != (key in visibleKeys)]
        if len(changingKeys) == 0:
            return
        self.sigSamplesAboutToChange.emit(changingKeys, ['isVisible'])
        with self.sigSamplesAboutToChange.blocked(), self.sigSamplesChanged.blocked():
            for key in changingKeys:
                self.samples[key].isVisible = key in visibleKeys
        self.sigSamplesChanged.emit(changingKeys, ['isVisible'])

    def setWhichSamplesSelected(self, selectedKeys: tp.List[str]):
        changingKeys = [key for key, sample in self.samples.items() if sample.isSelected != (key in selectedKeys)]
        if len(changingKeys) == 0:
            return
        self.sigSamplesAboutToChange.emit(changingKeys, ['isSelected'])
        with self.sigSamplesAboutToChange.blocked(), self.sigSamplesChanged.blocked():
            for key in changingKeys:
                self.samples[key].isSelected = key in selectedKeys
        self.sigSamplesChanged.emit(changingKeys, ['isSelected'])

    def __getitem__(self, key: str) -> Sample:
        return self._samples[key]

    def __setitem__(self, key: str, sample: Sample):
        assert key == sample.key
        self.setSample(sample=sample)

    def __iter__(self):
        return iter(self._samples)

    def __len__(self):
        return len(self._samples)

    def keys(self):
        return self._samples.keys()

    def items(self):
        return self._samples.items()

    def values(self):
        return self._samples.values()

    @property
    def samples(self):
        return self._samples

    def asList(self) -> tp.List[tp.Dict[str, tp.Any]]:
        return [sample.asDict() for sample in self._samples.values()]

    @classmethod
    def fromList(cls, sampleList: tp.List[tp.Dict[str, tp.Any]]) -> Samples:
        samples = {}
        for sampleDict in sampleList:
            sample = Sample.fromDict(sampleDict)
            samples[sample.key] = sample

        return cls(samples=samples)


def getSampleTimestampNow() -> Timestamp:
    return pd.Timestamp.now()
