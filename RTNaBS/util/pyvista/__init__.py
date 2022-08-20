import numpy as np
import pyvista as pv
import typing as tp

Actor = pv._vtk.vtkActor


def setActorUserTransform(actor: Actor, transf: np.ndarray):
    t = pv._vtk.vtkTransform()
    t.SetMatrix(pv.vtkmatrix_from_array(transf))
    actor.SetUserTransform(t)


def getActorUserTransform(actor: Actor) -> np.ndarray:
    return pv.array_from_vtkmatrix(actor.GetUserTransform().GetMatrix())


def addLineSegments(plotter: pv.BasePlotter, lines: pv.PolyData,
                    color='w', opacity=1., width=5,
                    reset_camera: bool = False,
                    label=None, name=None) -> Actor:
    """
    Similar to actor.add_lines but with a few improvements:
    - Allows `lines` arg to already be pv.PolyData (e.g. to already been passed through `pv.lines_from_points`.) This
        allows for the possibility of grouping multiple discontinuous line segments into one actor.
    - Added opacity arg
    - Added reset_camera arg
    """
    assert isinstance(lines, pv.PolyData)

    # Create mapper and add lines
    mapper = pv._vtk.vtkDataSetMapper()
    mapper.SetInputData(lines)

    rgb_color = pv.colors.Color(color)

    # Create actor
    actor = pv._vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetLineWidth(width)
    actor.GetProperty().EdgeVisibilityOn()
    actor.GetProperty().SetEdgeColor(rgb_color.float_rgb)
    actor.GetProperty().SetColor(rgb_color.float_rgb)
    actor.GetProperty().LightingOff()
    actor.GetProperty().SetOpacity(opacity)

    # legend label
    if label:
        if not isinstance(label, str):
            raise TypeError('Label must be a string')
        addr = actor.GetAddressAsString("")
        plotter.renderer._labels[addr] = [lines, label, rgb_color]

    # Add to renderer
    plotter.add_actor(actor, reset_camera=reset_camera, name=name, pickable=False)
    return actor


def concatenateLineSegments(lineSegmentGroups: tp.Iterable[pv.PolyData]) -> pv.PolyData:
    poly = pv.PolyData()
    poly.points = np.vstack(group.points for group in lineSegmentGroups)
    cells = np.vstack(group.lines.reshape((-1, 3)) for group in lineSegmentGroups)
    offset_points = 0
    offset_lines = 0
    for iGroup, group in enumerate(lineSegmentGroups):
        lines_group = group.lines.reshape((-1, 3))
        cells[offset_lines:(offset_lines+lines_group.shape[0]), 1:] += offset_points
        offset_points += group.points.shape[0]
        offset_lines += lines_group.shape[0]

    poly.lines = cells
    return poly

