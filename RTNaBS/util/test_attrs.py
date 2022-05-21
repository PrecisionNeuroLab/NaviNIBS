import attrs
from .attrs import attrsAsDict
from .numpy import array_equalish
import numpy as np
import pytest
import typing as tp


@attrs.define
class ExampleClass:
    aStr: str = 'test'
    anOptionalStr: tp.Optional[str] = None
    anOptionalArray: tp.Optional[np.ndarray] = attrs.field(factory=lambda: np.zeros((2, 2)))
    _aPrivateOptionalArray: tp.Optional[np.ndarray] = attrs.field(default=None)
    _aPrivateInt: int = 0


@pytest.fixture
def objA():
    return ExampleClass()


@pytest.fixture
def objB():
    return ExampleClass(aStr='b',
                        anOptionalStr='b',
                        anOptionalArray=np.zeros((5, 5)),
                        aPrivateOptionalArray=np.full((2,2), 3),
                        aPrivateInt=10)


def test_equality(objA, objB):
    assert objA == ExampleClass()

    assert objA != objB

    assert ExampleClass(anOptionalArray=np.zeros((3, 3))) !=\
           ExampleClass(anOptionalArray=np.full((3, 3), 1))


def test_asdict(objA, objB):

    with pytest.raises(ValueError):
        dictA1 = attrsAsDict(objA)

    dictA = attrsAsDict(objA, eqs=dict(anOptionalArray=array_equalish,
                                       aPrivateOptionalArray=array_equalish))

    assert len(dictA) == 0

    dictB = attrsAsDict(objB, eqs=dict(anOptionalArray=array_equalish,
                                       aPrivateOptionalArray=array_equalish))

    assert len(dictB) == 5








