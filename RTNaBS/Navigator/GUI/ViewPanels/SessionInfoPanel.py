from __future__ import annotations

import asyncio
import appdirs
import attrs
import logging
import os
import pathlib
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from . import MainViewPanel
from RTNaBS.util.Signaler import Signal
from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class SessionInfoPanel(MainViewPanel):
    _wdgts: tp.Dict[str, QtWidgets.QLineEdit] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        self._wdgt.setLayout(QtWidgets.QFormLayout())

        wdgt = QtWidgets.QLineEdit()
        wdgt.textEdited.connect(lambda text, key='subjectID': self._onTextEdited(key, text))
        self._wdgts['subjectID'] = wdgt
        self._wdgt.layout().addRow('Subject ID', wdgt)
        # TODO: continue here

        wdgt = QtWidgets.QLineEdit()
        wdgt.textEdited.connect(lambda text, key='sessionID': self._onTextEdited(key, text))
        self._wdgts['sessionID'] = wdgt
        self._wdgt.layout().addRow('Session ID', wdgt)

    def _onSessionSet(self):
        if self.session is not None:
            self.session.sigInfoChanged.connect(self._onSessionInfoChanged)
        self._onSessionInfoChanged()

    def _onSessionInfoChanged(self):
        if self.session is None:
            for key in ('subjectID', 'sessionID'):
                self._wdgts[key].setText('')
        else:
            for key in ('subjectID', 'sessionID'):
                val = getattr(self.session, key)
                self._wdgts[key].setText('' if val is None else val)

    def _onTextEdited(self, key: str, text: str):
        if len(text) == 0:
            text = None
        if self.session is not None:
            logger.info('Applying edited value of {} to session: {}'.format(key, text))
            setattr(self.session, key, text)
        else:
            logger.warning('Ignoring edited value of {} since session is closed.'.format(key))
