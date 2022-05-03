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
class HeadModelPanel(MainViewPanel):
    _filepathWdgt: QFileSelectWidget = attrs.field(init=False)

    def __attrs_post_init__(self):
        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        wdgt = QFileSelectWidget(browseMode='getOpenFilename',
                                 extFilters='Gmsh (*.msh)')
        wdgt.sigFilepathChanged.connect(self._onBrowsedNewFilepath)
        self._wdgt.layout().addWidget(wdgt)
        self._filepathWdgt = wdgt

    def _onSessionSet(self):
        super()._onSessionSet()
        self._filepathWdgt.filepath = self.session.headModel.filepath
        self._updateRelativeToPath()
        self.session.sigInfoChanged.connect(lambda: self._updateRelativeToPath())

    def _updateRelativeToPath(self):
        self._filepathWdgt.showRelativeTo = os.path.dirname(self.session.filepath)

    def _onBrowsedNewFilepath(self, newFilepath: str):
        self.session.headModel.filepath = newFilepath