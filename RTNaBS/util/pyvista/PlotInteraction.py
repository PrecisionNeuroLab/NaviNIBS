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
            functools.partial(pv.utilities.helpers.try_callback, _launch_pick_event),
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


async def interactivelyMoveActor(plotter: pv.Plotter, actor: Actor):
    from vtkmodules.vtkInteractionWidgets import (
        vtkFixedSizeHandleRepresentation3D,
        vtkBoxRepresentation,
        vtkAngleRepresentation3D,
        vtkPointHandleRepresentation3D,
        vtkBoxWidget2,
        vtkHandleWidget,
        vtkAngleWidget,
        vtkAffineWidget
    )

    actorBounds = np.asarray(actor.GetBounds()).reshape([3, 2])
    actorOrigin = Transforms.applyTransform(getActorUserTransform(actor), np.zeros((3,)))


    if True:
        wdgt = vtkBoxWidget2()
        wdgt.CreateDefaultRepresentation()
        wdgt.SetInteractor(plotter.iren.interactor)
        wdgt.SetCurrentRenderer(plotter.renderer)
        wdgt.SetScalingEnabled(False)
        handleRep = wdgt.GetRepresentation()
        handleRep.PlaceWidget(actor.GetBounds())
        handleRep.SetTransform(actor.GetUserTransform())
        handleRep.SetOutlineFaceWires(True)
        handleRep.SetSnapToAxes(True)  # TODO: debug, delete
        wdgt.On()

    elif True:
        wdgt = _vtk.vtkBoxWidget()
        wdgt.SetInteractor(plotter.iren.interactor)
        wdgt.SetCurrentRenderer(plotter.renderer)
        wdgt.SetRotationEnabled(True)
        wdgt.SetTranslationEnabled(True)
        wdgt.SetScalingEnabled(False)
        wdgt.PlaceWidget(actor.GetBounds())
        wdgt.SetTransform(actor.GetUserTransform())
        wdgt.On()
    else:
        handleWdgt = vtkHandleWidget()
        handleWdgt.SetInteractor(plotter.iren.interactor)
        handleWdgt.CreateDefaultRepresentation()
        handleWdgt.SetCurrentRenderer(plotter.renderer)
        handleRep: vtkPointHandleRepresentation3D = handleWdgt.GetHandleRepresentation()
        handleRep.SetWorldPosition(actorOrigin)
        handleRep.SetHandleSize(np.mean(actorBounds[:, 1] - actorBounds[:, 0]))
        handleWdgt.On()

    def interactionCallback(obj, event_type):
        transform = _vtk.vtkTransform()
        handleRep.GetTransform(transform)
        actor.SetUserTransform(transform)  # TODO: debug, delete

    # evtIDStr = _vtk.vtkCommand.GetStringFromEventId(_vtk.vtkCommand.GetEventIdFromString('InteractionEvent'))
    # logger.debug(f'evtIDStr: {evtIDStr}')
    wdgt.AddObserver('InteractionEvent', interactionCallback)

    if True:
        # TODO: debug, delete
        while True:
            await asyncio.sleep(1.)



