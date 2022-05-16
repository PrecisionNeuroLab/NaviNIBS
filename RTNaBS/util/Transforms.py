import numpy as np
import pytransform3d.transformations as ptt


def composeTransform(R: np.ndarray, p: np.ndarray) -> np.ndarray:
    return ptt.transform_from(R, p)


def applyTransform(A2B: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return ptt.transform(A2B, ptt.vectors_to_points(pts))[:, 0:3]