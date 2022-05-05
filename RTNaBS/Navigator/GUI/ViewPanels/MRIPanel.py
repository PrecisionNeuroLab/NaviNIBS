from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import logging
import os
import pathlib
import pyvista as pv
import pyvistaqt as pvqt
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
    _plotters: tp.Dict[str, pvqt.QtInteractor] = attrs.field(init=False, factory=dict)

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
        for iRow, iCol, key in ((0, 0, 'x'), (0, 1, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            self._plotters[key] = pvqt.BackgroundPlotter(
                show=False,
                app=QtWidgets.QApplication.instance()
            )
            # TODO: maybe connect close signal to plotter
            self._plotters[key].set_background(color='#DDDDDD')
            containerLayout.addWidget(self._plotters[key].interactor, iRow, iCol)

    def _onSessionSet(self):
        super()._onSessionSet()
        self._filepathWdgt.filepath = self.session.MRI.filepath

        self._updateRelativeToPath()
        self._updateImagePreview()

        self.session.sigInfoChanged.connect(self._updateRelativeToPath)
        self.session.MRI.sigFilepathChanged.connect(self._updateRelativeToPath)
        self.session.MRI.sigDataChanged.connect(self._updateImagePreview)

    def _updateRelativeToPath(self):
        self._filepathWdgt.showRelativeTo = os.path.dirname(self.session.filepath)

    def _onBrowsedNewFilepath(self, newFilepath: str):
        self.session.MRI.filepath = newFilepath

    def _updateImagePreview(self):
        data = self.session.MRI.data
        if data is None:
            # no data available
            # TODO: clear any previous return
            return

        # data available, update display


        for key in ('x', 'y', 'z'):
            slice = self.session.MRI.dataAsUniformGrid.slice_along_axis(n=1, axis=key)
            self._plotters[key].add_mesh(slice,
                                         cmap='gray')
            self._plotters[key].camera_position = 'xyz'.replace(key, '')

        self._plotters['3D'].add_volume(self.session.MRI.dataAsUniformGrid.gaussian_smooth(),
                                 cmap='bone',
                                 opacity='geom',
                                 shade=True)

        # TODO


