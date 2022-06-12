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
import pyvista as pv
import pyvistaqt as pvqt
from pyqtgraph.dockarea import DockArea, Dock
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp
from typing import ClassVar

from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Devices.IGTLinkToolPositionsServer import IGTLinkToolPositionsServer
from RTNaBS.Navigator.Model.Session import Session, Tool, CoilTool, SubjectTracker, Target
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define
class TargetingCoordinator:
    _session: Session
    _currentTargetKey: tp.Optional[str] = None
    _positionsClient: ToolPositionsClient = attrs.field(factory=ToolPositionsClient)
    _activeCoilKey: tp.Optional[str] = None

    _currentCoilToMRITransform: tp.Optional[Transform] = attrs.field(init=False, default=None)  # relative to head tracker

    sigCurrentTargetChanged: Signal = attrs.field(init=False, factory=Signal)
    sigCurrentCoilPositionChanged: Signal = attrs.field(init=False, factory=Signal)
    sigCurrentSubjectPositionChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        self._positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)
        self._session.tools[self.activeCoilKey].sigToolChanged.connect(lambda _: self.sigCurrentCoilPositionChanged.emit())

    def _onSessionTargetsChanged(self, targetKeysChanged: tp.List[str]):
        if self._currentTargetKey is not None and self._currentTargetKey in targetKeysChanged:
            self.sigCurrentTargetChanged.emit()

    def _onLatestPositionsChanged(self):
        self._currentCoilToMRITransform = None  # clear any previously cached value
        self.sigCurrentCoilPositionChanged.emit()
        self.sigCurrentSubjectPositionChanged.emit()

    @property
    def session(self):
        return self._session

    @property
    def currentTargetKey(self):
        if True:
            # TODO: debug, delete!
            return list(self._session.targets.keys())[0]

        return self._currentTargetKey

    @currentTargetKey.setter
    def currentTargetKey(self, newKey: tp.Optional[str]):
        if self._currentTargetKey == newKey:
            return
        self._currentTargetKey = newKey
        self.sigCurrentTargetChanged.emit()

    @property
    def currentTarget(self) -> tp.Optional[Target]:
        if self.currentTargetKey is not None:
            return self._session.targets[self.currentTargetKey]
        else:
            return None

    @property
    def activeCoilKey(self):
        if self._activeCoilKey is None:
            # if no active coil specified, use first active coil in list
            for toolKey, tool in self._session.tools.items():
                if tool.isActive and isinstance(tool, CoilTool):
                    self._activeCoilKey = toolKey
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