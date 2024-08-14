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
from NaviNIBS.util.Transforms import invertTransform
from NaviNIBS.util.numpy import array_equalish


logger = logging.getLogger(__name__)


@attrs.define()
class MRI:
    _filepath: tp.Optional[str] = None

    _clim2DMin: float | None = None
    _clim2DMax: float | None = None
    _clim3DMin: float | None = None
    _clim3DMax: float | None = None

    _data: tp.Optional[nib.Nifti1Image] = attrs.field(init=False, default=None)
    _dataAsUniformGrid: tp.Optional[pv.ImageData] = attrs.field(init=False, default=None)
    _inverseAffine: np.ndarray | None = attrs.field(init=False, default=None)
    _autoClim2DMin: float | None = attrs.field(init=False, default=None)
    _autoClim2DMax: float | None = attrs.field(init=False, default=None)
    _autoClim3DMin: float | None = attrs.field(init=False, default=None)
    _autoClim3DMax: float | None = attrs.field(init=False, default=None)

    sigFilepathChanged: Signal = attrs.field(init=False, factory=Signal)
    sigDataChanged: Signal = attrs.field(init=False, factory=Signal)
    sigManualClimChanged: Signal[str] = attrs.field(init=False, factory=Signal)
    """
    Includes label of which clim changed ('2D' or '3D')
    """
    sigClimChanged: Signal[str] = attrs.field(init=False, factory=Signal)
    """
    Includes label of which clim changed ('2D' or '3D')
    """

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

            if pv.__version__ <= '0.39.1':
                self._dataAsUniformGrid = pv.UniformGrid(
                    dims=self._data.shape)
            else:
                self._dataAsUniformGrid = pv.ImageData(
                    dimensions=self._data.shape)

            self._dataAsUniformGrid.point_data['MRI'] = np.asanyarray(self.data.dataobj).ravel(order='F')

        if True:
            # cache inverse of affine transform
            self._inverseAffine = invertTransform(self._data.affine)

        # clear cached auto clim values
        for dim in ('2D', '3D'):
            for minOrMax in ('Min', 'Max'):
                propKey = f'_autoClim{dim}{minOrMax}'
                setattr(self, propKey, None)

        self.sigDataChanged.emit()

        if self._clim2DMin is None or self._clim2DMax is None:
            self.sigClimChanged.emit('2D')

        if self._clim3DMin is None or self._clim3DMax is None:
            self.sigClimChanged.emit('3D')

    def clearCache(self):
        if self._data is None:
            return
        self._data = None
        self._dataAsUniformGrid = None
        self._inverseAffine = None
        # clear cached auto clim values
        for dim in ('2D', '3D'):
            for minOrMax in ('Min', 'Max'):
                propKey = f'_autoClim{dim}{minOrMax}'
                setattr(self, propKey, None)

    def _onFilepathChanged(self):
        with self.sigDataChanged.blocked():
            self.clearCache()
        self.sigDataChanged.emit()

        if self._clim2DMin is None or self._clim2DMax is None:
            self.sigClimChanged.emit('2D')

        if self._clim3DMin is None or self._clim3DMax is None:
            self.sigClimChanged.emit('3D')

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

    def loadCacheIfNeeded(self):
        if self.isSet and self._data is None:
            # data was not previously loaded, but it is available. Load now.
            self.loadCache()

    @property
    def data(self):
        self.loadCacheIfNeeded()
        return self._data

    @property
    def dataAsUniformGrid(self):
        """
        Note: this data is in the original image space (coordinate indices), without any transformations applied.
        """
        self.loadCacheIfNeeded()
        return self._dataAsUniformGrid

    @property
    def dataToScannerTransf(self) -> np.ndarray | None:
        """
        Transform from image array (coordinate indices) to native MRI space.

        See https://nipy.org/nibabel/coordinate_systems.html for more info.
        """
        self.loadCacheIfNeeded()
        if self._data is None:
            return None
        else:
            return self._data.affine

    @property
    def scannerToDataTransf(self) -> np.ndarray | None:
        """
        Inverse of ``dataToScannerTransf``
        """
        self.loadCacheIfNeeded()
        return self._inverseAffine

    @property
    def clim2DMin(self):
        """
        Manually-set minimum of color axis for 2D plots, or None to autoset.
        """
        return self._clim2DMin

    @clim2DMin.setter
    def clim2DMin(self, newVal: float | None):
        if self._clim2DMin == newVal:
            return
        logger.info(f'2D clim min changing from {self._clim2DMin} to {newVal}')
        self._clim2DMin = newVal
        self.sigClimChanged.emit('2D')
        self.sigManualClimChanged.emit('2D')

    @property
    def clim2DMax(self):
        """
        Manually-set maximum of color axis for 2D plots, or None to autoset.
        """
        return self._clim2DMax

    @clim2DMax.setter
    def clim2DMax(self, newVal: float | None):
        if self._clim2DMax == newVal:
            return
        logger.info(f'2D clim max changing from {self._clim2DMax} to {newVal}')
        self._clim2DMax = newVal
        self.sigClimChanged.emit('2D')
        self.sigManualClimChanged.emit('2D')

    @property
    def clim2D(self) -> tuple[float | None, float | None]:
        """
        Merged manually-set and auto-set clims depending on what is available.

        Only returns tuple of Nones if data is not available.
        """
        return (
            self.clim2DMin if self.clim2DMin is not None else self.autoClim2DMin,
            self.clim2DMax if self.clim2DMax is not None else self.autoClim2DMax
        )

    @property
    def clim3DMin(self):
        """
        Manually-set minimum of color axis for 3D plots, or None to autoset.
        """

        return self._clim3DMin

    @clim3DMin.setter
    def clim3DMin(self, newVal: float | None):
        if self._clim3DMin == newVal:
            return
        logger.info(f'3D clim min changing from {self._clim3DMin} to {newVal}')
        self._clim3DMin = newVal
        self.sigClimChanged.emit('3D')
        self.sigManualClimChanged.emit('3D')

    @property
    def clim3DMax(self):
        """
        Manually-set maximum of color axis for 3D plots, or None to autoset.
        """

        return self._clim3DMax

    @clim3DMax.setter
    def clim3DMax(self, newVal: float | None):
        if self._clim3DMax == newVal:
            return
        logger.info(f'3D clim max changing from {self._clim3DMax} to {newVal}')
        self._clim3DMax = newVal
        self.sigClimChanged.emit('3D')
        self.sigManualClimChanged.emit('3D')

    @property
    def clim3D(self):
        """
        Merged manually-set and auto-set clims depending on what is available.

        Only returns tuple of Nones if data is not available.
        """
        return (
            self.clim3DMin if self.clim3DMin is not None else self.autoClim3DMin,
            self.clim3DMax if self.clim3DMax is not None else self.autoClim3DMax
        )

    def _calculateAutoClim(self, dim: str, minOrMax: str):
        propKey = f'_autoClim{dim}{minOrMax}'

        if self._data is None:
            raise ValueError('No data loaded, cannot calculate auto clim')
        else:
            data = self._data.get_fdata()

            lowerThreshold = np.percentile(data, 90) / 10
            #logger.debug(f'Lower threshold for auto clim calculation: {lowerThreshold}')

            if minOrMax == 'Min':
                if dim == '2D':
                    pct = 1
                else:
                    pct = 20
            else:
                if dim == '2D':
                    pct = 95
                else:
                    pct = 80

            val = np.nanpercentile(np.where(data > lowerThreshold, data, np.nan), pct)

            logger.debug(f'Calculated auto clim {dim}{minOrMax}: {val}')

            setattr(self, propKey, val)

            self.sigClimChanged.emit(dim)

    def _getAutoClim(self, dim: str, minOrMax: str) -> float | None:
        self.loadCacheIfNeeded()
        if self._data is None:
            return None
        else:
            propKey = f'_autoClim{dim}{minOrMax}'
            if getattr(self, propKey) is None:
                self._calculateAutoClim(dim=dim, minOrMax=minOrMax)
            return getattr(self, propKey)

    @property
    def autoClim2DMin(self):
        return self._getAutoClim(dim='2D', minOrMax='Min')

    @property
    def autoClim2DMax(self):
        return self._getAutoClim(dim='2D', minOrMax='Max')

    @property
    def autoClim3DMin(self):
        return self._getAutoClim(dim='3D', minOrMax='Min')

    @property
    def autoClim3DMax(self):
        return self._getAutoClim(dim='3D', minOrMax='Max')

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

