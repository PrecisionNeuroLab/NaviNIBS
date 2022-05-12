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
from RTNaBS.Navigator.GUI.Widgets.MRISliceView import MRISliceView
from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget


logger = logging.getLogger(__name__)


@attrs.define()
class MRI3DView(MRISliceView):
    _clim: tp.Tuple[float, float] = (300, 1000)  # TODO: set to auto-initialize instead of hardcoding default

    def __attrs_post_init__(self):
        MRISliceView.__attrs_post_init__(self)

    @property
    def label(self):
        if self._label is None:
            return 'MRI3D'
        else:
            return self._label

    def _updateView(self):

        if self.session is None or self.session.MRI.data is None:
            # no data available
            if self._plotterInitialized:
                logger.debug('Clearing plot for {} slice'.format(self.label))
                self._plotter.clear()

                self.sliceOrigin = None
                self._plotterInitialized = False
            return

        # data available, update display
        logger.debug('Updating plot for {} slice'.format(self.label))
        if self._sliceOrigin is None:
            self.sliceOrigin = (self.session.MRI.data.affine @ np.append(np.asarray(self.session.MRI.data.shape) / 2,
                                                                         1))[:-1]
            return  # prev line will have triggered its own update

        if not self._plotterInitialized:
            logger.debug('Initializing 3D plot')
            self._plotter.add_volume(self.session.MRI.dataAsUniformGrid.gaussian_smooth(),
                                     scalars='MRI',
                                     name='vol',
                                     clim=self._clim,
                                     cmap='gray',
                                     mapper='gpu',
                                     opacity=[0, 1, 1],
                                     shade=False)

        logger.debug('Setting crosshairs for {} plot'.format(self.label))
        lineLength = 300  # TODO: scale by image size
        crosshairAxes = 'xyz'
        centerGapLength = 0
        for axis in crosshairAxes:
            mask = np.zeros((1, 3))
            mask[0, 'xyz'.index(axis)] = 1
            for iDir, dir in enumerate((-1, 1)):
                pts = dir*np.asarray([centerGapLength/2, lineLength])[:, np.newaxis] * mask + self._sliceOrigin
                lineKey = 'Crosshair_{}_{}_{}'.format(self.label, axis, iDir)
                if not self._plotterInitialized:
                    line = self._plotter.add_lines(pts, color='#11DD11', width=2, name=lineKey)
                    self._lineActors[lineKey] = line
                else:
                    logger.debug('Moving previous crosshairs')
                    line = self._lineActors[lineKey]
                    pts_pv = pv.lines_from_points(pts)
                    line.GetMapper().SetInputData(pts_pv)

        self._plotterInitialized = True


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
        for iRow, iCol, key in ((0, 0, 'x'), (0, 1, 'y'), (1, 0, 'z'), (1, 1, '3D')):
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
