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

from RTNaBS.util.Signaler import Signal
from RTNaBS.util.numpy import array_equalish


logger = logging.getLogger(__name__)


SurfMesh = pv.PolyData
VolMesh = pv.PolyData


@attrs.define()
class HeadModel:
    _filepath: tp.Optional[str] = None  # path to .msh file in simnibs folder
    # (note that .msh file and other nested files in same parent dir will be used)

    _skinSurf: tp.Optional[SurfMesh] = attrs.field(init=False, default=None)
    _gmSurf: tp.Optional[SurfMesh] = attrs.field(init=False, default=None)
    _eegPositions: tp.Optional[pd.DataFrame] = attrs.field(init=False, default=None)

    sigFilepathChanged: Signal = attrs.field(init=False, factory=Signal)
    sigDataChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str,)))  # emits key `which` indicating what changed, e.g. which='gmSurf'

    def __attrs_post_init__(self):
        self.sigFilepathChanged.connect(self._onFilepathChanged)
        self.validateFilepath(self._filepath)

    def loadCache(self, which: str):
        if not self.isSet:
            logger.warning('Load data requested, but no filepath set. Returning.')
            return

        parentDir = os.path.dirname(self.filepath)  # simnibs results dir
        subStr = os.path.splitext(os.path.basename(self.filepath))[0]  # e.g. 'sub-1234'
        m2mDir = os.path.join(parentDir, 'm2m_' + subStr)
        assert os.path.exists(m2mDir), 'm2m folder not found. Are full SimNIBS results available next to specified .msh file?'

        if which in ('skinSurf', 'gmSurf'):
            if which == 'gmSurf':
                meshPath = os.path.join(m2mDir, 'gm.stl')
            elif which == 'skinSurf':
                meshPath = os.path.join(m2mDir, 'skin.stl')
            else:
                raise NotImplementedError()

            logger.info('Loading {} mesh from {}'.format(which, meshPath))
            mesh = pv.read(meshPath)

            setattr(self, '_' + which, mesh)

        elif which == 'eegPositions':
            csvPath = os.path.join(m2mDir, 'eeg_positions', 'EEG10-10_UI_Jurak_2007.csv')
            columnLabels = ('type', 'x', 'y', 'z', 'label')
            logger.info('Loading EEG positions from {}'.format(csvPath))
            self._eegPositions = pd.read_csv(csvPath, names=columnLabels, index_col='label')
            assert self._eegPositions.shape[1] == len(columnLabels) - 1

        else:
            raise NotImplementedError()

        self.sigDataChanged.emit(which)

    def clearCache(self, which: str):

        if which == 'all':
            allKeys = ('skinSurf', 'gmSurf', 'eegPositions')  # TODO: add more keys here once implemented
            for w in allKeys:
                self.clearCache(which=w)
            return

        if which in ('skinSurf', 'gmSurf', 'eegPositions'):
            if getattr(self, '_' + which) is None:
                return
            setattr(self, '_' + which, None)
        else:
            raise NotImplementedError()

        self.sigDataChanged.emit(which)

    def _onFilepathChanged(self):
        with self.sigDataChanged.blocked():
            self.clearCache('all')
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
    def surfKeys(self):
        # TODO: set this dynamically instead of hardcoded
        # TODO: add others
        allSurfKeys = ('skinSurf', 'gmSurf')
        return allSurfKeys

    @property
    def gmSurf(self):
        if self.isSet and self._gmSurf is None:
            self.loadCache(which='gmSurf')
        return self._gmSurf

    @property
    def skinSurf(self):
        if self.isSet and self._skinSurf is None:
            self.loadCache(which='skinSurf')
        return self._skinSurf

    @property
    def eegPositions(self):
        if self.isSet and self._eegPositions is None:
            self.loadCache(which='eegPositions')
        return self._eegPositions

    def asDict(self, filepathRelTo: str) -> tp.Dict[str, tp.Any]:
        d = dict(
            filepath=self._filepath
        )
        if d['filepath'] is not None:
            d['filepath'] = os.path.relpath(d['filepath'], filepathRelTo)

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any], filepathRelTo: str) -> HeadModel:
        # TODO: validate against schema
        if 'filepath' in d:
            d['filepath'] = os.path.join(filepathRelTo, d['filepath'])
            cls.validateFilepath(d['filepath'])
        return cls(**d)

    @classmethod
    def validateFilepath(cls, filepath: tp.Optional[str]) -> None:
        if filepath is None:
            return
        assert filepath.endswith('.msh')
        assert os.path.exists(filepath), 'File not found at {}'.format(filepath)
        # TODO: also verify that expected related files (e.g. m2m_* folder) are next to the referenced .msh filepath