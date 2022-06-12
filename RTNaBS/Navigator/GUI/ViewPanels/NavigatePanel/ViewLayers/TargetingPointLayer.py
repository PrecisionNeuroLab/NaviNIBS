from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import pyvista as pv
import typing as tp
from typing import ClassVar

from . import PlotViewLayer
from .PlotLayersGroup import PlotLayersGroup
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments
from RTNaBS.util.Transforms import applyTransform, concatenateTransforms, invertTransform, composeTransform


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define(frozen=True)
class ProjectionSpecification:
    """
    Specifiers to describe projection of an orientation down the depth axis to a target plane (or sphere)
    """
    _toOrientation: str  # 'target' or 'coil'
    _toDepth: str  # if toOrientation=='coil', can be one of ['coil', 'skin', 'gm']; if toOrientation=='target', can be ['coil', 'skin', 'gm', 'target']
    _toShape: str  # 'sphere' or 'plane'


@attrs.define(kw_only=True)
class TargetingPointLayer(PlotViewLayer):
    _type: ClassVar[str] = 'TargetingPoint'

    _orientation: str  # 'target' or 'coil'
    _depth: tp.Union[str, ProjectionSpecification]
    # if orientation=='coil', can be one of ['coil', 'skin', 'gm', 'target']; if orientation=='target', can be ['coil', 'skin', 'gm', 'target']
    # Otherwise can be ProjectionSpecification instance to choose depth based on projecting to another orientation

    _color: str = '#0000ff'
    _opacity = 0.5
    _radius = 5.



    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.sigCurrentTargetChanged.connect(lambda: self._redraw(which='all'))
        self._coordinator.sigCurrentCoilPositionChanged.connect(lambda: self._redraw(which=['updatePosition']))

    def _getCoord(self, orientation: str, depth: tp.Union[str, ProjectionSpecification]) -> tp.Optional[np.ndarray]:
        match depth:
            case ProjectionSpecification():
                raise NotImplementedError  # TODO
            case 'coil':
                match orientation:
                    case 'target':
                        if self._coordinator.currentTarget is None:
                            coilCoord = None
                        else:
                            coilCoord = self._coordinator.currentTarget.entryCoordPlusDepthOffset
                    case 'coil':
                        transf = self._coordinator.currentCoilToMRITransform
                        if transf is None:
                            coilCoord = None
                        else:
                            coilCoord = applyTransform(transf, np.asarray([0, 0, 0]))
                    case _:
                        raise NotImplementedError
                return coilCoord
            case 'skin':
                raise NotImplementedError  # TODO
            case 'gm':
                raise NotImplementedError  # TODO
            case 'target':
                match orientation:
                    case 'target':
                        if self._coordinator.currentTarget is None:
                            targetCoord = None
                        else:
                            targetCoord = self._coordinator.currentTarget.targetCoord
                    case 'coil':
                        transf = self._coordinator.currentCoilToMRITransform
                        if transf is None:
                            targetCoord = None
                        else:
                            targetCoord = applyTransform(transf, np.asarray([0, 0, -np.linalg.norm(
                                self._coordinator.currentTarget.entryCoordPlusDepthOffset - self._coordinator.currentTarget.targetCoord)
                                                                             ]))
                    case _:
                        raise NotImplementedError
                return targetCoord
            case _:
                raise NotImplementedError

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
            else:
                setActorUserTransform(actor, composeTransform(np.eye(3), coord))
                if not actor.GetVisibility():
                    actor.VisibilityOn()

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
        self.addLayer(TargetingPointLayer, 'coilCoilPoint', color='#00ff00', orientation='coil', depth='coil')
        self.addLayer(TargetingPointLayer, 'coilTargetPoint', color='#00ff00', orientation='coil', depth='target')
        if False:  # TODO: debug, enable by default
            self.addLayer(TargetingPointLayer, 'coilProjectedTargetPoint', color='#00ff00', orientation='coil',
                          depth=ProjectionSpecification(toOrientation='target', toDepth='target', toShape='sphere'))


