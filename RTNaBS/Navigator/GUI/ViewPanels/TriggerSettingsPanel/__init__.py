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
from RTNaBS.util.GUI import DockWidgets as dw
from RTNaBS.util.GUI.DockWidgets.DockWidgetsContainer import DockWidgetsContainer
from .LSLTriggerSourceSettingsWidget import LSLTriggerSourceSettingsWidget
from .TriggerSourceSettingsWidget import TriggerSourceSettingsWidget

logger = logging.getLogger(__name__)


@attrs.define
class TriggerSettingsPanel(MainViewPanel):
    _key: str = 'Trigger settings'
    _wdgt: DockWidgetsContainer = attrs.field(init=False)
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.database-import'))

    _lslSettings: tp.Optional[LSLTriggerSourceSettingsWidget] = attrs.field(init=False, default=None)
    _hotkeySettings: dict[str, HotkeyTriggerSourceSettingsWidget] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        self._wdgt = DockWidgetsContainer(uniqueName=self._key)
        self._wdgt.setAffinities([self._key])

        super().__attrs_post_init__()

    def _finishInitialization(self):
        super()._finishInitialization()

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

    def _onSessionSet(self):
        super()._onSessionSet()

        # initialize right away so that we will start listening to streams and hotkeys immediately

        assert self._lslSettings is None, 'Updating with new session not implemented'
        self._lslSettings = LSLTriggerSourceSettingsWidget(dockKey=self._key, session=self.session)
        self._wdgt.addDockWidget(self._lslSettings.cdw, dw.DockWidgetLocation.OnLeft)

        assert len(self._hotkeySettings) == 0, 'Updating with new session not implemented'
        for triggerSourceKey, triggerSource in self.session.triggerSources.items():
            if triggerSource.type == 'HotkeyTriggerSource':
                self._hotkeySettings[triggerSourceKey] = HotkeyTriggerSourceSettingsWidget(
                    triggerSourceKey=triggerSourceKey,
                    title=f'{triggerSourceKey} hotkey settings',
                    dockKey=self._key,
                    session=self.session
                )
                self._wdgt.addDockWidget(self._hotkeySettings[triggerSourceKey].cdw, dw.DockWidgetLocation.OnRight)

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):
        pass

    def _onTriggered(self, triggerEvt: TriggerEvent):
        pass  # TODO: show GUI indicator about time of last trigger(s)