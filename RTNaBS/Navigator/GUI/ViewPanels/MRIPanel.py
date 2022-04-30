from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import logging
import os
import pathlib
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from . import MainViewPanel
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class MRIPanel(MainViewPanel):

    _filepathWdgt: QFileSelectWidget = attrs.field(init=False)

    def __attrs_post_init__(self):
        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        wdgt = QFileSelectWidget(browseMode='getOpenFilename')
        wdgt.sigFilepathChanged.connect(self._onBrowsedNewFilepath)
        self._wdgt.layout().addWidget(wdgt)
        self._filepathWdgt = wdgt

    def _onSessionSet(self):
        super()._onSessionSet()
        self._filepathWdgt.filepath = self.session.MRI.filepath

    def _onBrowsedNewFilepath(self, newFilepath: str):
        self.session.MRI.filepath = newFilepath



