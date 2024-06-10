from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import logging
import nibabel as nib
from nibabel.affines import apply_affine
import numpy as np
import os
import pathlib
import pyvista as pv
import pyvistaqt as pvqt
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from . import MainViewPanel
from NaviNIBS.Navigator.GUI.Widgets.MRIViews import MRISliceView, MRI3DView
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.GUI.QFileSelectWidget import QFileSelectWidget


logger = logging.getLogger(__name__)


@attrs.define()
class MRIPanel(MainViewPanel):
    _key: str = 'Set MRI'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.image'))
    _filepathWdgt: QFileSelectWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[MRISliceView, MRI3DView]] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def canBeEnabled(self) -> tuple[bool, str | None]:
        if self.session is None:
            return False, 'No session set'
        return True, None

    def _finishInitialization(self):
        # don't initialize computationally-demanding views until panel is activated (viewed)

        super()._finishInitialization()

        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        wdgt = QFileSelectWidget(browseMode='getOpenFilename',
                                 extFilters='Nifti (*.nii; *.nii.gz)')
        # TODO: set supported file formats to (.nii | .nii.gz) only
        wdgt.sigFilepathChanged.connect(self._onBrowsedNewFilepath)
        self._wdgt.layout().addWidget(wdgt)
        self._filepathWdgt = wdgt

        containerWdgt = QtWidgets.QWidget()
        containerWdgt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        containerLayout = QtWidgets.QGridLayout()
        containerWdgt.setLayout(containerLayout)
        self._wdgt.layout().addWidget(containerWdgt)
        for iRow, iCol, key in ((0, 1, 'x'), (0, 0, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            if key in ('x', 'y', 'z'):
                self._views[key] = MRISliceView(normal=key)
            elif key == '3D':
                self._views[key] = MRI3DView(label=key)
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
        self.session.MRI.sigFilepathChanged.connect(self._updateFilepath)

        for key, view in self._views.items():
            view.session = self.session

    def _updateFilepath(self):
        self._filepathWdgt.filepath = self.session.MRI.filepath

    def _onSessionInfoChanged(self, whatChanged: tp.Optional[list[str]] = None):
        if whatChanged is None or 'filepath' in whatChanged:
            self._updateRelativeToPath()

    def _updateRelativeToPath(self):
        self._filepathWdgt.showRelativeTo = os.path.dirname(self.session.filepath)
        self._filepathWdgt.showRelativePrefix = '<session>'

    def _onBrowsedNewFilepath(self, newFilepath: str):
        self.session.MRI.filepath = newFilepath
