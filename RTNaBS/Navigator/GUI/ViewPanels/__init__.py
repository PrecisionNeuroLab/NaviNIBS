from __future__ import annotations

import attrs
import logging
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class MainViewPanel:
    _session: tp.Optional[Session] = None
    _wdgt: QtWidgets.QWidget = attrs.field(init=False, factory=QtWidgets.QWidget)

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
