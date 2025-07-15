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
from NaviNIBS.util.GUI.Icons import getIcon
from NaviNIBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from NaviNIBS.util.GUI.SpatialTransformWidget import SpatialTransformDisplayWidget
from NaviNIBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class HeadModelPanel(MainViewPanel):
    _key: str = 'Set head model'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: getIcon('mdi6.head-cog-outline'))
    _filepathWdgt: QFileSelectWidget = attrs.field(init=False)
    _skinFilepathWdgt: QFileSelectWidget = attrs.field(init=False, default=None)
    _gmFilepathWdgt: QFileSelectWidget = attrs.field(init=False, default=None)
    _activeSurfWidget: QtWidgets.QListWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[SurfSliceView, Surf3DView]] = attrs.field(init=False, factory=dict)
    _surfAliases: tp.Dict[str, str] = attrs.field(init=False, factory=lambda: {
        'skinSurf': 'Skin',
        'csfSurf': 'CSF', 
        'gmSurf': 'Gray matter'
    })  # show nice aliases for the surfaces

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

        containerWdgt = QtWidgets.QWidget()
        containerWdgt.setLayout(QtWidgets.QFormLayout())
        self._wdgt.layout().addWidget(containerWdgt)

        wdgt = QFileSelectWidget(browseMode='getOpenFilename',
                                 extFilters='Gmsh (*.msh)',
                                 browseCaption='Select SimNIBS-generated .msh file',)
        wdgt.sigFilepathChanged.connect(self._onBrowsedNewFilepath)
        containerWdgt.layout().addRow('SimNIBS .msh file', wdgt)
        self._filepathWdgt = wdgt

        wdgt = QFileSelectWidget(browseMode='getOpenFilename',
                                    extFilters='Mesh (*.stl *.ply)',
                                    browseCaption='Select skin surface mesh file',
                                 )
        wdgt.sigFilepathChanged.connect(lambda newFilepath: setattr(self.session.headModel, 'skinSurfFilepath', newFilepath))
        containerWdgt.layout().addRow('Skin surface file', wdgt)
        self._skinFilepathWdgt = wdgt

        wdgt = QFileSelectWidget(browseMode='getOpenFilename',
                                    extFilters='Mesh (*.stl *.ply)',
                                    browseCaption='Select gray matter surface mesh file',
                                 )
        wdgt.sigFilepathChanged.connect(lambda newFilepath: setattr(self.session.headModel, 'gmSurfFilepath', newFilepath))
        containerWdgt.layout().addRow('Gray matter surface file', wdgt)
        self._gmFilepathWdgt = wdgt

        self._activeSurfWidget = QtWidgets.QListWidget()
        self._activeSurfWidget.itemSelectionChanged.connect(lambda *args, **kwargs: self._onSurfSelectionChanged())
        #self._activeSurfWidget.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.Maximum)
        containerWdgt.layout().addRow('Loaded surfaces', self._activeSurfWidget)
        containerWdgt.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.Maximum)

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
        self._updateFilepaths()
        self._updateRelativeToPath()
        self.session.sigInfoChanged.connect(self._onSessionInfoChanged)
        self.session.headModel.sigFilepathChanged.connect(self._updateFilepaths)
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
            prevSelectedKey = prevSelected[0].data(QtCore.Qt.ItemDataRole.UserRole)  # Get the actual key from user data
        else:
            prevSelectedKey = None
        self._activeSurfWidget.clear()
        if self.session is not None and self.session.headModel.isSet:
            for key in self.session.headModel.surfKeys:
                item = QtWidgets.QListWidgetItem()
                # Display the nice alias, but store the actual key in user data
                item.setText(self._surfAliases.get(key, key))
                item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
                self._activeSurfWidget.addItem(item)
            if prevSelectedKey is not None:
                selectKey = prevSelectedKey
            else:
                selectKey = 'gmSurf'
            # Find item by the stored key in user data
            for i in range(self._activeSurfWidget.count()):
                item = self._activeSurfWidget.item(i)
                if item.data(QtCore.Qt.ItemDataRole.UserRole) == selectKey:
                    self._activeSurfWidget.setCurrentItem(item)
                    break

        self._activeSurfWidget.setMaximumHeight(ceil(self._activeSurfWidget.sizeHintForRow(0) * (self._activeSurfWidget.count() + 0.2)))

        for key, view in self._views.items():
            view.updateView()

    def _onSurfSelectionChanged(self):
        selected = self._activeSurfWidget.selectedItems()
        if len(selected) == 0:
            return
        selectedKey = selected[0].data(QtCore.Qt.ItemDataRole.UserRole)  # Get the actual key from user data
        for key, view in self._views.items():
            view.activeSurf = selectedKey

    def _updateFilepaths(self):
        self._filepathWdgt.filepath = self.session.headModel.filepath
        self._skinFilepathWdgt.filepath = self.session.headModel.skinSurfFilepath
        self._gmFilepathWdgt.filepath = self.session.headModel.gmSurfFilepath
        if self.session.headModel.filepath is not None:
            self._skinFilepathWdgt.placeholderText = 'From SimNIBS .msh file'
            self._gmFilepathWdgt.placeholderText = 'From SimNIBS .msh file'
        else:
            self._skinFilepathWdgt.placeholderText = None
            self._gmFilepathWdgt.placeholderText = None

    def _onSessionInfoChanged(self, whatChanged: tp.Optional[list[str]] = None):
        if whatChanged is None or 'filepath' in whatChanged:
            self._updateRelativeToPath()

    def _updateRelativeToPath(self):
        for wdgt in (self._filepathWdgt, self._skinFilepathWdgt, self._gmFilepathWdgt):
            wdgt.showRelativeTo = os.path.dirname(self.session.filepath)
            wdgt.showRelativePrefix = '<session>'

    def _onBrowsedNewFilepath(self, newFilepath: str):
        self.session.headModel.filepath = newFilepath