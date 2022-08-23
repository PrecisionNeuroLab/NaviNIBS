import attrs
import json
import logging
import pandas as pd
import pylsl as lsl
import typing as tp
from typing import ClassVar

from RTNaBS.util.attrs import attrsAsDict
from RTNaBS.util.Signaler import Signal
from RTNaBS.util import exceptionToStr

logger = logging.getLogger(__name__)


@attrs.define
class TriggerEvent:
    type: str
    time: pd.Timestamp = attrs.field(factory=pd.Timestamp.now)
    metadata: tp.Dict[str, tp.Any] = attrs.field(factory=dict)


@attrs.define
class TriggerSource:
    type: ClassVar[str] = 'TriggerSource'
    _isEnabled: bool = True

    sigTriggerSettingChanged: Signal = attrs.field(init=False, factory=Signal)
    sigTriggered: Signal = attrs.field(init=False, factory=lambda: Signal((TriggerEvent,)))

    def __attrs_post_init__(self):
        pass

    def trigger(self, triggerEvt: TriggerEvent):
        triggerEvt.metadata['source'] = self.type
        logger.debug(f'Recorded trigger: {triggerEvt}')
        self.sigTriggered.emit(triggerEvt)

    def asDict(self) -> dict[str, tp.Any]:
        d = attrsAsDict(self)
        d['type'] = self.type
        return d

    @classmethod
    def fromDict(cls, d):
        type = d.pop('type')
        match(type):
            case LSLTriggerSource.type:
                return LSLTriggerSource(**d)
            case HotkeyTriggerSource.type:
                return HotkeyTriggerSource(**d)
            case _:
                raise NotImplementedError(f'Unexpected trigger source type: {type}')


@attrs.define
class LSLTriggerSource(TriggerSource):
    type: ClassVar[str] = 'LSLTriggerSource'
    _streamKey: tp.Optional[str] = None
    _triggerEvents: tp.Optional[list[str]] = None  # list of event values on which to trigger

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def streamKey(self):
        return self._streamKey

    @streamKey.setter
    def streamKey(self, newKey: tp.Optional[str]):
        if self._streamKey == newKey:
            return
        self._streamKey = newKey
        self.sigTriggerSettingChanged.emit()

    @property
    def triggerEvents(self):
        return self._triggerEvents

    @triggerEvents.setter
    def triggerEvents(self, newEvents: tp.Optional[list[str]]):
        if self._triggerEvents == newEvents:
            return
        self._triggerEvents = newEvents
        self.sigTriggerSettingChanged.emit()


@attrs.define
class HotkeyTriggerSource(TriggerSource):
    type: ClassVar[str] = 'HotkeyTriggerSource'

    _keyMapping: dict[str, str] = attrs.field(factory=lambda: {
        'F13': 'sample'  # TODO: determine foot pedal key mapping and fill in here
    })
    """
    Mapping from hotkey to trigger key.
    """


@attrs.define
class TriggerReceiver:
    _key: str
    sigTriggered: Signal = attrs.field(init=False, factory=lambda: Signal((TriggerEvent,)))

    def __attrs_post_init__(self):
        pass

    @property
    def key(self):
        return self._key


@attrs.define
class TriggerRouter:
    """
    Handle routing of triggers to any of multiple trigger receivers, e.g. to handle cases where when the
    registration panel is in the foreground, triggers should record a new registration point, but if the
    navigation panel is in the foreground, triggers should record a new coil orientation sample.

    Note: this is implemented here for centralization, but is not persisted in session file(s)
    """
    _receivers: dict[str, TriggerReceiver] = attrs.field(init=False, factory=dict)
    _exclusiveTriggerStacks: dict[str, list[str]] = attrs.field(init=False, factory=dict)
    _nonexclusiveTriggerReceivers: dict[str, set[str]] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        pass

    def registerReceiver(self, receiver: TriggerReceiver):
        assert receiver.key not in self._receivers
        self._receivers[receiver.key] = receiver

    def subscribeToTrigger(self, receiver: TriggerReceiver, triggerKey: str, exclusive: bool):
        assert receiver.key in self._receivers

        logger.debug(f"TriggerReceiver '{receiver.key}' subscribing to '{triggerKey}' {'exclusively' if exclusive else 'non-exclusively'}")

        if exclusive:
            if triggerKey not in self._exclusiveTriggerStacks:
                self._exclusiveTriggerStacks[triggerKey] = list()
            self._exclusiveTriggerStacks[triggerKey].append(receiver.key)
        else:
            if triggerKey not in self._nonexclusiveTriggerReceivers:
                self._nonexclusiveTriggerReceivers[triggerKey] = set()
            self._nonexclusiveTriggerReceivers[triggerKey].add(receiver.key)

    def unsubscribeFromTrigger(self, receiver: TriggerReceiver, triggerKey: str, exclusive: bool):
        assert receiver.key in self._receivers
        if exclusive:
            if triggerKey in self._exclusiveTriggerStacks and receiver.key in self._exclusiveTriggerStacks[triggerKey]:
                self._exclusiveTriggerStacks[triggerKey].remove(receiver.key)
            else:
                pass  # already unsubscribed
        else:
            if triggerKey in self._nonexclusiveTriggerReceivers and receiver.key in self._nonexclusiveTriggerReceivers[triggerKey]:
                self._nonexclusiveTriggerReceivers[triggerKey].remove(receiver.key)
            else:
                pass  # already unsubscribed

    def _onSourceTriggered(self, triggerEvt: TriggerEvent):
        if triggerEvt.type in self._exclusiveTriggerStacks:
            if len(self._exclusiveTriggerStacks[triggerEvt.type]) > 0:
                receiverKey = self._exclusiveTriggerStacks[triggerEvt.type][-1]
                logger.debug(f'Sending exclusive trigger {triggerEvt.type} to {receiverKey}')
                try:
                    self._receivers[receiverKey].sigTriggered.emit(triggerEvt)
                except Exception as e:
                    logger.error(f'Problem while signaling exclusive trigger: \n{exceptionToStr(e)}')
        if triggerEvt.type in self._nonexclusiveTriggerReceivers:
            for receiverKey in self._nonexclusiveTriggerReceivers[triggerEvt.type]:
                self._receivers[receiverKey].sigTriggered.emit(triggerEvt)

    def connectToTriggerSource(self, source: TriggerSource):
        source.sigTriggered.connect(self._onSourceTriggered)

    def disconnectFromTriggerSource(self, source: TriggerSource):
        source.sigTriggered.disconnect(self._onSourceTriggered)


@attrs.define
class TriggerSources:
    _sources: tp.Dict[str, TriggerSource] = attrs.field(factory=dict)
    _triggerRouter: TriggerRouter = attrs.field(init=False, factory=TriggerRouter)

    sigTriggerSettingChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str,)))  # includes key of TriggerSource

    def __attrs_post_init__(self):
        for key, source in self._sources.items():
            source.sigTriggerSettingChanged.connect(lambda sourceKey=key: self.sigTriggerSettingChanged.emit(sourceKey))
            self._triggerRouter.connectToTriggerSource(source)

    @property
    def triggerRouter(self):
        return self._triggerRouter

    def __getitem__(self, key: str) -> TriggerSource:
        return self._sources[key]

    def __setitem__(self, key, source: TriggerSource):
        assert isinstance(source, TriggerSource)
        if key in self._sources:
            self._triggerRouter.disconnectFromTriggerSource(self._sources[key])
            raise NotImplementedError  # TODO: disconnect signals from previous source
        self._sources[key] = source
        source.sigTriggerSettingChanged.connect(lambda sourceKey=key: self.sigTriggerSettingChanged.emit(sourceKey))
        self._triggerRouter.connectToTriggerSource(source)

    def __iter__(self):
        return iter(self._sources)

    def __len__(self):
        return len(self._sources)

    def keys(self):
        return self.sources.keys()

    def items(self):
        return self._sources.items()

    def asDict(self) -> dict[str, dict[str, tp.Any]]:
        return {key: source.asDict() for key, source in self._sources.items()}

    @classmethod
    def fromDict(cls, d):
        sources = dict()
        for key, triggerSourceDict in d.items():
            sources[key] = TriggerSource.fromDict(triggerSourceDict)
        return cls(sources=sources)



