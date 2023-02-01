from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import logging
import multiprocessing as mp
import numpy as np
import os
import pathlib

import pandas as pd
import shutil
import typing as tp
from typing import ClassVar

from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Navigator.Model.Session import Session, Tool, CoilTool, SubjectTracker, Target, Sample
from RTNaBS.util.CoilOrientations import PoseMetricCalculator
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms, applyTransform
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget


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


@attrs.define
class TargetingCoordinator:
    _session: Session
    _currentTargetKey: tp.Optional[str] = None
    _currentSampleKey: tp.Optional[str] = None
    _positionsClient: ToolPositionsClient = attrs.field(factory=ToolPositionsClient)
    _activeCoilKey: tp.Optional[str] = None

    _currentCoilToMRITransform: tp.Optional[Transform] = attrs.field(init=False, default=None)  # relative to head tracker
    _currentPoseMetrics: PoseMetricCalculator = attrs.field(init=False)
    _currentSamplePoseMetrics: PoseMetricCalculator = attrs.field(init=False)

    sigCurrentTargetChanged: Signal = attrs.field(init=False, factory=Signal)
    sigCurrentSampleChanged: Signal = attrs.field(init=False, factory=Signal)
    sigCurrentCoilPositionChanged: Signal = attrs.field(init=False, factory=Signal)
    sigCurrentSubjectPositionChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        self._positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)
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

        if self._activeCoilKey is not None:
            self._session.tools[self._activeCoilKey].sigKeyChanged.connect(self._onActiveCoilToolKeyChanged)
            self._session.tools[self._activeCoilKey].sigItemChanged.connect(self._onActiveCoilToolChanged)

    def _onActiveCoilToolKeyChanged(self, oldKey: str, newKey: str):
        logger.info(f'Recording active coil key change from {oldKey} to {newKey}')
        assert oldKey == self._activeCoilKey
        self._activeCoilKey = newKey

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
        logger.info(f'Clearing previously active coil key {self._activeCoilKey}')
        if self._activeCoilKey in self._session.tools:
            self._session.tools[self._activeCoilKey].sigKeyChanged.disconnect(self._onActiveCoilToolKeyChanged)
            self._session.tools[self._activeCoilKey].sigItemChanged.disconnect(self._onActiveCoilToolChanged)
        self._activeCoilKey = None  # clear cached active coil key, will be replaced with autodiscovered active key later as needed

        self._currentCoilToMRITransform = None  # clear any previously cached value
        self.sigCurrentCoilPositionChanged.emit()

    def _onSessionTargetsChanged(self, targetKeysChanged: tp.List[str], targetAttribsChanged: tp.Optional[tp.List[str]]):
        if self._currentTargetKey is not None and self._currentTargetKey in targetKeysChanged:
            logger.debug('Current target changed')
            self.sigCurrentTargetChanged.emit()

    def _onSessionSamplesChanged(self, sampleKeysChanged: tp.List[str], sampleAttribsChanged: tp.Optional[tp.List[str]]):
        if self._currentSampleKey is not None and self._currentSampleKey in sampleKeysChanged:
            self.sigCurrentSampleChanged.emit()

    def _onLatestPositionsChanged(self):
        self._currentCoilToMRITransform = None  # clear any previously cached value
        self.sigCurrentCoilPositionChanged.emit()
        self.sigCurrentSubjectPositionChanged.emit()

    @property
    def session(self):
        return self._session

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
    def activeCoilKey(self):
        if self._activeCoilKey is None:
            # if no active coil specified, use first active coil in list
            for toolKey, tool in self._session.tools.items():
                if tool.isActive and isinstance(tool, CoilTool):
                    self._activeCoilKey = toolKey
                    logger.info(f'Detected active coil key {self._activeCoilKey}')
                    self._session.tools[self._activeCoilKey].sigKeyChanged.connect(self._onActiveCoilToolKeyChanged)
                    self._session.tools[self._activeCoilKey].sigItemChanged.connect(self._onActiveCoilToolChanged)
                    break
            if self._activeCoilKey is None:
                raise KeyError('No active coil tool!')
        return self._activeCoilKey

    @activeCoilKey.setter
    def activeCoilKey(self, newKey: tp.Optional[str]):
        if newKey is not None:
            assert newKey in self._session.tools
            coilTool = self._session.tools[newKey]
            assert coilTool.isActive
            assert isinstance(coilTool, CoilTool)
        raise NotImplementedError()  # TODO
        # TODO: disconnect signals for previous active coil key
        # TODO: connect signals for new active coil key

    @property
    def currentCoilToMRITransform(self) -> tp.Optional[Transform]:
        if self._currentCoilToMRITransform is None:
            coilTrackerToCameraTransf = self._positionsClient.getLatestTransf(self.activeCoilKey, None)
            subjectTrackerToCameraTransf = self._positionsClient.getLatestTransf(self._session.tools.subjectTracker.key,
                                                                                 None)
            coilToTrackerTransf = self._session.tools[self.activeCoilKey].toolToTrackerTransf

            if coilToTrackerTransf is None or coilTrackerToCameraTransf is None or subjectTrackerToCameraTransf is None:
                # cannot compute valid position
                return None

            coilToMRITransform = concatenateTransforms([
                coilToTrackerTransf,
                coilTrackerToCameraTransf,
                invertTransform(subjectTrackerToCameraTransf),
                self._session.subjectRegistration.trackerToMRITransf
            ])

            self._currentCoilToMRITransform = coilToMRITransform

        return self._currentCoilToMRITransform

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
                            coilCoord = applyTransform(transf, np.asarray([0, 0, 0]))
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
                                        - self.currentTarget.targetCoord)]))
                    case _:
                        raise NotImplementedError
                return targetCoord
            case _:
                raise NotImplementedError