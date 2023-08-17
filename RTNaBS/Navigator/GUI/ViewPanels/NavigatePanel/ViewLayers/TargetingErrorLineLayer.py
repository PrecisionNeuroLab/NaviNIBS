from __future__ import annotations

import attrs
import logging
import numpy as np
import pyvista as pv
import typing as tp
from typing import ClassVar

from . import PlotViewLayer
from RTNaBS.Navigator.TargetingCoordinator import ProjectionSpecification

logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define(kw_only=True)
class TargetingErrorLineLayer(PlotViewLayer):
    _type: ClassVar[str] = 'TargetingErrorLine'

    _targetDepth: tp.Union[str, ProjectionSpecification] = 'target'
    _coilDepth: tp.Union[str, ProjectionSpecification] = 'target'

    _color: str = '#ff0000'
    _opacity: float = 0.5
    _lineWidth: float = 4.

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.sigCurrentTargetChanged.connect(lambda: self._queueRedraw(which='updatePosition'))
        self._coordinator.sigCurrentCoilPositionChanged.connect(lambda: self._queueRedraw(which=['updatePosition']))

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        super()._redraw(which=which)

        if not isinstance(which, str):
            # assume parent call above triggered appropriate redraws
            return

        if which == 'all':
            which = ['initLine']
            self._redraw(which=which)
            return

        actorKey = self._getActorKey('line')

        if which == 'initLine':
            # start with dummy position, to be updated later
            pts_line = np.asarray([[0., 0., 0.], [1., 1., 1.]])

            self._actors[actorKey] = self._plotter.add_lines(pts_line,
                                                             color=self._color,
                                                             width=self._lineWidth,
                                                             name=actorKey)

            self._redraw(which='updatePosition')

        elif which == 'updatePosition':
            actor = self._actors[actorKey]

            doShow = True
            pt_coil = self._coordinator.getTargetingCoord(orientation='coil', depth=self._coilDepth)
            if pt_coil is None:
                doShow = False
            else:
                pt_target = self._coordinator.getTargetingCoord(orientation='target',  depth=self._targetDepth)
                if pt_target is None:
                    doShow = False

            if not doShow:
                if actor.GetVisibility():
                    with self._plotter.allowNonblockingCalls():
                        actor.VisibilityOff()
                return

            pts_pv = pv.lines_from_points(np.vstack([pt_coil, pt_target]))

            with self._plotter.allowNonblockingCalls():
                actor.GetMapper().SetInputData(pts_pv)

                if not actor.GetVisibility():
                    actor.VisibilityOn()

                self._plotter.render()
        else:
            raise NotImplementedError(f'Unexpected redraw which: {which}')

