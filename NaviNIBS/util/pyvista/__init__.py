import numpy as np
import pyvista as pv
import typing as tp

Actor = pv._vtk.vtkActor


def setActorUserTransform(actor: Actor, transf: np.ndarray):
    t = pv._vtk.vtkTransform()
    t.SetMatrix(pv.vtkmatrix_from_array(transf))
    actor.SetUserTransform(t)


# test if running on mac
import platform
isMac = platform.system() == 'Darwin'
if False or isMac:
    from NaviNIBS.util.pyvista.plotting import BackgroundPlotter, PrimaryLayeredPlotter, SecondaryLayeredPlotter
    DefaultBackgroundPlotter = BackgroundPlotter
    RemotePlotterProxy = type('__None')  # for callers to easily check if DefaultBackgroundPlotter is RemotePlotterProxy
    DefaultPrimaryLayeredPlotter = PrimaryLayeredPlotter
    DefaultSecondaryLayeredPlotter = SecondaryLayeredPlotter
else:
    # note: mac does not support remote plotting via qt window embeddeding
    from NaviNIBS.util.pyvista.RemotePlotting.RemotePlotterProxy import RemotePlotterProxy
    from NaviNIBS.util.pyvista.RemotePlotting.RemoteLayeredPlotterProxies import RemotePrimaryLayeredPlotterProxy, RemoteSecondaryLayeredPlotterProxy

    DefaultBackgroundPlotter = RemotePlotterProxy
    DefaultPrimaryLayeredPlotter = RemotePrimaryLayeredPlotterProxy
    DefaultSecondaryLayeredPlotter = RemoteSecondaryLayeredPlotterProxy


def getActorUserTransform(actor: Actor) -> np.ndarray:
    return pv.array_from_vtkmatrix(actor.GetUserTransform().GetMatrix())


def concatenateLineSegments(lineSegmentGroups: tp.Iterable[pv.PolyData]) -> pv.PolyData:
    poly = pv.PolyData()
    poly.points = np.vstack([group.points for group in lineSegmentGroups])
    cells = np.vstack([group.lines.reshape((-1, 3)) for group in lineSegmentGroups])
    offset_points = 0
    offset_lines = 0
    for iGroup, group in enumerate(lineSegmentGroups):
        lines_group = group.lines.reshape((-1, 3))
        cells[offset_lines:(offset_lines+lines_group.shape[0]), 1:] += offset_points
        offset_points += group.points.shape[0]
        offset_lines += lines_group.shape[0]

    poly.lines = cells
    return poly

