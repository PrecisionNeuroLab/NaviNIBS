import numpy as np
import pytest
import pytransform3d.rotations as ptr
import pytransform3d.transformations as ptt

from RTNaBS.util.numpy import array_equalish
from RTNaBS.util.Transforms import transformToString, stringToTransform, composeTransform, invertTransform


@pytest.fixture
def transf1():
    transf = np.eye(4)
    transf[:3, :3] = ptr.active_matrix_from_extrinsic_euler_xyx([0, 1, 2])
    transf[:3, 3] = np.asarray([1, 2, 3])
    return transf


def test_transfToFromString(transf1):
    transfStr = transformToString(transf1)
    assert isinstance(transfStr, str)
    transf1_roundtrip = stringToTransform(transfStr)
    assert isinstance(transf1_roundtrip, np.ndarray)
    assert array_equalish(transf1, transf1_roundtrip)


def test_invertTransform(transf1):
    assert array_equalish(np.linalg.pinv(transf1), invertTransform(transf1))
    assert array_equalish(transf1, invertTransform(invertTransform(transf1)))


def test_composeTransform(transf1):
    assert array_equalish(transf1, composeTransform(transf1[:3, :3], transf1[:3, 3]))
    tmp = transf1.copy()
    tmp[:3, 3] = 0
    assert array_equalish(tmp, composeTransform(transf1[:3, :3]))