from __future__ import annotations

import shutil

import attrs
from datetime import datetime
import nibabel as nib
import json
import logging
import numpy as np
import os
import pandas as pd
import pyvista as pv
import tempfile
import typing as tp
from typing import ClassVar

from NaviNIBS.util.attrs import attrsAsDict
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.numpy import array_equalish


logger = logging.getLogger(__name__)


@attrs.define()
class MRI:
    _filepath: tp.Optional[str] = None

    _data: tp.Optional[nib.Nifti1Image] = attrs.field(init=False, default=None)
    _dataAsUniformGrid: tp.Optional[pv.ImageData] = attrs.field(init=False, default=None)

    sigFilepathChanged: Signal = attrs.field(init=False, factory=Signal)
    sigDataChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        self.sigFilepathChanged.connect(self._onFilepathChanged)
        self.validateFilepath(self._filepath)

    def loadCache(self):
        if not self.isSet:
            logger.warning('Load data requested, but no filepath set. Returning.')
            return

        logger.info('Loading image into cache from {}'.format(self.filepath))
        self._data = nib.load(self.filepath)

        if True:
            # create pyvista data object

            # get grid spacing from affine transform
            if np.all((self._data.affine[:-1, :-1] * (1-np.eye(3))) == 0):
                # for now, only on-diagonal transform supported
                gridSpacing = self._data.affine.diagonal()[:-1]
            else:
                raise NotImplementedError()

            if pv.__version__ <= '0.39.1':
                self._dataAsUniformGrid = pv.UniformGrid(
                    dims=self._data.shape)
            else:
                self._dataAsUniformGrid = pv.ImageData(
                    dimensions=self._data.shape,
                    spacing=gridSpacing,
                    origin=self._data.affine[:-1, 3]
                )

            self._dataAsUniformGrid.point_data['MRI'] = np.asanyarray(self.data.dataobj).ravel(order='F')

        self.sigDataChanged.emit()

    def clearCache(self):
        if self._data is None:
            return
        self._data = None
        self._dataAsUniformGrid = None

    def _onFilepathChanged(self):
        with self.sigDataChanged.blocked():
            self.clearCache()
        self.sigDataChanged.emit()

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, newPath: str):
        if self._filepath == newPath:
            return
        self.validateFilepath(newPath)
        self._filepath = newPath
        self.sigFilepathChanged.emit()
        # TODO: here or with slots connected to sigDataChanged, make sure any cached MRI data or metadata is cleared/reloaded

    @property
    def isSet(self):
        return self._filepath is not None

    @property
    def data(self):
        if self.isSet and self._data is None:
            # data was not previously loaded, but it is available. Load now.
            self.loadCache()
        return self._data

    @property
    def dataAsUniformGrid(self):
        if self.isSet and self._data is None:
            # data was not previously loaded, but it is available. Load now.
            self.loadCache()
        return self._dataAsUniformGrid

    def asDict(self, filepathRelTo: str) -> tp.Dict[str, tp.Any]:
        d = attrsAsDict(self)
        if 'filepath' in d:
            d['filepath'] = os.path.relpath(d['filepath'], filepathRelTo)

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any], filepathRelTo: str) -> MRI:
        # TODO: validate against schema
        if 'filepath' in d:
            d['filepath'] = os.path.join(filepathRelTo, d['filepath'])
            cls.validateFilepath(d['filepath'])
        return cls(**d)

    @classmethod
    def validateFilepath(cls, filepath: tp.Optional[str]) -> None:
        if filepath is None:
            return
        assert filepath.endswith('.nii') or filepath.endswith('.nii.gz')
        assert os.path.exists(filepath), 'File not found at {}'.format(filepath)

