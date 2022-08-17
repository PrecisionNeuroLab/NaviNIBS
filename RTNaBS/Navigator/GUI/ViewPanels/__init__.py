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

    _wdgt: QtWidgets.QWidget = attrs.field(init=False, factory=QtWidgets.QWidget)

    _hasBeenActivated: bool = attrs.field(init=False, default=False)
    _isActivated: bool = attrs.field(init=False, default=False)

    sigPanelActivated: Signal = attrs.field(init=False, factory=Signal)
    sigPanelDeactivated: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        self.sigPanelActivated.connect(self._onPanelActivated)
        self.sigPanelDeactivated.connect(self._onPanelDeactivated)

    @property
    def key(self):
        return self._key

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newVal: tp.Optional[session]):
        self._session = newVal
        self._onSessionSet()

    def _onSessionSet(self):
        pass  # to be implemented by subclass

    def _onPanelActivated(self):
        self._hasBeenActivated = True
        self._isActivated = True

    def _onPanelDeactivated(self):
        self._isActivated = False
