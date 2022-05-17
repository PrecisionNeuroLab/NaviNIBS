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