from __future__ import annotations

import logging
from typing import ClassVar

import attrs
import numpy as np

from NaviNIBS.Navigator.Model.GenericCollection import listItemAttrSetter
from NaviNIBS.Navigator.Model.ROIs import ROI, SurfaceMeshROI
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages import ROIStage
from NaviNIBS.Navigator.Model.Calculations import getClosestPointToPointOnMesh
from NaviNIBS.util.Transforms import invertTransform, applyTransform, composeTransform


logger = logging.getLogger(__name__)


@attrs.define(eq=False)
class AddFromTwoTargets(ROIStage):
    """
    Add an elliptical region to a surface-mesh ROI, defined by two targets.

    Each target's coil origin is projected onto the input ROI's mesh; the
    ellipse's major axis runs through those two surface-projected points, and
    the minor-axis width is proportional to the distance between them.
    """
    type: ClassVar[str] = 'AddFromTwoTargets'

    _target1Key: str | None = None
    """
    Key of the first Target in the session's targets collection.
    """

    _target2Key: str | None = None
    """
    Key of the second Target in the session's targets collection.
    """

    _minorAxisRatio: float = 0.5
    """
    Ratio of the ellipse's full minor-axis width to the distance between the two
    projected target points plus any padding.
    """

    _majorAxisPadding: float = 0.
    """
    Extra length (mm) added to the semi-major axis beyond half the distance
    between the two projected target points. Zero means the ellipse ends exactly
    at the two projected points.
    """

    _depthThickness: float = 20.
    """
    Thickness of elliptical cylinder (mm), centered at the mesh surface near the
    midpoint between the two projected target points.
    """

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def target1Key(self):
        return self._target1Key

    @target1Key.setter
    def target1Key(self, newKey: str | None):
        if self._target1Key == newKey:
            return
        self._disconnectFromTargets()
        logger.info(f'Setting AddFromTwoTargets target1Key to {newKey}')
        self.sigItemAboutToChange.emit(self, ['target1Key'])
        self._target1Key = newKey
        self.sigItemChanged.emit(self, ['target1Key'])
        self._connectToTargets()

    @property
    def target2Key(self):
        return self._target2Key

    @target2Key.setter
    def target2Key(self, newKey: str | None):
        if self._target2Key == newKey:
            return
        self._disconnectFromTargets()
        logger.info(f'Setting AddFromTwoTargets target2Key to {newKey}')
        self.sigItemAboutToChange.emit(self, ['target2Key'])
        self._target2Key = newKey
        self.sigItemChanged.emit(self, ['target2Key'])
        self._connectToTargets()

    @property
    def minorAxisRatio(self):
        return self._minorAxisRatio

    @minorAxisRatio.setter
    @listItemAttrSetter()
    def minorAxisRatio(self, newValue: float):
        pass

    @property
    def majorAxisPadding(self):
        return self._majorAxisPadding

    @majorAxisPadding.setter
    @listItemAttrSetter()
    def majorAxisPadding(self, newValue: float):
        pass

    @property
    def depthThickness(self):
        return self._depthThickness

    @depthThickness.setter
    @listItemAttrSetter()
    def depthThickness(self, newValue: float):
        pass

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSession):
        if self._session is newSession:
            return
        self._disconnectFromTargets()
        self.sigItemAboutToChange.emit(self, ['session'])
        self._session = newSession
        self.sigItemChanged.emit(self, ['session'])
        self._connectToTargets()

    @property
    def _targetKeys(self) -> tuple[str | None, str | None]:
        return self._target1Key, self._target2Key

    def _connectToTargets(self):
        if self._session is None:
            return
        for targetKey in self._targetKeys:
            if targetKey is not None and targetKey in self._session.targets:
                self._session.targets[targetKey].sigItemChanged.connect(self._onTargetChanged)

    def _disconnectFromTargets(self):
        if self._session is None:
            return
        for targetKey in self._targetKeys:
            if targetKey is not None and targetKey in self._session.targets:
                try:
                    self._session.targets[targetKey].sigItemChanged.disconnect(self._onTargetChanged)
                except Exception:
                    pass

    def _onTargetChanged(self, key, changedAttrs=None):
        if changedAttrs is not None:
            changedAttrs = changedAttrs.copy()
            # ignore irrelevant attribute changes
            for k in ('isSelected', 'isVisible', 'isHistorical', 'mayBeADependency', 'color'):
                try:
                    changedAttrs.remove(k)
                except ValueError:
                    pass
            if len(changedAttrs) == 0:
                # nothing left of interest
                return
        self.sigItemChanged.emit(self, ['targetUpdate'])

    def _process(self, roiKey: str, inputROI: ROI | None) -> SurfaceMeshROI:
        logger.debug(f'Generating ROI from two targets: {self._target1Key}, {self._target2Key} '
                     f'with minorAxisRatio={self._minorAxisRatio}, majorAxisPadding={self._majorAxisPadding}')

        if self._target1Key is None or self._target2Key is None:
            logger.warning('Both target keys must be specified, returning input ROI unchanged')
            return inputROI
        if self._session is None:
            logger.warning('No session available, returning input ROI unchanged')
            return inputROI

        for targetKey in self._targetKeys:
            if targetKey not in self._session.targets:
                logger.warning(f'Target key {targetKey!r} not found in session targets, returning input ROI unchanged')
                return inputROI

        target1 = self._session.targets[self._target1Key]
        target2 = self._session.targets[self._target2Key]
        coilToMRITransf1 = target1.coilToMRITransf
        coilToMRITransf2 = target2.coilToMRITransf
        if coilToMRITransf1 is None or coilToMRITransf2 is None:
            logger.warning('At least one target has no coilToMRITransf, returning input ROI unchanged')
            return inputROI

        assert isinstance(inputROI, SurfaceMeshROI)
        assert inputROI.meshKey is not None

        mesh = getattr(self._session.headModel, inputROI.meshKey)

        # project each target's coil origin onto the mesh surface
        origin1_MRISpace = applyTransform(coilToMRITransf1, np.zeros(3), doCheck=False)
        origin2_MRISpace = applyTransform(coilToMRITransf2, np.zeros(3), doCheck=False)

        closestPt1 = getClosestPointToPointOnMesh(
            session=self._session, whichMesh=inputROI.meshKey, point_MRISpace=origin1_MRISpace)
        closestPt2 = getClosestPointToPointOnMesh(
            session=self._session, whichMesh=inputROI.meshKey, point_MRISpace=origin2_MRISpace)

        if closestPt1 is None or closestPt2 is None:
            logger.warning('Could not project a target onto the mesh, returning input ROI unchanged')
            return inputROI

        center_MRISpace = (closestPt1 + closestPt2) / 2
        majorVec = closestPt2 - closestPt1
        dist = float(np.linalg.norm(majorVec))
        if dist == 0:
            logger.warning('The two projected target points coincide, returning input ROI unchanged')
            return inputROI
        majorDir = majorVec / dist

        # build an orthonormal frame: major axis exact, depth axis as close as
        # possible to the average coil "up/out-of-head" direction
        upDir = coilToMRITransf1[:3, 2] + coilToMRITransf2[:3, 2]
        if np.linalg.norm(np.cross(upDir, majorDir)) < 1e-6:
            # upDir nearly parallel to majorDir; pick an arbitrary perpendicular axis
            fallback = np.array([1., 0., 0.]) if abs(majorDir[0]) < 0.9 else np.array([0., 1., 0.])
            upDir = fallback
        yDir = np.cross(upDir, majorDir)
        yDir = yDir / np.linalg.norm(yDir)
        zDir = np.cross(majorDir, yDir)
        zDir = zDir / np.linalg.norm(zDir)

        R = np.column_stack((majorDir, yDir, zDir))
        twoTargetToMRITransf = composeTransform(R, center_MRISpace)
        MRIToTwoTargetTransf = invertTransform(twoTargetToMRITransf)

        localPts = applyTransform(MRIToTwoTargetTransf, mesh.points, doCheck=False)

        a = dist / 2 + self._majorAxisPadding
        b = self._minorAxisRatio * a

        if a <= 0 or b <= 0:
            logger.warning('Degenerate ellipse dimensions, returning input ROI unchanged')
            return inputROI

        ellipseCheck = (localPts[:, 0] / a) ** 2 + (localPts[:, 1] / b) ** 2 <= 1

        # depth slab centered at the local-z of the mesh surface near the center
        closestPtCenter = getClosestPointToPointOnMesh(
            session=self._session, whichMesh=inputROI.meshKey, point_MRISpace=center_MRISpace)

        if closestPtCenter is not None:
            zSurf = applyTransform(MRIToTwoTargetTransf, closestPtCenter, doCheck=False)[2]
            depthCheck = np.abs(localPts[:, 2] - zSurf) <= self._depthThickness / 2
            mask = ellipseCheck & depthCheck
        else:
            mask = ellipseCheck

        newVertexIndices = np.where(mask)[0]

        outputROI = inputROI.copy()
        outputROI.session = self._session

        if inputROI.meshVertexIndices is not None:
            newVertexIndices = np.union1d(inputROI.meshVertexIndices, newVertexIndices)

        if len(newVertexIndices) == 0:
            newVertexIndices = None

        outputROI.meshVertexIndices = newVertexIndices

        if outputROI.seedCoord is None and closestPtCenter is not None:
            outputROI.seedCoord = tuple(closestPtCenter)

        return outputROI
