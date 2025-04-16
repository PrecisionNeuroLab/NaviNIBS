from __future__ import annotations

import asyncio

import attrs
from datetime import datetime
import logging
from math import ceil
import numpy as np
import os
import pathlib
import pyvista as pv
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from . import MainViewPanel
from NaviNIBS.Navigator.GUI.Widgets.MRIViews import MRISliceView
from NaviNIBS.Navigator.GUI.Widgets.SurfViews import SurfSliceView, Surf3DView
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from NaviNIBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class HeadModelPanel(MainViewPanel):
    _key: str = 'Set head model'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.head-cog-outline'))
    _filepathWdgt: QFileSelectWidget = attrs.field(init=False)
    _activeSurfWidget: QtWidgets.QListWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[SurfSliceView, Surf3DView]] = attrs.field(init=False, factory=dict)

    finishedAsyncInit: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def canBeEnabled(self) -> tuple[bool, str | None]:
        if self.session is None:
            return False, 'No session set'
        if not self.session.MRI.isSet:
            return False, 'No MRI set'
        return True, None

    def _finishInitialization(self):
        # don't initialize computationally-demanding views until panel is activated (viewed)

        super()._finishInitialization()

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
        #self._activeSurfWidget.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.Maximum)
        containerWdgt.layout().addRow('Surfaces', self._activeSurfWidget)
        containerWdgt.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.Maximum)
        self._wdgt.layout().addWidget(containerWdgt)

        containerWdgt = QtWidgets.QWidget()
        containerWdgt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        containerLayout = QtWidgets.QGridLayout()
        containerWdgt.setLayout(containerLayout)
        self._wdgt.layout().addWidget(containerWdgt)
        for iRow, iCol, key in ((0, 1, 'x'), (0, 0, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            if key in ('x', 'y', 'z'):
                self._views[key] = SurfSliceView(normal=key)
            elif key == '3D':
                self._views[key] = Surf3DView(normal=key, surfOpacity=1)
            else:
                raise NotImplementedError()

            self._views[key].sigSliceOriginChanged.connect(lambda key=key: self._onSliceOriginChanged(sourceKey=key))

            containerLayout.addWidget(self._views[key].wdgt, iRow, iCol)

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

    def _onSliceOriginChanged(self, sourceKey: str):
        for key, view in self._views.items():
            if key == sourceKey:
                continue
            view.sliceOrigin = self._views[sourceKey].sliceOrigin

    def _onSessionSet(self):
        super()._onSessionSet()

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):
        self._updateFilepath()
        self._updateRelativeToPath()
        self.session.sigInfoChanged.connect(self._onSessionInfoChanged)
        self.session.headModel.sigFilepathChanged.connect(self._updateFilepath)
        self.session.headModel.sigDataChanged.connect(self._onHeadModelUpdated)

        for key, view in self._views.items():
            view.session = self.session

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._finishInitialization_async))

    async def _finishInitialization_async(self):
        for view in self._views.values():
            await view.finishedAsyncInit.wait()

        self.finishedAsyncInit.set()

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

        self._activeSurfWidget.setMaximumHeight(ceil(self._activeSurfWidget.sizeHintForRow(0) * (self._activeSurfWidget.count() + 0.2)))

        for key, view in self._views.items():
            view.updateView()

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