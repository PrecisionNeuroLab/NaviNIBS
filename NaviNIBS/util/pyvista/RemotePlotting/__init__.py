from __future__ import annotations
import attrs

import pyvista as pv


@attrs.define(frozen=True)
class ActorRef:
    """
    vtk Actors can't be pickled, so we use this class to pass to remote processes,
    and use ActorManager to keep track of which ref refers to which actor locally.
    """
    actorID: str


@attrs.define(frozen=True)
class PolyDataRef:
    """
    Hold reference to unique poly data so that they can be updated later.

    For example, when original caller plots a mesh, and then changes a scalars field
    later, we want that change to be communicated to the remote plotter.
    """
    id: str


@attrs.define
class PolyDataManager:
    _counter: int = 0
    _polyDatas: dict[PolyDataRef, pv.PolyData] = attrs.field(factory=dict)

    def addPolyData(self, polyData: pv.PolyData, id: str | None = None) -> PolyDataRef:
        if id is None:
            self._counter += 1
            id = f'<PolyData{self._counter}>'
        polyDataRef = PolyDataRef(id)
        # if id already exists in _polyDatas, it will be overwritten
        self._polyDatas[polyDataRef] = polyData
        return polyDataRef

    def getPolyData(self, polyDataRef: PolyDataRef) -> pv.PolyData:
        return self._polyDatas[polyDataRef]

    def __contains__(self, item):
        return item in self._polyDatas