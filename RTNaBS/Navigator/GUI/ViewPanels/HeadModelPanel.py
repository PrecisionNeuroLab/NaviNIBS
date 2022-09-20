from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import logging
import numpy as np
import os
import pathlib
import pyvista as pv
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from . import MainViewPanel
from RTNaBS.Navigator.GUI.Widgets.MRIViews import MRISliceView
from RTNaBS.Navigator.GUI.Widgets.SurfViews import SurfSliceView, Surf3DView
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class HeadModelPanel(MainViewPanel):
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.head-cog-outline'))
    _filepathWdgt: QFileSelectWidget = attrs.field(init=False)
    _activeSurfWidget: QtWidgets.QListWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[SurfSliceView, Surf3DView]] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        wdgt = QFileSelectWidget(browseMode='getOpenFilename',
                                 extFilters='Gmsh (*.msh)')
        wdgt.sigFilepathChanged.connect(self._onBrowsedNewFilepath)
        self._wdgt.layout().addWidget(wdgt)
        self._filepathWdgt = wdgt

        containerWdgt = QtWidgets.QWidget()
        containerWdgt.setLayout(QtWidgets.QFormLayout())
        self._activeSurfWidget = QtWidgets.QListWidget()
        self._activeSurfWidget.itemSelectionChanged.connect(lambda *args, **kwargs: self._onSurfSelectionChanged())
        self._activeSurfWidget.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.Maximum)
        containerWdgt.layout().addRow('Surfaces', self._activeSurfWidget)
        self._wdgt.layout().addWidget(containerWdgt)

        containerWdgt = QtWidgets.QWidget()
        containerLayout = QtWidgets.QGridLayout()
        containerWdgt.setLayout(containerLayout)
        self._wdgt.layout().addWidget(containerWdgt)
        for iRow, iCol, key in ((0, 1, 'x'), (0, 0, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            if key in ('x', 'y', 'z'):
                self._views[key] = SurfSliceView(normal=key)
            elif key == '3D':
                self._views[key] = Surf3DView(normal=key)
            else:
                raise NotImplementedError()

            self._views[key].sigSliceOriginChanged.connect(lambda key=key: self._onSliceOriginChanged(sourceKey=key))

            containerLayout.addWidget(self._views[key].wdgt, iRow, iCol)

    def canBeEnabled(self) -> bool:
        return self.session is not None and self.session.MRI.isSet

    def _finishInitialization(self):
        # don't initialize computationally-demanding views until panel is activated (viewed)

        super()._finishInitialization()

        for key, view in self._views.items():
            if view.session is None and self.session is not None:
                view.session = self.session

    def _onSliceOriginChanged(self, sourceKey: str):
        for key, view in self._views.items():
            if key == sourceKey:
                continue
            view.sliceOrigin = self._views[sourceKey].sliceOrigin

    def _onSessionSet(self):
        super()._onSessionSet()
        self._updateFilepath()
        self._updateRelativeToPath()
        self.session.sigInfoChanged.connect(self._onSessionInfoChanged)
        self.session.headModel.sigFilepathChanged.connect(self._updateFilepath)
        self.session.headModel.sigDataChanged.connect(self._onHeadModelUpdated)

        if self._hasInitialized:
            for key, view in self._views.items():
                view.session = self.session

    def _onHeadModelUpdated(self, whatChanged: str):
        prevSelected = self._activeSurfWidget.selectedItems()
        if len(prevSelected) > 0:
            prevSelectedKey = prevSelected[0].text()
        else:
            prevSelectedKey = None
        self._activeSurfWidget.clear()
        if self.session is not None and self.session.headModel.isSet:
            for key in self.session.headModel.surfKeys:
                self._activeSurfWidget.addItem(key)
            if prevSelectedKey is not None:
                selectKey = prevSelectedKey
            else:
                selectKey = 'gmSurf'
            self._activeSurfWidget.setCurrentItem(self._activeSurfWidget.findItems(selectKey, QtCore.Qt.MatchExactly)[0])

    def _onSurfSelectionChanged(self):
        selected = self._activeSurfWidget.selectedItems()
        if len(selected) == 0:
            return
        selectedKey = selected[0].text()
        for key, view in self._views.items():
            view.activeSurf = selectedKey

    def _updateFilepath(self):
        self._filepathWdgt.filepath = self.session.headModel.filepath

    def _onSessionInfoChanged(self, whatChanged: tp.Optional[list[str]] = None):
        if whatChanged is None or 'filepath' in whatChanged:
            self._updateRelativeToPath()

    def _updateRelativeToPath(self):
        self._filepathWdgt.showRelativeTo = os.path.dirname(self.session.filepath)
        self._filepathWdgt.showRelativePrefix = '<session>'

    def _onBrowsedNewFilepath(self, newFilepath: str):
        self.session.headModel.filepath = newFilepath