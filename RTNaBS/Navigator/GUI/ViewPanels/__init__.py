from __future__ import annotations

import attrs
import logging
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.util.GUI import DockWidgets as dw
from RTNaBS.util.GUI.DockWidgets.MainWindowWithDocksAndCloseSignal import MainWindowWithDocksAndCloseSignal
from RTNaBS.util.Signaler import Signal


logger = logging.getLogger(__name__)


@attrs.define()
class MainViewPanel:
    _key: str
    _session: tp.Optional[Session] = None

    _icon: tp.Optional[QtGui.QIcon] = attrs.field(default=None)  # to be set by subclass

    _wdgt: QtWidgets.QWidget = attrs.field(init=False, factory=QtWidgets.QWidget)
    _dockWdgt: dw.DockWidget = attrs.field(init=False)

    _hasInitialized: bool = attrs.field(init=False, default=False)
    _isInitializing: bool = attrs.field(init=False, default=False)  # True while in the middle of finishing initializing

    _isShown: bool = attrs.field(init=False, default=False)
    sigPanelShown: Signal = attrs.field(init=False, factory=Signal)
    sigPanelHidden: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        logger.info(f'Initializing {self.__class__.__name__}')
        self._dockWdgt = dw.DockWidget(self._key)
        self._dockWdgt.setAffinities(['MainViewPanel'])  # only allow docking with other main view panels
        self._dockWdgt.setWidget(self._wdgt)
        if self._icon is not None:
            self._dockWdgt.setIcon(self._icon)

        self._dockWdgt.hidden.connect(self.sigPanelHidden.emit)
        self._dockWdgt.shown.connect(self.sigPanelShown.emit)

        self.sigPanelShown.connect(self._onPanelShown)
        self.sigPanelHidden.connect(self._onPanelHidden)

        self._wdgt.setEnabled(False)
        self._wdgt.setVisible(False)

    @property
    def key(self):
        return self._key

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def dockWdgt(self):
        return self._dockWdgt

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newVal: tp.Optional[session]):
        self._session = newVal
        self._onSessionSet()

    @property
    def isVisible(self):
        return self._dockWdgt.isCurrentTab()

    @property
    def hasInitialized(self):
        return self._hasInitialized

    @property
    def isInitializing(self):
        return self._isInitializing

    def _onSessionSet(self):
        pass  # to be implemented by subclass

    def _onPanelShown(self):
        logger.info(f'Panel {self.key} shown')
        self._isShown = True
        if not self._hasInitialized and self.canBeEnabled():
            self.finishInitialization()

    def _onPanelHidden(self):
        logger.info(f'Panel {self.key} hidden')
        self._isShown = False

    def canBeEnabled(self) -> bool:
        return True  # can be implemented by subclass to indicate when panel is missing critical information and can't yet show anything useful

    def finishInitialization(self):
        if self._isInitializing:
            logger.warning(f"{self.key} is already finishing initializing, skipping.")
            return

        if self._hasInitialized:
            logger.warning(f"{self.key} finish initialization requested, but has already initialized. Not running again.")
            return

        if not self.canBeEnabled():
            logger.warning(f"{self.key} finish initialization requested, but not yet ready to be enabled. Skipping.")
            return

        self._isInitializing = True
        self._finishInitialization()
        self._isInitializing = False

        self._wdgt.setEnabled(True)
        self._wdgt.setVisible(True)
        self._hasInitialized = True

    def _finishInitialization(self):
        pass  # can be implemented by subclass

    def _onPanelDeactivated(self):
        pass
