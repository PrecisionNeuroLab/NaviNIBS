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
from RTNaBS.Navigator.Model.Triggering import LSLTriggerSource, TriggerEvent
from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError
from RTNaBS.util.lsl.LSLStreamSelector import LSLStreamSelector


logger = logging.getLogger(__name__)

@attrs.define
class LSLTriggerSourceSettingsWidget(TriggerSourceSettingsWidget[LSLTriggerSource]):
    _title: str = 'LSL trigger settings'
    _triggerSourceKey: str = 'LSLTriggerSource'
    _minInterTriggerPeriod: float = 0.2  # ignore repeated triggers within this time

    _streamSelector: LSLStreamSelector = attrs.field(init=False)
    _inlet: tp.Optional[lsl.StreamInlet] = attrs.field(init=False, default=None)
    _inletConnectedEvent: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    _pollPeriod: float = 0.05
    _pollTask: asyncio.Task = attrs.field(init=False)
    _lastTriggerTimePerAction: dict[str, tp.Optional[pd.Timestamp]] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QFormLayout())

        self._streamSelector = LSLStreamSelector()
        self._streamSelector.sigSelectedStreamKeyChanged.connect(self._onSelectedStreamKeyChanged)
        self._streamSelector.sigSelectedStreamAvailabilityChanged.connect(self._onSelectedStreamAvailabilityChanged)

        self._wdgt.layout().addRow('Trigger stream', self._streamSelector.wdgt)

        self._pollTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._pollForData))

    def _disconnectInlet(self):
        logger.info(f'Disconnecting from LSL stream')
        self._inletConnectedEvent.clear()
        self._inlet.close_stream()
        self._inlet = None
        # TODO: maybe start and stop polling task when connecting / disconnecting inlet

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
                if self.triggerSource.triggerEvents is not None:
                    action = self.triggerSource.triggerEvents.get(evtDat, None)
                if action is None:
                    action = self.triggerSource.defaultAction

                triggerEvt = TriggerEvent(
                    type=action,
                    time=pd.Timestamp.now() + pd.Timedelta(seconds=(evtTime - lsl.local_clock())),  # convert from lsl time to pandas timestamp
                    metadata=dict(originalType=evtDat)
                )
                if self._lastTriggerTimePerAction[action] is not None and (triggerEvt.time - self._lastTriggerTimePerAction[action]).total_seconds() < self._minInterTriggerPeriod:
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

    def _onSelectedStreamKeyChanged(self, newKey: str):
        if self.session is None:
            # not yet initialized
            return
        self.triggerSource.streamKey = newKey
        if self._inlet is not None:
            self._disconnectInlet()
        if self._streamSelector.selectedStreamIsAvailable:
            self._connectInlet()
        else:
            pass  # nothing to do, selected stream not available

    def _onSelectedStreamAvailabilityChanged(self):
        if self._inlet is None and self._streamSelector.selectedStreamIsAvailable:
            self._connectInlet()
        elif self._inlet is not None and not self._streamSelector.selectedStreamIsAvailable:
            self._disconnectInlet()

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
