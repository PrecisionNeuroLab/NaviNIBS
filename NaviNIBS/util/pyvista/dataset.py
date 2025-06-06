import collections.abc
import typing as tp
import numpy as np
import pyvista as pv
from pyvista import _vtk
if pv.__version__ <= '0.39.1':
    from pyvista.utilities.helpers import vtk_id_list_to_array
else:
    from pyvista.core.utilities import vtk_id_list_to_array


def find_closest_point(dataset: pv.DataSet, point: tp.Iterable, n: int = 1) -> int:
    """
    Similar to pv.DataSet.find_closest_point, but monkey-patches a cache of the
    vtkPointLocator to speed up repeated calls. It turns out this construction
    is more expensive than the actual find.

    Note: the cache is not automatically reset by changes to the dataset, so may break
    (or give incorrect results) if dataset is changed.
    """

    if not isinstance(point, (np.ndarray, collections.abc.Sequence)) or len(point) != 3:
        raise TypeError("Given point must be a length three sequence.")
    if not isinstance(n, int):
        raise TypeError("`n` must be a positive integer.")
    if n < 1:
        raise ValueError("`n` must be a positive integer.")

    if hasattr(dataset, '_point_locator'):
        locator = dataset._point_locator
    else:
        locator = _vtk.vtkPointLocator()
        locator.SetDataSet(dataset)
        locator.BuildLocator()
        dataset._point_locator = locator

        # must also monkey-patch getstate to not break pickling
        dataset.__getstate__orig = dataset.__getstate__
        def __getstate__(self):
            state = self.__getstate__orig()
            for key in ('_point_locator', '__getstate__orig', '__getstate__'):
                del state[key]
            return state
        dataset.__getstate__ = __getstate__.__get__(dataset, pv.DataSet)


    if n > 1:
        id_list = _vtk.vtkIdList()
        locator.FindClosestNPoints(n, point, id_list)
        return vtk_id_list_to_array(id_list)
    return locator.FindClosestPoint(point)


