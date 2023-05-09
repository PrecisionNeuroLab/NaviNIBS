from __future__ import annotations

import asyncio
import attrs
import logging
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.DockWidgetLayouts import DockWidgetLayout
from RTNaBS.Navigator.GUI.ViewPanels import MainViewPanel
from RTNaBS.util.GUI import DockWidgets as dw
from RTNaBS.util.GUI.DockWidgets.DockWidgetsContainer import DockWidgetsContainer
from RTNaBS.util.Signaler import Signal

logger = logging.getLogger(__name__)


@attrs.define
class MainViewPanelWithDockWidgets(MainViewPanel):
    _wdgt: DockWidgetsContainer = attrs.field(init=False)

    _layoutMayHaveChanged: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    sigAboutToRestoreLayout: Signal = attrs.field(init=False, factory=Signal)
    sigRestoredLayout: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        self._wdgt = DockWidgetsContainer(uniqueName=self._key)
        self._wdgt.setAffinities([self._key])

        super().__attrs_post_init__()

    def restoreLayoutIfAvailable(self) -> bool:
        """
        Must be called by subclass (if desired) after all necessary initialization is complete.

        Returns True if layout was restored, False otherwise.
        """
        if self.session is None:
            return False

        layout = self.session.dockWidgetLayouts.get(self._key, None)
        if layout is None or layout.layout is None:
            return False

        assert layout.affinities == [self._key]

        logger.debug(f'About to restore layout for {self._key}')
        self.sigAboutToRestoreLayout.emit()
        layout.restoreLayout(wdgt=self._wdgt)
        logger.debug(f'Restored layout for {self._key}')
        self.sigRestoredLayout.emit()
        return True

    def saveLayout(self):
        assert self.session is not None

        layout = self.session.dockWidgetLayouts.get(self._key, None)
        if layout is None:
            layout = DockWidgetLayout(
                key=self._key,
                affinities=[self._key])
            self.session.dockWidgetLayouts.addItem(layout)

        layout.saveLayout()


