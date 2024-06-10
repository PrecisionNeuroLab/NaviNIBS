import attrs
import numpy as np
import typing as tp

from NaviNIBS.util.attrs import attrsAsDict


def array_equalish(a: tp.Optional[np.ndarray], b: tp.Optional[np.ndarray], *args, **kwargs):
    """
    Similar to np.allclose but with shape comparison and None support like np.array_equal
    """

    if a is None or b is None:
        return a is None and b is None

    if not isinstance(a, np.ndarray) or not isinstance(b, np.ndarray):
        return a == b

    if not np.array_equal(a.shape, b.shape):
        return False

    return np.allclose(a, b, *args, **kwargs)


C = tp.TypeVar('C')


def attrsOptionalNDArrayField(init: bool = True) -> attrs.field:
    """
    Shorthand for an attrs field like ``x: np.ndarray | None = attrs.field(default=None)`` but with functional comparison behavior
    """
    return attrs.field(default=None,
                       init=init,
                       eq=attrs.cmp_using(
                           eq=array_equalish,
                           require_same_type=False))


def attrsWithNumpyAsDict(obj: C, npFields: tp.Optional[tp.Iterable[str]] = None,
                         eqs: tp.Optional[dict[str, tp.Callable]] = None,
                         **kwargs):
    """
    Helper function for serializing collections or collection items with numpy attributes.
    """
    if eqs is None:
        eqs = dict()
    if npFields is None:
        return attrsAsDict(obj, eqs=eqs, **kwargs)
    else:
        d = attrsAsDict(obj, eqs={field: array_equalish for field in npFields} | eqs, **kwargs)

        for key in npFields:
            if key in d and d[key] is not None:
                d[key] = d[key].tolist()

        return d


def attrsWithNumpyFromDict(cls: tp.Type[C], d: dict[str, tp.Any], npFields: tp.Optional[tp.Iterable[str]] = None, **kwargs) -> C:
    def convertOptionalNDArray(val: tp.Optional[tp.List[tp.Any]]) -> tp.Optional[np.ndarray]:
        if val is None:
            return None
        else:
            return np.asarray(val)

    for attrKey in npFields:
        if attrKey in d:
            d[attrKey] = convertOptionalNDArray(d[attrKey])

    return cls(**d, **kwargs)