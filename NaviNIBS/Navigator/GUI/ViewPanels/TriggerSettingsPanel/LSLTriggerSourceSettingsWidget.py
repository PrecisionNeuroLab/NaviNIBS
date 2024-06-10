from __future__ import annotations

import asyncio
import typing as tp

import attrs
import logging
import numpy as np
import pandas as pd
import pylsl as lsl
from qtpy import QtWidgets

from .TriggerSourceSettingsWidget import TriggerSourceSettingsWidget
from NaviNIBS.Navigator.Model.Triggering import LSLTriggerSource, TriggerEvent, TriggerSource
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.lsl.LSLStreamSelector import LSLStreamSelector


logger = logging.getLogger(__name__)


@attrs.define
class LSLTriggerSourceSettingsWidget(TriggerSourceSettingsWidget[LSLTriggerSource]):
    _title: str = 'LSL trigger settings'
    _triggerSourceKey: str = 'LSLTriggerSource'
    _streamSelector: LSLStreamSelector = attrs.field(init=False)
    _inlet: tp.Optional[lsl.StreamInlet] = attrs.field(init=False, default=None)
    _inletConnectedEvent: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    _pollPeriod: float = 0.05
    _pollTask: asyncio.Task = attrs.field(init=False)
    _lastTriggerTimePerAction: dict[str, tp.Optional[pd.Timestamp]] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QFormLayout())

        if self.triggerSource is None:
            # create new empty LSL trigger source
            self.session.triggerSources.addItem(LSLTriggerSource(key=self._triggerSourceKey))
            assert self.triggerSource is not None

        initialSelectedStreamKey = self.triggerSource.streamKey

        self._streamSelector = LSLStreamSelector(selectedStreamKey=initialSelectedStreamKey)
        self._streamSelector.sigSelectedStreamKeyChanged.connect(self._onSelectedStreamKeyChanged)
        self._streamSelector.sigSelectedStreamAvailabilityChanged.connect(self._onSelectedStreamAvailabilityChanged)

        self.triggerSource.sigItemChanged.connect(self._onTriggerSourceItemChanged)

        self._wdgt.layout().addRow('Trigger stream', self._streamSelector.wdgt)

        self._pollTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._pollForData))

        if self.fallbackTriggerSource is not None:
            self.fallbackTriggerSource.isEnabled = True

    @property
    def fallbackTriggerSource(self) -> TriggerSource | None:
        if self.triggerSource.fallbackTriggerSourceKey is None:
            return None

        if self.session is not None and self.triggerSource.fallbackTriggerSourceKey in self.session.triggerSources:
            return self.session.triggerSources[self.triggerSource.fallbackTriggerSourceKey]

        return None

    def _disconnectInlet(self):
        logger.info(f'Disconnecting from LSL stream {self._streamSelector.selectedStreamKey}')
        self._inletConnectedEvent.clear()
        self._inlet.close_stream()
        self._inlet = None
        logger.debug(f'Disconnected from LSL stream {self._streamSelector.selectedStreamKey}')
        # TODO: maybe start and stop polling task when connecting / disconnecting inlet
        if self.fallbackTriggerSource is not None:
            self.fallbackTriggerSource.isEnabled = True

    def _connectInlet(self):
        assert self._inlet is None
        if not self._streamSelector.selectedStreamIsAvailable:
            logger.info('Selected trigger stream is not available. Skipping attempt to connect')
            return

        isFixedRate = self._streamSelector.selectedStreamInfo.nominal_srate() > 0
        if isFixedRate:
            logger.warning('Fixed rate streams not currently supported. Not connecting inlet.')
            return

        logger.info(f'Connecting to LSL stream {self._streamSelector.selectedStreamKey}')
        self._inlet = lsl.StreamInlet(self._streamSelector.selectedStreamInfo,
                                      max_buflen=30,
                                      processing_flags=lsl.proc_ALL,
                                      recover=False)
        self._inletConnectedEvent.set()
        logger.debug(f'Connected to LSL stream {self._streamSelector.selectedStreamKey}')

        if self.fallbackTriggerSource is not None:
            self.fallbackTriggerSource.isEnabled = False

    async def _pollForData(self):
        while True:
            await self._inletConnectedEvent.wait()

            try:
                chunk, timestamps = self._inlet.pull_chunk(
                    timeout=0.,
                    max_samples=10
                )
            except lsl.LostError as e:
                logger.info(f'Previously connected stream inlet {self._streamSelector.selectedStreamKey} is no longer available')
                self._streamSelector.markStreamAsLost(streamKey=self._streamSelector.selectedStreamKey)
                self._disconnectInlet()
                continue

            chunk = np.asarray(chunk)
            if chunk.ndim == 2 and chunk.shape[1] == 1:
                chunk.resize(chunk.shape[0])

            if len(chunk) == 0:
                # no samples
                await asyncio.sleep(self._pollPeriod)
                continue

            evtTimes, evtData, evtIndices = self._getRelevantEvents(timestamps, chunk)

            if len(evtTimes) == 0:
                # no relevant events
                continue

            for evtTime, evtDat in zip(evtTimes, evtData):
                action = None
                if self.triggerSource.triggerEvents is not None:
                    action = self.triggerSource.triggerEvents.get(evtDat, None)
                if action is None:
                    action = self.triggerSource.defaultAction

                try:
                    metadata = dict(originalType=int(evtDat))
                except ValueError as e:
                    metadata = dict()

                if self.triggerSource.triggerValueIsEpochID:
                    metadata['epochID'] = int(evtDat)
                    # TODO: assert that epochID is actually unique (to catch issues where the selected stream
                    # is actually sending non-unique events rather than unique epochIDs)

                triggerEvt = TriggerEvent(
                    type=action,
                    time=pd.Timestamp.now() + pd.Timedelta(seconds=(evtTime - lsl.local_clock())),  # convert from lsl time to pandas timestamp
                    metadata=metadata
                )
                if self._lastTriggerTimePerAction.get(action, None) is not None and (triggerEvt.time - self._lastTriggerTimePerAction[action]).total_seconds() < self.triggerSource.minInterTriggerPeriod:
                    logger.debug('Ignoring trigger that occured too quickly after previous')
                    continue
                self.triggerSource.trigger(triggerEvt)
                self._lastTriggerTimePerAction[action] = triggerEvt.time

    def _getRelevantEvents(self, evtTimes: list[float], evtData: np.ndarray) -> tuple[list[float], list[np.ndarray], np.ndarray]:
        if self.triggerSource.triggerEvents is not None and len(self.triggerSource.triggerEvents) > 0:
            # only trigger for specific events of interest
            relevantEvtIndices = np.isin(evtData, self.triggerSource.triggerEvents)
        else:
            # trigger for any event
            if False:
                relevantEvtIndices = np.full(evtData.shape, True)
            else:
                # (except for off events like 0 and False)
                relevantEvtIndices = np.logical_not(np.isin(evtData, ('0', 'False')))

        if not np.any(relevantEvtIndices):
            return [], [], []

        relevantEvtTimes = []
        relevantEvtData = []
        for evtIndex in np.nonzero(relevantEvtIndices)[0]:
            relevantEvtTimes.append(evtTimes[evtIndex])
            relevantEvtData.append(evtData[evtIndex])
        return relevantEvtTimes, relevantEvtData, relevantEvtIndices

    def _connectOrDisconnectAsNeeded(self):
        if self._inlet is None and (self.triggerSource.isEnabled and self._streamSelector.selectedStreamIsAvailable):
            self._connectInlet()
        elif self._inlet is not None and (not self.triggerSource.isEnabled or not self._streamSelector.selectedStreamIsAvailable):
            self._disconnectInlet()

    def _onSelectedStreamKeyChanged(self, newKey: str):
        if self.session is None:
            # not yet initialized
            return
        if self._inlet is not None:
            self._disconnectInlet()
        self.triggerSource.streamKey = newKey
        self._connectOrDisconnectAsNeeded()

    def _onSelectedStreamAvailabilityChanged(self):
        self._connectOrDisconnectAsNeeded()

    def _onTriggerSourceItemChanged(self, key: str, whichAttrs: list[str] | None = None):
        if whichAttrs is None or any(x in whichAttrs for x in ('fallbackTriggerSourceKey', 'isEnabled')):
            if self.fallbackTriggerSource is not None:
                self.fallbackTriggerSource.isEnabled = self._inlet is None and self.triggerSource.isEnabled

        if whichAttrs is None or 'streamKey' in whichAttrs:
            self._streamSelector.selectedStreamKey = self.triggerSource.streamKey

        if whichAttrs is None or 'isEnabled' in whichAttrs:
            self._connectOrDisconnectAsNeeded()

    def _onSessionTriggerSettingChanged(self, triggerSourceKeys: list[str], attribs: tp.Optional[list[str]]):
        if self._triggerSourceKey not in triggerSourceKeys:
            return  # ignore other trigger sources
        self._streamSelector.selectedStreamKey = self.triggerSource.streamKey
        # TODO: handle any other change updates as needed

    def _onSessionSet(self):
        if self.triggerSource is None:
            # create new trigger source settings
            self.session.triggerSources[self._triggerSourceKey] = LSLTriggerSource()
        self._streamSelector.selectedStreamKey = self.triggerSource.streamKey
        self._session.triggerSources.sigItemsChanged.connect(self._onSessionTriggerSettingChanged)
