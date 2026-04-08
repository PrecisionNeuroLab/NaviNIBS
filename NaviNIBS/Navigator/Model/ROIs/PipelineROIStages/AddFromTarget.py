from __future__ import annotations

import logging
from typing import ClassVar

import attrs
import numpy as np

from NaviNIBS.Navigator.Model.GenericCollection import listItemAttrSetter
from NaviNIBS.Navigator.Model.ROIs import ROI, SurfaceMeshROI
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages import ROIStage
from NaviNIBS.Navigator.Model.Calculations import getClosestPointToPointOnMesh
from NaviNIBS.util.Transforms import invertTransform, applyTransform, decomposeTransform


logger = logging.getLogger(__name__)


@attrs.define(eq=False)
class AddFromTarget(ROIStage):
    type: ClassVar[str] = 'AddFromTarget'

    _targetKey: str | None = None
    """
    Key of the Target in the session's targets collection to use as the coil orientation reference.
    """

    _radiusX: float | None = None
    """
    Radius along the target's coil X axis (mm).
    """

    _radiusY: float | None = None
    """
    Radius along the target's coil Y axis (mm).
    """

    _depthThickness: float = 20.
    """
    Thickness of elliptical cylinder (mm), centered at depth from target to closest point on mesh surface
    """

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def targetKey(self):
        return self._targetKey

    @targetKey.setter
    def targetKey(self, newKey: str | None):
        if self._targetKey == newKey:
            return
        self._disconnectFromTarget()
        logger.info(f'Setting AddFromTarget targetKey to {newKey}')
        self.sigItemAboutToChange.emit(self, ['targetKey'])
        self._targetKey = newKey
        self.sigItemChanged.emit(self, ['targetKey'])
        self._connectToTarget()

    @property
    def radiusX(self):
        return self._radiusX

    @radiusX.setter
    @listItemAttrSetter()
    def radiusX(self, newValue: float | None):
        pass

    @property
    def radiusY(self):
        return self._radiusY

    @radiusY.setter
    @listItemAttrSetter()
    def radiusY(self, newValue: float | None):
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
        self._disconnectFromTarget()
        self.sigItemAboutToChange.emit(self, ['session'])
        self._session = newSession
        self.sigItemChanged.emit(self, ['session'])
        self._connectToTarget()

    def _connectToTarget(self):
        if self._targetKey is not None and self._session is not None:
            if self._targetKey in self._session.targets:
                self._session.targets[self._targetKey].sigItemChanged.connect(self._onTargetChanged)

    def _disconnectFromTarget(self):
        if self._targetKey is not None and self._session is not None:
            if self._targetKey in self._session.targets:
                try:
                    self._session.targets[self._targetKey].sigItemChanged.disconnect(self._onTargetChanged)
                except Exception:
                    pass

    def _onTargetChanged(self, key, changedAttrs=None):
        self.sigItemChanged.emit(self, ['targetUpdate'])

    def _process(self, roiKey: str, inputROI: ROI | None) -> SurfaceMeshROI:
        logger.debug(f'Generating ROI from target: {self._targetKey} with radiusX={self._radiusX}, radiusY={self._radiusY}')

        if self._targetKey is None:
            logger.warning('No target key specified, returning input ROI unchanged')
            return inputROI
        if self._radiusX is None:
            logger.warning('No radiusX specified, returning input ROI unchanged')
            return inputROI
        if self._radiusY is None:
            logger.warning('No radiusY specified, returning input ROI unchanged')
            return inputROI
        if self._session is None:
            logger.warning('No session available, returning input ROI unchanged')
            return inputROI

        if self._targetKey not in self._session.targets:
            logger.warning(f'Target key {self._targetKey!r} not found in session targets, returning input ROI unchanged')
            return inputROI

        target = self._session.targets[self._targetKey]
        coilToMRITransf = target.coilToMRITransf
        if coilToMRITransf is None:
            logger.warning(f'Target {self._targetKey!r} has no coilToMRITransf, returning input ROI unchanged')
            return inputROI

        assert isinstance(inputROI, SurfaceMeshROI)
        assert inputROI.meshKey is not None

        mesh = getattr(self._session.headModel, inputROI.meshKey)

        _, origin = decomposeTransform(coilToMRITransf)

        closestPt = getClosestPointToPointOnMesh(
            session=self._session,
            whichMesh=inputROI.meshKey,
            point_MRISpace=origin
        )

        centerDepth = None
        if closestPt is not None:
            centerDepth = np.linalg.norm(closestPt - origin)

        MRIToCoilTransf = invertTransform(coilToMRITransf)
        localPts = applyTransform(MRIToCoilTransf, mesh.points, doCheck=False)

        ellipseCheck = (localPts[:, 0] / self._radiusX) ** 2 + (localPts[:, 1] / self._radiusY) ** 2 <= 1

        if centerDepth is not None:
            depthCheck = (localPts[:, 2] < -centerDepth + self._depthThickness/2) \
                         & (localPts[:, 2] > -centerDepth - self._depthThickness/2)
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

        if outputROI.seedCoord is None and closestPt is not None:
            outputROI.seedCoord = tuple(closestPt)

        return outputROI
