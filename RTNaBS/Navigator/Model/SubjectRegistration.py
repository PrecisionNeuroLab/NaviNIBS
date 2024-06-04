from __future__ import annotations

import attrs
import copy
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

from RTNaBS.util.attrs import attrsAsDict
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.numpy import array_equalish, attrsWithNumpyAsDict, attrsWithNumpyFromDict

from RTNaBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem


logger = logging.getLogger(__name__)


FiducialCoord = np.ndarray
FiducialSet = tp.Dict[str, tp.Optional[FiducialCoord]]
Transform = np.ndarray
HeadPoint = np.ndarray


@attrs.define
class Fiducial(GenericCollectionDictItem[str]):
    _plannedCoord: tp.Optional[np.ndarray] = None
    """
    Note that these (planned) coordinates are in MRI space.
    """
    _sampledCoords: tp.Optional[np.ndarray] = None
    """
    Note that these (sampled) coordinates are in tracker space, not in MRI space. This is to not require modification when
    using other methods besides fiducials for defining tracker->MRI coordinate transform.
    
    Is of size Nx3, where N is the number of repeated samples of the same coordinate.
    """
    _sampledCoord: tp.Optional[np.ndarray] = None
    """
    Is of size (3,). 
    
    Can be used without sampledCoords to specify single sampled coordinate. Or can be used with sampledCoords to override the default sampledCoords->sampledCoord aggregation (a simple mean) with some other result (e.g. a trimmed mean) while still preserving all original samples.
    
    Note that this is cleared whenever sampledCoords is set, to avoid persisting an out-of-date aggregated sample. 
    """

    _alignmentWeight: float = 1.
    """
    Can be used to specify a relative positive weight for how this fiducial will be incorporated
    into registration. 
    
    For example, if the NAS fiducial is given weight 100, and the LPA and RPA
    fiducials have default weights of 1, then alignment will always very closely match measured 
    NAS to planned NAS location, and allow larger deviations for LPA and RPA for the sake of 
    closely matching NAS.  
    """

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        if self._sampledCoords is not None:
            if self._sampledCoords.ndim == 1:
                assert self._sampledCoords.shape[0] == 3
                # reshape from (3,) to (1, 3) to be consistent with size when there are multiple points
                self._sampledCoords = self._sampledCoords[np.newaxis, :]
            else:
                assert self._sampledCoords.ndim == 2
                assert self._sampledCoords.shape[1] == 3

        if self._sampledCoord is not None:
            assert self._sampledCoord.ndim == 1 and self._sampledCoord.shape[0] == 3

    @property
    def plannedCoord(self):
        return self._plannedCoord

    @plannedCoord.setter
    def plannedCoord(self, newCoord: tp.Optional[np.ndarray]):
        if array_equalish(newCoord, self._plannedCoord):
            return
        self.sigItemAboutToChange.emit(self._key, ['plannedCoord'])
        self._plannedCoord = newCoord
        self.sigItemChanged.emit(self._key, ['plannedCoord'])

    @property
    def sampledCoord(self):
        """
        Always returns single vector coordinate of size (3,), or None if the fiducial has not been sampled.
        If multiple repeats of the same fiducial are sampled (in sampledCoords), and no separate sampledCoord
        has been set, this returns an average of the samples.
        """
        if self._sampledCoord is not None:
            return self._sampledCoord
        elif self._sampledCoords is None:
            return None
        elif self._sampledCoords.shape[0] == 1:
            return self._sampledCoords.squeeze()
        else:
            return self._sampledCoords.mean(axis=0, keepdims=False)

    @sampledCoord.setter
    def sampledCoord(self, newCoord: tp.Optional[np.ndarray]):
        if array_equalish(newCoord, self._sampledCoord):
            return

        if newCoord is not None:
            assert newCoord.ndim == 1 and newCoord.shape[0] == 3
        changingAttrs = ['sampledCoord']
        if self._sampledCoords is None:
            changingAttrs.append('sampledCoords')
        self.sigItemAboutToChange.emit(self._key, changingAttrs)
        self._sampledCoord = newCoord
        self.sigItemChanged.emit(self._key, changingAttrs)

    @property
    def sampledCoords(self):
        if self._sampledCoords is not None:
            return self._sampledCoords
        elif self._sampledCoord is not None:
            return self._sampledCoord[np.newaxis, :]
        else:
            return None

    @sampledCoords.setter
    def sampledCoords(self, newCoords: tp.Optional[np.ndarray]):
        if array_equalish(newCoords, self._sampledCoords):
            if newCoords is None and self._sampledCoord is not None:
                self.sampledCoord = None
            return

        if newCoords is not None:
            assert newCoords.ndim == 2 and newCoords.shape[1] == 3
        changingAttrs = ['sampledCoords']
        if newCoords is not None or self._sampledCoord is not None:
            changingAttrs.append('sampledCoord')
        self.sigItemAboutToChange.emit(self._key, changingAttrs)
        self._sampledCoord = None  # reset whenever sampledCoords is set to avoid persisting an out-of-date aggregated sample
        self._sampledCoords = newCoords
        self.sigItemChanged.emit(self._key, changingAttrs)

    @property
    def alignmentWeight(self):
        return self._alignmentWeight

    @alignmentWeight.setter
    def alignmentWeight(self, newWeight: float):
        if newWeight == self._alignmentWeight:
            return
        self.sigItemAboutToChange.emit(self._key, ['alignmentWeight'])
        self._alignmentWeight = newWeight
        self.sigItemChanged.emit(self._key, ['alignmentWeight'])

    def asDict(self) -> dict[str, tp.Any]:
        npFields = ['plannedCoord', 'sampledCoord', 'sampledCoords']
        return attrsWithNumpyAsDict(self, npFields=npFields)

    @classmethod
    def fromDict(cls, d: dict[str, tp.Any]):
        npFields = ['plannedCoord', 'sampledCoord', 'sampledCoords']
        return attrsWithNumpyFromDict(cls, d, npFields=npFields)


@attrs.define
class Fiducials(GenericCollection[str, Fiducial]):
    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def plannedFiducials(self) -> dict[str, tp.Optional[np.ndarray]]:
        return {key: fid.plannedCoord for key, fid in self.items()}

    @property
    def sampledFiducials(self) -> dict[str, tp.Optional[np.ndarray]]:
        """
        Note that this returns a dict where each value is a `sampledCoord` (always None or shape (3,)),
        even if multiple samples exist for a given fiducial. For full access, use `allSampledFiducials`
        instead.
        """
        return {key: fid.sampledCoord for key, fid in self.items()}

    @property
    def allSampledFiducials(self) -> dict[str, tp.Optional[np.ndarray]]:
        return {key: fid.sampledCoords for key, fid in self.items()}

    @property
    def alignmentWeights(self) -> np.ndarray:
        return np.asarray([fid.alignmentWeight for fid in self.values()])

    @classmethod
    def fromList(cls, itemList: list[dict[str, tp.Any]]) -> Fiducials:
        items = {}
        for itemDict in itemList:
            items[itemDict['key']] = Fiducial.fromDict(itemDict)

        return cls(items=items)


@attrs.define
class HeadPoints:
    _headPoints: list[HeadPoint] = attrs.field(factory=list)
    """
    Note that these are coordinates are in tracker space, not in MRI space.
    """
    _alignmentWeights: tp.Optional[np.ndarray] = attrs.field(default=None)
    """
    Optional weights used for headpoint-based registration refinement, in format expected by 
    simpleicp's `rbp_observation_weights` argument (i.e. rot_x, rot_y, rot_z, t_x, t_y, t_z). 
    Can alternatively specify as a single scalar to apply the same weight to all terms, or as 
    two scalars to apply the first weight to all rotation term, the second weights to all
    translation terms.
    
    Note that values will be inverted (weightArg = 1 / weight) so that higher input values 
    correspond to greater weighting of head points, rather than greater weighting of fiducials.
    
    Note that these weights are different in definition and usage than `Fiducial.alignmentWeight`.
    """

    sigHeadpointsAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((list[int],)))
    """
    This signal includes list of indices of headpoints about to change.
    """

    sigHeadpointsChanged: Signal = attrs.field(init=False, factory=lambda: Signal((list[int],)))
    """"
    This signal includes list of indices of headpoints that changed.
    """

    sigAttribsAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((dict[str],)))
    """
    This signal includes keys and incoming values of attributes (besides main headPoints list) about to change.
    """

    sigAttribsChanged: Signal = attrs.field(init=False, factory=lambda: Signal((list[str],)))
    """
    This signal includes keys of attributes (besides main headPoints list) that changed.
    """

    def __attrs_post_init__(self):
        pass

    @property
    def alignmentWeights(self):
        return self._alignmentWeights

    @alignmentWeights.setter
    def alignmentWeights(self, newWeights: tp.Optional[np.ndarray]):
        if array_equalish(newWeights, self._alignmentWeights):
            return
        if newWeights is not None:
            assert isinstance(newWeights, np.ndarray)
            assert len(newWeights) in (1, 2, 6)
        self.sigAttribsAboutToChange.emit(dict(alignmentWeights=newWeights))
        self._alignmentWeights = newWeights
        self.sigAttribsChanged.emit(['alignmentWeights'])

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

    _fiducials: Fiducials = attrs.field(factory=Fiducials)

    _sampledHeadPoints: HeadPoints = attrs.field(factory=HeadPoints)  # in head tracker space
    _trackerToMRITransf: tp.Optional[Transform] = None

    _fiducialsHistory: tp.Dict[str, Fiducials] = attrs.field(factory=dict)
    _trackerToMRITransfHistory: tp.Dict[str, tp.Optional[Transform]] = attrs.field(factory=dict)

    sigTrackerToMRITransfAboutToChange: Signal = attrs.field(init=False, factory=Signal)
    sigTrackerToMRITransfChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):

        # make sure histories are up to date with current values
        if len(self._fiducials) > 0 and (len(self._fiducialsHistory) == 0 or
                                         not self.fiducialsEqual(list(self._fiducialsHistory.values())[-1], self._fiducials)):
            self._saveFiducialsToHistory()

        self._fiducials.sigItemsChanged.connect(self._onFiducialsChanged)

        if self._trackerToMRITransf is not None:
            if len(self._trackerToMRITransfHistory) == 0 or not array_equalish(list(self._trackerToMRITransfHistory.values())[-1], self._trackerToMRITransf):
                self._trackerToMRITransfHistory[self._getTimestampStr()] = self._trackerToMRITransf.copy()

    def _saveFiducialsToHistory(self):
        # TODO: make this more efficient
        self._fiducialsHistory[self._getTimestampStr()] = Fiducials.fromList(copy.deepcopy(self._fiducials.asList()))

    def _onFiducialsChanged(self, keys, attribs: tp.Optional[list[str]] = None):
        self._saveFiducialsToHistory()

    @property
    def fiducials(self):
        return self._fiducials

    @property
    def hasMinimumPlannedFiducials(self) -> bool:
        numFiducialsSet = 0
        for fid in self._fiducials.values():
            if fid.plannedCoord is not None:
                numFiducialsSet += 1
        return numFiducialsSet >= 3

    @property
    def hasMinimumSampledFiducials(self) -> bool:
        numFiducialsSet = 0
        for fid in self._fiducials.values():
            if fid.sampledCoord is not None and fid.plannedCoord is not None:
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

        self.sigTrackerToMRITransfAboutToChange.emit()

        self._trackerToMRITransf = newTransf

        self._trackerToMRITransfHistory[self._getTimestampStr()] = None if newTransf is None else newTransf.copy()

        self.sigTrackerToMRITransfChanged.emit()

    @property
    def isRegistered(self):
        return self.trackerToMRITransf is not None

    @property
    def approxHeadCenter(self) -> tp.Optional[np.ndarray]:
        lpa = self.fiducials.get('LPA', None)
        rpa = self.fiducials.get('RPA', None)
        if lpa is not None and rpa is not None and lpa.plannedCoord is not None and rpa.plannedCoord is not None:
            center = (lpa + rpa) / 2
        else:
            logger.warning('Insufficient information for determining approximate header center')
            # TODO: implement more general method of estimating center, e.g. more variety of LPA/RPA naming, name-agnostic averaging, etc.
            center = None
        return center

    def asDict(self) -> tp.Dict[str, tp.Any]:
        d = dict()

        d['fiducials'] = self._fiducials.asList()

        d['fiducialsHistory'] = [dict(time=key, fiducials=val.asList()) for key, val in self._fiducialsHistory.items()]

        d['sampledHeadPoints'] = self._sampledHeadPoints.asList()

        for key in ('fiducials', 'fiducialsHistory', 'sampledHeadPoints'):
            if len(d[key]) == 0:
                del d[key]  # don't include in output if it's empty anyways

        if self._sampledHeadPoints.alignmentWeights is not None:
            d['headPointAlignmentWeights'] = self._sampledHeadPoints.alignmentWeights.tolist()

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

        def convertTransf(transf: tp.List[tp.List[float, float, float]]) -> np.ndarray:
            return np.asarray(transf)

        def convertHistoryListToDict(historyList: tp.List[tp.Dict[str, tp.Any]], field: str, entryConverter: tp.Callable) -> tp.Dict[str, tp.Any]:
            historyDict = {}
            for entry in historyList:
                timeStr = entry['time']
                historyDict[timeStr] = entryConverter(entry[field])
            return historyDict

        d['fiducials'] = Fiducials.fromList(d['fiducials'])

        if 'fiducialsHistory' in d:
            d['fiducialsHistory'] = {entry['time']: Fiducials.fromList(entry['fiducials'])
                                     for entry in d['fiducialsHistory']}

        if 'sampledHeadPoints' in d:
            d['sampledHeadPoints'] = HeadPoints.fromList(d['sampledHeadPoints'])

        if 'headPointAlignmentWeights' in d:
            d['sampledHeadPoints'].alignmentWeights = np.asarray(d.pop('headPointAlignmentWeights'))

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
    def fiducialsEqual(fidsA: Fiducials, fidsB: Fiducials) -> bool:
        return json.dumps(fidsA.asList()) == json.dumps(fidsB.asList())
