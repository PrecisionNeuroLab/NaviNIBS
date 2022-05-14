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


logger = logging.getLogger(__name__)


@attrs.define()
class MRI:
    _filepath: tp.Optional[str] = None

    _data: tp.Optional[nib.Nifti1Image] = attrs.field(init=False, default=None)
    _dataAsUniformGrid: tp.Optional[pv.UniformGrid] = attrs.field(init=False, default=None)

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

            self._dataAsUniformGrid = pv.UniformGrid(
                dims=self._data.shape,
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
        self.sigDataChanged.emit()  # TODO: determine if necessary (since underlying uncached data didn't necessarily change)

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
        d = dict(
            filepath=self._filepath
        )
        if d['filepath'] is not None:
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


@attrs.define()
class MNIRegistration:
    pass


FiducialCoord = tp.Tuple[np.ndarray]
FiducialSet = tp.Dict[str, tp.Optional[FiducialCoord]]
Transform = np.ndarray


@attrs.define()
class SubjectRegistration:

    _plannedFiducials: FiducialSet = attrs.field(factory=dict)  # in MRI space
    _sampledFiducials: FiducialSet = attrs.field(factory=dict)  # in head tracker space
    _sampledHeadPoints: tp.Optional[np.ndarray] = attrs.field(default=None)
    _trackerToMRITransf: tp.Optional[Transform] = None

    _plannedFiducialsHistory: tp.Dict[str, FiducialSet] = attrs.field(factory=dict)
    _sampledFiducialsHistory: tp.Dict[str, FiducialSet] = attrs.field(factory=dict)
    _sampledHeadPointsHistory: tp.Dict[str, tp.Optional[np.ndarray]] = attrs.field(factory=dict)
    _trackerToMRITransfHistory: tp.Dict[str, tp.Optional[Transform]] = attrs.field(factory=dict)

    sigPlannedFiducialsChanged: Signal = attrs.field(init=False, factory=Signal)
    sigSampledFiducialsChanged: Signal = attrs.field(init=False, factory=Signal)
    sigSampledHeadPointsChanged: Signal = attrs.field(init=False, factory=Signal)
    sigTrackerToMRITransfChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):

        # convert types as needed (e.g. if coming from deserialized json)
        for whichSet in ('planned', 'sampled'):
            fiducials = getattr(self, '_' + whichSet + 'Fiducials')
            fiducialsHistory = getattr(self, '_' + whichSet + 'FiducialsHistory')
            for key in fiducials.keys():
                if isinstance(fiducials[key], list):
                    fiducials[key] = np.asarray(fiducials[key])
            for timeStr in fiducialsHistory.keys():
                for key in fiducialsHistory[timeStr].keys():
                    if isinstance(fiducialsHistory[timeStr][key], list):
                        fiducialsHistory[timeStr][key] = np.asarray(fiducialsHistory[timeStr][key])
        if isinstance(self._sampledHeadPoints, list):
            self._sampledHeadPoints = np.asarray(self._sampledHeadPoints)
        for timeStr in self._sampledHeadPointsHistory.keys():
            if isinstance(self._sampledHeadPointsHistory[timeStr], list):
                self._sampledHeadPointsHistory[timeStr] = np.asarray(self._sampledHeadPointsHistory[timeStr])
        if isinstance(self._trackerToMRITransf, list):
            self._trackerToMRITransf = np.asarray(self._trackerToMRITransf)
        for timeStr in self._trackerToMRITransfHistory.keys():
            if isinstance(self._trackerToMRITransfHistory[timeStr], list):
                self._trackerToMRITransfHistory[timeStr] = np.asarray(self._trackerToMRITransfHistory[timeStr])

        # make sure histories are up to date with current values
        for whichSet in ('planned', 'sampled'):
            fiducials = getattr(self, '_' + whichSet + 'Fiducials')
            fiducialsHistory = getattr(self, '_' + whichSet + 'FiducialsHistory')
            if len(fiducials) > 0 and (len(fiducialsHistory)==0 or list(fiducialsHistory.values())[-1] != fiducials):
                fiducialsHistory[self._getTimestampStr()] = fiducials.copy()
        if self._sampledHeadPoints is not None:
            if len(self._sampledHeadPointsHistory) == 0 or not np.array_equal(list(self._sampledHeadPointsHistory.values())[-1], self._sampledHeadPoints):
                self._sampledHeadPointsHistory[self._getTimestampStr()] = self._sampledHeadPoints.copy()
        if self._trackerToMRITransf is not None:
            if len(self._trackerToMRITransfHistory) == 0 or not np.array_equal(list(self._trackerToMRITransfHistory.values())[-1], self._trackerToMRITransf):
                self._trackerToMRITransfHistory[self._getTimestampStr()] = self._trackerToMRITransf.copy()

        # TODO: connect to sig*Changed signals to have some 'dirty' flag whenever planned or sampled fiducials or sampled headpoints are changed without updating latest trackerToMRITransf as well

    def getFiducial(self, whichSet: str, whichFiducial: str) -> tp.Optional[FiducialCoord]:
        if whichSet == 'planned':
            return self._plannedFiducials[whichFiducial]
        elif whichSet == 'sampled':
            return self._sampledFiducials[whichFiducial]
        else:
            raise NotImplementedError()

    def setFiducial(self, whichSet: str, whichFiducial: str, coord: tp.Optional[FiducialCoord]):
        try:
            prevCoord = self.getFiducial(whichSet=whichSet, whichFiducial=whichFiducial)
        except KeyError:
            hadCoord = False
        else:
            hadCoord = True
        if hadCoord and np.array_equal(prevCoord, coord):
            logger.debug('No change in {} {} coordinate, returning.'.format(whichSet, whichFiducial))
            return

        # TODO: do validation of coord

        fiducials = getattr(self, '_' + whichSet + 'Fiducials')

        logger.info('Set {} fiducial {} to {}'.format(whichSet, whichFiducial, coord))
        fiducials[whichFiducial] = coord

        # save new fiducials to history
        getattr(self, '_' + whichSet + 'FiducialsHistory')[self._getTimestampStr()] = fiducials.copy()

        getattr(self, 'sig' + whichSet.capitalize() + 'FiducialsChanged').emit()

    @property
    def plannedFiducials(self):
        return self._plannedFiducials  # note: result should not be modified

    @property
    def sampledFiducials(self):
        return self._sampledFiducials  # note: result should not be modified

    @property
    def trackerToMRITransf(self):
        return self._trackerToMRITransf

    @trackerToMRITransf.setter
    def trackerToMRITransf(self, newTransf: tp.Optional[Transform]):
        if np.array_equal(self._trackerToMRITransf, newTransf):
            logger.debug('No change in trackerToMRITransf, returning')
            return

        # TODO: do validation of newTransf

        self._trackerToMRITransfHistory[self._getTimestampStr()] = None if self._trackerToMRITransf is None else self._trackerToMRITransf.copy()

        logger.info('Set trackerToMRITransf to {}'.format(newTransf))
        self._trackerToMRITransf = newTransf
        self.sigTrackerToMRITransfChanged.emit()

    def asDict(self) -> tp.Dict[str, tp.Any]:
        d = dict()

        def exportFiducialSet(fiducials: FiducialSet) -> tp.Dict[str, tp.Optional[tp.List[float, float, float]]]:
            d = dict()
            for key, val in fiducials.items():
                if val is None:
                    d[key] = val
                else:
                    assert isinstance(val, np.ndarray)
                    d[key] = val.tolist()
            return d

        d['plannedFiducials'] = exportFiducialSet(self._plannedFiducials)
        d['sampledFiducials'] = exportFiducialSet(self._sampledFiducials)
        if self._sampledHeadPoints is not None:
            d['sampledHeadPoints'] = self._sampledHeadPoints.tolist()
        if self._trackerToMRITransf is not None:
            d['trackerToMRITransf'] = self._trackerToMRITransf.tolist()

        d['plannedFiducialsHistory'] = [dict(time=key, fiducials=exportFiducialSet(val)) for key, val in self._plannedFiducialsHistory.items()]
        d['sampledFiducialsHistory'] = [dict(time=key, fiducials=exportFiducialSet(val)) for key, val in self._sampledFiducialsHistory.items()]

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]) -> SubjectRegistration:
        # TODO: validate against schema
        return cls(**d)  # rely on constructor to do any necessary type conversions

    @staticmethod
    def _getTimestampStr():
        return datetime.today().strftime('%y%m%d%H%M%S.%f')



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

@attrs.define()
class Target:
    pass


@attrs.define()
class Session:
    _filepath: str  # path to compressed session file
    _subjectID: tp.Optional[str] = attrs.field(default=None)
    _sessionID: tp.Optional[str] = None
    _MRI: MRI = attrs.field(factory=MRI)
    _headModel: HeadModel = attrs.field(factory=HeadModel)
    _subjectRegistration: SubjectRegistration = attrs.field(factory=SubjectRegistration)
    MNIRegistration: tp.Optional[MNIRegistration] = None
    targets: tp.Dict[str, Target] = None

    _dirtyKeys: tp.Set[str] = attrs.field(init=False, factory=set)
    _compressedFileIsDirty: bool = True

    _sessionConfigFilename: ClassVar[str] = 'SessionConfig.json'
    _latestConfigFormatVersion: ClassVar[str] = '0.0.1'
    _unpackedSessionDir: tp.Optional[tp.Union[tempfile.TemporaryDirectory, str]] = attrs.field(default=None)

    sigInfoChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        if self._unpackedSessionDir is None:
            self._unpackedSessionDir = self.getTempUnpackDir()

        if not os.path.isdir(self.unpackedSessionDir):
            logger.debug('Creating dir for unpacking session at {}'.format(self.unpackedSessionDir))
            os.makedirs(self.unpackedSessionDir)

        self.sigInfoChanged.connect(lambda: self._dirtyKeys.add('info'))
        self.MRI.sigFilepathChanged.connect(lambda: self._dirtyKeys.add('MRI'))
        self.headModel.sigFilepathChanged.connect(lambda: self._dirtyKeys.add('headModel'))
        self.subjectRegistration.sigPlannedFiducialsChanged.connect(lambda: self._dirtyKeys.add('subjectRegistration'))
        self.subjectRegistration.sigSampledFiducialsChanged.connect(lambda: self._dirtyKeys.add('subjectRegistration'))
        self.subjectRegistration.sigSampledHeadPointsChanged.connect(lambda: self._dirtyKeys.add('subjectRegistration'))
        self.subjectRegistration.sigTrackerToMRITransfChanged.connect(lambda: self._dirtyKeys.add('subjectRegistration'))

        # TODO

    @property
    def subjectID(self):
        return self._subjectID

    @subjectID.setter
    def subjectID(self, newVal: str):
        if newVal != self._subjectID:
            self._subjectID = newVal
            self.sigInfoChanged.emit()

    @property
    def sessionID(self):
        return self._sessionID

    @sessionID.setter
    def sessionID(self, newVal: str):
        if newVal != self._sessionID:
            self._sessionID = newVal
            self.sigInfoChanged.emit()

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, newVal: str):
        if newVal != self._filepath:
            self._filepath = newVal
            self.sigInfoChanged.emit()

    @property
    def MRI(self):
        return self._MRI

    @property
    def headModel(self):
        return self._headModel

    @property
    def subjectRegistration(self):
        return self._subjectRegistration

    @property
    def compressedFileIsDirty(self):
        return len(self._dirtyKeys) > 0 or self._compressedFileIsDirty

    @property
    def unpackedSessionDir(self):
        if isinstance(self._unpackedSessionDir, tempfile.TemporaryDirectory):
            return self._unpackedSessionDir.name
        else:
            return self._unpackedSessionDir

    def saveToUnpackedDir(self, saveDirtyOnly: bool = True):

        keysToSave = self._dirtyKeys.copy()

        if not saveDirtyOnly:
            keysToSave.update(['info'])

        if len(keysToSave) == 0:
            logger.debug('Nothing to save')
            return

        self._compressedFileIsDirty = True

        configPath = os.path.join(self.unpackedSessionDir, self._sessionConfigFilename)
        if os.path.exists(configPath):
            with open(configPath, 'r') as f:
                config = json.load(f)
            assert config['formatVersion'] == self._latestConfigFormatVersion
        else:
            config = dict(formatVersion=self._latestConfigFormatVersion)

        if 'info' in keysToSave:
            logger.debug('Writing session info')
            infoFields = ('filepath', 'subjectID', 'sessionID')
            for field in infoFields:
                config[field] = getattr(self, field)
            keysToSave.remove('info')

        otherPathsRelTo = os.path.dirname(self.filepath)

        if 'MRI' in keysToSave:
            # save MRI path relative to location of compressed file
            logger.debug('Writing MRI info')
            config['MRI'] = self.MRI.asDict(filepathRelTo=otherPathsRelTo)
            keysToSave.remove('MRI')

        if 'headModel' in keysToSave:
            # save head model path relative to location of compressed file
            logger.debug('Writing headModel info')
            config['headModel'] = self.headModel.asDict(filepathRelTo=otherPathsRelTo)
            keysToSave.remove('headModel')

        if 'subjectRegistration' in keysToSave:
            logger.debug('Writing subjectRegistration info')
            config['subjectRegistration'] = self.subjectRegistration.asDict()
            # TODO: save contents of potentially larger *History fields to separate file(s)
            keysToSave.remove('subjectRegistration')

        # TODO: save other fields
        assert len(keysToSave) == 0

        with open(configPath, 'w') as f:
            json.dump(config, f)
            logger.debug('Wrote updated session config')

        self._dirtyKeys.clear()

    def saveToFile(self):
        self.saveToUnpackedDir()
        if not self._compressedFileIsDirty:
            logger.warning('Nothing to save')
            return

        logger.debug('Making archive')
        shutil.make_archive(
            base_name=self._filepath,
            format='zip',
            root_dir=self.unpackedSessionDir,
            base_dir='.',
        )
        shutil.move(self._filepath + '.zip', self._filepath)
        logger.debug('Done saving')

        self._compressedFileIsDirty = False

    @classmethod
    def createNew(cls, filepath: str, unpackedSessionDir: tp.Optional[str] = None):
        self = cls(filepath=filepath, unpackedSessionDir=unpackedSessionDir)
        self.saveToUnpackedDir(saveDirtyOnly=False)
        return self

    @classmethod
    def loadFromFile(cls, filepath: str, unpackedSessionDir: tp.Optional[str] = None):
        if unpackedSessionDir is None:
            unpackedSessionDir = cls.getTempUnpackDir()
        logger.debug('Unpacking archive from {}\nto {}'.format(filepath, unpackedSessionDir))
        assert os.path.exists(filepath)
        shutil.unpack_archive(filepath, unpackedSessionDir, 'zip')
        logger.debug('Done unpacking')
        return cls.loadFromUnpackedDir(unpackedSessionDir=unpackedSessionDir, filepath=filepath,
                                       compressedFileIsDirty=False)

    @classmethod
    def loadFromUnpackedDir(cls, unpackedSessionDir: str, filepath: tp.Optional[str] = None, **kwargs):
        configPath = os.path.join(unpackedSessionDir, cls._sessionConfigFilename)
        with open(configPath, 'r') as f:
            config = json.load(f)
        # TODO: validate against schema
        assert config['formatVersion'] == cls._latestConfigFormatVersion

        kwargs['unpackedSessionDir'] = unpackedSessionDir
        kwargs['filepath'] = filepath if filepath is not None else config['filepath']
        for key in ('subjectID', 'sessionID'):
            kwargs[key] = config[key]

        otherPathsRelTo = os.path.dirname(kwargs['filepath'])

        if 'MRI' in config:
            kwargs['MRI'] = MRI.fromDict(config['MRI'], filepathRelTo=otherPathsRelTo)

        if 'headModel' in config:
            kwargs['headModel'] = HeadModel.fromDict(config['headModel'], filepathRelTo=otherPathsRelTo)

        if 'subjectRegistration' in config:
            kwargs['subjectRegistration'] = SubjectRegistration.fromDict(config['subjectRegistration'])

        # TODO: load other available fields

        logger.debug('Loaded from unpacked dir:\n{}'.format(kwargs))

        return cls(**kwargs)

    @classmethod
    def getTempUnpackDir(cls):
        return tempfile.TemporaryDirectory(prefix='RTNaBSSession_').name
