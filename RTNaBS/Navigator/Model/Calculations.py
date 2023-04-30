from __future__ import annotations

import logging
import numpy as np
import pyvista as pv
import pytransform3d.rotations as ptr
import pytransform3d.transformations as ptt
from skspatial.objects import Vector
import typing as tp

if tp.TYPE_CHECKING:
    from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.util.Transforms import applyTransform, composeTransform, invertTransform, estimateAligningTransform, concatenateTransforms

logger = logging.getLogger(__name__)


def getClosestPointToPointOnMesh(session: Session, whichMesh: str, point_MRISpace: np.ndarray) -> tp.Optional[np.ndarray]:
    surf = getattr(session.headModel, whichMesh)
    if surf is None:
        return None

    assert isinstance(surf, pv.PolyData)

    # find closest point to coil on surf
    closestPtIndex = surf.find_closest_point(point_MRISpace)
    closestPt = surf.points[closestPtIndex, :]

    return closestPt


def calculateAngleFromMidlineFromCoilToMRITransf(session: Session, coilToMRITransf: np.ndarray | None) -> float | None:
    if coilToMRITransf is None:
        return None

    # TODO: dynamically switch between MNI space and fiducial space depending on whether MNI transf is available
    if True:
        # use fiducial locations to define aligned coordinate space
        nas = session.subjectRegistration.fiducials.get('NAS', None)
        lpa = session.subjectRegistration.fiducials.get('LPA', None)
        rpa = session.subjectRegistration.fiducials.get('RPA', None)
        nas, lpa, rpa = tuple(fid.plannedCoord for fid in (nas, lpa, rpa))
        if any(coord is None for coord in (nas, lpa, rpa)):
            logger.debug('Missing fiducial(s), cannot find midline axis')
            return None

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

    coilLoc_stdSpace = applyTransform([coilToMRITransf, MRIToStdTransf], np.asarray([0, 0, 0]))

    iDir = np.argmax(np.abs(coilLoc_stdSpace))
    match iDir:
        case 0:
            # far left or right
            refDir1 = np.asarray([0, -1, 0])  # this handle angle corresponds to 0 degrees from midline
            refDir2 = np.asarray([0, 0, -1]) * np.sign(coilLoc_stdSpace[iDir])  # this handle angle corresponds to +90 degrees from midline
        case 1:
            # far anterior or posterior
            refDir1 = np.asarray([0, 0, 1]) * np.sign(coilLoc_stdSpace[iDir])  # this handle angle corresponds to 0 degrees from midline
            refDir2 = np.asarray([1, 0, 0])  # this handle angle corresponds to +90 degrees from midline
        case 2:
            # far up (or down)
            refDir1 = np.asarray([0, -1, 0])  # this handle angle corresponds to 0 degrees from midline
            refDir2 = np.asarray([1, 0, 0]) * np.sign(coilLoc_stdSpace[iDir])  # this handle angle corresponds to +90 degrees from midline
        case _:
            raise NotImplementedError

    handleDir_std = np.diff(applyTransform([coilToMRITransf, MRIToStdTransf], np.asarray([[0, 0, 0], [0, -1, 0]])), axis=0)

    handleComp1 = np.dot(handleDir_std, refDir1)
    handleComp2 = np.dot(handleDir_std, refDir2)

    angle = np.arctan2(handleComp2, handleComp1)

    return np.rad2deg(angle).item()


def calculateCoilToMRITransfFromTargetEntryAngle(session: Session,
                                                 targetCoord: np.ndarray | None,
                                                 entryCoord: np.ndarray | None,
                                                 angle: float | None,
                                                 depthOffset: float | None,
                                                 prevCoilToMRITransf: np.ndarray | None = None)\
        -> np.ndarray | None:
    """
    Implemented outside of Target class to allow preparing for invalidating old coilToMRITransf when targetCoord, entryCoord, angle, or depthOffset change, before actually applying that change to the instance.
    """


    if targetCoord is None:
        # estimate targetCoord from prevCoilToMRITransf
        coilOrigin_coilSpace = np.asarray([0, 0, 0])
        coilOrigin_MRISpace = applyTransform(prevCoilToMRITransf, coilOrigin_coilSpace)
        pt_gm = getClosestPointToPointOnMesh(session=session,
                                             whichMesh='gmSurf',
                                             point_MRISpace=coilOrigin_MRISpace)
        # estimate gm pt by projecting same distance down coilToMRITransf depth axis
        coilToCortexDist = np.linalg.norm(coilOrigin_MRISpace - pt_gm)
        target_coilSpace = np.asarray([0, 0, -coilToCortexDist])
        targetCoord = applyTransform(prevCoilToMRITransf, target_coilSpace)

    if entryCoord is None:
        coilOrigin_coilSpace = np.asarray([0, 0, 0])
        coilOrigin_MRISpace = applyTransform(prevCoilToMRITransf, coilOrigin_coilSpace)
        entryCoord = coilOrigin_MRISpace

    if angle is None:
        angle = calculateAngleFromMidlineFromCoilToMRITransf(session=session, coilToMRITransf=prevCoilToMRITransf)

    logger.debug(f'Target angle: {angle}')

    if depthOffset is None:
        depthOffset = 0

    vec_targetDepth = Vector(entryCoord - targetCoord)
    vec_defaultCoilDepth = Vector([0, 0, 1])
    vec_rotAxis = vec_defaultCoilDepth.cross(vec_targetDepth)
    rotAngle = vec_targetDepth.angle_signed_3d(vec_defaultCoilDepth, vec_rotAxis)
    coilToMRITransf = np.eye(4)
    coilToMRITransf[:3, :3] = ptr.matrix_from_axis_angle(np.append(vec_rotAxis, -rotAngle))  # TODO: double check sign
    coilToMRITransf[:3, 3] = entryCoord
    coilToMRITransf[:3, 3] = applyTransform(coilToMRITransf, np.asarray([0, 0, depthOffset]))

    # determine how much to rotate coil handle to get desired angle from midline
    intermediateAngleFromMidline = calculateAngleFromMidlineFromCoilToMRITransf(session=session,
                                                                                coilToMRITransf=coilToMRITransf)
    angleDiff = angle - intermediateAngleFromMidline
    logger.debug(f'angleFromMidline: {intermediateAngleFromMidline} angleDiff: {angleDiff}')
    vec_rotAxis = np.asarray([0, 0, 1])  # TODO: check sign
    coilToMRITransf[:3, :3] = coilToMRITransf[:3, :3] @ ptr.matrix_from_axis_angle(np.append(vec_rotAxis, np.deg2rad(angleDiff)))

    # in case sign was flipped above, correct again but with opposite sign;
    # if correction was correct above, this delta should be zero and additional rotation should have no effect
    # (note: this could be made more computationally efficient)
    intermediateAngleFromMidline = calculateAngleFromMidlineFromCoilToMRITransf(session=session,
                                                                                coilToMRITransf=coilToMRITransf)
    angleDiff = angle - intermediateAngleFromMidline
    logger.debug(f'angleFromMidline: {intermediateAngleFromMidline} angleDiff: {angleDiff}')
    vec_rotAxis = np.asarray([0, 0, -1])
    coilToMRITransf[:3, :3] = coilToMRITransf[:3, :3] @ ptr.matrix_from_axis_angle(np.append(vec_rotAxis, np.deg2rad(angleDiff)))

    if True:
        # TODO: debug, delete
        intermediateAngleFromMidline = calculateAngleFromMidlineFromCoilToMRITransf(session=session,
                                                                                    coilToMRITransf=coilToMRITransf)
        angleDiff = angle - intermediateAngleFromMidline
        logger.debug(f'angleFromMidline: {intermediateAngleFromMidline} angleDiff: {angleDiff}')
        if abs(angleDiff) > 10:
            logger.warning('Problem setting angle')

    logger.debug(f'newCoilToMRITransf: {coilToMRITransf}')

    return coilToMRITransf
