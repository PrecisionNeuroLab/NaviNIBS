import numpy as np
import pyvista as pv
from pyvista.plotting.mapper import DataSetMapper
import typing as tp

Actor = pv._vtk.vtkActor

# test if running on mac
import platform
isMac = platform.system() == 'Darwin'
if False or isMac:
    from RTNaBS.util.pyvista.plotting import BackgroundPlotter
    DefaultBackgroundPlotter = BackgroundPlotter
    RemotePlotterProxy = None  # for callers to easily check if DefaultBackgroundPlotter is RemotePlotterProxy
else:
    # note: mac does not support remote plotting via qt window embeddeding
    from RTNaBS.util.pyvista.RemotePlotting.RemotePlotterProxy import RemotePlotterProxy

    DefaultBackgroundPlotter = RemotePlotterProxy


def setActorUserTransform(actor: Actor, transf: np.ndarray):
    t = pv._vtk.vtkTransform()
    t.SetMatrix(pv.vtkmatrix_from_array(transf))
    actor.SetUserTransform(t)


def getActorUserTransform(actor: Actor) -> np.ndarray:
    return pv.array_from_vtkmatrix(actor.GetUserTransform().GetMatrix())


def addLineSegments(plotter: pv.BasePlotter, lines: pv.PolyData,
                    color='w',
                    opacity=1.,
                    width=5,
                    reset_camera: bool = False,
                    label=None,
                    name=None,
                    scalar_bar_args: dict | None = None,
                    scalars: str | None = None,
                    show_scalar_bar: bool | None = None,
                    cmap: str | tp.Any | None = None) -> Actor:
    """
    Similar to actor.add_lines but with a few improvements:
    - Allows `lines` arg to already be pv.PolyData (e.g. to already been passed through `pv.lines_from_points`.) This
        allows for the possibility of grouping multiple discontinuous line segments into one actor.
    - Added opacity arg
    - Added reset_camera arg
    """
    assert isinstance(lines, pv.PolyData)

    # Create mapper and add lines
    mapper = DataSetMapper(lines)

    if scalars is None:
        assert scalar_bar_args is None
        rgb_color = pv.colors.Color(color)
    else:
        if show_scalar_bar is None:
            show_scalar_bar = plotter._theme.show_scalar_bar

        n_colors = 256

        scalar_bar_args = scalar_bar_args.copy()
        scalar_bar_args.setdefault('n_colors', n_colors)
        scalar_bar_args['mapper'] = mapper

        nan_color = pv.Color(None, default_opacity=1., default_color=plotter._theme.nan_color)

        if isinstance(scalars, str):
            scalars = pv.utilities.helpers.get_array(lines, scalars, preference='points')

        clim = [np.nanmin(scalars), np.nanmax(scalars)]

        if any(np.isnan(x) for x in clim):
            # autoset clim within set_scalars errors if all values are nan
            clim = [0, 1]

        mapper.set_scalars(lines,
                           scalars=scalars,
                           scalar_bar_args=scalar_bar_args,
                           rgb=False,
                           component=None,
                           preference='points',
                           interpolate_before_map=True,
                           _custom_opac=False,
                           annotations=None,
                           log_scale=False,
                           nan_color=nan_color,
                           above_color=None,
                           below_color=None,
                           cmap=cmap,
                           flip_scalars=False,
                           opacity=opacity,
                           categories=False,
                           n_colors=n_colors,
                           clim=clim,
                           theme=plotter._theme,
                           show_scalar_bar=show_scalar_bar)

    # Create actor
    actor = pv._vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetLineWidth(width)
    actor.GetProperty().EdgeVisibilityOn()
    if scalars is None:
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

    if show_scalar_bar and scalars is not None:
        plotter.add_scalar_bar(**scalar_bar_args)

    # Add to renderer
    plotter.add_actor(actor, reset_camera=reset_camera, name=name, pickable=False)
    return actor


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

