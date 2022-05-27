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
    def sampledHeadPoints(self):
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
