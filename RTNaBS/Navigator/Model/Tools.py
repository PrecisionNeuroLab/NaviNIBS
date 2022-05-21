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
class Tool:
    _key: str
    _usedFor: str  # e.g. 'subject', 'coil', 'pointer'
    _isActive: bool = True
    _romFilepath: tp.Optional[str] = None
    _stlFilepath: tp.Optional[str] = None
    _filepathsRelTo: str = '<session>'  # <install> for relative to RTNaBS install dir, <session> for relative to session file
    _trackerToToolTransf: tp.Optional[np.ndarray] = None

    _installPath: tp.Optional[str] = None  # used for relative paths
    _sessionPath: tp.Optional[str] = None  # used for relative paths

    _trackerToToolTransfHistory: tp.Dict[str, tp.Optional[np.ndarray]] = attrs.field(factory=dict)

    sigToolAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((str,)))  # includes key
    sigKeyChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str, str)))  # includes old key, new key
    sigUsedForChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str, str, str)))  # includes key, old usedFor, new usedFor
    sigToolChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str,)))  # includes key

    def __attrs_post_init__(self):
        if self._installPath is None:
            self._installPath = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')

    @property
    def key(self):
        return self._key

    @key.setter
    def key(self, newKey: str):
        if self._key == newKey:
            return
        self.sigToolAboutToChange.emit(self._key)
        self.sigToolAboutToChange.emit(newKey)
        prevKey = self._key
        self._key = newKey
        self.sigKeyChanged.emit(prevKey, newKey)
        self.sigToolChanged.emit(prevKey)
        self.sigToolChanged.emit(self._key)

    @property
    def usedFor(self):
        return self._usedFor

    @usedFor.setter
    def usedFor(self, newUsedFor: str):
        if self._usedFor == newUsedFor:
            return
        self.sigToolAboutToChange.emit(self._key)
        prevUsedFor = self._usedFor
        self._usedFor = newUsedFor
        self.sigUsedForChanged.emit(self._key, prevUsedFor, newUsedFor)
        self.sigToolChanged.emit(self._key)

    @property
    def isActive(self):
        return self._isActive

    @property
    def romFilepath(self):
        if self._romFilepath is None:
            return None
        else:
            return os.path.join(self.filepathsRelTo, self._romFilepath)

    @romFilepath.setter
    def romFilepath(self, newFilepath: tp.Optional[str]):
        if newFilepath == self.romFilepath:
            return
        logger.info('Changing {} romFilepath to {}'.format(self.key, newFilepath))
        self.sigToolAboutToChange.emit(self.key)
        self._romFilepath = os.path.relpath(newFilepath, self.filepathsRelTo)
        self.sigToolChanged.emit(self.key)

    @property
    def stlFilepath(self):
        if self._stlFilepath is None:
            return None
        else:
            return os.path.join(self.filepathsRelTo, self._stlFilepath)

    @stlFilepath.setter
    def stlFilepath(self, newFilepath: tp.Optional[str]):
        if newFilepath == self.stlFilepath:
            return
        logger.info('Changing {} stlFilepath to {}'.format(self.key, newFilepath))
        self.sigToolAboutToChange.emit(self.key)
        self._stlFilepath = os.path.relpath(newFilepath, self.filepathsRelTo)
        self.sigToolChanged.emit(self.key)

    @property
    def filepathsRelTo(self):
        if self._filepathsRelTo == '<install>':
            assert self._installPath is not None
            return self._installPath
        elif self._filepathsRelTo == '<session>':
            assert self._sessionPath is not None
            return self._sessionPath
        else:
            return self._filepathsRelTo

    @property
    def sessionPath(self):
        return self._sessionPath

    @sessionPath.setter
    def sessionPath(self, newPath: tp.Optional[str]):
        if self._sessionPath == newPath:
            return

        if self._filepathsRelTo == '<session>':
            self.sigToolAboutToChange.emit(self.key)

        self._sessionPath = newPath

        if self._filepathsRelTo == '<session>':
            self.sigToolChanged.emit(self.key)

    @property
    def trackerToToolTransf(self):
        return self._trackerToToolTransf

    @trackerToToolTransf.setter
    def trackerToToolTransf(self, newTransf: tp.Optional[np.ndarray]):
        if array_equalish(self._trackerToToolTransf, newTransf):
            logger.debug('No change in trackerToToolTransf, returning')
            return

        # TODO: do validation of newTransf

        self.sigToolAboutToChange.emit(self.key)
        self._trackerToToolTransfHistory[self._getTimestampStr()] = None if self._trackerToToolTransf is None else self._trackerToToolTransf.copy()

        logger.info('Set trackerToToolTransf to {}'.format(newTransf))
        self._trackerToToolTransf = newTransf
        self.sigToolChanged.emit(self.key)

    def asDict(self) -> tp.Dict[str, tp.Any]:
        d = attrsAsDict(self, eqs=dict(
            trackerToToolTransf=array_equalish,
            trackerToToolTransfHistory=lambda a, b: len(a) == len(b) \
                                                    and ((keyA == keyB and array_equalish(a[keyA], b[keyB])) for keyA, keyB in zip(a, b))))
        if 'trackerToToolTransf' in d:
            d['trackerToToolTransf'] = d['trackerToToolTransf'].tolist()
        if 'trackerToToolTransfHistory' in d:
            d['trackerToToolTransfHistory'] = [dict(time=key, trackerToToolTransf=val.tolist()) for key, val in d['trackerToToolTransfHistory']]

        for key in ('installPath', 'sessionPath'):
            d.pop(key, None)

        d['usedFor'] = self.usedFor

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any], sessionPath: tp.Optional[str] = None):
        if 'trackerToToolTransf' in d:
            d['trackerToToolTransf'] = np.asarray(d['trackerToToolTransf'])
        if 'trackerToToolTransfHistory' in d:
            def convertTransf(transf: tp.List[tp.List[float, float, float]]) -> np.ndarray:
                return np.asarray(transf)

            def convertHistoryListToDict(historyList: tp.List[tp.Dict[str, tp.Any]], field: str,
                                         entryConverter: tp.Callable) -> tp.Dict[str, tp.Any]:
                historyDict = {}
                for entry in historyList:
                    timeStr = entry['time']
                    historyDict[timeStr] = entryConverter(entry[field])
                return historyDict

            d['trackerToToolTransfHistory'] = convertHistoryListToDict(
                d['trackerToToolTransfHistory'], 'trackerToToolTransf', convertTransf)

        return cls(**d, sessionPath=sessionPath)

    @staticmethod
    def _getTimestampStr():
        return datetime.today().strftime('%y%m%d%H%M%S.%f')


@attrs.define
class SubjectTracker(Tool):
    _usedFor: str = 'subject'


@attrs.define
class CoilTool(Tool):
    _usedFor: str = 'coil'
    _coilStlFilepath: tp.Optional[str] = None

    @property
    def coilStlFilepath(self):
        if self._coilStlFilepath is None:
            return None
        else:
            return os.path.join(self.filepathsRelTo, self._coilStlFilepath)

    @coilStlFilepath.setter
    def coilStlFilepath(self, newFilepath: tp.Optional[str]):
        if newFilepath == self.coilStlFilepath:
            return
        logger.info('Changing {} coilStlFilepath to {}'.format(self.key, newFilepath))
        self.sigToolAboutToChange.emit(self.key)
        self._coilStlFilepath = os.path.relpath(newFilepath, self.filepathsRelTo)
        self.sigToolChanged.emit(self.key)


@attrs.define
class Pointer(Tool):
    _usedFor: str = 'pointer'


@attrs.define
class CalibrationPlate(Tool):
    _usedFor: str = 'calibration'


@attrs.define
class Tools:
    _tools: tp.Dict[str, Tool] = attrs.field(factory=dict)

    _sessionPath: tp.Optional[str] = None  # used for relative paths

    sigToolsAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal(
        (tp.List[str],)))  # includes list of keys of tools about to change
    sigToolsChanged: Signal = attrs.field(init=False, factory=lambda: Signal(
        (tp.List[str],)))  # includes list of keys of changed tools

    def __attrs_post_init__(self):
        for key, tool in self._tools.items():
            assert tool.key == key
            tool.sigToolAboutToChange.connect(self._onToolAboutToChange)
            tool.sigKeyChanged.connect(self._onToolKeyChanged)
            tool.sigUsedForChanged.connect(self._onToolUsedForChanged)
            tool.sigToolChanged.connect(self._onToolChanged)

    def addTool(self, tool: Tool):
        assert tool.key not in self._tools
        return self.setTool(tool=tool)

    def addToolFromDict(self, toolDict: tp.Dict[str, tp.Any]):
        self.addTool(self._toolFromDict(toolDict, sessionPath=self._sessionPath))

    def deleteTool(self, key: str):
        raise NotImplementedError()  # TODO

    def setTool(self, tool: Tool):
        self.sigToolsAboutToChange.emit([tool.key])
        if tool.key in self._tools:
            self._tools[tool.key].sigToolAboutToChange.disconnect(self._onToolAboutToChange)
            self._tools[tool.key].sigKeyChanged.disconnect(self._onToolKeyChanged)
            self._tools[tool.key].sigUsedForChanged.disconnect(self._onToolUsedForChanged)
            self._tools[tool.key].sigToolChanged.disconnect(self._onToolChanged)
        self._tools[tool.key] = tool

        tool.sigToolAboutToChange.connect(self._onToolAboutToChange)
        tool.sigKeyChanged.connect(self._onToolKeyChanged)
        tool.sigUsedForChanged.connect(self._onToolUsedForChanged)
        tool.sigToolChanged.connect(self._onToolChanged)

        self.sigToolsChanged.emit([tool.key])

    def setTools(self, tools: tp.List[Tool]):
        # assume all keys are changing, though we could do comparisons to find subset changed
        oldKeys = list(self.tools.keys())
        newKeys = [tool.key for tool in tools]
        combinedKeys = list(set(oldKeys) | set(newKeys))
        self.sigToolsAboutToChange.emit(combinedKeys)
        for key in oldKeys:
            self._tools[key].sigToolAboutToChange.disconnect(self._onToolAboutToChange)
            self._tools[key].sigKeyChanged.disconnect(self._onToolKeyChanged)
            self._tools[key].sigUsedForChanged.disconnect(self._onToolUsedForChanged)
            self._tools[key].sigToolChanged.disconnect(self._onToolChanged)
        self._tools = {tool.key: tool for tool in tools}
        for key, tool in self._tools.items():
            tool.sigToolAboutToChange.connect(self._onToolAboutToChange)
            tool.sigKeyChanged.connect(self._onToolKeyChanged)
            tool.sigUsedForChanged.connect(self._onToolUsedForChanged)
            tool.sigToolChanged.connect(self._onToolChanged)
        self.sigToolsChanged.emit(combinedKeys)

    def _getActiveToolKeys(self) -> tp.Dict[str, tp.Union[str, tp.List[str,...]]]:
        activeToolKeys = {}
        for key, tool in self._tools.items():
            if not tool.isActive:
                continue
            if tool.usedFor in ('subject', 'pointer'):
                assert tool.usedFor not in activeToolKeys, 'More than one active tool for {}'.format(tool.usedFor)

        raise NotImplementedError()  # TODO

    def _checkActiveTools(self):
        raise NotImplementedError()  # TODO: assert that only one subject, pointer, and (for now) coil tracker are active
        
    def _onToolAboutToChange(self, key: str):
        self.sigToolsAboutToChange.emit([key])

    def _onToolKeyChanged(self, fromKey: str, toKey: str):
        # assume sigToolsAboutToChange+self.sigToolsChanged will be emitted before and after this by emitter
        assert toKey not in self._tools
        self._tools = {(toKey if key == fromKey else key): val for key, val in self._tools.items()}

    def _onToolChanged(self, key: str):
        self.sigToolsChanged.emit([key])

    def __getitem__(self, key):
        return self._tools[key]

    def __setitem__(self, key, tool: Tool):
        assert key == tool.key
        self.setTool(tool=tool)

    def __iter__(self):
        return iter(self._tools)

    def __len__(self):
        return len(self._tools)

    def keys(self):
        return self._tools.keys()

    def items(self):
        return self._tools.items()

    def values(self):
        return self._tools.values()
    
    @property
    def tools(self):
        return self._tools  # note: result should not be modified directly

    @property
    def sessionPath(self):
        return self._sessionPath

    @sessionPath.setter
    def sessionPath(self, newPath: tp.Optional[str]):
        if self._sessionPath == newPath:
            return

        changingKeys = [tool.key for tool in self._tools.values() if tool.filepathsRelTo == '<session>']
        self.sigToolsAboutToChange.emit(changingKeys)
        self._sessionPath = newPath
        with self.sigToolsAboutToChange.blocked(), self.sigToolsChanged.blocked():
            for tool in self._tools.values():
                tool.sessionPath = self._sessionPath
        self.sigToolsAboutToChange.emit()

    def asList(self) -> tp.List[tp.Dict[str, tp.Any]]:
        return [tool.asDict() for tool in self._tools.values()]

    @classmethod
    def fromList(cls, toolList: tp.List[tp.Dict[str, tp.Any]], sessionPath: tp.Optional[str] = None) -> Tools:

        tools = {}
        for toolDict in toolList:
            tools[toolDict['key']] = cls._toolFromDict(toolDict, sessionPath=sessionPath)

        return cls(tools=tools, sessionPath=sessionPath)

    def _onToolUsedForChanged(self, key: str, fromUsedFor: str, toUsedFor: str):

        toolDict = self._tools[key].asDict()
        if fromUsedFor == 'coil':
            toolDict.pop('coilStlFilepath', None)
        tool = self._toolFromDict(toolDict, sessionPath=self._sessionPath)
        self.setTool(tool)

    @classmethod
    def _toolFromDict(cls, toolDict: tp.Dict[str, tp.Any], sessionPath: tp.Optional[str] = None) -> Tool:
        usedFor = toolDict['usedFor']
        if usedFor == 'coil':
            tool = CoilTool.fromDict(toolDict, sessionPath=sessionPath)
        else:
            tool = Tool.fromDict(toolDict, sessionPath=sessionPath)
        return tool
    
    
    