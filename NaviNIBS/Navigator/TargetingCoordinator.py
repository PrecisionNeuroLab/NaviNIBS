from __future__ import annotations

import asyncio

import attrs
import logging
import multiprocessing as mp
import numpy as np
import os
import pathlib
import time

import pandas as pd
import shutil
import typing as tp
from typing import ClassVar

from NaviNIBS.Devices.ToolPositionsClient import ToolPositionsClient
from NaviNIBS.Navigator.Model.Session import Session, Tool, CoilTool, SubjectTracker, Target, Sample
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.CoilOrientations import PoseMetricCalculator
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import invertTransform, concatenateTransforms, applyTransform
from NaviNIBS.util.GUI.QFileSelectWidget import QFileSelectWidget


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define(frozen=True)
class ProjectionSpecification:
    """
    Specifiers to describe projection of an orientation down the depth axis to a target plane (or sphere)
    """
    _toOrientation: str  # 'target' or 'coil'
    _toDepth: str  # if toOrientation=='coil', can be one of ['coil', 'skin', 'gm']; if toOrientation=='target', can be ['coil', 'skin', 'gm', 'target']
    _toShape: str  # 'sphere' or 'plane'


_targetingCoordinatorSingleton = None


@attrs.define
class TargetingCoordinator:
    _session: Session = attrs.field(repr=False)
    _currentTargetKey: tp.Optional[str] = None
    _currentSampleKey: tp.Optional[str] = None
    _positionsClient: ToolPositionsClient = attrs.field(factory=ToolPositionsClient)
    __cachedActiveCoilKey: str | None = attrs.field(init=False, default=None)
    __cachedActiveCoil: CoilTool | None = attrs.field(init=False, default=None)

    _currentCoilToMRITransform: tp.Optional[Transform] = attrs.field(init=False, default=None)  # relative to head tracker
    _currentPoseMetrics: PoseMetricCalculator = attrs.field(init=False, repr=False)
    _currentSamplePoseMetrics: PoseMetricCalculator = attrs.field(init=False, repr=False)

    _isOnTargetWhenDistErrorUnder: float = 1  # in mm
    _isOnTargetWhenZAngleErrorUnder: float = 2.  # in deg
    _isOnTargetWhenHorizAngleErrorUnder: float = 4.  # in deg
    _isOnTargetWhenZDistErrorUnder: float = 4.  # in mm
    _isOnTargetMinTime: float = 0.5  # in sec, don't report being on target until after staying on target for at least this long
    _isOffTargetWhenDistErrorExceeds: float = 2.  # in mm
    _isOffTargetWhenZAngleErrorExceeds: float = 4.  # in deg
    _isOffTargetWhenHorizAngleErrorExceeds: float = 8.  # in deg
    _isOffTargetWhenZDistErrorExceeds: float = 8.  # in mm
    _isOffTargetMinTime: float = 0.1  # in sec, don't report being off target until after staying off target for at least this long
    _doMonitorOnTarget: bool = False
    _monitorOnTargetRate: float = 5.  # in Hz
    _isOnTarget: bool = attrs.field(init=False, default=False)
    _onTargetMaybeChangedAtTime: float | None = None
    _needToCheckIfOnTarget: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    _monitorOnTargetTask: asyncio.Task | None = attrs.field(init=False, default=None, repr=False)
    sigIsOnTargetChanged: Signal = attrs.field(init=False, factory=Signal, repr=False)  # only emitted when doMonitorOnTarget is True and isOnTarget changes

    sigActiveCoilKeyChanged: Signal = attrs.field(init=False, factory=Signal, repr=False)
    sigCurrentTargetChanged: Signal = attrs.field(init=False, factory=Signal, repr=False)
    """
    Emitted when a different target becomes current AND when an attribute of the current target changes. 
    """
    sigCurrentSampleChanged: Signal = attrs.field(init=False, factory=Signal, repr=False)
    sigCurrentCoilPositionChanged: Signal = attrs.field(init=False, factory=Signal, repr=False)
    sigCurrentSubjectPositionChanged: Signal = attrs.field(init=False, factory=Signal, repr=False)

    def __attrs_post_init__(self):
        self._positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)
        self._session.tools.sigItemsAboutToChange.connect(self._onToolsAboutToChange)
        self._session.tools.sigItemsChanged.connect(self._onToolsChanged)
        self._session.targets.sigItemsChanged.connect(self._onSessionTargetsChanged)
        self._session.targets.sigItemKeyAboutToChange.connect(self._onSessionTargetKeyAboutToChange)
        self._session.targets.sigItemKeyChanged.connect(
            self._onSessionTargetKeyChanged)

        self._currentPoseMetrics = PoseMetricCalculator(
            session=self._session,
            sample=Sample(
                key='CurrentPose',
                timestamp=pd.Timestamp.now()
            )
        )
        self.sigCurrentTargetChanged.connect(lambda: setattr(self._currentPoseMetrics.sample, 'targetKey', self.currentTargetKey))
        self.sigCurrentCoilPositionChanged.connect(self._updateCurrentPoseMetricsSample)
        # TODO: connect signal so that change in activeCoilKey is reflected in currentPoseMetrics.sample.coilKey

        self._currentSamplePoseMetrics = PoseMetricCalculator(
            session=self._session,
            sample=self._session.samples[self._currentSampleKey] if self._currentSampleKey is not None else None
        )

        self.sigCurrentTargetChanged.connect(lambda: self._needToCheckIfOnTarget.set())

        if self._doMonitorOnTarget:
            self._startMonitoringOnTarget()

    @property
    def _cachedActiveCoilKey(self):
        return self.__cachedActiveCoilKey

    @_cachedActiveCoilKey.setter
    def _cachedActiveCoilKey(self, newKey: str | None):
        if newKey == self.__cachedActiveCoilKey:
            return
        oldKey = self.__cachedActiveCoilKey
        logger.info(f'Recording active coil key change from {oldKey} to {newKey}')

        if newKey is not None and self.__cachedActiveCoil is self._session.tools[newKey]:
            # key of existing active coil changed, don't need to disconnect/reconnect signals
            self.__cachedActiveCoilKey = newKey
        else:
            if self.__cachedActiveCoil is not None:
                self.__cachedActiveCoil.sigKeyChanged.disconnect(self._onActiveCoilToolKeyChanged)
                self.__cachedActiveCoil.sigItemChanged.disconnect(self._onActiveCoilToolChanged)
                self.__cachedActiveCoil = None

            self.__cachedActiveCoilKey = newKey
            if newKey is not None:
                self.__cachedActiveCoil = self._session.tools[newKey]
                self.__cachedActiveCoil.sigKeyChanged.connect(self._onActiveCoilToolKeyChanged)
                self.__cachedActiveCoil.sigItemChanged.connect(self._onActiveCoilToolChanged)

        self.sigActiveCoilKeyChanged.emit()

    def _onToolsAboutToChange(self, keys: list[str], attribs: list[str] | None = None):
        if attribs is None or 'isActive' in attribs:
            self._cachedActiveCoilKey = None  # clear cache so that active coil is re-detected on next request

    def _onToolsChanged(self, keys: list[str], attribs: list[str] | None = None):
        if attribs is None or 'isActive' in attribs:
            self._cachedActiveCoilKey = None  # clear cache so that active coil is re-detected on next request

    def _onActiveCoilToolKeyChanged(self, oldKey: str, newKey: str):
        assert oldKey == self._cachedActiveCoilKey
        self._cachedActiveCoilKey = newKey

    def _onActiveCoilToolChanged(self, key: str, attribs: tp.Optional[list[str]] = None):
        """
        This is called whenever any attribute of active coil tool changes, not just when it becomes inactive
        """
        if attribs is None:
            if key in self._session.tools and self._session.tools[key].isActive:
                # tool is still active
                self._currentCoilToMRITransform = None  # clear any previously cached value
                self.sigCurrentCoilPositionChanged.emit()  # transform may have changed
                return
        elif 'isActive' not in attribs:
            assert self._session.tools[key].isActive
            self._currentCoilToMRITransform = None  # clear any previously cached value
            self.sigCurrentCoilPositionChanged.emit()  # transform may have changed
            return

        # tool is no longer active
        self._cachedActiveCoilKey = None

        self._currentCoilToMRITransform = None  # clear any previously cached value
        self.sigCurrentCoilPositionChanged.emit()

    def _onSessionTargetsChanged(self, targetKeysChanged: tp.List[str], targetAttribsChanged: tp.Optional[tp.List[str]]):
        if self._currentTargetKey is not None and self._currentTargetKey in targetKeysChanged:

            if self._currentTargetKey not in self.session.targets:
                logger.debug('Current target deleted')
                self.currentTargetKey = None  # this will emit sigCurrentTargetChanged
                return

            logger.debug('Current target changed')
            self.sigCurrentTargetChanged.emit()

    def _onSessionSamplesChanged(self, sampleKeysChanged: tp.List[str], sampleAttribsChanged: tp.Optional[tp.List[str]]):
        if self._currentSampleKey is not None and self._currentSampleKey in sampleKeysChanged:
            self.sigCurrentSampleChanged.emit()

    def _onLatestPositionsChanged(self):
        self._currentCoilToMRITransform = None  # clear any previously cached value
        self.sigCurrentCoilPositionChanged.emit()
        self.sigCurrentSubjectPositionChanged.emit()
        self._needToCheckIfOnTarget.set()

    async def _loop_monitorOnTarget(self):
        while True:
            await self._needToCheckIfOnTarget.wait()
            self._checkIfOnTarget()
            await asyncio.sleep(1/self._monitorOnTargetRate)

    def _checkIfOnTarget(self):
        if not self._doMonitorOnTarget:
            return

        self._needToCheckIfOnTarget.clear()

        if self._isOnTarget:
            # previously on target
            isStillOnTarget = True
            if isStillOnTarget:
                depthAngleError = self._currentPoseMetrics.getDepthAngleError()
                if np.isnan(depthAngleError) or abs(depthAngleError) > self._isOffTargetWhenZAngleErrorExceeds:
                    isStillOnTarget = False

            if isStillOnTarget:
                horizAngleError = self._currentPoseMetrics.getHorizAngleError()
                if np.isnan(horizAngleError) or abs(horizAngleError) > self._isOffTargetWhenHorizAngleErrorExceeds:
                    isStillOnTarget = False

            if isStillOnTarget:
                horizDistError = self._currentPoseMetrics.getTargetErrorAtCoil()
                if np.isnan(horizDistError) or abs(horizDistError) > self._isOffTargetWhenDistErrorExceeds:
                    isStillOnTarget = False

            if isStillOnTarget:
                depthDistError = self._currentPoseMetrics.getDepthOffsetError()
                if np.isnan(depthDistError) or abs(depthDistError) > self._isOffTargetWhenZDistErrorExceeds:
                    isStillOnTarget = False

            if self._onTargetMaybeChangedAtTime is not None:
                if isStillOnTarget:
                    self._onTargetMaybeChangedAtTime = None
                else:
                    currentTime = time.time()
                    if currentTime - self._onTargetMaybeChangedAtTime > self._isOffTargetMinTime:
                        self._onTargetMaybeChangedAtTime = None
                        self._isOnTarget = False
                        logger.debug(f'isOnTarget: {self._isOnTarget}')
                        self.sigIsOnTargetChanged.emit()

            elif not isStillOnTarget:
                self._onTargetMaybeChangedAtTime = time.time()
                if self._isOffTargetMinTime > 0:
                    pass # don't report change yet, not sure if this is an erroneous measure
                else:
                    self._isOnTarget = False
                    logger.debug(f'isOnTarget: {self._isOnTarget}')
                    self.sigIsOnTargetChanged.emit()

            else:
                pass  # still on target

        else:
            # not previously on target
            isStillOffTarget = False
            if not isStillOffTarget:
                depthAngleError = self._currentPoseMetrics.getDepthAngleError()
                if np.isnan(depthAngleError) or abs(depthAngleError) > self._isOnTargetWhenZAngleErrorUnder:
                    isStillOffTarget = True

            if not isStillOffTarget:
                horizAngleError = self._currentPoseMetrics.getHorizAngleError()
                if np.isnan(horizAngleError) or abs(horizAngleError) > self._isOnTargetWhenHorizAngleErrorUnder:
                    isStillOffTarget = True

            if not isStillOffTarget:
                horizDistError = self._currentPoseMetrics.getTargetErrorAtCoil()
                if np.isnan(horizDistError) or abs(horizDistError) > self._isOnTargetWhenDistErrorUnder:
                    isStillOffTarget = True

            if not isStillOffTarget:
                depthDistError = self._currentPoseMetrics.getDepthOffsetError()
                if np.isnan(depthDistError) or abs(depthDistError) > self._isOnTargetWhenZDistErrorUnder:
                    isStillOffTarget = True

            if self._onTargetMaybeChangedAtTime is not None:
                if isStillOffTarget:
                    self._onTargetMaybeChangedAtTime = None
                else:
                    currentTime = time.time()
                    if currentTime - self._onTargetMaybeChangedAtTime > self._isOnTargetMinTime:
                        self._onTargetMaybeChangedAtTime = None
                        self._isOnTarget = True
                        logger.debug(f'isOnTarget: {self._isOnTarget}')
                        self.sigIsOnTargetChanged.emit()

            elif not isStillOffTarget:
                self._onTargetMaybeChangedAtTime = time.time()
                if self._isOnTargetMinTime > 0:
                    pass  # don't report change yet, wait to be stable on target
                else:
                    self._isOnTarget = True
                    logger.debug(f'isOnTarget: {self._isOnTarget}')
                    self.sigIsOnTargetChanged.emit()

            else:
                pass  # still off target

        if self._onTargetMaybeChangedAtTime is not None:
            self._needToCheckIfOnTarget.set()  # check back again soon, even if tool positions didn't change


    @property
    def session(self):
        return self._session

    @property
    def positionsClient(self):
        return self._positionsClient

    @property
    def currentTargetKey(self):
        return self._currentTargetKey

    @currentTargetKey.setter
    def currentTargetKey(self, newKey: tp.Optional[str]):
        if self._currentTargetKey == newKey:
            return
        self._currentTargetKey = newKey
        logger.debug(f'Current target key changed to {newKey}')
        self.sigCurrentTargetChanged.emit()

    @property
    def currentTarget(self) -> tp.Optional[Target]:
        if self.currentTargetKey is not None:
            return self._session.targets[self.currentTargetKey]
        else:
            return None

    @property
    def currentSampleKey(self):
        return self._currentSampleKey

    @currentSampleKey.setter
    def currentSampleKey(self, newKey: tp.Optional[str]):
        if self._currentSampleKey == newKey:
            return
        self._currentSampleKey = newKey
        self._currentSamplePoseMetrics.sample = self.currentSample
        self.sigCurrentSampleChanged.emit()

    @property
    def currentSample(self) -> tp.Optional[Sample]:
        if self.currentSampleKey is not None:
            return self._session.samples[self.currentSampleKey]
        else:
            return None

    @property
    def currentPoseMetrics(self) -> PoseMetricCalculator:
        return self._currentPoseMetrics

    @property
    def currentSamplePoseMetrics(self) -> PoseMetricCalculator:
        return self._currentSamplePoseMetrics

    @property
    def activeCoilTool(self):
        if self.activeCoilKey is None:
            return None
        else:
            return self._session.tools[self.activeCoilKey]

    @property
    def activeCoilKey(self) -> str | None:
        if self._cachedActiveCoilKey is None:
            # if no active coil specified, use first active coil in list
            for toolKey, tool in self._session.tools.items():
                if tool.isActive and isinstance(tool, CoilTool):
                    self._cachedActiveCoilKey = toolKey
                    logger.info(f'Detected active coil key {self._cachedActiveCoilKey}')
                    break
            if self._cachedActiveCoilKey is None:
                return None
        return self._cachedActiveCoilKey

    @property
    def currentCoilToMRITransform(self) -> tp.Optional[Transform]:
        if self._currentCoilToMRITransform is None:
            if self.activeCoilTool is None:
                # no coil active
                return None
            coilTrackerToCameraTransf = self._positionsClient.getLatestTransf(self.activeCoilTool.trackerKey, None)
            subjectTrackerToCameraTransf = self._positionsClient.getLatestTransf(self._session.tools.subjectTracker.trackerKey,
                                                                                 None)
            coilToTrackerTransf = self.activeCoilTool.toolToTrackerTransf

            subjectTrackerToMRITransf = self._session.subjectRegistration.trackerToMRITransf

            if coilToTrackerTransf is None \
                    or coilTrackerToCameraTransf is None \
                    or subjectTrackerToCameraTransf is None\
                    or subjectTrackerToMRITransf is None:
                # cannot compute valid position
                return None

            coilToMRITransform = concatenateTransforms([
                coilToTrackerTransf,
                coilTrackerToCameraTransf,
                invertTransform(subjectTrackerToCameraTransf),
                subjectTrackerToMRITransf
            ])

            self._currentCoilToMRITransform = coilToMRITransform

        return self._currentCoilToMRITransform

    @property
    def doMonitorOnTarget(self):
        return self._doMonitorOnTarget

    @doMonitorOnTarget.setter
    def doMonitorOnTarget(self, doMonitor: bool):
        if doMonitor == self._doMonitorOnTarget:
            return
        self._doMonitorOnTarget = doMonitor
        if doMonitor:
            self._startMonitoringOnTarget()
        else:
            self._stopMonitoringOnTarget()

    @property
    def isOnTarget(self):
        if not self._doMonitorOnTarget:
            self.doMonitorOnTarget = True
        assert self._doMonitorOnTarget
        if self._needToCheckIfOnTarget.is_set():
            # check immediately to make sure we don't give outdated information
            self._checkIfOnTarget()
        return self._isOnTarget

    def _startMonitoringOnTarget(self):
        assert self._monitorOnTargetTask is None
        self._needToCheckIfOnTarget.set()
        self._monitorOnTargetTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._loop_monitorOnTarget))

    def _stopMonitoringOnTarget(self):
        assert self._monitorOnTargetTask is not None
        self._monitorOnTargetTask.cancel()
        self._monitorOnTargetTask = None

    def _onSessionTargetKeyAboutToChange(self, fromKey: str, toKey: str):
        if self._currentTargetKey == fromKey:
            self._currentTargetKey = toKey
            # other changes will be handled when sigItemsChanged is emitted later

    def _onSessionTargetKeyChanged(self, fromKey: str, toKey: str):
        if fromKey == self._currentPoseMetrics.sample.targetKey:
            self._currentPoseMetrics.sample.targetKey = toKey

    def _updateCurrentPoseMetricsSample(self):
        self._currentPoseMetrics.sample.timestamp = pd.Timestamp.now()
        self._currentPoseMetrics.sample.coilToMRITransf = self.currentCoilToMRITransform

    def createTargetFromCurrentSample(self, doAddToSession: bool = True) -> Target:
        logger.info('Creating target from current sample')
        currentSample = self.currentSample
        if currentSample is None:
            raise ValueError('No sample currently set')
        return self._createTargetFromSample(sample=currentSample, doAddToSession=doAddToSession)

    def createTargetFromCurrentPose(self, doAddToSession: bool = True) -> Target:
        logger.info('Creating target from current pose')
        sample = attrs.evolve(self._currentPoseMetrics.sample,
                              _key='Pose ' + self._currentPoseMetrics.sample.timestamp.strftime('%y.m.%d %H:%M:%S.%f'))
        return self._createTargetFromSample(sample=sample, doAddToSession=doAddToSession)

    def _createTargetFromSample(self, sample: Sample, doAddToSession) -> Target:
        baseKey = sample.key
        targetKey = baseKey
        counter = 1
        while targetKey in self._session.targets:
            counter += 1
            targetKey = f'{baseKey} ({counter})'


        calculator = PoseMetricCalculator(sample=sample, session=self.session)

        coilToScalpDist = calculator.getSampleCoilToScalpDist()
        coilToBrainDist = calculator.getSampleCoilToCortexDist()
        handleAngle = calculator.getAngleFromMidline()

        targetCoord = applyTransform(sample.coilToMRITransf, np.asarray([0, 0, -coilToBrainDist]))
        entryCoord = applyTransform(sample.coilToMRITransf, np.asarray([0, 0, -coilToScalpDist]))

        target = Target(
            key=targetKey,
            targetCoord=targetCoord,
            entryCoord=entryCoord,
            angle=handleAngle,
            depthOffset=coilToScalpDist,
            coilToMRITransf=sample.coilToMRITransf
        )

        if doAddToSession:
            self.session.targets.addItem(target)

        return target

    def getTargetingCoord(self, orientation: str, depth: tp.Union[str, ProjectionSpecification]) -> tp.Optional[np.ndarray]:
        """
        Convenience function for getting a specific coordinate related to targeting orientations.

        Abstracts some of the math needed for things like projecting the current coil orientation down the depth axis
        to plane of target.

        May return None if we are currently missing pose information for a tracker, etc.
        """
        match depth:
            case ProjectionSpecification():
                raise NotImplementedError  # TODO
            case 'coil':
                match orientation:
                    case 'target':
                        if self.currentTarget is None:
                            coilCoord = None
                        else:
                            coilCoord = self.currentTarget.entryCoordPlusDepthOffset
                    case 'coil':
                        transf = self.currentCoilToMRITransform
                        if transf is None:
                            coilCoord = None
                        else:
                            coilCoord = applyTransform(transf, np.asarray([0, 0, 0]), doCheck=False)
                    case _:
                        raise NotImplementedError
                return coilCoord
            case 'skin':
                raise NotImplementedError  # TODO
            case 'gm':
                raise NotImplementedError  # TODO
            case 'target':
                match orientation:
                    case 'target':
                        if self.currentTarget is None:
                            targetCoord = None
                        else:
                            targetCoord = self.currentTarget.targetCoord
                    case 'coil':
                        transf = self.currentCoilToMRITransform
                        if transf is None:
                            targetCoord = None
                        else:
                            if self.currentTarget is None:
                                targetCoord = None
                            else:
                                targetCoord = applyTransform(
                                    transf, np.asarray([0, 0, -np.linalg.norm(
                                        self.currentTarget.entryCoordPlusDepthOffset \
                                        - self.currentTarget.targetCoord)]),
                                    doCheck=False)
                    case _:
                        raise NotImplementedError
                return targetCoord
            case _:
                raise NotImplementedError

    @classmethod
    def getSingleton(cls, session: Session) -> TargetingCoordinator:
        """
        Get the singleton instance of the TargetingCoordinator for the given session.
        """
        global _targetingCoordinatorSingleton
        if _targetingCoordinatorSingleton is None:
            _targetingCoordinatorSingleton = cls(session=session)
        else:
            assert _targetingCoordinatorSingleton.session is session
        return _targetingCoordinatorSingleton

