from __future__ import annotations

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

from RTNaBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem

logger = logging.getLogger(__name__)


@attrs.define
class TriggerEvent:
    type: str
    time: pd.Timestamp = attrs.field(factory=pd.Timestamp.now)
    metadata: tp.Dict[str, tp.Any] = attrs.field(factory=dict)


@attrs.define
class TriggerSource(GenericCollectionDictItem[str]):
    type: ClassVar[str] = 'TriggerSource'
    _isEnabled: bool = True

    sigTriggered: Signal = attrs.field(init=False, factory=lambda: Signal((TriggerEvent,)))

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

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
                return HotkeyTriggerSource.fromDict(d)
            case _:
                raise NotImplementedError(f'Unexpected trigger source type: {type}')


@attrs.define
class LSLTriggerSource(TriggerSource):
    type: ClassVar[str] = 'LSLTriggerSource'
    _streamKey: tp.Optional[str] = None
    _triggerEvents: tp.Optional[dict[str, tp.Optional[str]]] = None  # dict mapping of {eventValue: action} on which to trigger
    _defaultAction: str = 'pulse'

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def streamKey(self):
        return self._streamKey

    @streamKey.setter
    def streamKey(self, newKey: tp.Optional[str]):
        if self._streamKey == newKey:
            return
        self.sigItemAboutToChange.emit(self.key, ['streamKey'])
        self._streamKey = newKey
        self.sigItemChanged.emit(self.key, ['streamKey'])

    @property
    def triggerEvents(self):
        return self._triggerEvents

    @triggerEvents.setter
    def triggerEvents(self, newEvents: tp.Optional[dict[str, tp.Optional[str]]]):
        if self._triggerEvents == newEvents:
            return
        self.sigItemAboutToChange.emit(self.key, ['triggerEvents'])
        self._triggerEvents = newEvents
        self.sigItemChanged.emit(self.key, ['triggerEvents'])

    @property
    def defaultAction(self):
        return self._defaultAction


@attrs.define
class Hotkey(GenericCollectionDictItem[str]):
    _key: str
    """
    Literal key that will trigger hotkey (e.g. "." or "PgDn")
    """
    _action: str
    """
    Action to trigger when hotkey fires (e.g. "sample", "previous", or "next")
    """
    _keyboardDeviceID: tp.Optional[str] = None
    """
    Can specify a keyboard's device ID to only respond to keypresses from that device. Useful for situations
    like when using a presenter remote that generates common keypresses like '.' and 'esc', and you don't want
    triggers to be generated when pressing the same keys on a normal keyboard.

    If None, no device filter will be applied.
    """

    @property
    def action(self):
        return self._action

    @action.setter
    def action(self, newAction: str):
        if self._action == newAction:
            return
        self.sigItemAboutToChange.emit(self.key, ['action'])
        self._action = newAction
        self.sigItemChanged.emit(self.key, ['action'])

    @property
    def keyboardDeviceID(self):
        return self._keyboardDeviceID

    @keyboardDeviceID.setter
    def keyboardDeviceID(self, newID: tp.Optional[str]):
        if self._keyboardDeviceID == newID:
            return

        self.sigItemAboutToChange.emit(self.key, ['keyboardDeviceID'])
        self._keyboardDeviceID = newID
        self.sigItemChanged.emit(self.key, ['keyboardDeviceID'])


@attrs.define
class Hotkeys(GenericCollection[str, Hotkey]):
    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @classmethod
    def fromList(cls, itemList: list[dict[str, tp.Any]]) -> Hotkeys:
        items = {}
        for itemDict in itemList:
            items[itemDict['key']] = Hotkey.fromDict(itemDict)

        return cls(items=items)


@attrs.define
class HotkeyTriggerSource(TriggerSource):
    type: ClassVar[str] = 'HotkeyTriggerSource'

    _hotkeys: Hotkeys = attrs.field(factory=Hotkeys)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._hotkeys.sigItemsAboutToChange.connect(lambda *args: self.sigItemAboutToChange.emit(self.key, ['hotkeys']))
        self._hotkeys.sigItemsChanged.connect(lambda *args: self.sigItemChanged.emit(self.key, ['hotkeys']))

    @property
    def hotkeys(self):
        return self._hotkeys

    def asDict(self) -> dict[str, tp.Any]:
        d = attrsAsDict(self, exclude=['hotkeys'])
        d['type'] = self.type
        d['hotkeys'] = self.hotkeys.asList()
        return d

    @classmethod
    def fromDict(cls, d):
        if 'hotkeys' in d:
            d['hotkeys'] = Hotkeys.fromList(d['hotkeys'])
        return cls(**d)


@attrs.define
class TriggerReceiver:
    _key: str
    _minTimeBetweenEvents: tp.Optional[float] = None

    _lastTriggeredEvent: tp.Optional[TriggerEvent] = attrs.field(init=False, default=None)
    sigTriggered: Signal = attrs.field(init=False, factory=lambda: Signal((TriggerEvent,)))

    def __attrs_post_init__(self):
        pass

    @property
    def key(self):
        return self._key

    def trigger(self, event: TriggerEvent):
        if self._minTimeBetweenEvents is not None and self._lastTriggeredEvent is not None:
            timeBetweenEvents = (event.time - self._lastTriggeredEvent.time).total_seconds()
            if timeBetweenEvents < self._minTimeBetweenEvents:
                logger.debug(f'Event {event} occured too quickly after previous, ignoring.')
                return

        self._lastTriggeredEvent = event
        self.sigTriggered.emit(event)


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
                    self._receivers[receiverKey].trigger(triggerEvt)
                except Exception as e:
                    logger.error(f'Problem while signaling exclusive trigger: \n{exceptionToStr(e)}')
        if triggerEvt.type in self._nonexclusiveTriggerReceivers:
            for receiverKey in self._nonexclusiveTriggerReceivers[triggerEvt.type]:
                self._receivers[receiverKey].trigger(triggerEvt)

    def connectToTriggerSource(self, source: TriggerSource):
        source.sigTriggered.connect(self._onSourceTriggered)

    def disconnectFromTriggerSource(self, source: TriggerSource):
        source.sigTriggered.disconnect(self._onSourceTriggered)


@attrs.define
class TriggerSources(GenericCollection[str, TriggerSource]):
    _triggerRouter: TriggerRouter = attrs.field(init=False, factory=TriggerRouter)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        for key, source in self.items():
            self._triggerRouter.connectToTriggerSource(source)

    @property
    def triggerRouter(self):
        return self._triggerRouter

    def setItem(self, item: TriggerSource):
        key = item.key
        if key in self._items:
            self._triggerRouter.disconnectFromTriggerSource(self[key])
        super().setItem(item=item)
        self._triggerRouter.connectToTriggerSource(item)

    @classmethod
    def fromList(cls, itemList: list[dict[str, tp.Any]]) -> TriggerSources:
        items = {}
        for itemDict in itemList:
            items[itemDict['key']] = TriggerSource.fromDict(itemDict)

        return cls(items=items)



