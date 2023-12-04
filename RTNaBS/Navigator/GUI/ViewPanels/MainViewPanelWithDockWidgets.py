from __future__ import annotations

import asyncio
import attrs
import logging
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.DockWidgetLayouts import DockWidgetLayout
from RTNaBS.Navigator.GUI.ViewPanels import MainViewPanel
from RTNaBS.util import exceptionToStr
from RTNaBS.util.GUI.Dock import Dock, DockArea
from RTNaBS.util.Signaler import Signal

logger = logging.getLogger(__name__)


@attrs.define
class MainViewPanelWithDockWidgets(MainViewPanel):
    _wdgt: DockArea = attrs.field(init=False)

    _dockWidgets: dict[str, Dock] = attrs.field(init=False, factory=dict)
    _layoutMayHaveChanged: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    sigAboutToRestoreLayout: Signal = attrs.field(init=False, factory=Signal)
    sigRestoredLayout: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        self._wdgt = DockArea(affinities=[self._key])
        self._wdgt.setContentsMargins(2, 2, 2, 2)

        super().__attrs_post_init__()

    def _createDockWidget(self,
                          title: str,
                          widget: tp.Optional[QtWidgets.QWidget] = None,
                          layout: tp.Optional[QtWidgets.QLayout] = None):

        dock = Dock(name=self._key + title,
                   title=title,
                   closable=False,
                   affinities=[self._key])

        if widget is None:
            widget = QtWidgets.QWidget()
        if layout is not None:
            widget.setLayout(layout)
        dock.addWidget(widget)
        self._dockWidgets[title] = dock
        return dock, widget

    def restoreLayoutIfAvailable(self) -> bool:
        """
        Must be called by subclass (if desired) after all necessary initialization is complete.

        Returns True if layout was restored, False otherwise.
        """
        if self.session is None:
            return False

        layout = self.session.dockWidgetLayouts.get(self._key, None)
        if layout is None or layout.state is None:
            return False

        assert layout.affinities == [self._key]

        logger.debug(f'About to restore layout for {self._key}')
        self.sigAboutToRestoreLayout.emit()
        try:
            self._wdgt.restoreState(layout.state)
        except Exception as e:
            logger.warning(f'Unable to restore layout: {exceptionToStr(e)}')
            return False

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

        layout.state = self._wdgt.saveState()


