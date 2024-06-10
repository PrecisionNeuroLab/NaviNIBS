from __future__ import annotations

import keyboard
import typing as tp

import attrs
import logging
from qtpy import QtWidgets

from .TriggerSourceSettingsWidget import TriggerSourceSettingsWidget
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model.Triggering import HotkeyTriggerSource, Hotkey, Hotkeys, TriggerEvent
from NaviNIBS.Navigator.GUI.CollectionModels.HotkeysTableModel import HotkeysTableModel
from NaviNIBS.Navigator.GUI.Widgets.CollectionTableWidget import CollectionTableWidget


logger = logging.getLogger(__name__)


@attrs.define(kw_only=True)
class HotkeysTableWidget(CollectionTableWidget[str, Hotkey, Hotkeys, HotkeysTableModel]):
    _triggerSourceKey: str
    _Model: tp.Callable[[Session], HotkeysTableModel] = None

    def __attrs_post_init__(self):
        self._Model = lambda session: HotkeysTableModel(session=session, triggerSourceKey=self._triggerSourceKey)

        super().__attrs_post_init__()


@attrs.define
class HotkeyTriggerSourceSettingsWidget(TriggerSourceSettingsWidget[HotkeyTriggerSource]):
    _triggerSourceKey: str
    _title: str = 'Hotkey trigger settings'

    _hotkeyCallbacks: dict[str, tp.Callable] = attrs.field(init=False, factory=dict)
    _hotkeysTable: HotkeysTableWidget = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._hotkeysTable = HotkeysTableWidget(triggerSourceKey=self._triggerSourceKey, session=self.session)
        self._wdgt.setLayout(QtWidgets.QHBoxLayout())
        self._wdgt.layout().addWidget(self._hotkeysTable.wdgt)

        self.triggerSource.hotkeys.sigItemsAboutToChange.connect(self._onHotkeysAboutToChange)
        self.triggerSource.hotkeys.sigItemsChanged.connect(self._onHotkeysChanged)
        self.triggerSource.hotkeys.sigItemKeyAboutToChange.connect(self._onHotkeyKeyAboutToChange)
        self.triggerSource.hotkeys.sigItemKeyChanged.connect(self._onHotkeyKeyChanged)

        for key in self.triggerSource.hotkeys.keys():
            self._registerHotkey(key)

    def _onHotkeysAboutToChange(self, keys: list[str], attribs: tp.Optional[list[str]]):
        if attribs is None:
            # key may be being removed
            for key in keys:
                if key in self._hotkeyCallbacks:
                    self._unregisterHotkey(key)

    def _onHotkeysChanged(self, keys: list[str], attribs: tp.Optional[list[str]]):
        if attribs is None:
            # key may have just been removed or added
            for key in keys:
                if key in self.triggerSource.hotkeys:
                    # key may have just been added
                    self._registerHotkey(key)
                else:
                    pass # key was just removed, already handled by aboutToChange above

    def _onHotkeyKeyAboutToChange(self, oldKey: str, newKey: str):
        if oldKey in self._hotkeyCallbacks:
            self._unregisterHotkey(oldKey)

    def _onHotkeyKeyChanged(self, oldKey: str, newKey: str):
        pass  # wil be registered by onHotkeysChanged

    def _onKeyPressed(self, whichKey: str, evt: keyboard.KeyboardEvent):
        hotkey = self.triggerSource.hotkeys[whichKey]
        if hotkey.keyboardDeviceID is None or str(evt.device) == str(hotkey.keyboardDeviceID):
            logger.info(f'Hotkey pressed: {whichKey} ({evt})')
            triggerEvt = TriggerEvent(type=hotkey.action)
            self.triggerSource.trigger(triggerEvt)
        else:
            logger.debug(f'Hotkey pressed but did not match keyboardDeviceID: {whichKey} {evt}')

    def _registerHotkey(self, key: str):
        logger.info(f'Registering hotkey {key}')
        assert key not in self._hotkeyCallbacks
        hotkey = self.triggerSource.hotkeys[key]
        callback = lambda evt, key=key: self._onKeyPressed(whichKey=key, evt=evt)
        self._hotkeyCallbacks[key] = callback
        if False:
            keyboard.hook_key(keyboard.key_to_scan_codes(key)[-1], callback=callback)
        else:
            keyboard.hook_key(key, callback=callback)

    def _unregisterHotkey(self, key: str):
        logger.info(f'Unregistering hotkey {key}')
        assert key in self._hotkeyCallbacks
        keyboard.unhook(self._hotkeyCallbacks.pop(key))
