from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import typing as tp
from typing import ClassVar

from . import PlotViewLayer
from RTNaBS.util.pyvista import Actor, setActorUserTransform, concatenateLineSegments


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define
class MeshSurfaceLayer(PlotViewLayer):
    _type: ClassVar[str] = 'MeshSurface'
    _color: str = '#d9a5b2'
    _opacity: float = 1
    _surfKey: str = 'gmSimpleSurf'

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
