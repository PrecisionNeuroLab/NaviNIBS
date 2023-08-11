import asyncio
import functools
import logging
import numpy as np
import pyvista as pv
from pyvista import _vtk
import typing as tp
import weakref

from . import Actor

from RTNaBS.util.pyvista import Actor, setActorUserTransform, getActorUserTransform
from RTNaBS.util import Transforms


logger = logging.getLogger(__name__)


def _launch_pick_event(interactor, event):
    """Create a Pick event based on coordinate or left-click."""
    click_x, click_y = interactor.GetEventPosition()
    click_z = 0

    picker = interactor.GetPicker()
    renderer = interactor.GetInteractorStyle()._parent()._plotter.renderer
    picker.Pick(click_x, click_y, click_z, renderer)


def set_mouse_event_for_picking(plotter: pv.BasePlotter, eventKey: str):
    """
    To be called after pyvista plotter.enable_*_picking(), allowing for
    picking to be triggered by alternate mouse events, such as left double click instead of single press
    :param eventKey: can be any relevant vtk event, e.g. 'LeftButtonPressEvent', 'RightButtonPressEvent'.
    :return: None

    Unfortunately, pyvista / Qt interactions mean that some events never reach VTK and so won't work here.
    For example, double click events don't work without some extra interactor modifications (see https://discourse.vtk.org/t/why-single-click-works-but-double-click-does-not/3599)
    """

    plotter.iren.interactor.AddObserver(
        eventKey,
        functools.partial(pv.core.utilities.misc.try_callback, _launch_pick_event),
    )


async def pickActor(plotter: pv.Plotter,
                    show: bool = False,
                    show_message: tp.Union[bool, str] = True,
                    style: str = 'wireframe',
                    line_width: float = 5,
                    color: str = 'pink',
                    font_size: int = 18,
                    left_clicking: bool = False,
                    **kwargs) -> tp.Optional[Actor]:
    """
    Adapted from pv.plotter.enable_mesh_picking but with a few improvements/modifications:
    - Supports highlighting selection even after UserTransform was set on original actor
    - Async call until pick is finished
    - Returns Actor instead of mesh PolyData
    """

    pickedActor = None
    event_pickFinished = asyncio.Event()

    selectActor = None

    def end_pick_call_back(picked, event):
        nonlocal pickedActor, selectActor

        is_valid_selection = False
        plotter_ = weakref.ref(plotter)

        pickedActor = picked.GetActor()
        if pickedActor:
            mesh = pickedActor.GetMapper().GetInput()
            is_valid_selection = True

        event_pickFinished.set()

        if not is_valid_selection:
            return

        selectActor = None

        if show:
            # Select the renderer where the mesh is added.
            active_renderer_index = plotter_().renderers._active_index
            for index in range(len(plotter.renderers)):
                renderer = plotter.renderers[index]
                for actor in renderer._actors.values():
                    try:
                        mapper = actor.GetMapper()
                    except Exception as e:
                        # not all non-mesh actors have mappers, just ignore
                        continue
                    if isinstance(mapper, _vtk.vtkDataSetMapper) and mapper.GetInput() == mesh:
                        loc = plotter_().renderers.index_to_loc(index)
                        plotter_().subplot(*loc)
                        break

            # Use try in case selection is empty or invalid
            try:
                selectActor = plotter_().add_mesh(
                    mesh,
                    name='_mesh_picking_selection',
                    style=style,
                    color=color,
                    line_width=line_width,
                    pickable=False,
                    reset_camera=False,
                    **kwargs,
                )
            except Exception as e:  # pragma: no cover
                logging.warning("Unable to show mesh when picking:\n\n%s", str(e))
            else:
                selectActor.SetUserTransform(pickedActor.GetUserTransform())

            # Reset to the active renderer.
            loc = plotter_().renderers.index_to_loc(active_renderer_index)
            plotter_().subplot(*loc)

            # render here prior to returning
            plotter_().render()

    if show_message:
        if show_message is True:
            show_message = "\nPress P to pick a single dataset under the mouse pointer."
            if left_clicking:
                show_message += "\nor click to select a dataset under the mouse pointer."

        textActor = plotter.add_text(str(show_message), font_size=font_size, name='_mesh_picking_message', color='black')
    else:
        textActor = None

    if left_clicking:
        leftClickObserver = plotter.iren.interactor.AddObserver(
            "LeftButtonPressEvent",
            functools.partial(pv.core.utilities.misc.try_callback, _launch_pick_event),
        )
    else:
        leftClickObserver = None

    picker = _vtk.vtkPropPicker()
    endPickObserver = picker.AddObserver(_vtk.vtkCommand.EndPickEvent, end_pick_call_back)
    plotter.enable_trackball_style()
    plotter.iren.set_picker(picker)

    logging.debug('Waiting for pick to finish')
    await event_pickFinished.wait()
    logging.debug('Pick finished')

    # clean up
    if textActor is not None:
        plotter.remove_actor(textActor)

    if selectActor is not None:
        async def removeSelectActorAfterDelay():
            await asyncio.sleep(0.5)
            plotter.remove_actor(selectActor)
        asyncio.create_task(removeSelectActorAfterDelay())

    picker.RemoveObserver(endPickObserver)
    if left_clicking:
        plotter.iren.interactor.RemoveObserver(leftClickObserver)

    return pickedActor


async def interactivelyMoveActor(plotter: pv.Plotter, actor: Actor, onNewTransf: tp.Callable[[_vtk.vtkTransform], None]):
    from vtkmodules.vtkInteractionWidgets import (
        vtkBoxWidget2,
    )

    wdgt = vtkBoxWidget2()
    wdgt.CreateDefaultRepresentation()
    wdgt.SetInteractor(plotter.iren.interactor)
    wdgt.SetCurrentRenderer(plotter.renderer)
    wdgt.SetScalingEnabled(False)
    wdgt.SetMoveFacesEnabled(False)
    handleRep = wdgt.GetRepresentation()
    handleRep.SetPlaceFactor(1.)
    if True:
        # temporarily reset transform to get bounds of actor in local space
        # TODO: do this with some other bounding box calculation rather than resetting transform
        actorTransf = actor.GetUserTransform()
        actor.SetUserTransform(_vtk.vtkTransform())
        localActorBounds = actor.GetBounds()
        actor.SetUserTransform(actorTransf)
        handleRep.PlaceWidget(localActorBounds)
        handleRep.SetTransform(actorTransf)
    else:
        handleRep.PlaceWidget(actor.GetBounds())
    #handleRep.SetOutlineFaceWires(True)
    handleRep.SetSnapToAxes(True)  # TODO: debug, delete
    wdgt.On()

    newTransfPending = asyncio.Event()
    newestTransf: tp.Optional[_vtk.vtkTransform] = None

    async def publishNewPosition():
        while True:
            await newTransfPending.wait()
            newTransfPending.clear()
            assert newestTransf is not None
            onNewTransf(newestTransf)
            await asyncio.sleep(0.1)  # don't publish updates more frequently than this

    publishTask = asyncio.create_task(publishNewPosition())

    def interactionCallback(obj, event_type):
        nonlocal newestTransf
        transform = _vtk.vtkTransform()
        handleRep.GetTransform(transform)
        actor.SetUserTransform(transform)  # TODO: debug, delete
        newestTransf = transform
        newTransfPending.set()

    # evtIDStr = _vtk.vtkCommand.GetStringFromEventId(_vtk.vtkCommand.GetEventIdFromString('InteractionEvent'))
    # logger.debug(f'evtIDStr: {evtIDStr}')
    wdgt.AddObserver('InteractionEvent', interactionCallback)

    if True:
        # TODO: debug, delete
        while True:
            await asyncio.sleep(1.)

    publishTask.cancel()
    # TODO: other clean-up


