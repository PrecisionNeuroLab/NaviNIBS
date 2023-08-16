from __future__ import annotations

import attrs
import logging
import numpy as np
import typing as tp
from typing import ClassVar

from . import PlotViewLayer
from .PlotLayersGroup import PlotLayersGroup
from RTNaBS.Navigator.TargetingCoordinator import ProjectionSpecification
from RTNaBS.util.pyvista import setActorUserTransform
from RTNaBS.util.Transforms import composeTransform


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define(kw_only=True)
class TargetingPointLayer(PlotViewLayer):
    _type: ClassVar[str] = 'TargetingPoint'

    _orientation: str  # 'target' or 'coil'
    _depth: tp.Union[str, ProjectionSpecification]
    """
    if orientation=='coil', can be one of ['coil', 'skin', 'gm', 'target']; if orientation=='target', can be ['coil', 'skin', 'gm', 'target']
    Otherwise can be ProjectionSpecification instance to choose depth based on projecting to another orientation
    """

    _color: str = '#0000ff'
    _opacity: float = 0.5
    _radius: float = 5.

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.sigCurrentTargetChanged.connect(lambda: self._queueRedraw(which='all'))
        self._coordinator.sigCurrentCoilPositionChanged.connect(lambda: self._queueRedraw(which=['updatePosition']))

    def _getCoord(self, orientation: str, depth: tp.Union[str, ProjectionSpecification]) -> tp.Optional[np.ndarray]:
        return self._coordinator.getTargetingCoord(orientation=orientation, depth=depth)

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        super()._redraw(which=which)

        if not isinstance(which, str):
            # assume parent call above triggered appropriate redraws
            return

        if which == 'all':
            which = ['initPoint']
            self._redraw(which=which)
            return

        elif which == 'initPoint':
            actorKey = self._getActorKey('point')

            self._actors[actorKey] = self._plotter.add_points(np.asarray([0., 0., 0.]),
                                                              color=self._color,
                                                              opacity=self._opacity,
                                                              point_size=self._radius*2,
                                                              reset_camera=False,
                                                              render_points_as_spheres=True,
                                                              name=actorKey)

            self._redraw('updatePosition')

        elif which == 'updatePosition':
            actorKey = self._getActorKey('point')
            actor = self._actors[actorKey]

            coord = self._getCoord(orientation=self._orientation, depth=self._depth)

            if coord is None:
                # no valid pose available
                if actor.GetVisibility():
                    actor.VisibilityOff()
                    self._plotter.render()
            else:
                setActorUserTransform(actor, composeTransform(np.eye(3), coord))
                if not actor.GetVisibility():
                    actor.VisibilityOn()
                self._plotter.render()

        else:
            raise NotImplementedError('Unexpected redraw which: {}'.format(which))


@attrs.define
class TargetingTargetPointsLayer(PlotLayersGroup):
    _type: ClassVar[str] = 'TargetingTargetPoints'

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self.addLayer(TargetingPointLayer, 'targetCoilPoint', color='#0000ff', orientation='target', depth='coil')
        self.addLayer(TargetingPointLayer, 'targetTargetPoint', color='#0000ff', orientation='target', depth='target')


@attrs.define
class TargetingCoilPointsLayer(PlotLayersGroup):
    _type: ClassVar[str] = 'TargetingCoilPoints'

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self.addLayer(TargetingPointLayer, 'coilCoilPoint', color='#00ff00', orientation='coil', depth='coil', radius=8)
        self.addLayer(TargetingPointLayer, 'coilTargetPoint', color='#00ff00', orientation='coil', depth='target', radius=8)
        if False:  # TODO: debug, enable by default
            self.addLayer(TargetingPointLayer, 'coilProjectedTargetPoint', color='#00ff00', orientation='coil',
                          depth=ProjectionSpecification(toOrientation='target', toDepth='target', toShape='sphere'))


