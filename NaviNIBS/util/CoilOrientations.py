from __future__ import annotations
import attrs
import functools
import logging
import numpy as np
import pyvista as pv
from skspatial.objects import Line, Plane, Vector
import types
import typing as tp

from NaviNIBS.Navigator.Model import Session
from NaviNIBS.Navigator.Model.Calculations import calculateAngleFromMidlineFromCoilToMRITransf, getClosestPointToPointOnMesh
from NaviNIBS.Navigator.Model.Samples import Sample
from NaviNIBS.Navigator.Model.Targets import Target
from NaviNIBS.util.pyvista.dataset import find_closest_point, find_closest_cell
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import applyTransform, composeTransform, invertTransform, estimateAligningTransform


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


T = tp.TypeVar('T')


@attrs.define(frozen=True)
class MetricSpecification:
    getter: tp.Callable[[], float]
    units: str
    label: str
    doShowByDefault: bool = True


@attrs.define
class PoseMetricCalculator:
    _session: Session = attrs.field(repr=False)
    _sample: tp.Optional[Sample]

    _cachedValues: dict[str, tp.Any] = attrs.field(init=False, factory=dict, repr=False)
    _supportedMetrics: list[MetricSpecification] = attrs.field(init=False, factory=list)

    sigCacheReset: Signal = attrs.field(init=False, factory=Signal)
    """
    Emitted whenever previously cached values are cleared, e.g. due to change in sample orientation, sample, or target.
    """

    def __attrs_post_init__(self):
        # self.session.MNIRegistration.sigTransformChanged.connect(lambda *args: self._clearCachedValues())  # TODO: debug, uncomment
        self.session.headModel.sigDataChanged.connect(lambda *args: self._clearCachedValues())
        self.session.targets.sigItemsChanged.connect(self._onTargetsChanged)
        self.session.subjectRegistration.fiducials.sigItemsChanged.connect(self._onFiducialsChanged)
        if self._sample is not None:
            self._sample.sigItemChanged.connect(self._onSampleChanged)

        self._supportedMetrics.extend([
            MetricSpecification(getter=self.getTargetErrorInBrain, units=' mm', label='Target error in brain'),
            MetricSpecification(getter=self.getTargetErrorAtCoil, units=' mm', label='Target error at coil'),
            MetricSpecification(getter=self.getTargetXErrorAtCoil, units=' mm', label='Target X error at coil', doShowByDefault=False),
            MetricSpecification(getter=self.getTargetYErrorAtCoil, units=' mm', label='Target Y error at coil', doShowByDefault=False),
            MetricSpecification(getter=self.getDepthOffsetError, units=' mm', label='Depth offset error'),
            MetricSpecification(getter=self.getDepthAngleError, units='°', label='Depth angle error'),
            MetricSpecification(getter=self.getDepthTargetXAngleError, units='°', label='Depth target X angle error', doShowByDefault=False),
            MetricSpecification(getter=self.getDepthTargetYAngleError, units='°', label='Depth target Y angle error', doShowByDefault=False),
            MetricSpecification(getter=self.getDepthCoilXAngleError, units='°', label='Depth coil X angle error', doShowByDefault=False),
            MetricSpecification(getter=self.getDepthCoilYAngleError, units='°', label='Depth coil Y angle error', doShowByDefault=False),
            MetricSpecification(getter=self.getNormalCoilXAngleError, units='°', label='Normal coil X angle error', doShowByDefault=False),
            MetricSpecification(getter=self.getNormalCoilYAngleError, units='°', label='Normal coil Y angle error', doShowByDefault=False),
            MetricSpecification(getter=self.getHorizAngleError, units='°', label='Horiz angle error'),
            MetricSpecification(getter=self.getAngleFromMidline, units='°', label='Angle from midline'),
            MetricSpecification(getter=self.getAngleFromNormal, units='°', label='Angle from normal'),
            MetricSpecification(getter=self.getSampleCoilToCortexDist, units=' mm', label='Coil to cortex dist'),
            MetricSpecification(getter=self.getSampleCoilToScalpDist, units=' mm', label='Coil to scap dist'),
            MetricSpecification(getter=self.getCoilPosX, units=' mm', label='Coil X position', doShowByDefault=False),
            MetricSpecification(getter=self.getCoilPosY, units=' mm', label='Coil Y position', doShowByDefault=False),
            MetricSpecification(getter=self.getCoilPosZ, units=' mm', label='Coil Z position', doShowByDefault=False),
        ])

    def _cacheWrap(self, fn: tp.Callable[..., T]) -> T:
        """
        Note: this assumes no other args or kwargs are needed for fn, since their values are not
        included in the cache key
        """
        # noinspection PyUnresolvedReferences
        key = fn.cacheKey

        if key in self._cachedValues:
            return self._cachedValues[key]
        else:
            val = fn(doUseCache=False)
            self._cachedValues[key] = val
            return val

    def getValueForMetric(self, label: str):
        whichMetric = None
        for metric in self._supportedMetrics:
            if metric.label == label:
                whichMetric = metric
        if whichMetric is None:
            raise KeyError(f'No metric with label {label} found')

        return whichMetric.getter()

    @property
    def supportedMetrics(self):
        return self._supportedMetrics

    @property
    def session(self):
        return self._session

    @property
    def sample(self):
        return self._sample

    @sample.setter
    def sample(self, newSample: Sample | None):
        if self._sample is newSample:
            return
        logger.debug('Sample changed')
        if self._sample is not None:
            self._sample.sigItemChanged.disconnect(self._onSampleChanged)
        self._sample = newSample
        if self._sample is not None:
            self._sample.sigItemChanged.connect(self._onSampleChanged)
        self._clearCachedValues()

    def _onTargetsChanged(self, targetKeys: list[str], targetAttrs: tp.Optional[list[str]] = None):
        if self._sample is not None and self._sample.targetKey in targetKeys and \
                (targetAttrs is None or len(set(targetAttrs) - {'isVisible'}) > 0):
            self._clearCachedValues(exceptKeys=[
                self.getAngleFromMidline.cacheKey,
                self.getAngleFromNormal.cacheKey,
                self.getCoilPosX.cacheKey,
                self.getCoilPosY.cacheKey,
                self.getCoilPosZ.cacheKey,
            ])

    def _onSampleChanged(self, sampleKey: str, sampleAttrs: tp.Optional[list[str]]):
        """
        Called when sample.sigItemChanged is emitted, not when `self.sample = ...` setter is called.
        """
        if sampleAttrs is None or len(set(sampleAttrs) - {'isVisible', 'isSelected', 'timestamp'}) > 0:
            logger.debug(f'PoseMetricCalculator onSampleChanged')
            if sampleAttrs is None or 'targetKey' in sampleAttrs:
                self._clearCachedValues()
            else:
                self._clearCachedValues(exceptKeys=[
                    self.getTargetCoilToScalpDist.cacheKey,
                    self.getTargetCoilToCortexDist.cacheKey,
                ])

    def _onFiducialsChanged(self, fidKeys: list[str], attribs: tp.Optional[list[str]] = None):
        if attribs is None or 'plannedCoord' in attribs:
            self._onPlannedFiducialsChanged()

    def _onPlannedFiducialsChanged(self):
        self._clearCachedValues(includingKeys=[self.getAngleFromMidline.cacheKey])

    def _clearCachedValues(self, includingKeys: tp.Optional[list[str]] = None, exceptKeys: tp.Optional[list[str]] = None):
        if includingKeys is None and exceptKeys is None:
            self._cachedValues = dict()
        else:
            if includingKeys is None:
                includingKeys = list(self._cachedValues.keys())
            if exceptKeys is None:
                exceptKeys = []
            self._cachedValues = {key: val for key, val in self._cachedValues.items()
                                  if (key in exceptKeys or key not in includingKeys)}
        self.sigCacheReset.emit()

    def _getTargetErrorAtDepth(self, depthFromTargetCoil: float, axis: tp.Optional[int] = None) -> float:

        target = self._session.targets[self._sample.targetKey]

        targetLinePts_targetCoilSpace = np.asarray([[0, 0, -depthFromTargetCoil], [0, 0, -depthFromTargetCoil + 10]])

        plane = Plane(point=targetLinePts_targetCoilSpace[0, :].squeeze(),
                      normal=np.diff(targetLinePts_targetCoilSpace, axis=0).squeeze())

        sampleLinePts_sampleCoilSpace = np.asarray([[0, 0, 0], [0, 0, 1]])
        sampleLinePts_targetCoilSpace = applyTransform([self._sample.coilToMRITransf, invertTransform(target.coilToMRITransf)], sampleLinePts_sampleCoilSpace, doCheck=False)
        line = Line(sampleLinePts_targetCoilSpace[0, :].squeeze(), np.diff(sampleLinePts_targetCoilSpace, axis=0).squeeze())

        try:
            samplePtOnPlane = plane.intersect_line(line)
        except ValueError:
            # sample axis is parallel to plane
            return np.nan

        if axis is None:
            dist = np.linalg.norm(targetLinePts_targetCoilSpace[0, :].squeeze() - samplePtOnPlane)
        else:
            dist = targetLinePts_targetCoilSpace[0, axis].squeeze() - samplePtOnPlane[axis]  # note that this is signed

        return dist

    def getTargetCoilToScalpDist(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(self.getTargetCoilToScalpDist)

        if self._sample is None or self._sample.targetKey is None:
            return np.nan

        skinSurf = self._session.headModel.skinSurf
        if skinSurf is None:
            return np.nan

        target = self._session.targets[self._sample.targetKey]

        return self._getCoilToSurfDist(coilToMRITransf=target.coilToMRITransf,
                                       surf=skinSurf)

    getTargetCoilToScalpDist.cacheKey = 'targetCoilToScalpDist'

    def getTargetCoilToCortexDist(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(self.getTargetCoilToCortexDist)

        if self._sample is None or self._sample.targetKey is None:
            return np.nan

        gmSurf = self._session.headModel.gmSurf
        if gmSurf is None:
            return np.nan

        target = self._session.targets[self._sample.targetKey]

        return self._getCoilToSurfDist(coilToMRITransf=target.coilToMRITransf,
                                       surf=gmSurf)

    getTargetCoilToCortexDist.cacheKey = 'targetCoilToCortexDist'

    def getSampleCoilToScalpDist(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(self.getSampleCoilToScalpDist)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        skinSurf = self._session.headModel.skinSurf
        if skinSurf is None:
            return np.nan

        return self._getCoilToSurfDist(coilToMRITransf=self._sample.coilToMRITransf,
                                       surf=skinSurf)

    getSampleCoilToScalpDist.cacheKey = 'sampleCoilToScalpDist'

    def getSampleCoilToCortexDist(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(self.getSampleCoilToCortexDist)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        gmSurf = self._session.headModel.gmSurf
        if gmSurf is None:
            return np.nan

        return self._getCoilToSurfDist(coilToMRITransf=self._sample.coilToMRITransf,
                                       surf=gmSurf)

    getSampleCoilToCortexDist.cacheKey = 'sampleCoilToCortexDist'

    def _getCoilToSurfDist(self, coilToMRITransf: np.ndarray, surf: pv.PolyData) -> float:
        coilOrigin_MRI = applyTransform(coilToMRITransf, np.zeros((3,)), doCheck=False)

        # TODO: maybe use additional constraint to find distance within a small range along coil depth axis
        # (e.g. a small sphere sliding down along the depth axis until reaching cortex)
        # Currently, this may find a closest point at a very oblique angle from coil center if coil is tilted

        if False:
            # find closest point, constrained to vertices in surf
            closestPtIndex = find_closest_point(surf, coilOrigin_MRI)
            closestPt = surf.points[closestPtIndex, :]
        else:
            # find closest point, anywhere on surface
            _, closestPt = find_closest_cell(surf, point=coilOrigin_MRI, return_closest_point=True)

        if False:
            # unsigned distance
            return np.linalg.norm(closestPt - coilOrigin_MRI)
        else:
            # signed distance, where coil -Z axis pointing down to surface is positive offset
            closestPt_coilSpace = applyTransform(invertTransform(coilToMRITransf), closestPt, doCheck=False)
            return -1*closestPt_coilSpace[2]

    def getTargetErrorInBrain(self, doUseCache: bool = True) -> float:
        """
        Distance from target in brain to sample after projecting down to plane of cortical target.

        Note: this determines depth of the target in the brain by finding closest point to the gm surf,
        so that we don't need to assume that target.targetCoord is already at the cortical surface.
        """
        if doUseCache:
            return self._cacheWrap(fn=self.getTargetErrorInBrain)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        coilTargetToCortexDist = self.getTargetCoilToCortexDist()

        if np.isnan(coilTargetToCortexDist):
            return np.nan

        return self._getTargetErrorAtDepth(depthFromTargetCoil=coilTargetToCortexDist)

    getTargetErrorInBrain.cacheKey = 'targetErrorInBrain'

    def getTargetErrorAtCoil(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(self.getTargetErrorAtCoil)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        if self._sample.targetKey is None:
            return np.nan

        return self._getTargetErrorAtDepth(depthFromTargetCoil=0)

    getTargetErrorAtCoil.cacheKey = 'targetErrorAtCoil'

    def getTargetXErrorAtCoil(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(self.getTargetXErrorAtCoil)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        if self._sample.targetKey is None:
            return np.nan

        return self._getTargetErrorAtDepth(depthFromTargetCoil=0, axis=0)  # note that this is relative to the target X axis, not sample X axis

    getTargetXErrorAtCoil.cacheKey = 'targetXErrorAtCoil'

    def getTargetYErrorAtCoil(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(self.getTargetYErrorAtCoil)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        if self._sample.targetKey is None:
            return np.nan

        return self._getTargetErrorAtDepth(depthFromTargetCoil=0, axis=1)  # note that this is relative to the target X axis, not sample X axis

    getTargetYErrorAtCoil.cacheKey = 'targetYErrorAtCoil'

    def getDepthOffsetError(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getDepthOffsetError)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        if self._sample.targetKey is None:
            return np.nan

        target = self._session.targets[self._sample.targetKey]

        sampleCoilPt_sampleCoilSpace = np.asarray([0, 0, 0])
        sampleCoilPt_targetCoilSpace = applyTransform(
            [self._sample.coilToMRITransf, invertTransform(target.coilToMRITransf)],
            sampleCoilPt_sampleCoilSpace, doCheck=False)

        offset = sampleCoilPt_targetCoilSpace[2]

        return offset

    getDepthOffsetError.cacheKey = 'depthOffsetError'

    def getDepthAngleError(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getDepthAngleError)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        if self._sample.targetKey is None:
            return np.nan

        target = self._session.targets[self._sample.targetKey]

        targetLinePts_targetCoilSpace = np.asarray([[0, 0, 0], [0, 0, 1]])
        targetLinePts_MRISpace = applyTransform(target.coilToMRITransf, targetLinePts_targetCoilSpace, doCheck=False)
        targetVector = Vector(np.diff(targetLinePts_MRISpace, axis=0).squeeze())

        sampleLinePts_sampleCoilSpace = np.asarray([[0, 0, 0], [0, 0, 1]])
        sampleLinePts_MRISpace = applyTransform(self._sample.coilToMRITransf, sampleLinePts_sampleCoilSpace, doCheck=False)
        sampleVector = Vector(np.diff(sampleLinePts_MRISpace, axis=0).squeeze())

        angle = targetVector.angle_between(sampleVector)

        return np.rad2deg(angle)

    getDepthAngleError.cacheKey = 'depthAngleError'

    def _getDepthComponentAngleError(self, iDim: int, relTo: str):
        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        if self._sample.targetKey is None:
            return np.nan

        target = self._session.targets[self._sample.targetKey]

        match relTo:
            case 'target':
                targetLinePts_targetCoilSpace = np.asarray([[0, 0, 0], [0, 0, 1]])

                sampleLinePts_sampleCoilSpace = np.asarray([[0, 0, 0], [0, 0, 1]])
                sampleLinePts_targetCoilSpace = applyTransform([self._sample.coilToMRITransf,
                                                                invertTransform(target.coilToMRITransf)],
                                                                sampleLinePts_sampleCoilSpace, doCheck=False)

                targetVector = Vector(np.diff(targetLinePts_targetCoilSpace[:, [iDim, 2]], axis=0).squeeze())
                sampleVector = Vector(np.diff(sampleLinePts_targetCoilSpace[:, [iDim, 2]], axis=0).squeeze())

            case 'coil':
                targetLinePts_targetCoilSpace = np.asarray([[0, 0, 0], [0, 0, 1]])
                targetLinePts_sampleCoilSpace = applyTransform([target.coilToMRITransf,
                                                               invertTransform(self._sample.coilToMRITransf)],
                                                               targetLinePts_targetCoilSpace, doCheck=False)

                sampleLinePts_sampleCoilSpace = np.asarray([[0, 0, 0], [0, 0, 1]])

                targetVector = Vector(np.diff(targetLinePts_sampleCoilSpace[:, [iDim, 2]], axis=0).squeeze())
                sampleVector = Vector(np.diff(sampleLinePts_sampleCoilSpace[:, [iDim, 2]], axis=0).squeeze())

            case _:
                raise NotImplementedError

        angle = targetVector.angle_signed(sampleVector)

        return np.rad2deg(angle)

    def getDepthTargetXAngleError(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getDepthTargetXAngleError)

        return self._getDepthComponentAngleError(iDim=0, relTo='target')

    getDepthTargetXAngleError.cacheKey = 'depthTargetXAngleError'

    def getDepthTargetYAngleError(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getDepthTargetYAngleError)

        return self._getDepthComponentAngleError(iDim=1, relTo='target')

    getDepthTargetYAngleError.cacheKey = 'depthTargetYAngleError'

    def getDepthCoilXAngleError(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getDepthCoilXAngleError)

        return self._getDepthComponentAngleError(iDim=0, relTo='coil')

    getDepthCoilXAngleError.cacheKey = 'depthCoilXAngleError'

    def getDepthCoilYAngleError(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getDepthCoilYAngleError)

        return self._getDepthComponentAngleError(iDim=1, relTo='coil')

    getDepthCoilYAngleError.cacheKey = 'depthCoilYAngleError'

    def getHorizAngleError(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getHorizAngleError)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        if self._sample.targetKey is None:
            return np.nan

        target = self._session.targets[self._sample.targetKey]

        # project point representing sample coil handle onto horizontal plane of target coil, then determine angle between handles

        sampleLinePts_sampleCoilSpace = np.asarray([[0, 0, 0], [0, -1, 0]])
        sampleLinePts_targetCoilSpace = applyTransform(
            [self._sample.coilToMRITransf, invertTransform(target.coilToMRITransf)],
            sampleLinePts_sampleCoilSpace, doCheck=False)

        targetHandleVector2D_targetCoilSpace = Vector([0, -1])
        sampleHandleVector2D_targetCoilSpace = Vector(np.diff(sampleLinePts_targetCoilSpace[:, 0:2], axis=0).squeeze())

        angle = targetHandleVector2D_targetCoilSpace.angle_signed(sampleHandleVector2D_targetCoilSpace)

        return np.rad2deg(angle)

    getHorizAngleError.cacheKey = 'horizAngleError'

    def getAngleFromMidline(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getAngleFromMidline)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        return calculateAngleFromMidlineFromCoilToMRITransf(self.session, self._sample.coilToMRITransf)

    getAngleFromMidline.cacheKey = 'angleFromMidline'

    def getAngleFromNormal(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getAngleFromNormal)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        coilToCortexDist = self.getSampleCoilToCortexDist()
        if np.isnan(coilToCortexDist):
            return np.nan

        if False:
            pt_gm = self.getClosestPointToCoilOnGM()
            closestPt_skin = self.getClosestPointToCoilOnSkin()
        else:
            pt_gm = applyTransform(self._sample.coilToMRITransf, np.asarray([0, 0, -coilToCortexDist]), doCheck=False)
            closestPt_skin = self.getClosestPointToSampleCortexDepthOnSkin()

        if closestPt_skin is None or pt_gm is None:
            return np.nan

        # find ideal normal by defining line through these two points
        idealNormal = Vector(closestPt_skin - pt_gm)
        actualNormal = Vector(np.diff(applyTransform(self._sample.coilToMRITransf,
                                                     np.asarray([[0, 0, -1], [0, 0, 0,]]),
                                                     doCheck=False), axis=0).squeeze())

        angle = idealNormal.angle_between(actualNormal)

        return np.rad2deg(angle)

    getAngleFromNormal.cacheKey = 'angleFromNormal'

    def getCoilPosX(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getCoilPosX)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        return self._sample.coilToMRITransf[0, 3]

    getCoilPosX.cacheKey = 'coilPosX'

    def getCoilPosY(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getCoilPosY)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        return self._sample.coilToMRITransf[1, 3]

    getCoilPosY.cacheKey = 'coilPosY'

    def getCoilPosZ(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getCoilPosZ)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        return self._sample.coilToMRITransf[2, 3]

    getCoilPosZ.cacheKey = 'coilPosZ'

    def getClosestPointToCoilOnSkin(self, doUseCache: bool = True) -> tp.Optional[np.ndarray]:
        if doUseCache:
            return self._cacheWrap(fn=self.getClosestPointToCoilOnSkin)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return None

        return getClosestPointToPointOnMesh(session=self._session,
                                            whichMesh='skinSurf',
                                            point_MRISpace=applyTransform(self._sample.coilToMRITransf, np.zeros((3,)), doCheck=False))

    getClosestPointToCoilOnSkin.cacheKey = 'closestPointToCoilOnSkin'

    def getClosestPointToCoilOnGM(self, doUseCache: bool = True) -> tp.Optional[np.ndarray]:
        if doUseCache:
            return self._cacheWrap(fn=self.getClosestPointToCoilOnGM)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return None

        return getClosestPointToPointOnMesh(session=self._session,
                                            whichMesh='gmSurf',
                                            point_MRISpace=applyTransform(self._sample.coilToMRITransf, np.zeros((3,)), doCheck=False))

    getClosestPointToCoilOnGM.cacheKey = 'closestPointToCoilOnGM'

    def getClosestPointToSampleCortexDepthOnSkin(self, doUseCache: bool = True) -> tp.Optional[np.ndarray]:
        if doUseCache:
            return self._cacheWrap(fn=self.getClosestPointToSampleCortexDepthOnSkin)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return None

        coilToCortexDist = self.getSampleCoilToCortexDist()
        if np.isnan(coilToCortexDist):
            return None

        point_MRISpace = applyTransform(self._sample.coilToMRITransf, np.asarray([0, 0, -coilToCortexDist]), doCheck=False)

        return getClosestPointToPointOnMesh(session=self._session,
                                            whichMesh='skinSurf',
                                            point_MRISpace=point_MRISpace)

    getClosestPointToSampleCortexDepthOnSkin.cacheKey = 'closestPointToSampleCortexDepthOnSkin'

    def _getNormalCoilAngleError(self, iDim: int) -> float:

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        coilToCortexDist = self.getSampleCoilToCortexDist()
        if np.isnan(coilToCortexDist):
            return np.nan

        pt_gm = applyTransform(self._sample.coilToMRITransf, np.asarray([0, 0, -coilToCortexDist]), doCheck=False)
        closestPt_skin = self.getClosestPointToSampleCortexDepthOnSkin()

        if closestPt_skin is None:
            return np.nan

        # find ideal normal by defining line through these two points
        idealNormalPts_mriSpace = np.vstack([pt_gm, closestPt_skin])
        idealNormalPts_coilSpace = applyTransform(invertTransform(self._sample.coilToMRITransf), idealNormalPts_mriSpace, doCheck=False)
        idealNormal = Vector(np.diff(idealNormalPts_coilSpace[:, [iDim, 2]], axis=0).squeeze())
        actualNormal = Vector([0, 1])

        angle = idealNormal.angle_signed(actualNormal)  # TODO: check sign

        return np.rad2deg(angle)

    def getNormalCoilXAngleError(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getNormalCoilXAngleError)

        return self._getNormalCoilAngleError(iDim=0)

    getNormalCoilXAngleError.cacheKey = 'normalCoilXAngleError'

    def getNormalCoilYAngleError(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getNormalCoilYAngleError)

        return self._getNormalCoilAngleError(iDim=1)

    getNormalCoilYAngleError.cacheKey = 'normalCoilYAngleError'
