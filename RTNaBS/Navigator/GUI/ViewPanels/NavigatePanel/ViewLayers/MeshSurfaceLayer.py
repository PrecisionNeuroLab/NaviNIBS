from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import typing as tp
from typing import ClassVar

from . import PlotViewLayer
from RTNaBS.util.pyvista import Actor, setActorUserTransform, concatenateLineSegments
from RTNaBS.util.Transforms import concatenateTransforms, invertTransform


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define(kw_only=True)
class ToolMeshSurfaceLayer(PlotViewLayer):
    _type: ClassVar[str] = 'ToolMeshSurface'
    _color: str | None = None
    """
    If None, will use tool's defined color
    """
    _toolKey: str
    _doShowToolMesh: bool = True
    _doShowTrackerMesh: bool = False
    _opacity: float | None = None
    """
    If None, will use tool's defined opacity
    """

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)

    def _onLatestPositionsChanged(self):
        self._queueRedraw(which='updatePosition')
        # TODO: connect to signals to redraw mesh when tool mesh, color, or opacity changes

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        super()._redraw(which=which)

        if not isinstance(which, str):
            # assume parent call above triggered appropriate redraws
            return

        if which == 'all':
            which = ['initSurfs']
            self._redraw(which=which)
            return

        if which == 'initSurfs':
            for toolOrTracker in ('tool', 'tracker'):
                match toolOrTracker:
                    case 'tool':
                        if not self._doShowToolMesh:
                            continue

                    case 'tracker':
                        if not self._doShowTrackerMesh:
                            continue

                    case _:
                        raise NotImplementedError

                tool = self._coordinator.session.tools[self._toolKey]

                mesh = getattr(tool, f'{toolOrTracker}Surf')

                assert mesh is not None

                actorKey = self._getActorKey(f'{toolOrTracker}Surf')

                if actorKey in self._actors:
                    with self._plotter.allowNonblockingCalls():
                        self._plotter.remove_actor(self._actors.pop(actorKey))

                color = self._color
                if color is None:
                    color = getattr(tool, f'{toolOrTracker}Color')
                    if color is None:
                        color = '#999999'  # default color

                opacity = self._opacity
                if opacity is None:
                    opacity = getattr(tool, f'{toolOrTracker}Opacity')
                    if opacity is None:
                        opacity = 1.  # default opacity

                self._actors[actorKey] = self._plotter.add_mesh(mesh=mesh,
                                                                color=color,
                                                                opacity=opacity,
                                                                specular=0.5,
                                                                diffuse=0.5,
                                                                ambient=0.5,
                                                                smooth_shading=True,
                                                                split_sharp_edges=True,
                                                                name=actorKey)

            self._plotter.reset_camera_clipping_range()

            self._redraw('updatePosition')

        elif which == 'updatePosition':

            for toolOrTracker in ('tool', 'tracker'):
                match toolOrTracker:
                    case 'tool':
                        if not self._doShowToolMesh:
                            continue

                    case 'tracker':
                        if not self._doShowTrackerMesh:
                            continue

                    case _:
                        raise NotImplementedError

                actorKey = self._getActorKey(f'{toolOrTracker}Surf')

                try:
                    actor = self._actors[actorKey]
                except KeyError:
                    self._redraw(which='initSurfs')
                    return

                tool = self._coordinator.session.tools[self._toolKey]

                match toolOrTracker:
                    case 'tool':
                        toolOrTrackerStlToTrackerTransf = tool.toolToTrackerTransf @ tool.toolStlToToolTransf
                    case 'tracker':
                        toolOrTrackerStlToTrackerTransf = tool.trackerStlToTrackerTransf
                    case _:
                        raise NotImplementedError

                if self._plotInSpace != 'MRI':
                    raise NotImplementedError

                trackerToWorldTransf = self._coordinator.positionsClient.getLatestTransf(key=self._toolKey, default=None)
                doHide = True
                if trackerToWorldTransf is not None:
                    subjectTrackerToWorldTransf = self._coordinator.positionsClient.getLatestTransf(self._coordinator.session.tools.subjectTracker.key, None)
                    if subjectTrackerToWorldTransf is not None:
                        subjectTrackerToMRITransf = self._coordinator.session.subjectRegistration.trackerToMRITransf
                        if subjectTrackerToMRITransf is not None:
                            # we have enough info to assemble valid transf
                            doHide = False

                            surfToMRITransf = concatenateTransforms([
                                toolOrTrackerStlToTrackerTransf,
                                trackerToWorldTransf,
                                invertTransform(subjectTrackerToWorldTransf),
                                subjectTrackerToMRITransf,
                            ])

                            with self._plotter.allowNonblockingCalls():
                                setActorUserTransform(actor, surfToMRITransf)
                                if not actor.GetVisibility():
                                    actor.SetVisibility(True)

                if doHide:
                    with self._plotter.allowNonblockingCalls():
                        if actor.GetVisibility():
                            actor.SetVisibility(False)

        else:
            raise NotImplementedError


@attrs.define
class HeadMeshSurfaceLayer(PlotViewLayer):
    _type: ClassVar[str] = 'HeadMeshSurface'
    _color: str = '#d9a5b2'
    _surfKey: str = 'gmSimpleSurf'
    _opacity: float = 1.

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        super()._redraw(which=which)

        if not isinstance(which, str):
            # assume parent call above triggered appropriate redraws
            return

        if which == 'all':
            which = ['initSurf']
            self._redraw(which=which)
            return

        if which == 'initSurf':
            mesh = getattr(self._coordinator.session.headModel, self._surfKey)

            actorKey = self._getActorKey('surf')

            self._actors[actorKey] = self._plotter.add_mesh(mesh=mesh,
                                                            color=self._color,
                                                            opacity=self._opacity,
                                                            specular=0.5,
                                                            diffuse=0.5,
                                                            ambient=0.5,
                                                            smooth_shading=True,
                                                            split_sharp_edges=True,
                                                            name=actorKey)

            self._plotter.reset_camera_clipping_range()

            self._redraw('updatePosition')

        elif which == 'updatePosition':
            if self._plotInSpace == 'MRI':
                if False:
                    actorKey = self._getActorKey('surf')
                    transf = np.eye(4)
                    setActorUserTransform(self._actors[actorKey], transf)
                    self._plotter.render()
                else:
                    pass  # assume since plotInSpace is always MRI (for now) that we don't need to update anything
        else:
            raise NotImplementedError
