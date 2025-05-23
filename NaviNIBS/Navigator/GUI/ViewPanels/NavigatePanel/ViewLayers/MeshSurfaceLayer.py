from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import typing as tp
from typing import ClassVar

from . import PlotViewLayer
from NaviNIBS.util.pyvista import Actor, setActorUserTransform, concatenateLineSegments
from NaviNIBS.util.Transforms import concatenateTransforms, invertTransform


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

                doHide = True

                match self._plotInSpace:
                    case 'MRI':
                        trackerToWorldTransf = self._coordinator.positionsClient.getLatestTransf(key=tool.trackerKey, default=None)
                        if trackerToWorldTransf is not None:
                            subjectTrackerToWorldTransf = self._coordinator.positionsClient.getLatestTransf(self._coordinator.session.tools.subjectTracker.trackerKey, None)
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
                                        self._plotter.render()

                    case 'World':
                        trackerToWorldTransf = self._coordinator.positionsClient.getLatestTransf(key=tool.trackerKey, default=None)
                        if trackerToWorldTransf is not None:
                            doHide = False

                            surfToWorldTransf = concatenateTransforms([
                                toolOrTrackerStlToTrackerTransf,
                                trackerToWorldTransf,
                            ])

                            with self._plotter.allowNonblockingCalls():
                                setActorUserTransform(actor, surfToWorldTransf)
                                if not actor.GetVisibility():
                                    actor.SetVisibility(True)
                                self._plotter.render()

                    case _:
                        raise NotImplementedError

                if doHide:
                    with self._plotter.allowNonblockingCalls():
                        if actor.GetVisibility():
                            actor.SetVisibility(False)
                        self._plotter.render()

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

        if self._plotInSpace != 'MRI':
            self._coordinator.positionsClient.sigLatestPositionsChanged.connect(lambda: self._queueRedraw(which='updatePosition'))

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
            match self._plotInSpace:
                case 'MRI':
                    if False:
                        actorKey = self._getActorKey('surf')
                        transf = np.eye(4)
                        setActorUserTransform(self._actors[actorKey], transf)
                        self._plotter.render()
                    else:
                        pass  # assume since plotInSpace is MRI that we don't need to update anything
                case 'World':

                    actorKey = self._getActorKey('surf')
                    if actorKey not in self._actors:
                        self._redraw(which='initSurf')
                        return

                    actor = self._actors[actorKey]

                    doHide = True
                    subjectTrackerToWorldTransf = self._coordinator.positionsClient.getLatestTransf(self._coordinator.session.tools.subjectTracker.trackerKey, None)
                    if subjectTrackerToWorldTransf is not None:
                        subjectTrackerToMRITransf = self._coordinator.session.subjectRegistration.trackerToMRITransf
                        if subjectTrackerToMRITransf is not None:
                            # we have enough info to assemble valid transf
                            doHide = False

                            surfToWorldTransf = concatenateTransforms([
                                invertTransform(subjectTrackerToMRITransf),
                                subjectTrackerToWorldTransf,
                            ])

                            with self._plotter.allowNonblockingCalls():
                                setActorUserTransform(actor, surfToWorldTransf)
                                if not actor.GetVisibility():
                                    actor.SetVisibility(True)
                                self._plotter.render()

                    if doHide:
                        with self._plotter.allowNonblockingCalls():
                            if actor.GetVisibility():
                                actor.SetVisibility(False)
                                self._plotter.render()

                case _:
                    raise NotImplementedError

        else:
            raise NotImplementedError
