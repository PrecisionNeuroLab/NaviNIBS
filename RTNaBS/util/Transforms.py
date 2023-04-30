import json
import sys

import numpy as np
import pytransform3d.transformations as ptt
import typing as tp


def composeTransform(R: np.ndarray, p: tp.Optional[np.ndarray] = None) -> np.ndarray:
    if p is None:
        p = np.zeros((3,))
    return ptt.transform_from(R, p)


def decomposeTransform(A2B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Extract translation and rotation components from transform. Assume the transform is rigid.
    :return: (3x3 rotation matrix, 3-elem translation vector)
    """
    R = A2B[0:3, 0:3]
    T = A2B[0:3, 3]
    return R, T


def applyTransform(A2B: tp.Union[np.ndarray, tp.Iterable[np.ndarray]], pts: np.ndarray, doStrictCheck: bool = True) -> np.ndarray:
    """
    Apply 4x4 transform(s) to a set of points
    :param A2B: single 4x4 transform, or iterable of 4x4 transforms. If multiple, will apply in reversed order, so that
        `applyTransform([space1ToSpace2Transf, space2TransfToSpace3Transf], pts)` correctly transforms from space1 to space3
        as might be expected with `space2TransfToSpace3Transf @ space1ToSpace2Transf @ augmentedPts`
    :param pts: Nx3 points. Or can be in shape (3,) and will return transformed points with same shape.
    :return: transformed points
    """
    if pts.ndim == 1:
        didInsertAxis = True
        pts = pts[np.newaxis, :]
    else:
        didInsertAxis = False
        assert pts.shape[1] == 3

    if not isinstance(A2B, np.ndarray):
        A2B = concatenateTransforms(A2B)

    result = ptt.transform(A2B, ptt.vectors_to_points(pts), strict_check=doStrictCheck)[:, 0:3]
    if didInsertAxis:
        result = result[0, :]
    return result


def concatenateTransforms(A2B: tp.Iterable[np.ndarray]) -> np.ndarray:
    """
    Combine transforms in **reverse** order, using same ordering convention as `applyTransform`, such that
    `applyTransform(concatenateTransforms([space1ToSpace2Transf, space2TransfToSpace3Transf]), pts)` correctly transforms from space1 to space3
    as might be expected with `space2TransfToSpace3Transf @ space1ToSpace2Transf @ augmentedPts`
    """
    A2B_combined = np.eye(4)
    for A2B_i in A2B:
        A2B_combined = A2B_i @ A2B_combined
    return A2B_combined


def invertTransform(A2B: np.ndarray) -> np.ndarray:
    return np.linalg.pinv(A2B)


def transformToString(A2B: np.ndarray, precision=12) -> str:
    return json.dumps(A2B.round(decimals=precision).tolist())


def stringToTransform(inputStr: str) -> np.ndarray:
    """
    Raises ValueError if unable to convert to valid transform
    :param inputStr: str representation of a transform, e.g. from
        `transformToString(np.asarray([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]))`
    :return: transform as 4x4 ndarray
    """
    listVal = json.loads(inputStr)
    if not isinstance(listVal, list):
        raise ValueError('Expected list of lists')
    arrayVal = np.asarray(listVal, dtype=np.float64)  # raises value error if problem
    ptt.check_transform(arrayVal)
    return arrayVal


def calculateRotationMatrixFromTwoVectors(vecA: np.ndarray, vecB: np.ndarray) -> np.ndarray:
    """
    Calculate rotationg such that X-axis points in direction of vecA, Y-axis points in direction of (oproj_vecA vecB)
    adapted from https://rock-learning.github.io/pytransform3d/_apidoc/pytransform3d.rotations.matrix_from_two_vectors.html

    :param vecA:
    :param vecB:
    :return: 3x3 rotation matrix
    """
    vecA = vecA / np.linalg.norm(vecA)
    vecB = vecB / np.linalg.norm(vecB)

    assert not np.array_equal(vecA, vecB)

    dirB = vecB - vecA * np.dot(vecA, vecB)

    dirC = np.cross(vecA, dirB)

    return np.column_stack((vecA, dirB, dirC))


def estimateAligningTransform(ptsA: np.ndarray, ptsB: np.ndarray, method: str = 'kabsch-svd', weights: tp.Optional[np.ndarray] = None) -> np.ndarray:
    """
    Estimate a transform that aligns one set of points onto another.

    Some methods assume row-wise matching of points between sets.
    
    For details of the kabsch-svd method, see:
     - https://stackoverflow.com/questions/60877274/optimal-rotation-in-3d-with-kabsch-algorithm
     - https://zpl.fi/aligning-point-patterns-with-kabsch-umeyama-algorithm/
     - http://nghiaho.com/?page_id=671
    
    :param ptsA: Nx3 ndarray of points 
    :param ptsB: Mx3 ndarray of points
    :param method: method to use for estimating transform. Default is 'kabsch-svd''
    :param weights: default None, otherwise format depends on method:
        if method == 'kabsch-svd':
            Kabsch weighted algorithm will be used. Weights should be of length equal to number of points in ptsA and ptsB.
        elif method == 'ICP':
            Weights should be of length 6, with values as defined by simpleicp's `rbp_observation_weights` argument.
    :return: A2B, 4x4 transform aligning ptsA to ptsB 
    """

    match method:
        case 'kabsch-svd':
            if ptsA.shape != ptsB.shape:
                raise ValueError('ptsA and ptsB should be matched sizes!')

            if any(pts.ndim < 2 or pts.shape[1] != 3 for pts in (ptsA, ptsB)):
                raise ValueError('pts should be of size Nx3')

            if ptsA.shape[0] < 3:
                raise ValueError('Need at least 3 points to estimate aligning transform')

            if weights is None:
                # adapted from http://nghiaho.com/?page_id=671
                centroidA = ptsA.mean(axis=0)
                centroidB = ptsB.mean(axis=0)

                ptsA_ctrd = ptsA - centroidA
                ptsB_ctrd = ptsB - centroidB

                H = ptsA_ctrd.T @ ptsB_ctrd

                U, S, Vt = np.linalg.svd(H)
                R = Vt.T @ U.T

                # reflection
                if np.linalg.det(R) < 0:
                    Vt[2, :] *= -1
                    R = Vt.T @ U.T

                t = -R @ centroidA.reshape(-1, 1) + centroidB.reshape(-1, 1)

                transf = np.eye(4)
                transf[:3, :3] = R
                transf[:3, 3] = t.reshape(3)

            else:
                from rmsd import kabsch_weighted
                [R, p, _] = kabsch_weighted(ptsB, ptsA, weights)
                transf = composeTransform(R, p)

            return transf

        case 'ICP':
            runKwargs = dict()
            if weights is not None:
                assert len(weights) == 6
                runKwargs['rbp_observation_weights'] = weights

            import simpleicp

            pc_fix = simpleicp.PointCloud(ptsB, columns=('x', 'y', 'z'))
            pc_mov = simpleicp.PointCloud(ptsA, columns=('x', 'y', 'z'))
            icp = simpleicp.SimpleICP()
            icp.add_point_clouds(pc_fix, pc_mov)
            H, X_mov_transformed, rigid_body_transformation_params = icp.run(**runKwargs)

            return H

        case _:
            raise NotImplementedError()


