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
import platformdirs
import pyvista as pv
import tempfile
import typing as tp
from typing import ClassVar

from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.numpy import array_equalish, attrsWithNumpyAsDict, attrsWithNumpyFromDict
from NaviNIBS.util.attrs import attrsAsDict
from NaviNIBS.Devices import positionsServerHostname, positionsServerPubPort, positionsServerCmdPort

from NaviNIBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem


logger = logging.getLogger(__name__)


SurfMesh = pv.PolyData


@attrs.define
class Tool(GenericCollectionDictItem[str]):
    _usedFor: str  # e.g. 'subject', 'coil', 'pointer'
    _label: tp.Optional[str] = None
    """
    Optional "nice" label for display. If not specified, ``key`` will be used instead. Label does not need to be unique, but could be confusing in some GUI displays if multiple tools with the same label are shown.
    """
    _trackerKey: tp.Optional[str] = None
    """
    Optional key to match up to tracking data. If not specified, ``key`` will be used instead.
    """
    _isActive: bool = True
    """
    Whether to actively use for tracking/pointing
    """
    _doRenderTool: bool = True
    """
    Whether to show tool in camera view, etc. To actually render, a valid pose and mesh must be available. 
    """
    _doRenderTracker: bool = True
    """
    Whether to show tracker in camera view, etc. To actually render, a valid pose and mesh must be available.
    """
    _doShowTrackingState: bool = True
    """
    Whether to include this tool in tracking status widget(s). To actually show, must not also be excluded by other hide filters on the widget.
    """
    _romFilepath: tp.Optional[str] = None
    """
    Path to a .rom file describing optical marker positions on a rigid body tracker, as used by NDI Polaris systems.
    """
    _trackerStlFilepath: tp.Optional[str] = None
    """
    Path to a surface mesh file (e.g. .stl, .ply) for visualization of the tracker.
    """
    _toolStlFilepath: tp.Optional[str] = None
    """
    Path to a surface mesh file (e.g. .stl, .ply) for visualization of the tool.
    """
    _filepathsRelTo: str = '<userDataDir>'
    """
    Can be one of:
    
    - ``'<install>'``: relative to NaviNIBS install dir
    - ``'<userDataDir>'``: relative to user data dir
    - ``'<session>'``: relative to session file
    - absolute path
    """
    _toolToTrackerTransf: tp.Optional[np.ndarray] = None
    """
    Used for aligning actual tool position to Polaris-reported tracker position (e.g. actual coil to coil tracker, or actual pointer to uncalibrated pointer tracker)
    """
    _toolStlToToolTransf: tp.Optional[
        np.ndarray] = None
    """
    Used for visualization of tool surface mesh only; can be used to align mesh with actual tool orientation
    """
    _trackerStlToTrackerTransf: tp.Optional[np.ndarray] = None  # used for visualization of tracker STL only; can be used to align STL with actual reported tracker orientation
    _toolColor: str | None = None
    _trackerColor: str | None = None
    """
    Note: some surf file formats (e.g. .ply) allow specifying color of elements within the file; if color is None here and colors are available in the surf file, those colors will be used.
    """
    _toolOpacity: float | None = None
    _trackerOpacity: float | None = None

    _installPath: tp.Optional[str] = None  # used for relative paths
    _sessionPath: tp.Optional[str] = None  # used for relative paths

    _trackerSurf: tp.Optional[SurfMesh] = attrs.field(init=False, default=None)
    _toolSurf: tp.Optional[SurfMesh] = attrs.field(init=False, default=None)

    _initialTrackerPose: tp.Optional[np.ndarray] = None
    """
    For defining initial pose of tool, e.g. for tools that never get a camera-reported position, or 
    for a default (simulated) position when a camera is not connected. 
    """
    _initialTrackerPoseRelativeTo: str = 'world'

    _toolToTrackerTransfHistory: tp.Dict[str, tp.Optional[np.ndarray]] = attrs.field(factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        if self._installPath is None:
            self._installPath = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')

    @property
    def label(self):
        if self._label is None:
            return self.key
        else:
            return self._label

    @label.setter
    def label(self, newLabel: tp.Optional[str]):
        if self._label == newLabel:
            return
        self.sigItemAboutToChange.emit(self._key, ['label'])
        self._label = newLabel
        self.sigItemChanged.emit(self._key, ['label'])

    @property
    def labelIsSet(self):
        return self._label is not None

    @property
    def trackerKey(self):
        if self._trackerKey is None:
            return self._key
        else:
            return self._trackerKey

    @trackerKey.setter
    def trackerKey(self, newKey: tp.Optional[str]):
        if self._trackerKey == newKey:
            return
        self.sigItemAboutToChange.emit(self._key, ['trackerKey'])
        self._trackerKey = newKey
        self.sigItemChanged.emit(self._key, ['trackerKey'])

    @property
    def trackerKeyIsSet(self):
        return self._trackerKey is not None

    @property
    def usedFor(self):
        return self._usedFor

    @usedFor.setter
    def usedFor(self, newUsedFor: str):
        if self._usedFor == newUsedFor:
            return

        logger.info('Changing {} usedFor from {} to {}'.format(self.key, self._usedFor, newUsedFor))
        self.sigItemAboutToChange.emit(self._key, ['usedFor'])
        self._usedFor = newUsedFor
        self.sigItemChanged.emit(self._key, ['usedFor'])

    @property
    def isActive(self):
        return self._isActive

    @isActive.setter
    def isActive(self, newIsActive: bool):
        if self._isActive == newIsActive:
            return

        logger.info('Changing {} isActive to {}'.format(self.key, newIsActive))
        self.sigItemAboutToChange.emit(self._key, ['isActive'])
        self._isActive = newIsActive
        self.sigItemChanged.emit(self._key, ['isActive'])

    @property
    def doRenderTool(self):
        return self._doRenderTool

    @doRenderTool.setter
    def doRenderTool(self, newDoRenderTool: bool):
        if self._doRenderTool == newDoRenderTool:
            return

        logger.info('Changing {} doRenderTool to {}'.format(self.key, newDoRenderTool))
        self.sigItemAboutToChange.emit(self._key, ['doRenderTool'])
        self._doRenderTool = newDoRenderTool
        self.sigItemChanged.emit(self._key, ['doRenderTool'])

    @property
    def doRenderTracker(self):
        return self._doRenderTracker

    @doRenderTracker.setter
    def doRenderTracker(self, newDoRenderTracker: bool):
        if self._doRenderTracker == newDoRenderTracker:
            return

        logger.info('Changing {} doRenderTracker to {}'.format(self.key, newDoRenderTracker))
        self.sigItemAboutToChange.emit(self._key, ['doRenderTracker'])
        self._doRenderTracker = newDoRenderTracker
        self.sigItemChanged.emit(self._key, ['doRenderTracker'])

    @property
    def doShowTrackingState(self):
        return self._doShowTrackingState

    @doShowTrackingState.setter
    def doShowTrackingState(self, newDoShowTrackingState: bool):
        if self._doShowTrackingState == newDoShowTrackingState:
            return

        logger.info('Changing {} doShowTrackingState to {}'.format(self.key, newDoShowTrackingState))
        self.sigItemAboutToChange.emit(self._key, ['doShowTrackingState'])
        self._doShowTrackingState = newDoShowTrackingState
        self.sigItemChanged.emit(self._key, ['doShowTrackingState'])

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
        self.sigItemAboutToChange.emit(self.key, ['romFilepath'])
        self._romFilepath = os.path.relpath(newFilepath, self.filepathsRelTo)
        self.sigItemChanged.emit(self.key, ['romFilepath'])

    @property
    def toolStlFilepath(self):
        if self._toolStlFilepath is None:
            return None
        else:
            return os.path.join(self.filepathsRelTo, self._toolStlFilepath)

    @toolStlFilepath.setter
    def toolStlFilepath(self, newFilepath: tp.Optional[str]):
        if newFilepath == self.toolStlFilepath:
            return
        logger.info('Changing {} toolStlFilepath to {}'.format(self.key, newFilepath))
        self._toolSurf = None
        self.sigItemAboutToChange.emit(self.key, ['toolStlFilepath'])
        self._toolStlFilepath = os.path.relpath(newFilepath, self.filepathsRelTo)
        self.sigItemChanged.emit(self.key, ['toolStlFilepath'])

    @property
    def trackerStlFilepath(self):
        if self._trackerStlFilepath is None:
            return None
        else:
            return os.path.join(self.filepathsRelTo, self._trackerStlFilepath)

    @trackerStlFilepath.setter
    def trackerStlFilepath(self, newFilepath: tp.Optional[str]):
        if newFilepath == self.trackerStlFilepath:
            return
        logger.info('Changing {} trackerStlFilepath to {}'.format(self.key, newFilepath))
        self._trackerSurf = None
        self.sigItemAboutToChange.emit(self.key, ['trackerStlFilepath'])
        self._trackerStlFilepath = os.path.relpath(newFilepath, self.filepathsRelTo)
        self.sigItemChanged.emit(self.key, ['trackerStlFilepath'])

    @property
    def filepathsRelTo(self):
        match self._filepathsRelTo:
            case '<install>':
                assert self._installPath is not None
                return self._installPath
            case '<userDataDir>':
                return platformdirs.user_data_dir(appname='NaviNIBS', appauthor=False)
            case '<session>':
                assert self._sessionPath is not None
                return self._sessionPath
            case _:
                return self._filepathsRelTo

    @property
    def filepathsRelToKey(self):
        if self._filepathsRelTo in ('<install>', '<userDataDir>', '<session>'):
            return self._filepathsRelTo
        else:
            return None

    @property
    def sessionPath(self):
        return self._sessionPath

    @sessionPath.setter
    def sessionPath(self, newPath: tp.Optional[str]):
        if self._sessionPath == newPath:
            return

        filepathAttribs = [
            'romFilepath',
            'toolStlFilepath',
            'trackerStlFilepath'
            'sessionPath'
        ]

        if self._filepathsRelTo == '<session>':
            self.sigItemAboutToChange.emit(self.key, filepathAttribs)

        self._sessionPath = newPath

        if self._filepathsRelTo == '<session>':
            self.sigItemChanged.emit(self.key, filepathAttribs)

    @property
    def toolToTrackerTransf(self):
        if self._toolToTrackerTransf is None:
            return np.eye(4)
        else:
            return self._toolToTrackerTransf

    @toolToTrackerTransf.setter
    def toolToTrackerTransf(self, newTransf: tp.Optional[np.ndarray]):
        if array_equalish(self._toolToTrackerTransf, newTransf):
            logger.debug('No change in toolToTrackerTransf, returning')
            return

        # TODO: do validation of newTransf

        self.sigItemAboutToChange.emit(self.key, ['toolToTrackerTransf'])
        logger.info('Set toolToTrackerTransf to {}'.format(newTransf))
        self._toolToTrackerTransf = newTransf
        self._toolToTrackerTransfHistory[self._getTimestampStr()] = None if self._toolToTrackerTransf is None else self._toolToTrackerTransf.copy()
        self.sigItemChanged.emit(self.key, ['toolToTrackerTransf'])

    @property
    def toolStlToToolTransf(self):
        if self._toolStlToToolTransf is None:
            return np.eye(4)
        else:
            return self._toolStlToToolTransf

    @toolStlToToolTransf.setter
    def toolStlToToolTransf(self, newTransf: tp.Optional[np.ndarray]):
        if array_equalish(self._toolStlToToolTransf, newTransf):
            logger.debug('No change in toolStlToToolTransf, returning')
            return

        self.sigItemAboutToChange.emit(self.key, ['toolStlToToolTransf'])
        logger.info('Set toolStlToToolTransf to {}'.format(newTransf))
        self._toolStlToToolTransf = newTransf
        self.sigItemChanged.emit(self.key, ['toolStlToToolTransf'])

    @property
    def trackerStlToTrackerTransf(self):
        if self._trackerStlToTrackerTransf is None:
            return np.eye(4)
        else:
            return self._trackerStlToTrackerTransf

    @trackerStlToTrackerTransf.setter
    def trackerStlToTrackerTransf(self, newTransf: tp.Optional[np.ndarray]):
        if array_equalish(self._trackerStlToTrackerTransf, newTransf):
            logger.debug('No change in trackerStlToTrackerTransf, returning')
            return

        self.sigItemAboutToChange.emit(self.key, ['trackerStlToTrackerTransf'])
        logger.info('Set trackerStlToTrackerTransf to {}'.format(newTransf))
        self._trackerStlToTrackerTransf = newTransf
        self.sigItemChanged.emit(self.key, ['trackerStlToTrackerTransf'])

    @property
    def trackerColor(self):
        return self._trackerColor

    @property
    def toolColor(self):
        return self._toolColor

    @property
    def trackerOpacity(self):
        return self._trackerOpacity

    @property
    def toolOpacity(self):
        return self._toolOpacity

    @property
    def toolToTrackerTransfHistory(self):
        return self._toolToTrackerTransfHistory

    @property
    def trackerSurf(self):
        if self._trackerStlFilepath is not None and self._trackerSurf is None:
            logger.info('Loading tracker mesh from {}'.format(self.trackerStlFilepath))
            self._trackerSurf = pv.read(self.trackerStlFilepath)
        return self._trackerSurf

    @property
    def toolSurf(self):
        if self._toolStlFilepath is not None and self._toolSurf is None:
            logger.info('Loading tool mesh from {}'.format(self.toolStlFilepath))
            self._toolSurf = pv.read(self.toolStlFilepath)
        return self._toolSurf

    @property
    def initialTrackerPose(self):
        return self._initialTrackerPose

    @initialTrackerPose.setter
    def initialTrackerPose(self, newPose: tp.Optional[np.ndarray]):
        if array_equalish(self._initialTrackerPose, newPose):
            logger.debug('No change in initialTrackerPose, returning')
            return

        self.sigItemAboutToChange.emit(self.key, ['initialTrackerPose'])
        logger.info('Set initialTrackerPose to {}'.format(newPose))
        self._initialTrackerPose = newPose
        self.sigItemChanged.emit(self.key, ['initialTrackerPose'])

    @property
    def initialTrackerPoseRelativeTo(self):
        return self._initialTrackerPoseRelativeTo

    @initialTrackerPoseRelativeTo.setter
    def initialTrackerPoseRelativeTo(self, newPoseRelativeTo: tp.Optional[str]):
        if self._initialTrackerPoseRelativeTo == newPoseRelativeTo:
            logger.debug('No change in initialTrackerPoseRelativeTo, returning')
            return

        self.sigItemAboutToChange.emit(self.key, ['initialTrackerPoseRelativeTo'])
        logger.info('Set initialTrackerPoseRelativeTo to {}'.format(newPoseRelativeTo))
        self._initialTrackerPoseRelativeTo = newPoseRelativeTo
        self.sigItemChanged.emit(self.key, ['initialTrackerPoseRelativeTo'])

    def asDict(self) -> tp.Dict[str, tp.Any]:
        npFields = ('toolToTrackerTransf', 'toolStlToToolTransf', 'trackerStlToTrackerTransf', 'initialTrackerPose')
        d = attrsWithNumpyAsDict(self,
                                 npFields=npFields,
                                 eqs=dict(
                                     toolToTrackerTransfHistory=lambda a, b: len(a) == len(b) and
                                                                             ((keyA == keyB and
                                                                               array_equalish(a[keyA], b[keyB]))
                                                                              for keyA, keyB in zip(a, b))))

        if 'toolToTrackerTransfHistory' in d:
            d['toolToTrackerTransfHistory'] = [dict(time=key,
                                                    toolToTrackerTransf=val.tolist() if val is not None else None)
                                               for key, val in d['toolToTrackerTransfHistory'].items()]

        for key in ('installPath', 'sessionPath'):
            d.pop(key, None)

        d['usedFor'] = self.usedFor

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any], sessionPath: tp.Optional[str] = None):
        if 'toolToTrackerTransfHistory' in d:
            def convertTransf(transf: tp.List[tp.List[float, float, float]]) -> np.ndarray:
                return np.asarray(transf)

            def convertHistoryListToDict(historyList: tp.List[tp.Dict[str, tp.Any]], field: str,
                                         entryConverter: tp.Callable) -> tp.Dict[str, tp.Any]:
                historyDict = {}
                for entry in historyList:
                    timeStr = entry['time']
                    historyDict[timeStr] = entryConverter(entry[field])
                return historyDict

            d['toolToTrackerTransfHistory'] = convertHistoryListToDict(
                d['toolToTrackerTransfHistory'], 'toolToTrackerTransf', convertTransf)

        npFields = ('toolToTrackerTransf', 'toolStlToToolTransf', 'trackerStlToTrackerTransf', 'initialTrackerPose')

        return attrsWithNumpyFromDict(cls, d, npFields=npFields,
                                      sessionPath=sessionPath)

    @staticmethod
    def _getTimestampStr():
        return datetime.today().strftime('%y%m%d%H%M%S.%f')


@attrs.define
class SubjectTracker(Tool):
    _usedFor: str = 'subject'

    @Tool.toolToTrackerTransf.getter
    def toolToTrackerTransf(self):
        """
        Override parent class to assert that transf is identity, since there should never be a
        different transform specified for the subject tracker.
        """
        assert self._toolToTrackerTransf is None
        return np.eye(4)

    @Tool.toolToTrackerTransf.setter
    def toolToTrackerTransf(self, newTransf):
        raise NotImplementedError()  # should never set this transf


@attrs.define
class CoilTool(Tool):
    _usedFor: str = 'coil'

    @Tool.toolToTrackerTransf.getter
    def toolToTrackerTransf(self) -> np.ndarray | None:
        """
        Override parent class to not assume identity transform by default. Unlike other tools, this will
        return None if no transform is set.
        """
        return self._toolToTrackerTransf


@attrs.define
class Pointer(Tool):
    _usedFor: str = 'pointer'


@attrs.define
class CalibrationPlate(Tool):
    _usedFor: str = 'calibration'


@attrs.define
class ToolPositionsServerInfo:
    _hostname: str = positionsServerHostname
    _pubPort: int = positionsServerPubPort
    _cmdPort: int = positionsServerCmdPort
    _type: str = 'IGTLink'
    _doAutostart: bool = True
    _initKwargs: dict[str, tp.Any] = attrs.field(factory=dict)  # args to be passed when starting ToolPositionsServer (e.g. `{'igtlHostname': '192.168.1.123`}`)

    sigInfoChanged: Signal = attrs.field(init=False, factory=lambda: Signal((tp.List[str],)))

    @property
    def hostname(self):
        return self._hostname

    @property
    def pubPort(self):
        return self._pubPort

    @property
    def cmdPort(self):
        return self._cmdPort

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, newType: tp.Optional[str]):
        if newType == self._type:
            return
        self._type = newType
        self.sigInfoChanged.emit(['type'])

    @property
    def doAutostart(self):
        return self._doAutostart

    @property
    def initKwargs(self):
        return self._initKwargs

    def asDict(self) -> tp.Dict[str, tp.Any]:
        return attrsAsDict(self)


@attrs.define
class Tools(GenericCollection[str, Tool]):
    _positionsServerInfo: ToolPositionsServerInfo = attrs.field(factory=ToolPositionsServerInfo)

    _sessionPath: tp.Optional[str] = None  # used for relative paths

    sigPositionsServerInfoChanged: Signal = attrs.field(init=False, factory=lambda: Signal((tp.List[str],)))
    """ includes list of keys of changed info attributes """

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self._positionsServerInfo.sigInfoChanged.connect(self.sigPositionsServerInfoChanged.emit)

    def addItemFromDict(self, toolDict: dict[str, tp.Any]):
        self.addItem(self._toolFromDict(toolDict, sessionPath=self._sessionPath))

    @property
    def subjectTracker(self) -> tp.Optional[SubjectTracker]:
        subjectTracker = None
        for key, tool in self.items():
            if not tool.isActive:
                continue
            if isinstance(tool, SubjectTracker):
                if subjectTracker is not None:
                    raise ValueError('More than one subject tracker tool is active')
                else:
                    subjectTracker = tool
        return subjectTracker

    @property
    def pointer(self) -> tp.Optional[Pointer]:
        pointer = None
        for key, tool in self.items():
            if not tool.isActive:
                continue
            if isinstance(tool, Pointer):
                if pointer is not None:
                    raise ValueError('More than one pointer tool is active')
                else:
                    pointer = tool
        return pointer

    @property
    def calibrationPlate(self) -> tp.Optional[CalibrationPlate]:
        calibrationPlate = None
        for key, tool in self.items():
            if not tool.isActive:
                continue
            if isinstance(tool, CalibrationPlate):
                if calibrationPlate is not None:
                    raise ValueError('More than one calibration plate is active')
                else:
                    calibrationPlate = tool
        return calibrationPlate

    @property
    def positionsServerInfo(self):
        return self._positionsServerInfo

    def _getActiveToolKeys(self) -> dict[str, tp.Union[str, list[str,...]]]:
        activeToolKeys = {}
        for key, tool in self._tools.items():
            if not tool.isActive:
                continue
            if tool.usedFor in ('subject', 'pointer'):
                assert tool.usedFor not in activeToolKeys, 'More than one active tool for {}'.format(tool.usedFor)

        raise NotImplementedError()  # TODO

    def _checkActiveTools(self):
        raise NotImplementedError()  # TODO: assert that only one subject, pointer, and (for now) coil tracker are active

    @property
    def sessionPath(self):
        return self._sessionPath

    @sessionPath.setter
    def sessionPath(self, newPath: tp.Optional[str]):
        if self._sessionPath == newPath:
            return

        changingKeys = [tool.key for tool in self.values() if tool.filepathsRelTo == '<session>']
        self.sigItemsAboutToChange.emit(changingKeys)
        self._sessionPath = newPath
        with self.sigItemsAboutToChange.blocked(), self.sigItemsChanged.blocked():
            for tool in self.values():
                tool.sessionPath = self._sessionPath
        self.sigItemsChanged.emit(changingKeys)

    def asList(self) -> tp.List[tp.Dict[str, tp.Any]]:
        toolList = super().asList()

        # add ToolPositionsServer info as another entry in list next to tools
        serverInfo = self._positionsServerInfo.asDict()
        if len(serverInfo) > 0:
            serverInfo['key'] = 'ToolPositionsServer'
            toolList.append(serverInfo)

        return toolList

    @classmethod
    def fromList(cls, toolList: tp.List[tp.Dict[str, tp.Any]], sessionPath: tp.Optional[str] = None) -> Tools:

        tools = {}
        serverInfo = None
        for toolDict in toolList:
            key = toolDict['key']
            if key == 'ToolPositionsServer':
                # this is not actually a tool dict, it is connection info for ToolPositionsServer
                serverInfo = ToolPositionsServerInfo(**{key: val for key, val in toolDict.items() if key != 'key'})
            else:
                tools[key] = cls._toolFromDict(toolDict, sessionPath=sessionPath)

        if serverInfo is None:
            serverInfo = ToolPositionsServerInfo()

        return cls(items=tools, sessionPath=sessionPath, positionsServerInfo=serverInfo)

    def _onItemChanged(self, key: str, attribKeys: tp.Optional[list[str]] = None):
        super()._onItemChanged(key=key, attribKeys=attribKeys)
        if attribKeys is not None and 'usedFor' in attribKeys:
            self._onToolUsedForChanged(key=key)

    def _onToolUsedForChanged(self, key: str):
        toolDict = self[key].asDict()
        tool = self._toolFromDict(toolDict, sessionPath=self._sessionPath)
        self.setItem(tool)

    @classmethod
    def _toolFromDict(cls, toolDict: tp.Dict[str, tp.Any], sessionPath: tp.Optional[str] = None) -> Tool:
        usedFor = toolDict['usedFor']

        match usedFor:
            case 'coil':
                ToolCls = CoilTool
            case 'pointer':
                ToolCls = Pointer
            case 'calibration':
                ToolCls = CalibrationPlate
            case 'subject':
                ToolCls = SubjectTracker
            case '' | 'visualization':
                ToolCls = Tool
            case _:
                raise NotImplementedError('Unexpected tool usedFor: {}'.format(usedFor))

        tool = ToolCls.fromDict(toolDict, sessionPath=sessionPath)

        return tool
    
    
    