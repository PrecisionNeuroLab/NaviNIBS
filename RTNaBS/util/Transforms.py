import json
import sys

import numpy as np
import pytransform3d.transformations as ptt
import typing as tp


def composeTransform(R: np.ndarray, p: tp.Optional[np.ndarray] = None) -> np.ndarray:
    if p is None:
        p = np.zeros((3,))
    return ptt.transform_from(R, p)


def applyTransform(A2B: np.ndarray, pts: np.ndarray) -> np.ndarray:
    if pts.ndim == 1:
        didInsertAxis = True
        pts = pts[np.newaxis, :]
    else:
        didInsertAxis = False
    result = ptt.transform(A2B, ptt.vectors_to_points(pts))[:, 0:3]
    if didInsertAxis:
        result = result[0, :]
    return result


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
