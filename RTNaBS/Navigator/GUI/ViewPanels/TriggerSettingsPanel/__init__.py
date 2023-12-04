from __future__ import annotations

import attrs
import logging

import qtawesome as qta
from qtpy import QtGui
import typing as tp

from . import HotkeyTriggerSourceSettingsWidget
from .HotkeyTriggerSourceSettingsWidget import HotkeyTriggerSourceSettingsWidget

from .. import MainViewPanel
from RTNaBS.Navigator.Model.Triggering import TriggerEvent
from RTNaBS.Navigator.GUI.ViewPanels.MainViewPanelWithDockWidgets import MainViewPanelWithDockWidgets
from .LSLTriggerSourceSettingsWidget import LSLTriggerSourceSettingsWidget
from .TriggerSourceSettingsWidget import TriggerSourceSettingsWidget

logger = logging.getLogger(__name__)


@attrs.define
class TriggerSettingsPanel(MainViewPanelWithDockWidgets):
    _key: str = 'Trigger settings'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.database-import'))

    _lslSettingsWidgets: list[LSLTriggerSourceSettingsWidget] = attrs.field(init=False, factory=list)
    _hotkeySettings: dict[str, HotkeyTriggerSourceSettingsWidget] = attrs.field(init=False, factory=dict)
    _hasInitializedTriggerSources: bool = attrs.field(init=False, default=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _finishInitialization(self):
        super()._finishInitialization()

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

    def _onSessionSet(self):
        super()._onSessionSet()

        # initialize right away so that we will start listening to streams and hotkeys immediately
        assert not self._hasInitializedTriggerSources, 'Updating with new session not implemented'

        assert len(self._hotkeySettings) == 0, 'Updating with new session not implemented'
        for triggerSourceKey, triggerSource in self.session.triggerSources.items():
            match triggerSource.type:
                case 'LSLTriggerSource':
                    self._lslSettingsWidgets.append(LSLTriggerSourceSettingsWidget(
                        dockKey=self._key,
                        session=self.session,
                        triggerSourceKey=triggerSourceKey,
                    ))
                    self._wdgt.addDock(self._lslSettingsWidgets[-1].dock, position='left')
                case 'HotkeyTriggerSource':
                    self._hotkeySettings[triggerSourceKey] = HotkeyTriggerSourceSettingsWidget(
                        triggerSourceKey=triggerSourceKey,
                        title=f'{triggerSourceKey} hotkey settings',
                        dockKey=self._key,
                        session=self.session
                    )
                    self._wdgt.addDock(self._hotkeySettings[triggerSourceKey].dock, position='right')
                case _:
                    raise NotImplementedError(f'Unexpected trigger source type: {triggerSource.type}')

        self._hasInitializedTriggerSources = True

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):
        pass

    def _onTriggered(self, triggerEvt: TriggerEvent):
        pass  # TODO: show GUI indicator about time of last trigger(s)