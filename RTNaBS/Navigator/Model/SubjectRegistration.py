from __future__ import annotations

import attrs
from datetime import datetime
import nibabel as nib
import json
import logging
import numpy as np
import os
import pandas as pd
import pyvista as pv
import shutil
import tempfile
import typing as tp
from typing import ClassVar

from RTNaBS.util.Signaler import Signal
from RTNaBS.util.numpy import array_equalish


logger = logging.getLogger(__name__)


FiducialCoord = np.ndarray
FiducialSet = tp.Dict[str, tp.Optional[FiducialCoord]]
Transform = np.ndarray
HeadPoint = np.ndarray


@attrs.define
class HeadPoints:
    _headPoints: list[HeadPoint]

    sigHeadpointsAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((list[int],)))
    """
    This signal includes list of indices of headpoints about to change.
    """

    sigHeadpointsChanged: Signal = attrs.field(init=False, factory=lambda: Signal((list[int],)))
    """"
    This signal includes list of indices of headpoints that changed.
    """

    def __attrs_post_init__(self):
        pass

    def append(self, point: HeadPoint):
        index = len(self)
        self.sigHeadpointsAboutToChange.emit([index])
        self._headPoints.append(point)
        self.sigHeadpointsChanged.emit([index])

    def extend(self, points: np.ndarray):
        assert points.shape[1] == 3
        indices = len(self) + np.arange(points.shape[0])
        self.sigHeadpointsAboutToChange.emit(indices)
        self._headPoints.extend(points)
        self.sigHeadpointsChanged.emit(indices)

    def remove(self, indexOrIndices: int | list[int]):
        if isinstance(indexOrIndices, int):
            indexOrIndices = [indexOrIndices]

        indexOrIndices = sorted(indexOrIndices)

        changedIndices = list(range(indexOrIndices[0], len(self)));
        self.sigHeadpointsAboutToChange.emit(changedIndices)
        for index in reversed(indexOrIndices):
            del self._headPoints[index]
        self.sigHeadpointsChanged.emit(changedIndices)

    def replace(self, points: np.ndarray):
        """
        Clear all previous points, set to new points.
        """
        indices = list(range(0, max(len(points), len(self))))
        self.sigHeadpointsAboutToChange.emit(indices)
        self._headPoints.clear()
        with self.sigHeadpointsAboutToChange.blocked(), self.sigHeadpointsChanged.blocked():
            self.extend(points)
        self.sigHeadpointsChanged.emit(indices)

    def clear(self):
        indices = list(range(0, len(self)))
        self.sigHeadpointsAboutToChange.emit(indices)
        self._headPoints.clear()
        self.sigHeadpointsChanged.emit(indices)

    def __len__(self):
        return len(self._headPoints)

    def __iter__(self):
        return iter(self._headPoints)

    def __getitem__(self, index):
        return self._headPoints[index]

    def asList(self):
        return [headPt.tolist() for headPt in self._headPoints]

    @classmethod
    def fromList(cls, l: list[tuple[float, float, float]]):
        return cls(headPoints=[np.asarray(headPt) for headPt in l])


@attrs.define()
class SubjectRegistration:

    _plannedFiducials: FiducialSet = attrs.field(factory=dict)  # in MRI space
    _sampledFiducials: FiducialSet = attrs.field(factory=dict)  # in head tracker space
    _sampledHeadPoints: HeadPoints = attrs.field(factory=HeadPoints)  # in head tracker space
    _trackerToMRITransf: tp.Optional[Transform] = None

    _plannedFiducialsHistory: tp.Dict[str, FiducialSet] = attrs.field(factory=dict)
    _sampledFiducialsHistory: tp.Dict[str, FiducialSet] = attrs.field(factory=dict)
    _trackerToMRITransfHistory: tp.Dict[str, tp.Optional[Transform]] = attrs.field(factory=dict)

    sigPlannedFiducialsChanged: Signal = attrs.field(init=False, factory=Signal)
    sigSampledFiducialsChanged: Signal = attrs.field(init=False, factory=Signal)

    sigTrackerToMRITransfChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):

        # make sure histories are up to date with current values
        for whichSet in ('planned', 'sampled'):
            fiducials = getattr(self, '_' + whichSet + 'Fiducials')
            fiducialsHistory = getattr(self, '_' + whichSet + 'FiducialsHistory')
            if len(fiducials) > 0 and (len(fiducialsHistory) == 0 or not self.fiducialsEqual(list(fiducialsHistory.values())[-1],  fiducials)):
                fiducialsHistory[self._getTimestampStr()] = fiducials.copy()
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
    def hasMinimumPlannedFiducials(self) -> bool:
        numFiducialsSet = 0
        for fid in self._plannedFiducials.values():
            if fid is not None:
                numFiducialsSet += 1
        return numFiducialsSet >= 3

    @property
    def sampledFiducials(self):
        return self._sampledFiducials  # note: result should not be modified, should instead call setter

    @property
    def hasMinimumSampledFiducials(self) -> bool:
        numFiducialsSet = 0
        for key, fid in self._sampledFiducials.items():
            if fid is not None and self._plannedFiducials.get(key, None) is not None:
                numFiducialsSet += 1
        return numFiducialsSet >= 3

    @property
    def sampledHeadPoints(self) -> HeadPoints:
        return self._sampledHeadPoints
    
    @property
    def trackerToMRITransf(self):
        return self._trackerToMRITransf

    @trackerToMRITransf.setter
    def trackerToMRITransf(self, newTransf: tp.Optional[Transform]):
        if array_equalish(self._trackerToMRITransf, newTransf):
            logger.debug('No change in trackerToMRITransf, returning')
            return

        # TODO: do validation of newTransf

        logger.info('Set trackerToMRITransf to {}'.format(newTransf))
        self._trackerToMRITransf = newTransf

        self._trackerToMRITransfHistory[self._getTimestampStr()] = None if newTransf is None else newTransf.copy()

        self.sigTrackerToMRITransfChanged.emit()

    @property
    def isRegistered(self):
        return self.trackerToMRITransf is not None

    @property
    def approxHeadCenter(self) -> tp.Optional[np.ndarray]:
        lpa = self.plannedFiducials.get('LPA', None)
        rpa = self.plannedFiducials.get('RPA', None)
        if lpa is not None and rpa is not None:
            center = (lpa + rpa) / 2
        else:
            logger.warning('Insufficient information for determining approximate header center')
            # TODO: implement more general method of estimating center, e.g. more variety of LPA/RPA naming, name-agnostic averaging, etc.
            center = None
        return center

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
        d['sampledHeadPoints'] = self._sampledHeadPoints.asList()
        if self._trackerToMRITransf is not None:
            d['trackerToMRITransf'] = self._trackerToMRITransf.tolist()
        if len(self._trackerToMRITransfHistory) > 0:
            d['trackerToMRITransfHistory'] = [dict(time=key, trackerToMRITransf=val.tolist() if val is not None else None) for key, val in self._trackerToMRITransfHistory.items()]

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
            d['sampledHeadPoints'] = HeadPoints.fromList(d['sampledHeadPoints'])

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
