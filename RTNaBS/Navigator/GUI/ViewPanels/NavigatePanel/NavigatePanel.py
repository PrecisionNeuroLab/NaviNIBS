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
import pyvista as pv
import pyvistaqt as pvqt
from pyqtgraph.dockarea import DockArea, Dock
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp
from typing import ClassVar

from .. import MainViewPanel
from .NavigationView import NavigationView, TargetingCrosshairsView, SinglePlotterNavigationView
from .TargetingCoordinator import TargetingCoordinator
from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Navigator.GUI.Widgets.SamplesTreeWidget import SamplesTreeWidget
from RTNaBS.Navigator.GUI.Widgets.TargetsTreeWidget import TargetsTreeWidget
from RTNaBS.Navigator.GUI.Widgets.TrackingStatusWidget import TrackingStatusWidget
from RTNaBS.Navigator.Model.Session import Session, Target
from RTNaBS.Navigator.Model.Tools import Tool, CoilTool, SubjectTracker, CalibrationPlate, Pointer
from RTNaBS.Navigator.Model.Triggering import TriggerReceiver, TriggerEvent
from RTNaBS.Navigator.Model.Samples import Samples, Sample, getSampleTimestampNow
from RTNaBS.util.CoilOrientations import PoseMetricCalculator
from RTNaBS.util.GUI import DockWidgets as dw
from RTNaBS.util.GUI.DockWidgets.DockWidgetsContainer import DockWidgetsContainer
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define
class _PoseMetricGroup:
    """
    Grouped information for displaying pose metrics in a widget.

    Set up to allow initial specification of title, container, and calculator key str, then finish initialization when calculator is set later.

    """
    _title: str
    _container: QtWidgets.QWidget
    _calculator: tp.Union[str, PoseMetricCalculator] = None
    _indicators: dict[str, QtWidgets.QLabel] = attrs.field(init=False, factory=dict)
    _hasInitialized: bool = attrs.field(init=False, default=False)

    def __attrs_post_init__(self):
        pass

    @property
    def calculator(self):
        return self._calculator

    @calculator.setter
    def calculator(self, calculator: PoseMetricCalculator):
        assert not self._hasInitialized
        assert isinstance(calculator, PoseMetricCalculator)
        self._calculator = calculator

        self._container.setLayout(QtWidgets.QFormLayout())

        for poseMetric in self._calculator.supportedMetrics:
            wdgt = QtWidgets.QLabel(f"NaN{poseMetric.units}")
            self._container.layout().addRow(poseMetric.label, wdgt)
            self._indicators[poseMetric.label] = wdgt

        self._calculator.sigCacheReset.connect(self._update)

        self._hasInitialized = True

        self._update()

    def _update(self):
        logger.debug(f'Updating pose metric indicators for {self._title}')
        for poseMetric in self._calculator.supportedMetrics:
            wdgt = self._indicators[poseMetric.label]
            val = poseMetric.getter()
            wdgt.setText(f"{val:.1f}{poseMetric.units}")
        logger.debug(f'Done updating pose metric indicators for {self._title}')


@attrs.define
class NavigatePanel(MainViewPanel):
    _wdgt: DockWidgetsContainer = attrs.field(init=False)
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.head-flash'))
    _dockWidgets: dict[str, dw.DockWidget] = attrs.field(init=False, factory=dict)
    _trackingStatusWdgt: TrackingStatusWidget = attrs.field(init=False)
    _targetsTreeWdgt: TargetsTreeWidget = attrs.field(init=False)
    _poseMetricGroups: list[_PoseMetricGroup] = attrs.field(init=False, factory=list)
    _samplesTreeWdgt: SamplesTreeWidget = attrs.field(init=False)
    _sampleBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _sampleToTargetBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _views: dict[str, NavigationView] = attrs.field(init=False, factory=dict)

    _coordinator: TargetingCoordinator = attrs.field(init=False)
    _triggerReceiver: TriggerReceiver = attrs.field(init=False)

    _hasInitialized: bool = attrs.field(init=False, default=False)

    def __attrs_post_init__(self):
        self._wdgt = DockWidgetsContainer(uniqueName=self._key)
        self._wdgt.setAffinities([self._key])

        super().__attrs_post_init__()

        def createDockWidget(title: str,
                             widget: tp.Optional[QtWidgets.QWidget] = None,
                             layout: tp.Optional[QtWidgets.QLayout] = None):
            cdw = dw.DockWidget(
                uniqueName=self._key + title,
                options=dw.DockWidgetOptions(notClosable=True),
                title=title,
                affinities=[self._key]
            )
            if widget is None:
                widget = QtWidgets.QWidget()
            if layout is not None:
                widget.setLayout(layout)
            widget.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Minimum)
            cdw.setWidget(widget)
            cdw.__childWidget = widget  # monkey-patch reference to child, since setWidget doesn't seem to claim ownernship
            self._dockWidgets[title] = cdw
            return cdw, widget

        cdw, container = createDockWidget(
            title='Tools tracking status',
        )
        self._trackingStatusWdgt = TrackingStatusWidget(session=self.session,
                                                        wdgt=container,
                                                        hideToolTypes=[CalibrationPlate, Pointer])
        self._wdgt.addDockWidget(cdw, dw.DockWidgetLocation.OnLeft)

        self._targetsTreeWdgt = TargetsTreeWidget(
            session=self.session
        )
        self._targetsTreeWdgt.sigCurrentTargetChanged.connect(self._onCurrentTargetChanged)
        cdw, container = createDockWidget(
            title='Targets',
            widget=self._targetsTreeWdgt.wdgt
        )
        self._wdgt.addDockWidget(cdw, dw.DockWidgetLocation.OnBottom)

        poseGroupDicts = [
            dict(title='Current pose metrics', calculatorKey='currentPoseMetrics'),
            dict(title='Selected sample metrics', calculatorKey='currentSamplePoseMetrics')
        ]

        for poseGroupDict in poseGroupDicts:
            cdw, container = createDockWidget(
                title=poseGroupDict['title'],
            )

            poseGroup = _PoseMetricGroup(
                title=poseGroupDict['title'],
                container=container,
                calculator=poseGroupDict['calculatorKey']
            )

            self._wdgt.addDockWidget(cdw, dw.DockWidgetLocation.OnBottom)

            self._poseMetricGroups.append(poseGroup)

        cdw, container = createDockWidget(
            title='Samples',
            layout=QtWidgets.QVBoxLayout())
        self._wdgt.addDockWidget(cdw, dw.DockWidgetLocation.OnBottom)

        btn = QtWidgets.QPushButton('Add a sample')
        btn.clicked.connect(self._onSampleBtnClicked)
        container.layout().addWidget(btn)
        self._sampleBtn = btn
        # TODO: change color to warning indicator when coil or tracker are not visible

        btn = QtWidgets.QPushButton('Create target from sample')
        btn.clicked.connect(self._onSampleToTargetBtnClicked)
        container.layout().addWidget(btn)
        self._sampleToTargetBtn = btn
        # TODO: only enable when one or more samples are selected

        btn = QtWidgets.QPushButton('Hide all samples')
        btn.clicked.connect(lambda *args: self.session.samples.setWhichSamplesVisible([]))
        container.layout().addWidget(btn)

        # TODO: add a 'Create target from pose' button (but clearly separate, maybe in different panel, from 'Create target from sample' button)

        self._samplesTreeWdgt = SamplesTreeWidget(
            session=self.session
        )
        self._samplesTreeWdgt.sigCurrentSampleChanged.connect(self._onCurrentSampleChanged)
        container.layout().addWidget(self._samplesTreeWdgt.wdgt)

        self._triggerReceiver = TriggerReceiver(key=self._key)
        self._triggerReceiver.sigTriggered.connect(self._onReceivedTrigger)

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

        # now that coordinator is available, finish initializing pose metric groups
        for poseGroup in self._poseMetricGroups:
            assert isinstance(poseGroup.calculator, str)
            poseGroup.calculator = getattr(self._coordinator, poseGroup.calculator)

        self._initializeDefaultViews()  # TODO: only do this if not restoring from previously saved config

        # register that we want to receive sample triggers when visible
        self.session.triggerSources.triggerRouter.registerReceiver(self._triggerReceiver)
        if self._isShown:
            self.session.triggerSources.triggerRouter.subscribeToTrigger(receiver=self._triggerReceiver, triggerKey='sample', exclusive=True)

    def _onCurrentTargetChanged(self, newTargetKey: str):
        """
        Called when targetTreeWdgt selection or coordinator currentTarget changes, NOT when attributes of currently selected target change
        """
        if self._hasInitialized:
            logger.debug(f'Current target key changed to {newTargetKey}')
            self._coordinator.currentTargetKey = newTargetKey
            self._targetsTreeWdgt.currentTargetKey = newTargetKey

    def _onPanelShown(self):
        super()._onPanelShown()
        if self.session is not None:
            self.session.triggerSources.triggerRouter.subscribeToTrigger(receiver=self._triggerReceiver, triggerKey='sample', exclusive=True)

        for view in self._views.values():
            if isinstance(view, SinglePlotterNavigationView):
                view.plotter.resumeRendering()

    def _onPanelHidden(self):
        super()._onPanelHidden()
        if self._hasInitialized:
            self.session.triggerSources.triggerRouter.unsubscribeFromTrigger(receiver=self._triggerReceiver, triggerKey='sample', exclusive=True)

            for view in self._views.values():
                if isinstance(view, SinglePlotterNavigationView):
                    view.plotter.pauseRendering()

    def _onCurrentSampleChanged(self, newSampleKey: str):
        """
        Called when sampleTreeWdgt selection or coordinator currentSample changes, NOT when attributes of currently selected sample change
        """
        if self._hasInitialized:
            self._coordinator.currentSampleKey = newSampleKey
            self._samplesTreeWdgt.currentSampleKey = newSampleKey

    def _onSampleBtnClicked(self, _):
        self._recordSample(timestamp=pd.Timestamp.now())

    def _onReceivedTrigger(self, triggerEvt: TriggerEvent):
        self._recordSample(timestamp=triggerEvt.time)

    def _recordSample(self, timestamp: tp.Optional[pd.Timestamp]):
        sampleKey = self.session.samples.getUniqueSampleKey(timestamp=timestamp)
        coilToMRITransf = self._coordinator.currentCoilToMRITransform  # may be None if missing a tracker, etc.

        if abs(timestamp - pd.Timestamp.now()).total_seconds() > 10:
            # We are getting "old" triggers or lagging for other reasons. Mark orientation as invalid
            logger.warning('Requested sample time is far from current time. Unable to get up-to-date orientation information')
            coilToMRITransf = None

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
        newTarget = self._coordinator.createTargetFromCurrentSample(doAddToSession=True)
        self._targetsTreeWdgt.currentTargetKey = newTarget.key

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
                kwargs.setdefault('doShowSkinSurf', True)
            case 'TargetingCrosshairs-Y':
                View = TargetingCrosshairsView
                kwargs.setdefault('alignCameraTo', 'target-Y')
                kwargs.setdefault('doShowSkinSurf', True)

            case _:
                raise NotImplementedError('Unexpected viewType: {}'.format(viewType))

        # TODO: maybe add optional code here to generate unique key using input as a base key
        assert key not in self._views

        view: NavigationView = View(key=key, dockKeyPrefix=self._key, coordinator=self._coordinator, **kwargs)

        assert view.key not in self._views
        self._views[view.key] = view
        self._wdgt.addDockWidget(view.dock, location=dw.DockWidgetLocation.OnRight)

