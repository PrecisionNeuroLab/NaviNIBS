from __future__ import annotations

import logging
import numpy as np
import pyvista as pv
import pytransform3d.rotations as ptr
import pytransform3d.transformations as ptt
from skspatial.objects import Vector, Plane
import typing as tp
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.util.Transforms import applyTransform, composeTransform, invertTransform, estimateAligningTransform, concatenateTransforms, applyDirectionTransform
from NaviNIBS.util.pyvista.dataset import find_closest_point

logger = logging.getLogger(__name__)


def getClosestPointToPointOnMesh(session: Session, whichMesh: str, point_MRISpace: np.ndarray) -> tp.Optional[np.ndarray]:
    surf = getattr(session.headModel, whichMesh)
    if surf is None:
        return None

    assert isinstance(surf, pv.PolyData)

    # find closest point to coil on surf
    closestPtIndex = find_closest_point(surf, point_MRISpace)
    closestPt = surf.points[closestPtIndex, :]

    return closestPt


def calculateMidlineRefDirectionsFromCoilToMRITransf(session: Session, coilToMRITransf: np.ndarray | None) -> tuple[np.ndarray, np.ndarray] | tuple[None, None]:
    """
    Calculate the reference directions for angle=0 and angle=+90 degrees from midline, in the MRI space.

    Note that these directions are dependent on the coilToMRITransf, since the definition of "midline" can differ when on top of the head vs. extreme left/right vs. extreme anterior/posterior.

    :return
        refDir1: handle angle (i.e. coil's -y axis) corresponding to 0 degrees from midline
        refDir2: handle angle (i.e. coil's -y axis) corresponding to +90 degrees from midline
        May return (None, None) if coilToMRITransf is None.
    """

    if coilToMRITransf is None:
        return None, None

    # TODO: dynamically switch between MNI space and fiducial space depending on whether MNI transf is available
    if True:
        # use fiducial locations to define aligned coordinate space
        nas = session.subjectRegistration.fiducials.get('NAS', None)
        lpa = session.subjectRegistration.fiducials.get('LPA', None)
        rpa = session.subjectRegistration.fiducials.get('RPA', None)
        nas, lpa, rpa = tuple(fid.plannedCoord for fid in (nas, lpa, rpa))
        if any(coord is None for coord in (nas, lpa, rpa)):
            logger.debug('Missing fiducial(s), cannot find midline axis')
            return None, None

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

    coilLoc_stdSpace = applyTransform([coilToMRITransf, MRIToStdTransf], np.asarray([0, 0, 0]), doCheck=False)

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

    # convert refDirs to MRI space
    refDir1_MRI = applyDirectionTransform(invertTransform(MRIToStdTransf), refDir1, doCheck=False)
    refDir2_MRI = applyDirectionTransform(invertTransform(MRIToStdTransf), refDir2, doCheck=False)

    return refDir1_MRI, refDir2_MRI


def calculateAngleFromMidlineFromCoilToMRITransf(session: Session, coilToMRITransf: np.ndarray | None) -> float | None:
    if coilToMRITransf is None:
        return None

    refDir1_MRI, refDir2_MRI = calculateMidlineRefDirectionsFromCoilToMRITransf(session, coilToMRITransf)
    if refDir1_MRI is None or refDir2_MRI is None:
        return None

    handleDir_coilSpace = np.asarray([0, -1, 0])
    handleDir_MRI = applyDirectionTransform(coilToMRITransf, handleDir_coilSpace, doCheck=False)

    handleComp1 = np.dot(handleDir_MRI, refDir1_MRI)
    handleComp2 = np.dot(handleDir_MRI, refDir2_MRI)

    angle = np.arctan2(handleComp2, handleComp1)

    return np.rad2deg(angle).item()


def calculateCoilToMRITransfFromTargetEntryAngle(session: Session | None,
                                                 targetCoord: np.ndarray | None,
                                                 entryCoord: np.ndarray | None,
                                                 angle: float | None,
                                                 depthOffset: float | None,
                                                 prevCoilToMRITransf: np.ndarray | None = None)\
        -> np.ndarray | None:
    """
    Implemented outside of Target class to allow preparing for invalidating old coilToMRITransf when targetCoord, entryCoord, angle, or depthOffset change, before actually applying that change to the instance.
    """

    if session is None:
        return None

    if targetCoord is None:
        # estimate targetCoord from prevCoilToMRITransf
        coilOrigin_coilSpace = np.asarray([0, 0, 0])
        coilOrigin_MRISpace = applyTransform(prevCoilToMRITransf, coilOrigin_coilSpace, doCheck=False)
        pt_gm = getClosestPointToPointOnMesh(session=session,
                                             whichMesh='gmSurf',
                                             point_MRISpace=coilOrigin_MRISpace)
        # estimate gm pt by projecting same distance down coilToMRITransf depth axis
        coilToCortexDist = np.linalg.norm(coilOrigin_MRISpace - pt_gm)
        target_coilSpace = np.asarray([0, 0, -coilToCortexDist])
        targetCoord = applyTransform(prevCoilToMRITransf, target_coilSpace, doCheck=False)

    if entryCoord is None:
        coilOrigin_coilSpace = np.asarray([0, 0, 0])
        coilOrigin_MRISpace = applyTransform(prevCoilToMRITransf, coilOrigin_coilSpace, doCheck=False)
        entryCoord = coilOrigin_MRISpace

    if angle is None:
        angle = calculateAngleFromMidlineFromCoilToMRITransf(session=session, coilToMRITransf=prevCoilToMRITransf)

    logger.debug(f'Target angle: {angle}')

    if depthOffset is None:
        depthOffset = 0

    # determine rotation axis and angle to align with desired entry direction
    vec_targetDepth = Vector(entryCoord - targetCoord)
    vec_defaultCoilDepth = Vector([0, 0, 1])
    vec_rotAxis = vec_defaultCoilDepth.cross(vec_targetDepth)
    rotAngle = vec_targetDepth.angle_signed_3d(vec_defaultCoilDepth, vec_rotAxis)
    coilToMRITransf = np.eye(4)
    coilToMRITransf[:3, :3] = ptr.matrix_from_axis_angle(np.append(vec_rotAxis, -rotAngle))  # TODO: double check sign
    coilToMRITransf[:3, 3] = entryCoord
    coilToMRITransf[:3, 3] = applyTransform(coilToMRITransf, np.asarray([0, 0, depthOffset]), doCheck=False)

    # determine how much to rotate coil handle to get desired angle from midline
    refDir1_MRI, refDir2_MRI = calculateMidlineRefDirectionsFromCoilToMRITransf(session, coilToMRITransf)
    #logger.debug(f'refDir1_MRI: {refDir1_MRI} refDir2_MRI: {refDir2_MRI}')

    refDir1_coil = Vector(applyDirectionTransform(invertTransform(coilToMRITransf), refDir1_MRI, doCheck=False))
    refDir2_coil = Vector(applyDirectionTransform(invertTransform(coilToMRITransf), refDir2_MRI, doCheck=False))
    #logger.debug(f'refDir1_coil: {refDir1_coil} refDir2_coil: {refDir2_coil}')
    refPlaneNormal_coil = refDir1_coil.cross(refDir2_coil)
    #logger.debug(f'refPlaneNormal_coil: {refPlaneNormal_coil}')
    # note: this reference plane is likely tilted out of the XY plane of the coil space
    rotHandleInRefPlaneTransf = np.eye(4)
    rotHandleInRefPlaneTransf[:3, :3] = ptr.matrix_from_axis_angle(np.append(refPlaneNormal_coil, np.deg2rad(angle)))
    handleDirInRefPlane_coil = Vector(applyDirectionTransform(rotHandleInRefPlaneTransf, refDir1_coil, doCheck=False))
    #logger.debug(f'handleDirInRefPlane_coil: {handleDirInRefPlane_coil}')
    if True:
        vec_rotAxis = refPlaneNormal_coil.cross(handleDirInRefPlane_coil)
        projectWithinPlane = Plane.from_vectors(np.asarray([0, 0, 0]), handleDirInRefPlane_coil, refPlaneNormal_coil)
        xyPlane_coil = Plane.from_vectors(np.asarray([0, 0, 0]), np.asarray([1, 0, 0]), np.asarray([0, 1, 0]))
        handleLineInCoilPlane_coil = projectWithinPlane.intersect_plane(xyPlane_coil)
        handleDirInCoilPlane_coil = handleLineInCoilPlane_coil.direction
        if handleDirInCoilPlane_coil.dot(handleDirInRefPlane_coil) < 0:
            handleDirInCoilPlane_coil = -handleDirInCoilPlane_coil
    else:
        # project this handle direction onto the XY plane of the coil space
        handleDirInCoilPlane_coil = handleDirInRefPlane_coil.copy()
        handleDirInCoilPlane_coil[2] = 0
    logger.debug(f'handleDirInCoilPlane_coil: {handleDirInCoilPlane_coil}')

    initialHandleDir_coil = Vector([0, -1, 0])
    vec_rotAxis = np.asarray([0, 0, 1])  # TODO: check sign
    targetHandleAngle = initialHandleDir_coil.angle_signed_3d(handleDirInCoilPlane_coil, vec_rotAxis)
    logger.debug(f'targetHandleAngle: {np.rad2deg(targetHandleAngle)}')

    coilToMRITransf[:3, :3] = coilToMRITransf[:3, :3] @ ptr.matrix_from_axis_angle(np.append(vec_rotAxis, targetHandleAngle))

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
