import json
import sys

import numpy as np
import pytransform3d.transformations as ptt
import typing as tp


def composeTransform(R: np.ndarray, p: tp.Optional[np.ndarray] = None) -> np.ndarray:
    if p is None:
        p = np.zeros((3,))
    return ptt.transform_from(R, p)


def applyTransform(A2B: tp.Union[np.ndarray, tp.Iterable[np.ndarray]], pts: np.ndarray) -> np.ndarray:
    """
    Apply 4x4 transform(s) to a set of points
    :param A2B: single 4x4 transform, or iterable of 4x4 transforms. If multiple, will apply in reversed order, so that
        `applyTransform([space1ToSpace2Transf, space2TransfToSpace3Transf], pts)` correctly transforms from space1 to space3
        as might be expected with `space2TransfToSpace3Transf @ space1ToSpace2Transf @ augmentedPts`
    :param pts: Nx3 points. Or can be in shape (3,) and will return transformed points with same shape.
    :return: transform points
    """
    if pts.ndim == 1:
        didInsertAxis = True
        pts = pts[np.newaxis, :]
    else:
        didInsertAxis = False
        assert pts.shape[1] == 3

    if not isinstance(A2B, np.ndarray):
        A2B = concatenateTransforms(A2B)

    result = ptt.transform(A2B, ptt.vectors_to_points(pts))[:, 0:3]
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


def estimateAligningTransform(ptsA: np.ndarray, ptsB: np.ndarray, method: str = 'kabsch-svd') -> np.ndarray:
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
    :return: A2B, 4x4 transform aligning ptsA to ptsB 
    """

    # adapted from http://nghiaho.com/?page_id=671

    match method:
        case 'kabsch-svd':
            if ptsA.shape != ptsB.shape:
                raise ValueError('ptsA and ptsB should be matched sizes!')

            if any(pts.ndim < 2 or pts.shape[1] != 3 for pts in (ptsA, ptsB)):
                raise ValueError('pts should be of size Nx3')

            if ptsA.shape[0] < 3:
                raise ValueError('Need at least 3 points to estimate aligning transform')

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

            return transf

        case 'ICP':
            import simpleicp

            pc_fix = simpleicp.PointCloud(ptsB, columns=('x', 'y', 'z'))
            pc_mov = simpleicp.PointCloud(ptsA, columns=('x', 'y', 'z'))
            icp = simpleicp.SimpleICP()
            icp.add_point_clouds(pc_fix, pc_mov)
            H, X_mov_transformed, rigid_body_transformation_params = icp.run()

            return H

        case _:
            raise NotImplementedError()


