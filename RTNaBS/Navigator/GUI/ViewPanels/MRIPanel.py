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
    _plottersInitialized: bool = attrs.field(init=False, default=False)
    _sliceOrigin: tp.Optional[np.ndarray] = None

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

    def _onSlicePointChanged(self, key: str):
        pos = self._plotters[key].picked_point
        self._sliceOrigin = pos
        self._updateImagePreview()

    def _updateImagePreview(self):
        data = self.session.MRI.data

        if data is None:
            # no data available

            for plotter in self._plotters.values():
                plotter.clear()

            self._sliceOrigin = None
            self._plottersInitialized = False
            return

        # data available, update display
        if self._sliceOrigin is None:
            self._sliceOrigin = (self.session.MRI.data.affine @ np.append(np.asarray(self.session.MRI.data.shape)/2, 1))[:-1]

        for iKey, key in enumerate(('x', 'y', 'z')):
            slice = self.session.MRI.dataAsUniformGrid.slice(normal=key, origin=self._sliceOrigin)
            self._plotters[key].add_mesh(slice,
                                         name='slice',
                                         cmap='gray')
            self._plotters[key].camera_position = 'xyz'.replace(key, '')
            self._plotters[key].enable_image_style()
            if not self._plottersInitialized:
                self._plotters[key].enable_point_picking(left_clicking=True,
                                                         show_message=False,
                                                         show_point=False,
                                                         callback=lambda newPt, key=key: self._onSlicePointChanged(key))

        if not self._plottersInitialized:
            self._plotters['3D'].add_volume(self.session.MRI.dataAsUniformGrid.gaussian_smooth(),
                                     name='vol',
                                     cmap='bone',
                                     opacity='geom',
                                     shade=True)

        for key in ('x', 'y', 'z', '3D'):
            lineLength = 300  # TODO: scale by image size
            pts = None
            if key == '3D':
                crosshairAxes = 'xyz'
                centerGapLength = 10  # TODO: scale by image size
            else:
                crosshairAxes = 'xyz'.replace(key, '')
                centerGapLength = 0
            for axis in crosshairAxes:
                mask = np.zeros((1, 3))
                mask[0, 'xyz'.index(axis)] = 1
                newPts = np.asarray([centerGapLength/2, lineLength])[:, np.newaxis] * mask
                newPts = np.concatenate((newPts, -1 * newPts), axis=0)
                newPts += self._sliceOrigin
                if pts is None:
                    pts = newPts
                else:
                    pts = np.concatenate((pts, newPts), axis=0)
            self._plotters[key].add_lines(pts, color='#11DD11', width=2, name='Crosshair')

        self._plottersInitialized = True

