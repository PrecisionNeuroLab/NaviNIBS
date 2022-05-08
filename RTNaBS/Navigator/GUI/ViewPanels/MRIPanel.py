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
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class MRIPanel(MainViewPanel):
    _filepathWdgt: QFileSelectWidget = attrs.field(init=False)
    _plotters: tp.Dict[str, pvqt.QtInteractor] = attrs.field(init=False, factory=dict)
    _plottersInitialized: bool = attrs.field(init=False, default=False)
    _lineActors: tp.Dict[str, pv.Line] = attrs.field(init=False, factory=dict)
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
            self._plotters[key].set_background(color='#000000')
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
        logger.debug('Slice point changed: {} {}'.format(key, pos))
        if True:
            # ignore any out-of-plane offset that can be caused by slice rendering differences
            pos['xyz'.index(key)] = self._sliceOrigin['xyz'.index(key)]
        self._sliceOrigin = pos
        self._updateImagePreview()

    def _onSliceScrolled(self, key, change: int):
        logger.debug('Slice scrolled: {} {}'.format(key, change))
        offset = np.zeros((3,))
        offset['xyz'.index(key)] = change
        pos = self._sliceOrigin + offset
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

        def _onMouseEvent(obj, event, axisKey: str):
            if event == 'MouseWheelForwardEvent':
                logger.debug('MouseWheelForwardEvent')
                self._onSliceScrolled(key=axisKey, change=1)

            elif event == 'MouseWheelBackwardEvent':
                logger.debug('MouseWheelBackwardEvent')
                self._onSliceScrolled(key=axisKey, change=-1)

        for iKey, key in enumerate(('x', 'y', 'z')):
            logger.debug('Updating plot for {} slice'.format(key))
            if not self._plottersInitialized:
                self._plotters[key].enable_parallel_projection()
                self._plotters[key].enable_point_picking(left_clicking=True,
                                                         show_message=False,
                                                         show_point=False,
                                                         callback=lambda newPt, key=key: self._onSlicePointChanged(
                                                             key))
                self._plotters[key].enable_image_style()
                for event in ('MouseWheelForwardEvent', 'MouseWheelBackwardEvent'):
                    self._plotters[key].iren._style_class.AddObserver(event, lambda obj, event, axisKey=key: _onMouseEvent(obj, event, axisKey=axisKey))

            if False:
                slice = self.session.MRI.dataAsUniformGrid.slice(normal=key, origin=self._sliceOrigin)
                self._plotters[key].add_mesh(slice,
                                             name='slice',
                                             cmap='gray')
                self._plotters[key].camera_position = 'xyz'.replace(key, '')
            else:
                # volume plotting with camera clipping
                if not self._plottersInitialized:
                    vol = self.session.MRI.dataAsUniformGrid
                    self._plotters[key].add_volume(vol,
                                                   scalars='MRI',
                                                   name='MRI',
                                                   mapper='gpu',
                                                   clim=[300, 2000],
                                                   cmap='gray')

        if not self._plottersInitialized:
            logger.debug('Initializing 3D plot')
            self._plotters['3D'].add_volume(self.session.MRI.dataAsUniformGrid.gaussian_smooth(),
                                     scalars='MRI',
                                     name='vol',
                                     clim=[300, 1000],
                                     cmap='gray',
                                     mapper='gpu',
                                     opacity=[0, 1, 1],
                                     shade=False)

        for key in ('x', 'y', 'z', '3D'):
            logger.debug('Setting crosshairs for {} plot'.format(key))
            lineLength = 300  # TODO: scale by image size
            if key == '3D':
                crosshairAxes = 'xyz'
                centerGapLength = 0
            else:
                crosshairAxes = 'xyz'.replace(key, '')
                centerGapLength = 10  # TODO: scale by image size
            for axis in crosshairAxes:
                mask = np.zeros((1, 3))
                mask[0, 'xyz'.index(axis)] = 1
                for iDir, dir in enumerate((-1, 1)):
                    pts = dir*np.asarray([centerGapLength/2, lineLength])[:, np.newaxis] * mask + self._sliceOrigin
                    lineKey = 'Crosshair_{}_{}_{}'.format(key, axis, iDir)
                    if not self._plottersInitialized:
                        line = self._plotters[key].add_lines(pts, color='#11DD11', width=2, name=lineKey)
                        self._lineActors[lineKey] = line
                    else:
                        logger.debug('Moving previous crosshairs')
                        line = self._lineActors[lineKey]
                        pts_pv = pv.lines_from_points(pts)
                        line.GetMapper().SetInputData(pts_pv)

            if key != '3D':
                offsetDir = np.zeros((3,))
                offsetDir['xyz'.index(key)] = 1
                if True:
                    self._plotters[key].camera.position = offsetDir * 100 + self._sliceOrigin
                else:
                    # hack to prevent resetting clipping range due to pyvista implementation quirk
                    tmp = self._plotters[key].camera._renderer
                    self._plotters[key].camera._renderer = None
                    self._plotters[key].camera.position = offsetDir * 100 + self._sliceOrigin
                    self._plotters[key].camera._renderer = tmp

                self._plotters[key].camera.focal_point = self._sliceOrigin
                self._plotters[key].camera.up = np.roll(offsetDir, (2, 1, 2)['xyz'.index(key)])
                self._plotters[key].camera.clipping_range = (99, 102)
                self._plotters[key].camera.parallel_scale = 90

        self._plottersInitialized = True

