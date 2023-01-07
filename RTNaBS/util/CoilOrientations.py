from __future__ import annotations
import attrs
import functools
import logging
import numpy as np
import pyvista as pv
from skspatial.objects import Line, Plane, Vector
import types
import typing as tp

from RTNaBS.Navigator.Model import Session
from RTNaBS.Navigator.Model.Samples import Sample
from RTNaBS.Navigator.Model.Targets import Target
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import applyTransform, composeTransform, invertTransform, estimateAligningTransform


logger = logging.getLogger(__name__)


@attrs.define(frozen=True)
class MetricSpecification:
    getter: tp.Callable[[], float]
    units: str
    label: str


@attrs.define
class PoseMetricCalculator:
    _session: Session
    _sample: tp.Optional[Sample]

    _cachedValues: dict[str, float] = attrs.field(init=False, factory=dict)
    _supportedMetrics: list[MetricSpecification] = attrs.field(init=False, factory=list)

    sigCacheReset: Signal = attrs.field(init=False, factory=Signal)
    """
    Emitted whenever previously cached values are cleared, e.g. due to change in sample orientation, sample, or target.
    """

    def __attrs_post_init__(self):
        # self.session.MNIRegistration.sigTransformChanged.connect(lambda *args: self._clearCachedValues())  # TODO: debug, uncomment
        self.session.headModel.sigDataChanged.connect(lambda *args: self._clearCachedValues())
        self.session.targets.sigItemsChanged.connect(self._onTargetsChanged)
        self.session.subjectRegistration.sigPlannedFiducialsChanged.connect(self._onPlannedFiducialsChanged)
        if self._sample is not None:
            self._sample.sigItemChanged.connect(self._onSampleChanged)

        self._supportedMetrics.extend([
            MetricSpecification(getter=self.getTargetErrorInBrain, units=' mm', label='Target error in brain'),
            MetricSpecification(getter=self.getTargetErrorAtCoil, units=' mm', label='Target error at coil'),
            MetricSpecification(getter=self.getDepthOffsetError, units=' mm', label='Depth offset error'),
            MetricSpecification(getter=self.getDepthAngleError, units='°', label='Depth angle error'),
            MetricSpecification(getter=self.getHorizAngleError, units='°', label='Horiz angle error'),
            MetricSpecification(getter=self.getAngleFromMidline, units='°', label='Angle from midline'),
            MetricSpecification(getter=self.getAngleFromNormal, units='°', label='Angle from normal')
        ])

    def _cacheWrap(self, fn: tp.Callable[..., float]) -> float:
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
    def sample(self, newSample: Sample):
        if self._sample is newSample:
            return
        logger.debug('Sample changed')
        if self._sample is not None:
            self._sample.sigItemChanged.disconnect(self._onSampleChanged)
        self._sample = newSample
        self._sample.sigItemChanged.connect(self._onSampleChanged)
        self._clearCachedValues()

    def _onTargetsChanged(self, targetKeys: list[str], targetAttrs: tp.Optional[list[str]]):
        if self._sample is not None and self._sample.targetKey in targetKeys and \
                (targetAttrs is None or len(set(targetAttrs) - {'isVisible'}) > 0):
            self._clearCachedValues(exceptKeys=[
                self.getAngleFromMidline.cacheKey,
                self.getAngleFromNormal.cacheKey
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

    def _getTargetErrorAtDepth(self, depthFromTargetCoil: float) -> float:

        target = self._session.targets[self._sample.targetKey]

        targetLinePts_targetCoilSpace = np.asarray([[0, 0, -depthFromTargetCoil], [0, 0, -depthFromTargetCoil + 10]])
        targetLinePts_MRISpace = applyTransform(target.coilToMRITransf, targetLinePts_targetCoilSpace)

        plane = Plane(point=targetLinePts_MRISpace[0, :].squeeze(),
                      normal=np.diff(targetLinePts_MRISpace, axis=0).squeeze())

        sampleLinePts_sampleCoilSpace = np.asarray([[0, 0, 0], [0, 0, 1]])
        sampleLinePts_MRISpace = applyTransform(self._sample.coilToMRITransf, sampleLinePts_sampleCoilSpace)
        line = Line(sampleLinePts_MRISpace[0, :].squeeze(), np.diff(sampleLinePts_MRISpace, axis=0).squeeze())

        try:
            samplePtOnPlane = plane.intersect_line(line)
        except ValueError:
            # sample axis is parallel to plane
            return np.nan

        dist = np.linalg.norm(targetLinePts_MRISpace[0, :].squeeze() - samplePtOnPlane)

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
        coilOrigin_MRI = applyTransform(coilToMRITransf, np.zeros((3,)))

        # TODO: maybe use additional constraint to find distance within a small range along coil depth axis
        # (e.g. a small sphere sliding down along the depth axis until reaching cortex)
        # Currently, this may find a closest point at a very oblique angle from coil center if coil is tilted

        closestPtIndex = surf.find_closest_point(coilOrigin_MRI)
        closestPt = surf.points[closestPtIndex, :]

        if False:
            # unsigned distance
            return np.linalg.norm(closestPt - coilOrigin_MRI)
        else:
            # signed distance, where coil -Z axis pointing down to surface is positive offset
            closestPt_coilSpace = applyTransform(invertTransform(coilToMRITransf), closestPt)
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
            sampleCoilPt_sampleCoilSpace)

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
        targetLinePts_MRISpace = applyTransform(target.coilToMRITransf, targetLinePts_targetCoilSpace)
        targetVector = Vector(np.diff(targetLinePts_MRISpace, axis=0).squeeze())

        sampleLinePts_sampleCoilSpace = np.asarray([[0, 0, 0], [0, 0, 1]])
        sampleLinePts_MRISpace = applyTransform(self._sample.coilToMRITransf, sampleLinePts_sampleCoilSpace)
        sampleVector = Vector(np.diff(sampleLinePts_MRISpace, axis=0).squeeze())

        angle = targetVector.angle_between(sampleVector)

        return np.rad2deg(angle)

    getDepthAngleError.cacheKey = 'depthAngleError'

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
            sampleLinePts_sampleCoilSpace)

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

        # TODO: dynamically switch between MNI space and fiducial space depending on whether MNI transf is available
        if True:
            # use fiducial locations to define aligned coordinate space
            nas = self.session.subjectRegistration.plannedFiducials.get('NAS', None)
            lpa = self.session.subjectRegistration.plannedFiducials.get('LPA', None)
            rpa = self.session.subjectRegistration.plannedFiducials.get('RPA', None)
            if any(coord is None for coord in (nas, lpa, rpa)):
                logger.debug('Missing fiducial(s), cannot find midline axis')
                return np.nan

            centerPt = (lpa + rpa) / 2
            dirPA = nas - centerPt
            dirPA /= np.linalg.norm(dirPA)
            dirLR = rpa - lpa
            dirLR /= np.linalg.norm(dirLR)
            dirDU = np.cross(dirLR, dirPA)
            MRIToStdTransf = estimateAligningTransform(np.asarray([centerPt, centerPt + dirDU, centerPt + dirLR]),
                                                       np.asarray([[0, 0, 0], [0, 0, 1], [1, 0, 0]]))
        else:
            # TODO: use MNI transform to get midline points instead of assuming MRI is already aligned
            raise NotImplementedError

        coilLoc_stdSpace = applyTransform([self._sample.coilToMRITransf, MRIToStdTransf], np.asarray([0, 0, 0]))

        iDir = np.argmax(coilLoc_stdSpace)
        match iDir:
            case 0:
                # far left or right
                refDir1 = np.asarray([0, -1, 0])  # this handle angle corresponds to 0 degrees from midline
                refDir2 = np.asarray([0, 0, -1])  # this handle angle corresponds to +90 degrees from midline
            case 1:
                # far anterior or posterior
                refDir1 = np.asarray([0, 0, 1]) * np.sign(coilLoc_stdSpace[iDir])  # this handle angle corresponds to 0 degrees from midline
                refDir2 = np.asarray([-1, 0, 0]) * np.sign(coilLoc_stdSpace[iDir])  # this handle angle corresponds to +90 degrees from midline
            case 2:
                # far up (or down)
                refDir1 = np.asarray([0, -1, 0])  # this handle angle corresponds to 0 degrees from midline
                refDir2 = np.asarray([-1, 0, 0]) * np.sign(coilLoc_stdSpace[iDir]) # this handle angle corresponds to +90 degrees from midline
            case _:
                raise NotImplementedError

        handleDir_std = np.diff(applyTransform([self._sample.coilToMRITransf, MRIToStdTransf], np.asarray([[0, 0, 0], [0, -1, 0]])), axis=0)

        handleComp1 = np.dot(handleDir_std, refDir1)
        handleComp2 = np.dot(handleDir_std, refDir2)

        angle = np.arctan2(handleComp2, handleComp1)

        return np.rad2deg(angle).item()

    getAngleFromMidline.cacheKey = 'angleFromMidline'

    def getAngleFromNormal(self, doUseCache: bool = True) -> float:
        if doUseCache:
            return self._cacheWrap(fn=self.getAngleFromNormal)

        if self._sample is None or self._sample.coilToMRITransf is None:
            return np.nan

        gmSurf = self._session.headModel.gmSurf
        skinSurf = self._session.headModel.skinSurf
        if gmSurf is None or skinSurf is None:
            return np.nan

        assert isinstance(gmSurf, pv.PolyData)
        assert isinstance(skinSurf, pv.PolyData)

        coilOrigin_MRI = applyTransform(self._sample.coilToMRITransf, np.zeros((3,)))

        # find closest point to coil in brain
        # TODO: maybe use csf surf or some other locally smoothed surf to avoid gyri details from affecting results too much
        closestPtIndex = gmSurf.find_closest_point(coilOrigin_MRI)
        closestPt_gm = gmSurf.points[closestPtIndex, :]

        # find closest point to this brain point on the scalp
        closestPtIndex = skinSurf.find_closest_point(closestPt_gm)
        closestPt_skin = skinSurf.points[closestPtIndex, :]

        # find ideal normal by defining line through these two points
        idealNormal = Vector(closestPt_skin - closestPt_gm)
        actualNormal = Vector(np.diff(applyTransform(self._sample.coilToMRITransf, np.asarray([[0, 0, -1], [0, 0, 0,]])), axis=0).squeeze())

        angle = idealNormal.angle_between(actualNormal)

        return np.rad2deg(angle)

    getAngleFromNormal.cacheKey = 'angleFromNormal'