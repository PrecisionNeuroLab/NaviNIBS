import numpy as np
import typing as tp


def array_equalish(a: tp.Optional[np.ndarray], b: tp.Optional[np.ndarray], *args, **kwargs):
    """
    Similar to np.allclose but with shape comparison and None support like np.array_equal
    """

    if a is None or b is None:
        return a is None and b is None

    if not np.array_equal(a.shape, b.shape):
        return False

    return np.allclose(a, b, *args, **kwargs)