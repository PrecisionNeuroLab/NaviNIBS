import numpy as np
import pytest
import pytransform3d.rotations as ptr
import pytransform3d.transformations as ptt

from RTNaBS.util.numpy import array_equalish
from RTNaBS.util.Transforms import transformToString, stringToTransform, composeTransform, invertTransform, applyTransform, estimateAligningTransform


@pytest.fixture
def transf1():
    transf = np.eye(4)
    transf[:3, :3] = ptr.active_matrix_from_extrinsic_euler_xyx([0, 1, 2])
    transf[:3, 3] = np.asarray([1, 2, 3])
    return transf


@pytest.fixture
def transf2():
    transf = np.eye(4)
    transf[:3, :3] = ptr.active_matrix_from_extrinsic_euler_xyx([3, 2, 1])
    transf[:3, 3] = np.asarray([4, 5, 6])
    return transf


@pytest.fixture(params=[0, 1])
def transf(transf1, transf2, request):
    match request.param:
        case 0:
            return transf1
        case 1:
            return transf2
        case _:
            raise NotImplementedError()


@pytest.fixture
def pts1():
    return np.asarray([10, 11, 12])


@pytest.fixture
def pts2(pts1):
    return pts1[np.newaxis, :]


@pytest.fixture
def pts3():
    return np.random.rand(20, 3)


@pytest.fixture(params=[0, 1, 2])
def pts(pts1, pts2, pts3, request):
    match request.param:
        case 0:
            return pts1
        case 1:
            return pts2
        case 2:
            return pts3
        case _:
            raise NotImplementedError()


def test_transfToFromString(transf):
    transfStr = transformToString(transf)
    assert isinstance(transfStr, str)
    transf_roundtrip = stringToTransform(transfStr)
    assert isinstance(transf_roundtrip, np.ndarray)
    assert array_equalish(transf, transf_roundtrip)


def test_invertTransform(transf):
    assert array_equalish(np.linalg.pinv(transf), invertTransform(transf))
    assert array_equalish(transf, invertTransform(invertTransform(transf)))


def test_composeTransform(transf):
    assert array_equalish(transf, composeTransform(transf[:3, :3], transf[:3, 3]))
    tmp = transf.copy()
    tmp[:3, 3] = 0
    assert array_equalish(tmp, composeTransform(transf[:3, :3]))


def test_applyTransform(transf1, transf2, pts):
    transfPts_a = applyTransform(transf2, applyTransform(transf1, pts))
    transfPts_b = applyTransform([transf1, transf2], pts)
    assert array_equalish(transfPts_a, transfPts_b)
    untransfPts = applyTransform(np.linalg.pinv(transf1), applyTransform(np.linalg.pinv(transf2), transfPts_a))
    assert array_equalish(pts, untransfPts)


@pytest.mark.parametrize("method", ['kabsch-svd'])
def test_estimateAligningTransf(transf, pts, method):
    transfPts = applyTransform(transf, pts)
    if pts.ndim < 2 or pts.shape[1] != 3 or pts.shape[0] < 3:
        with pytest.raises(ValueError):
            estTransf = estimateAligningTransform(pts, transfPts, method=method)
        return
    else:
        estTransf = estimateAligningTransform(pts, transfPts, method=method)
    assert array_equalish(estTransf, transf)
    transfPts2 = applyTransform(estTransf, pts)
    assert array_equalish(transfPts, transfPts2)

