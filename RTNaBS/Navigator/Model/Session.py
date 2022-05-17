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


FiducialCoord = np.ndarray
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

        # make sure histories are up to date with current values
        for whichSet in ('planned', 'sampled'):
            fiducials = getattr(self, '_' + whichSet + 'Fiducials')
            fiducialsHistory = getattr(self, '_' + whichSet + 'FiducialsHistory')
            if len(fiducials) > 0 and (len(fiducialsHistory) == 0 or not self.fiducialsEqual(list(fiducialsHistory.values())[-1],  fiducials)):
                fiducialsHistory[self._getTimestampStr()] = fiducials.copy()
        if self._sampledHeadPoints is not None:
            if len(self._sampledHeadPointsHistory) == 0 or not array_equalish(list(self._sampledHeadPointsHistory.values())[-1], self._sampledHeadPoints):
                self._sampledHeadPointsHistory[self._getTimestampStr()] = self._sampledHeadPoints.copy()
        if self._trackerToMRITransf is not None:
            if len(self._trackerToMRITransfHistory) == 0 or not array_equalish(list(self._trackerToMRITransfHistory.values())[-1], self._trackerToMRITransf):
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
        if hadCoord and array_equalish(prevCoord, coord):
            logger.debug('No change in {} {} coordinate, returning.'.format(whichSet, whichFiducial))
            return

        # TODO: do validation of coord

        fiducials = getattr(self, '_' + whichSet + 'Fiducials')

        logger.info('Set {} fiducial {} to {}'.format(whichSet, whichFiducial, coord))
        fiducials[whichFiducial] = coord

        # save new fiducials to history
        getattr(self, '_' + whichSet + 'FiducialsHistory')[self._getTimestampStr()] = fiducials.copy()

        getattr(self, 'sig' + whichSet.capitalize() + 'FiducialsChanged').emit()

    def deleteFiducial(self, whichSet: str, whichFiducial: str):
        fiducials = getattr(self, '_' + whichSet + 'Fiducials')
        logger.info('Delete {} fiducial {}'.format(whichSet, whichFiducial))
        del fiducials[whichFiducial]
        getattr(self, 'sig' + whichSet.capitalize() + 'FiducialsChanged').emit()

    @property
    def plannedFiducials(self):
        return self._plannedFiducials  # note: result should not be modified, should instead call setter

    @plannedFiducials.setter
    def plannedFiducials(self, newFiducials: FiducialSet):
        # TODO: do input validation

        if self.fiducialsEqual(self._plannedFiducials, newFiducials):
            logger.debug('No change in plannedFiducials, returning')
            return

        self._plannedFiducials = newFiducials
        self.sigPlannedFiducialsChanged.emit()

    @property
    def sampledFiducials(self):
        return self._sampledFiducials  # note: result should not be modified, should instead call setter

    @property
    def trackerToMRITransf(self):
        return self._trackerToMRITransf

    @trackerToMRITransf.setter
    def trackerToMRITransf(self, newTransf: tp.Optional[Transform]):
        if array_equalish(self._trackerToMRITransf, newTransf):
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

        d['plannedFiducialsHistory'] = [dict(time=key, fiducials=exportFiducialSet(val)) for key, val in self._plannedFiducialsHistory.items()]
        d['sampledFiducialsHistory'] = [dict(time=key, fiducials=exportFiducialSet(val)) for key, val in self._sampledFiducialsHistory.items()]

        if self._sampledHeadPoints is not None:
            d['sampledHeadPoints'] = self._sampledHeadPoints.tolist()
        if len(self._sampledHeadPointsHistory) > 0:
            d['sampledHeadPointsHistory'] = [dict(time=key, sampledHeadPoints=val.tolist()) for key, val in self._sampledHeadPointsHistory.items()]

        if self._trackerToMRITransf is not None:
            d['trackerToMRITransf'] = self._trackerToMRITransf.tolist()
        if len(self._trackerToMRITransfHistory) > 0:
            d['trackerToMRITransfHistory'] = [dict(time=key, trackerToMRITransf=val.tolist()) for key, val in self._trackerToMRITransfHistory.items()]

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]) -> SubjectRegistration:
        # TODO: validate against schema

        # note: input dict is modified for arg conversion below

        # convert types as needed (e.g. if coming from deserialized json)

        def convertFiducials(fiducials: tp.Dict[str, tp.Any]) -> tp.Dict[str, tp.Any]:
            for key in fiducials.keys():
                if isinstance(fiducials[key], list):
                    fiducials[key] = np.asarray(fiducials[key])
            return fiducials

        def convertHeadpoints(headPts: tp.List[tp.List[float, float, float]]) -> np.ndarray:
            return np.asarray(headPts)

        def convertTransf(transf: tp.List[tp.List[float, float, float]]) -> np.ndarray:
            return np.asarray(transf)

        def convertHistoryListToDict(historyList: tp.List[tp.Dict[str, tp.Any]], field: str, entryConverter: tp.Callable) -> tp.Dict[str, tp.Any]:
            historyDict = {}
            for entry in historyList:
                timeStr = entry['time']
                historyDict[timeStr] = entryConverter(entry[field])
            return historyDict

        for whichSet in ('planned', 'sampled'):
            d[whichSet + 'Fiducials'] = convertFiducials(d[whichSet + 'Fiducials'])

            d[whichSet + 'FiducialsHistory'] = convertHistoryListToDict(d[whichSet + 'FiducialsHistory'],
                                                                        field='fiducials',
                                                                        entryConverter=convertFiducials)

        if 'sampledHeadPoints' in d:
            d['sampledHeadPoints'] = convertHeadpoints(d['sampledHeadPoints'])

        if 'sampledHeadPointsHistory' in d:
            d['sampledHeadPointsHistory'] = convertHistoryListToDict(d['sampledHeadPointsHistory'],
                                                                     field='sampledHeadPoints',
                                                                     entryConverter=convertHeadpoints)

        if 'trackerToMRITransf' in d:
            d['trackerToMRITransf'] = convertTransf(d['trackerToMRITransf'])

        if 'trackerToMRITransfHistory' in d:
            d['trackerToMRITransfHistory'] = convertHistoryListToDict(d['trackerToMRITransfHistory'],
                                                                      field='trackerToMRITransf',
                                                                      entryConverter=convertTransf)

        return cls(**d)

    @staticmethod
    def _getTimestampStr():
        return datetime.today().strftime('%y%m%d%H%M%S.%f')

    @staticmethod
    def fiducialsEqual(fidsA: FiducialSet, fidsB: FiducialSet) -> bool:
        if len(fidsA) != len(fidsB):
            return False

        if list(fidsA.keys()) != list(fidsB.keys()):
            # order matters for this comparison
            return False

        for key in fidsA.keys():
            if not array_equalish(fidsA[key], fidsB[key]):
                return False

        return True

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
    """
    Can specify (targetCoord, entryCoord, angle, [depthOffset]) to autogenerate coilToMRITransf,
    or (coilToMRITransf, [targetCoord]) to use transform directly.
    """
    _key: str
    _targetCoord: tp.Optional[np.ndarray] = None
    _entryCoord: tp.Optional[np.ndarray] = None
    _angle: tp.Optional[float] = None  # typical coil handle angle, in coil's horizontal plane
    _depthOffset: tp.Optional[float] = None  # offset beyond entryCoord, e.g. due to EEG electrode thickness, coil foam
    _coilToMRITransf: tp.Optional[np.ndarray] = None

    _cachedCoilToMRITransf: tp.Optional[np.ndarray] = attrs.field(init=False, default=None)

    sigTargetAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((str,)))  # includes key
    sigTargetChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str,)))  # includes key

    @property
    def key(self):
        return self._key

    @property
    def targetCoord(self):
        return self._targetCoord

    @property
    def entryCoord(self):
        return self._entryCoord

    @property
    def angle(self):
        return self._angle if self._angle is not None else 0

    @property
    def depthOffset(self):
        return self._depthOffset if self._depthOffset is not None else 0

    @property
    def coilToMRITransf(self):
        if self._coilToMRITransf is not None:
            return self._coilToMRITransf
        else:
            if self._cachedCoilToMRITransf is None:
                raise NotImplementedError()  # TODO: generate transform from target, entry, angle, offset
                self._cachedCoilToMRITransf = 'todo'
            return self._cachedCoilToMRITransf

    def asDict(self) -> tp.Dict[str, tp.Any]:
        def convertOptionalNDArray(val: tp.Optional[np.ndarray]) -> tp.Optional[tp.List[tp.Any]]:
            if val is None:
                return None
            else:
                # noinspection PyTypeChecker
                return val.tolist()

        d = dict(
            key=self._key,
            targetCoord=convertOptionalNDArray(self._targetCoord),
            entryCoord=convertOptionalNDArray(self._entryCoord),
            angle=self._angle,
            depthOffset=self._depthOffset,
            coilToMRITransf=convertOptionalNDArray(self._coilToMRITransf)
        )
        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]):

        def convertOptionalNDArray(val: tp.Optional[tp.List[tp.Any]]) -> tp.Optional[np.ndarray]:
            if val is None:
                return None
            else:
                return np.asarray(val)

        for attrKey in ('targetCoord', 'entryCoord', 'coilToMRITransf'):
            if attrKey in d:
                d[attrKey] = convertOptionalNDArray(d[attrKey])

        return cls(**d)


@attrs.define()
class Targets:
    _targets: tp.Dict[str, Target] = attrs.field(factory=dict)

    sigTargetsAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((tp.List[str],)))  # includes list of keys of targets about to change
    sigTargetsChanged: Signal = attrs.field(init=False, factory=lambda: Signal((tp.List[str],)))  # includes list of keys of changed targets

    def __attrs_post_init__(self):
        for key, target in self._targets.items():
            assert target.key == key
            target.sigTargetAboutToChange.connect(self._onTargetAboutToChange)
            target.sigTargetChanged.connect(self._onTargetChanged)

    def addTarget(self, target: Target):
        assert target.key not in self._targets
        return self.setTarget(target=target)

    def deleteTarget(self, key: str):
        raise NotImplementedError()  # TODO

    def setTarget(self, target: Target):
        self.sigTargetsAboutToChange.emit([target.key])
        if target.key in self._targets:
            self._targets[target.key].sigTargetAboutToChange.disconnect(self._onTargetAboutToChange)
            self._targets[target.key].sigTargetChanged.disconnect(self._onTargetChanged)
        self._targets[target.key] = target

        target.sigTargetAboutToChange.connect(self._onTargetAboutToChange)
        target.sigTargetChanged.connect(self._onTargetChanged)

        self.sigTargetsChanged.emit([target.key])

    def setTargets(self, targets: tp.List[Target]):
        # assume all keys are changing, though we could do comparisons to find subset changed
        oldKeys = list(self.targets.keys())
        newKeys = [target.key for target in targets]
        combinedKeys = list(set(oldKeys) | set(newKeys))
        self.sigTargetsAboutToChange.emit(combinedKeys)
        for key in oldKeys:
            self._targets[key].sigTargetAboutToChange.disconnect(self._onTargetAboutToChange)
            self._targets[key].sigTargetChanged.disconnect(self._onTargetChanged)
        self._targets = {target.key: target for target in targets}
        for key, target in self._targets.items():
            self._targets[key].sigTargetAboutToChange.connect(self._onTargetAboutToChange)
            self._targets[key].sigTargetChanged.connect(self._onTargetChanged)
        self.sigTargetsChanged.emit(combinedKeys)

    def _onTargetAboutToChange(self, key: str):
        self.sigTargetsAboutToChange.emit([key])

    def _onTargetChanged(self, key: str):
        self.sigTargetsChanged.emit([key])

    def __getitem__(self, key):
        return self._targets[key]

    def __setitem__(self, key, target: Target):
        assert key == target.key
        self.setTarget(target=target)

    def __iter__(self):
        return iter(self._targets)

    def __len__(self):
        return len(self._targets)

    def keys(self):
        return self._targets.keys()

    def items(self):
        return self._targets.items()

    def values(self):
        return self._targets.values()

    def merge(self, otherTargets: Targets):

        self.sigTargetsAboutToChange.emit(list(otherTargets.keys()))

        with self.sigTargetsAboutToChange.blocked(), self.sigTargetsChanged.blocked():
            for target in otherTargets.values():
                self.setTarget(target)

        self.sigTargetsChanged.emit(list(otherTargets.keys()))

    @property
    def targets(self):
        return self._targets  # note: result should not be modified directly

    def asList(self) -> tp.List[tp.Dict[str, tp.Any]]:
        return [target.asDict() for target in self._targets.values()]

    @classmethod
    def fromList(cls, targetList: tp.List[tp.Dict[str, tp.Any]]) -> Targets:

        targets = {}
        for targetDict in targetList:
            targets[targetDict['key']] = Target.fromDict(targetDict)

        return cls(targets=targets)


@attrs.define()
class Session:
    _filepath: str  # path to compressed session file
    _subjectID: tp.Optional[str] = attrs.field(default=None)
    _sessionID: tp.Optional[str] = None
    _MRI: MRI = attrs.field(factory=MRI)
    _headModel: HeadModel = attrs.field(factory=HeadModel)
    _subjectRegistration: SubjectRegistration = attrs.field(factory=SubjectRegistration)
    MNIRegistration: tp.Optional[MNIRegistration] = None
    _targets: Targets = attrs.field(factory=Targets)

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
        self.targets.sigTargetsChanged.connect(lambda targetKeys: self._dirtyKeys.add('targets'))

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
    def targets(self):
        return self._targets

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

        if 'targets' in keysToSave:
            logger.debug('Writing targets info')
            config['targets'] = self.targets.asList()
            keysToSave.remove('targets')

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

    def mergeFromFile(self, filepath: str, sections: tp.Optional[tp.List[str]] = None):
        """
        Import session elements from another file. Specify `sections` to only read a subset of elements from the file to merge, e.g. `sections=['targets']` to ignore everything but the targets section in the loaded file.
        """

        logger.info('Merge {} from file: {}'.format('all' if sections is None else sections, filepath))

        _, ext = os.path.splitext(filepath)

        if ext == '.json':
            if sections == ['targets']:
                with open(filepath, 'r') as f:
                    d = json.load(f)
                # TODO: validate against schema
                assert 'targets' in d, 'Targets to import/merge should be in json with "targets" as a field in a root-level dict'
                newTargets = Targets.fromList(d['targets'])
                self.targets.merge(newTargets)
            else:
                raise NotImplementedError()
        else:
            raise NotImplementedError()  # TODO: implement more general merging of .rtnabs files

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

        if 'targets' in config:
            kwargs['targets'] = Targets.fromList(config['targets'])

        # TODO: load other available fields

        logger.debug('Loaded from unpacked dir:\n{}'.format(kwargs))

        return cls(**kwargs)

    @classmethod
    def getTempUnpackDir(cls):
        return tempfile.TemporaryDirectory(prefix='RTNaBSSession_').name
