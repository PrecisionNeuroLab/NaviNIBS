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
from RTNaBS.Navigator.GUI.Widgets.MRIViews import MRISliceView, MRI3DView
from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget


logger = logging.getLogger(__name__)




@attrs.define()
class MRIPanel(MainViewPanel):
    _filepathWdgt: QFileSelectWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[MRISliceView, MRI3DView]] = attrs.field(init=False, factory=dict)
    _hasBeenActivated: bool = attrs.field(init=False, default=False)

    def __attrs_post_init__(self):
        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        wdgt = QFileSelectWidget(browseMode='getOpenFilename',
                                 extFilters='Nifti (*.nii; *.nii.gz)')
        # TODO: set supported file formats to (.nii | .nii.gz) only
        wdgt.sigFilepathChanged.connect(self._onBrowsedNewFilepath)
        self._wdgt.layout().addWidget(wdgt)
        self._filepathWdgt = wdgt

        containerWdgt = QtWidgets.QWidget()
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

        self.sigPanelActivated.connect(self._onPanelActivated)

    def _onPanelActivated(self):
        # don't initialize computationally-demanding views until panel is activated (viewed)
        for key, view in self._views.items():
            if view.session is None and self.session is not None:
                view.session = self.session
        self._hasBeenActivated = True

    def _onSliceOriginChanged(self, sourceKey: str):
        for key, view in self._views.items():
            if key == sourceKey:
                continue
            view.sliceOrigin = self._views[sourceKey].sliceOrigin

    def _onSessionSet(self):
        super()._onSessionSet()
        self._updateFilepath()
        self._updateRelativeToPath()
        self.session.sigInfoChanged.connect(self._updateRelativeToPath)
        self.session.MRI.sigFilepathChanged.connect(self._updateFilepath)

        if self._hasBeenActivated:
            for key, view in self._views.items():
                view.session = self.session

    def _updateFilepath(self):
        self._filepathWdgt.filepath = self.session.MRI.filepath

    def _updateRelativeToPath(self):
        self._filepathWdgt.showRelativeTo = os.path.dirname(self.session.filepath)

    def _onBrowsedNewFilepath(self, newFilepath: str):
        self.session.MRI.filepath = newFilepath
