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

from .. import MainViewPanel
from .NavigationView import NavigationView, TargetingCrosshairsView
from .TargetingCoordinator import TargetingCoordinator
from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Devices.IGTLinkToolPositionsServer import IGTLinkToolPositionsServer
from RTNaBS.Navigator.GUI.Widgets.SamplesTreeWidget import SamplesTreeWidget
from RTNaBS.Navigator.GUI.Widgets.TargetsTreeWidget import TargetsTreeWidget
from RTNaBS.Navigator.GUI.Widgets.TrackingStatusWidget import TrackingStatusWidget
from RTNaBS.Navigator.Model.Session import Session, Target
from RTNaBS.Navigator.Model.Tools import Tool, CoilTool, SubjectTracker, CalibrationPlate, Pointer
from RTNaBS.Navigator.Model.Samples import Samples, Sample, getSampleTimestampNow
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define
class NavigatePanel(MainViewPanel):
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.head-flash'))
    _trackingStatusWdgt: TrackingStatusWidget = attrs.field(init=False)
    _targetsTreeWdgt: TargetsTreeWidget = attrs.field(init=False)
    _samplesTreeWdgt: SamplesTreeWidget = attrs.field(init=False)
    _sampleBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _sampleToTargetBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _views: tp.Dict[str, NavigationView] = attrs.field(init=False, factory=dict)
    _viewsDock: DockArea = attrs.field(init=False)

    _coordinator: TargetingCoordinator = attrs.field(init=False)

    _hasInitialized: bool = attrs.field(init=False, default=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        sidebar = QtWidgets.QWidget()
        sidebar.setLayout(QtWidgets.QVBoxLayout())
        sidebar.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.MinimumExpanding)
        self._wdgt.layout().addWidget(sidebar)

        self._trackingStatusWdgt = TrackingStatusWidget(session=self.session,
                                                        hideToolTypes=[CalibrationPlate, Pointer])
        sidebar.layout().addWidget(self._trackingStatusWdgt.wdgt)

        targetsBox = QtWidgets.QGroupBox('Targets')
        targetsBox.setLayout(QtWidgets.QVBoxLayout())
        sidebar.layout().addWidget(targetsBox)

        self._targetsTreeWdgt = TargetsTreeWidget(
            session=self.session
        )
        self._targetsTreeWdgt.sigCurrentTargetChanged.connect(self._onCurrentTargetChanged)
        targetsBox.layout().addWidget(self._targetsTreeWdgt.wdgt)

        currentErrorBox = QtWidgets.QGroupBox('Current error')
        currentErrorBox.setLayout(QtWidgets.QVBoxLayout())
        sidebar.layout().addWidget(currentErrorBox)
        # TODO: create widgets in currentErrorBox providing feedback on current coil placement relative to active target

        samplesBox = QtWidgets.QGroupBox('Samples')
        samplesBox.setLayout(QtWidgets.QVBoxLayout())
        sidebar.layout().addWidget(samplesBox)

        btn = QtWidgets.QPushButton('Add a sample')
        btn.clicked.connect(self._onSampleBtnClicked)
        samplesBox.layout().addWidget(btn)
        self._sampleBtn = btn
        # TODO: change color to warning indicator when coil or tracker are not visible

        btn = QtWidgets.QPushButton('Convert sample to target')
        btn.clicked.connect(self._onSampleToTargetBtnClicked)
        samplesBox.layout().addWidget(btn)
        self._sampleToTargetBtn = btn
        # TODO: only enable when one or more samples are selected

        self._samplesTreeWdgt = SamplesTreeWidget(
            session=self.session
        )
        self._samplesTreeWdgt.sigCurrentSampleChanged.connect(self._onCurrentSampleChanged)
        samplesBox.layout().addWidget(self._samplesTreeWdgt.wdgt)

        self._viewsDock = DockArea()
        self._wdgt.layout().addWidget(self._viewsDock)

    def canBeEnabled(self) -> bool:
        return self.session is not None and self.session.MRI.isSet and self.session.headModel.isSet \
               and self.session.tools.subjectTracker is not None \
               and self.session.subjectRegistration.isRegistered

    def _finishInitialization(self):
        super()._finishInitialization()
        if not self._hasInitialized:
            self._initializePanel()

    def _onSessionSet(self):
        super()._onSessionSet()
        if self._hasInitialized:
            raise NotImplementedError()  # TODO: handle change in session when previously initialized with different session
        else:
            # we'll handle the new session when initializing later
            pass

    def _initializePanel(self):
        assert not self._hasInitialized
        self._hasInitialized = True
        self._trackingStatusWdgt.session = self.session
        self._coordinator = TargetingCoordinator(session=self._session,
                                                 currentTargetKey=self._targetsTreeWdgt.currentTargetKey,
                                                 currentSampleKey=self._samplesTreeWdgt.currentSampleKey)
        self._coordinator.sigCurrentTargetChanged.connect(lambda: self._onCurrentTargetChanged(self._coordinator.currentTargetKey))
        self._coordinator.sigCurrentSampleChanged.connect(lambda: self._onCurrentSampleChanged(self._coordinator.currentSampleKey))
        self._targetsTreeWdgt.session = self._session
        self._samplesTreeWdgt.session = self._session
        self._initializeDefaultViews()  # TODO: only do this if not restoring from previously saved config

    def _onCurrentTargetChanged(self, newTargetKey: str):
        """
        Called when targetTreeWdgt selection or coordinator currentTarget changes, NOT when attributes of currently selected target change
        """
        if self._hasInitialized:
            self._coordinator.currentTargetKey = newTargetKey
            self._targetsTreeWdgt.currentTargetKey = newTargetKey

    def _onCurrentSampleChanged(self, newSampleKey: str):
        """
        Called when sampleTreeWdgt selection or coordinator currentSample changes, NOT when attributes of currently selected sample change
        """
        if self._hasInitialized:
            self._coordinator.currentSampleKey = newSampleKey
            self._samplesTreeWdgt.currentSampleKey = newSampleKey

    def _onSampleBtnClicked(self, _):
        timestamp = getSampleTimestampNow()
        sampleKey = self.session.samples.getUniqueSampleKey(timestamp=timestamp)
        coilToMRITransf = self._coordinator.currentCoilToMRITransform  # may be None if missing a tracker, etc.

        sample = Sample(
            key=sampleKey,
            timestamp=timestamp,
            coilToMRITransf=coilToMRITransf,
            targetKey=self._coordinator.currentTargetKey,
            coilKey=self._coordinator.activeCoilKey
        )

        self.session.samples.addSample(sample)

        logger.info(f'Manually recorded a sample: {sample}')

    def _onSampleToTargetBtnClicked(self, _):
        raise NotImplementedError  # TODO

    def _initializeDefaultViews(self):
        if len(self._views) > 0:
            raise NotImplementedError()  # TODO: clear any previous views from dock and self._views

        self.addView(key='Crosshairs', viewType='TargetingCrosshairs')
        self.addView(key='Crosshairs-X', viewType='TargetingCrosshairs-X')
        self.addView(key='Crosshairs-Y', viewType='TargetingCrosshairs-Y')

        # TODO: set up other default views

    def addView(self, key: str, viewType: str, **kwargs):

        match viewType:
            case 'TargetingCrosshairs':
                View = TargetingCrosshairsView
            case 'TargetingCrosshairs-X':
                View = TargetingCrosshairsView
                kwargs.setdefault('alignCameraTo', 'target-X')
            case 'TargetingCrosshairs-Y':
                View = TargetingCrosshairsView
                kwargs.setdefault('alignCameraTo', 'target-Y')

            case _:
                raise NotImplementedError('Unexpected viewType: {}'.format(viewType))

        # TODO: maybe add optional code here to generate unique key using input as a base key
        assert key not in self._views

        view = View(key=key, coordinator=self._coordinator, **kwargs)

        assert view.key not in self._views
        self._views[view.key] = view
        self._viewsDock.addDock(view.dock)

