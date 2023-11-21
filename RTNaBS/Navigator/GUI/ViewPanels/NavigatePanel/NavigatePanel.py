from __future__ import annotations

import asyncio
import attrs
import logging
import numpy as np

import pandas as pd
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from .NavigationView import NavigationView, TargetingCrosshairsView, SinglePlotterNavigationView
from RTNaBS.Navigator.TargetingCoordinator import TargetingCoordinator
from RTNaBS.Navigator.GUI.Widgets.CollectionTableWidget import SamplesTableWidget
from RTNaBS.Navigator.GUI.Widgets.CollectionTableWidget import TargetsTableWidget
from RTNaBS.Navigator.GUI.Widgets.TrackingStatusWidget import TrackingStatusWidget
from RTNaBS.Navigator.GUI.ViewPanels.MainViewPanelWithDockWidgets import MainViewPanelWithDockWidgets
from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.Tools import CalibrationPlate, Pointer
from RTNaBS.Navigator.Model.Triggering import TriggerReceiver, TriggerEvent
from RTNaBS.Navigator.Model.Samples import Sample
from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError
from RTNaBS.util.CoilOrientations import PoseMetricCalculator
from RTNaBS.util.GUI.Dock import Dock
from RTNaBS.util.GUI.QScrollContainer import QScrollContainer
from RTNaBS.util.pyvista import DefaultBackgroundPlotter, RemotePlotterProxy

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

Transform = np.ndarray


@attrs.define
class _PoseMetricGroup:
    """
    Grouped information for displaying pose metrics in a widget.

    Set up to allow initial specification of title, container, and calculator key str, then finish initialization when calculator is set later.

    """
    _title: str
    _container: QtWidgets.QWidget
    _scroll: QScrollContainer = attrs.field(init=False)
    _calculatorKey: str
    _calculator: tp.Optional[PoseMetricCalculator] = None
    _indicators: dict[str, QtWidgets.QLabel] = attrs.field(init=False, factory=dict)
    _hasInitialized: bool = attrs.field(init=False, default=False)
    _needsUpdateEvent: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    _updateRateLimit: float = 2  # in Hz

    def __attrs_post_init__(self):
        asyncio.create_task(asyncTryAndLogExceptionOnError(self._loop_keepUpdated))

    @property
    def calculatorKey(self):
        return self._calculatorKey

    @property
    def calculator(self):
        return self._calculator

    @calculator.setter
    def calculator(self, calculator: PoseMetricCalculator):
        if calculator is self._calculator:
            return

        assert isinstance(calculator, PoseMetricCalculator)

        self._calculator = calculator

        poseMetrics = [poseMetric for poseMetric in self._calculator.supportedMetrics if poseMetric.doShowByDefault]

        if not self._hasInitialized:
            self._scroll = QScrollContainer(innerContainerLayout=QtWidgets.QFormLayout())
            self._container.setLayout(QtWidgets.QVBoxLayout())
            self._container.layout().setContentsMargins(0, 0, 0, 0)
            self._container.layout().addWidget(self._scroll.scrollArea)
            self._container.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Preferred)

            for poseMetric in poseMetrics:
                wdgt = QtWidgets.QLabel(f"NaN{poseMetric.units}")
                self._scroll.innerContainerLayout.addRow(poseMetric.label, wdgt)
                self._indicators[poseMetric.label] = wdgt

            self._hasInitialized = True
        else:
            assert len(poseMetrics) == self._container.layout().rowCount()
            assert len(poseMetrics) == len(self._indicators)
            assert all(poseMetric.label in self._indicators for poseMetric in poseMetrics)

        self._calculator.sigCacheReset.connect(self._updateSoon)

        self._update()

    def _updateSoon(self):
        self._needsUpdateEvent.set()

    async def _loop_keepUpdated(self):
        while True:
            await self._needsUpdateEvent.wait()
            self._update()
            await asyncio.sleep(1/self._updateRateLimit)

    def _update(self):
        logger.debug(f'Updating pose metric indicators for {self._title}')
        self._needsUpdateEvent.clear()
        poseMetrics = [poseMetric for poseMetric in self._calculator.supportedMetrics if poseMetric.doShowByDefault]
        for poseMetric in poseMetrics:
            wdgt = self._indicators[poseMetric.label]
            val = poseMetric.getter()
            wdgt.setText(f"{val:.1f}{poseMetric.units}")
        logger.debug(f'Done updating pose metric indicators for {self._title}')


@attrs.define
class BackgroundSamplePoseMetadataSetter:
    _session: Session | None = attrs.field(init=False, default=None)
    _pendingSampleKeys: list[str] = attrs.field(init=False, factory=list)
    _needsUpdateEvent: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    _calculator: PoseMetricCalculator | None = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        asyncio.create_task(asyncTryAndLogExceptionOnError(self._loop_keepUpdated))

    async def _loop_keepUpdated(self):
        while True:
            await self._needsUpdateEvent.wait()
            self._needsUpdateEvent.clear()
            if self._session is None:
                continue

            if len(self._pendingSampleKeys) == 0:
                continue

            sampleKey = self._pendingSampleKeys.pop(0)

            if len(self._pendingSampleKeys) > 0:
                self._needsUpdateEvent.set()

            if sampleKey not in self.session.samples:
                # sample was presumably deleted while pending
                continue

            sample = self.session.samples[sampleKey]

            if self._calculator is None:
                self._calculator = PoseMetricCalculator(
                    session=self.session,
                    sample=sample
                )
            else:
                self._calculator.sample = sample

            with sample.changingMetadata():
                for metric in self._calculator.supportedMetrics:
                    if not metric.doShowByDefault:
                        continue
                    sample.metadata[metric.label] = self._calculator.getValueForMetric(metric.label)

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSes: Session | None):
        if self._session is newSes:
            return
        if self._session is not None:
            raise NotImplementedError  # TODO: disconnect from signals, update self._calculator.session, etc.
        else:
            assert self._calculator is None  # will be given session ref when instantiated
        self._session = newSes
        self._session.samples.sigItemKeyChanged.connect(self._onSampleKeyChanged)
        self._needsUpdateEvent.set()

    def queueSamples(self, sampleKeys: list[str]):
        self._pendingSampleKeys.extend([key for key in sampleKeys if key not in self._pendingSampleKeys])
        self._needsUpdateEvent.set()

    def _onSampleKeyChanged(self, oldKey: str, newKey: str):
        if oldKey in self._pendingSampleKeys:
            if newKey not in self._pendingSampleKeys:
                self._pendingSampleKeys.append(newKey)
                self._needsUpdateEvent.set()


@attrs.define
class NavigatePanel(MainViewPanelWithDockWidgets):
    _key: str = 'Navigate'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.head-flash'))
    _trackingStatusWdgt: TrackingStatusWidget = attrs.field(init=False)
    _targetsTableWdgt: TargetsTableWidget = attrs.field(init=False)
    _poseMetricGroups: list[_PoseMetricGroup] = attrs.field(init=False, factory=list)
    _samplesTableWdgt: SamplesTableWidget = attrs.field(init=False)
    _sampleBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _hideSampleBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _hideAllSamplesBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _showSampleBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _sampleToTargetBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _views: dict[str, NavigationView] = attrs.field(init=False, factory=dict)

    _coordinator: TargetingCoordinator = attrs.field(init=False)
    _triggerReceiver: TriggerReceiver = attrs.field(init=False)
    _backgroundSamplePoseMetadataSetter: BackgroundSamplePoseMetadataSetter = attrs.field(
        init=False,
        factory=BackgroundSamplePoseMetadataSetter)

    _hasInitializedNavigationViews: bool = attrs.field(init=False, default=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def canBeEnabled(self) -> tuple[bool, str | None]:
        if self.session is None:
            return False, 'No session set'
        if not self.session.MRI.isSet:
            return False, 'No MRI set'
        if not self.session.headModel.isSet:
            return False, 'No head model set'
        if self.session.tools.subjectTracker is None:
            return False, 'No active subject tracker configured'
        if not self.session.subjectRegistration.isRegistered:
            return False, 'Subject not registered'
        return True, None

    def _finishInitialization(self):
        super()._finishInitialization()

        dock, container = self._createDockWidget(
            title='Tools tracking status',
        )
        self._trackingStatusWdgt = TrackingStatusWidget(wdgt=container,
                                                        hideToolTypes=[CalibrationPlate, Pointer])
        self._wdgt.addDock(dock, position='left')
        dock.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)

        self._targetsTableWdgt = TargetsTableWidget()
        self._targetsTableWdgt.sigCurrentItemChanged.connect(self._onCurrentTargetChanged)
        self._targetsTableWdgt.wdgt.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        container.layout().addWidget(self._targetsTableWdgt.wdgt)

        dock, container = self._createDockWidget(
            title='Targets',
            widget=self._targetsTableWdgt.wdgt
        )
        self._wdgt.addDock(dock, position='bottom')

        poseGroupDicts = [
            dict(title='Current pose metrics', calculatorKey='currentPoseMetrics'),
            dict(title='Selected sample metrics', calculatorKey='currentSamplePoseMetrics')
        ]

        for poseGroupDict in poseGroupDicts:
            dock, container = self._createDockWidget(
                title=poseGroupDict['title'],
            )
            dock.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)

            poseGroup = _PoseMetricGroup(
                title=poseGroupDict['title'],
                container=container,
                calculatorKey=poseGroupDict['calculatorKey']
            )

            self._wdgt.addDock(dock, position='bottom')

            self._poseMetricGroups.append(poseGroup)

        dock, container = self._createDockWidget(
            title='Samples',
            layout=QtWidgets.QVBoxLayout())
        self._wdgt.addDock(dock, position='bottom')

        btnContainer = QtWidgets.QWidget()
        btnContainerLayout = QtWidgets.QGridLayout()
        btnContainer.setLayout(btnContainerLayout)
        container.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Add a sample')
        btn.clicked.connect(self._onSampleBtnClicked)
        btnContainerLayout.addWidget(btn, 0, 0)
        self._sampleBtn = btn
        # TODO: change color to warning indicator when coil or tracker are not visible

        btn = QtWidgets.QPushButton('Sample ⇒ Target')
        btn.clicked.connect(self._onSampleToTargetBtnClicked)
        btnContainerLayout.addWidget(btn, 0, 1)
        btn.setEnabled(False)
        self._sampleToTargetBtn = btn
        # TODO: only enable when one or more samples are selected

        btn = QtWidgets.QPushButton('Hide sample')
        btn.clicked.connect(self._onHideSampleBtnClicked)
        btn.setEnabled(False)
        btnContainerLayout.addWidget(btn, 1, 1)
        self._hideSampleBtn = btn

        btn = QtWidgets.QPushButton('Show sample')
        btn.clicked.connect(self._onShowSampleBtnClicked)
        btn.setEnabled(False)
        btnContainerLayout.addWidget(btn, 1, 0)
        self._showSampleBtn = btn

        btn = QtWidgets.QPushButton('Hide all samples')
        btn.clicked.connect(self._onHideAllSamplesBtnClicked)
        btnContainerLayout.addWidget(btn, 2, 1)
        self._hideAllSamplesBtn = btn

        # TODO: add a 'Create target from pose' button (but clearly separate, maybe in different panel, from 'Create target from sample' button)

        self._samplesTableWdgt = SamplesTableWidget()
        self._samplesTableWdgt.sigCurrentItemChanged.connect(self._onCurrentSampleChanged)
        self._samplesTableWdgt.sigSelectionChanged.connect(self._onSelectedSamplesChanged)
        self._samplesTableWdgt.wdgt.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        container.layout().addWidget(self._samplesTableWdgt.wdgt)

        self._triggerReceiver = TriggerReceiver(
            key=self._key,
            #minTimeBetweenEvents=1.1,  # TODO: make this GUI-configurable and config-file-configurable
        )
        self._triggerReceiver.sigTriggered.connect(self._onReceivedTrigger)

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

    def _onSessionSet(self):
        super()._onSessionSet()

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):
        logger.debug(f'{self.__class__.__name__} onPanelInitializedAndSessionSet')
        self._trackingStatusWdgt.session = self.session
        self._coordinator = TargetingCoordinator.getSingleton(session=self.session)
        self._coordinator.sigCurrentTargetChanged.connect(lambda: self._onCurrentTargetChanged(self._coordinator.currentTargetKey))
        self._coordinator.sigCurrentSampleChanged.connect(lambda: self._onCurrentSampleChanged(self._coordinator.currentSampleKey))
        self._targetsTableWdgt.session = self.session
        self._samplesTableWdgt.session = self.session
        self._backgroundSamplePoseMetadataSetter.session = self.session

        self.session.samples.sigItemsChanged.connect(self._onSamplesChanged)

        # now that coordinator is available, finish initializing pose metric groups
        for poseGroup in self._poseMetricGroups:
            poseGroup.calculator = getattr(self._coordinator, poseGroup.calculatorKey)

        if not self._hasInitializedNavigationViews:
            self._initializeDefaultViews()  # TODO: only do this if not restoring from previously saved config

        # register that we want to receive sample triggers when visible
        self.session.triggerSources.triggerRouter.registerReceiver(self._triggerReceiver)
        if self._isShown:
            self.session.triggerSources.triggerRouter.subscribeToTrigger(
                receiver=self._triggerReceiver, triggerKey='sample', exclusive=True)
            self.session.triggerSources.triggerRouter.subscribeToTrigger(
                receiver=self._triggerReceiver, triggerKey='pulse', exclusive=True)

        self.restoreLayoutIfAvailable()

    def _onCurrentTargetChanged(self, newTargetKey: str | None):
        """
        Called when targetTreeWdgt selection or coordinator currentTarget changes, NOT when attributes of currently selected target change
        """
        if self._hasInitialized:
            logger.debug(f'Current target key changed to {newTargetKey}')
            self._coordinator.currentTargetKey = newTargetKey
            self._targetsTableWdgt.currentCollectionItemKey = newTargetKey

    def _onPanelShown(self):
        super()._onPanelShown()
        if self.session is not None:
            self.session.triggerSources.triggerRouter.subscribeToTrigger(receiver=self._triggerReceiver, triggerKey='sample', exclusive=True)

        for view in self._views.values():
            if isinstance(view, SinglePlotterNavigationView):
                if not isinstance(view.plotter, RemotePlotterProxy) or view.plotter.isReadyEvent.is_set():
                    view.plotter.resumeRendering()

    def _onPanelHidden(self):
        super()._onPanelHidden()
        if self._hasInitialized and self.session is not None:
            self.session.triggerSources.triggerRouter.unsubscribeFromTrigger(receiver=self._triggerReceiver, triggerKey='sample', exclusive=True)

            for view in self._views.values():
                if isinstance(view, SinglePlotterNavigationView):
                    if not isinstance(view.plotter, RemotePlotterProxy) or view.plotter.isReadyEvent.is_set():
                        view.plotter.pauseRendering()

    def _onSamplesChanged(self, changesKeys: list[str], changedAttribs: tp.Optional[list[str]] = None):
        if changedAttribs is None or 'isVisible' in changedAttribs:
            self._onSelectedSamplesChanged(self._samplesTableWdgt.selectedCollectionItemKeys)

    def _onCurrentSampleChanged(self, newSampleKey: str):
        """
        Called when sampleTreeWdgt selection or coordinator currentSample changes, NOT when attributes of currently selected sample change
        """
        if self._hasInitialized:
            self._coordinator.currentSampleKey = newSampleKey

    def _onSelectedSamplesChanged(self, selectedKeys: list[str]):
        if self._hasInitialized:
            numSelectedHidden = sum(not self.session.samples[key].isVisible for key in selectedKeys)
            self._showSampleBtn.setEnabled(numSelectedHidden > 0)
            self._showSampleBtn.setText('Show samples' if numSelectedHidden > 1 else 'Show sample')

            numSelectedVisible = len(selectedKeys) - numSelectedHidden
            self._hideSampleBtn.setEnabled(numSelectedVisible > 0)
            self._hideSampleBtn.setText('Hide samples' if numSelectedVisible > 1 else 'Hide sample')

            self._sampleToTargetBtn.setEnabled(len(selectedKeys) > 0)

    def _onSampleBtnClicked(self, _):
        self._recordSample(timestamp=pd.Timestamp.now())

    def _onReceivedTrigger(self, triggerEvt: TriggerEvent):
        self._recordSample(timestamp=triggerEvt.time,
                           metadata=triggerEvt.metadata)

    def _recordSample(self, timestamp: tp.Optional[pd.Timestamp], metadata: tp.Optional[dict[str, tp.Any]] = None):
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
            coilKey=self._coordinator.activeCoilKey,
            metadata=metadata if metadata is not None else {}
        )

        self.session.samples.addItem(sample)

        self._backgroundSamplePoseMetadataSetter.queueSamples([sample.key])

        logger.info(f'Manually recorded a sample: {sample}')

    def _onSampleToTargetBtnClicked(self, _):
        newTarget = self._coordinator.createTargetFromCurrentSample(doAddToSession=True)
        self._targetsTableWdgt.currentCollectionItemKey = newTarget.key

    def _onShowSampleBtnClicked(self, _):
        selKeys = self._samplesTableWdgt.selectedCollectionItemKeys
        self.session.samples.setAttribForItems(selKeys, dict(isVisible=[True for key in selKeys]))

    def _onHideAllSamplesBtnClicked(self, _):
        self.session.samples.setWhichSamplesVisible([])

    def _onHideSampleBtnClicked(self, _):
        selKeys = self._samplesTableWdgt.selectedCollectionItemKeys
        self.session.samples.setAttribForItems(selKeys, dict(isVisible=[False for key in selKeys]))

    def _initializeDefaultViews(self):
        if self._hasInitializedNavigationViews:
            assert len(self._views) > 0
            raise NotImplementedError()  # TODO: clear any previous views from dock and self._views

        self.addView(key='Crosshairs', View='TargetingCrosshairs')
        self.addView(key='Crosshairs-X', View='TargetingCrosshairs-X')
        self.addView(key='Crosshairs-Y', View='TargetingCrosshairs-Y')

        # TODO: set up other default views

        self._hasInitializedNavigationViews = True

    def addView(self, key: str, View: str | tp.Callable[[...], NavigationView],
                position: str | None = None,
                positionRelativeTo: Dock | None = None,
                **kwargs):

        # TODO: maybe add optional code here to generate unique key using input as a base key
        assert key not in self._views

        if isinstance(View, str):
            match View:
                case 'TargetingCrosshairs':
                    View = TargetingCrosshairsView
                    kwargs.setdefault('doShowHandleAngleError', True)
                    kwargs.setdefault('cameraDist', 150)
                case 'TargetingCrosshairs-X':
                    View = TargetingCrosshairsView
                    kwargs.setdefault('doParallelProjection', True)
                    kwargs.setdefault('alignCameraTo', 'coil+X')
                    kwargs.setdefault('cameraDist', 75)
                    kwargs.setdefault('doShowSkinSurf', True)
                    kwargs.setdefault('doShowTargetTangentialAngleError', True)
                    kwargs.setdefault('doShowScalpTangentialAngleError', True)
                case 'TargetingCrosshairs-Y':
                    View = TargetingCrosshairsView
                    kwargs.setdefault('doParallelProjection', True)
                    kwargs.setdefault('alignCameraTo', 'coil-Y')
                    kwargs.setdefault('cameraDist', 75)
                    kwargs.setdefault('doShowSkinSurf', True)
                    kwargs.setdefault('doShowTargetTangentialAngleError', True)
                    kwargs.setdefault('doShowScalpTangentialAngleError', True)

                case _:
                    raise NotImplementedError('Unexpected viewType: {}'.format(View))

        view: NavigationView = View(key=key, dockKeyPrefix=self._key, coordinator=self._coordinator, **kwargs)
        view.wdgt.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        assert view.key not in self._views
        self._views[view.key] = view

        if key == 'Crosshairs-Y':
            # TODO: use position and positionRelativeTo if either/both specified
            self._wdgt.addDock(view.dock,
                               position='right',
                               relativeTo=self._views['Crosshairs-X'].dock)
        elif key == 'Crosshairs-X':
            # TODO: use position and positionRelativeTo if either/both specified
            self._wdgt.addDock(view.dock,
                               position='bottom',
                               relativeTo=self._views['Crosshairs'].dock)
        else:
            if position is None:
                position = 'right'
            self._wdgt.addDock(view.dock,
                               position=position,
                               relativeTo=positionRelativeTo)
