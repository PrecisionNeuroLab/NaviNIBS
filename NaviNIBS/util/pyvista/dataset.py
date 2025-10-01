import collections.abc
import typing as tp
import numpy as np
import numpy.typing as npt
import pyvista
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
        if False:
            dataset._point_locator = locator
        else:
            pv.set_new_attribute(dataset, '_point_locator', locator)

        # TODO: implement more efficient search algorithms from https://github.com/pyvista/pyvista-support/issues/107

        if pyvista.PICKLE_FORMAT in ['xml', 'legacy']:
            # must also monkey-patch getstate to not break pickling
            if False:
                dataset.__getstate__orig = dataset.__getstate__
            else:
                pv.set_new_attribute(dataset, '__getstate__orig', dataset.__getstate__)
            def __getstate__(self):
                state = self.__getstate__orig()
                for key in ('_point_locator', '__getstate__orig', '__getstate__'):
                    del state[key]
                return state
            dataset.__getstate__ = __getstate__.__get__(dataset, pv.DataSet)
        else:
            # new pickle format buries these attributes deeper in state
            pv.set_new_attribute(dataset, '__getstate__orig', dataset.__getstate__)
            def __getstate__(self):
                state = self.__getstate__orig()
                for key in ('_point_locator', '__getstate__orig', '__getstate__'):
                    del state[1][0]['_PYVISTA_STATE_DICT'][key]
                return state
            dataset.__getstate__ = __getstate__.__get__(dataset, pv.DataSet)


    if n > 1:
        id_list = _vtk.vtkIdList()
        locator.FindClosestNPoints(n, point, id_list)
        return vtk_id_list_to_array(id_list)
    return locator.FindClosestPoint(point)


def find_closest_cell(dataset: pv.DataSet, point: tp.Iterable, return_closest_point: bool = False) -> \
        int | npt.NDArray[int] | tuple[int | npt.NDArray[int], npt.NDArray[int]]:
    """
    Similar to pv.DataSet.find_closest_cell, but monkey-patches a cache of the
    vtkPointLocator to speed up repeated calls. It turns out this construction
    is more expensive than the actual find.

    Note: the cache is not automatically reset by changes to the dataset, so may break
    (or give incorrect results) if dataset is changed.
    """
    from pyvista.core.utilities.arrays import _coerce_pointslike_arg

    point, singular = _coerce_pointslike_arg(point, copy=False)

    if hasattr(dataset, '_cell_locator'):
        locator = dataset._cell_locator
    else:
        locator = _vtk.vtkCellLocator()
        locator.SetDataSet(dataset)
        locator.BuildLocator()
        if False:
            dataset._cell_locator = locator
        else:
            pv.set_new_attribute(dataset, '_cell_locator', locator)

        if pyvista.PICKLE_FORMAT in ['xml', 'legacy']:
            # must also monkey-patch getstate to not break pickling
            if False:
                dataset.__getstate__orig = dataset.__getstate__
            else:
                pv.set_new_attribute(dataset, '__getstate__orig', dataset.__getstate__)
            def __getstate__(self):
                state = self.__getstate__orig()
                for key in ('_cell_locator', '__getstate__orig', '__getstate__'):
                    del state[key]
                return state
            dataset.__getstate__ = __getstate__.__get__(dataset, pv.DataSet)
        else:
            # new pickle format buries these attributes deeper in state
            pv.set_new_attribute(dataset, '__getstate__orig', dataset.__getstate__)
            def __getstate__(self):
                state = self.__getstate__orig()
                for key in ('_cell_locator', '__getstate__orig', '__getstate__'):
                    del state[1][0]['_PYVISTA_STATE_DICT'][key]
                return state
            dataset.__getstate__ = __getstate__.__get__(dataset, pv.DataSet)

    cell = _vtk.vtkGenericCell()

    closest_cells: list[int] = []
    closest_points: list[list[float]] = []

    for node in point:
        closest_point = [0.0, 0.0, 0.0]
        cell_id = _vtk.mutable(0)
        sub_id = _vtk.mutable(0)
        dist2 = _vtk.mutable(0.0)

        locator.FindClosestPoint(node, closest_point, cell, cell_id, sub_id, dist2)  # type: ignore[call-overload]
        closest_cells.append(int(cell_id))
        closest_points.append(closest_point)

    out_cells: int | npt.NDArray[int] = (
        closest_cells[0] if singular else np.array(closest_cells)
    )
    out_points = np.array(closest_points[0]) if singular else np.array(closest_points)

    if return_closest_point:
        return out_cells, out_points
    return out_cells

