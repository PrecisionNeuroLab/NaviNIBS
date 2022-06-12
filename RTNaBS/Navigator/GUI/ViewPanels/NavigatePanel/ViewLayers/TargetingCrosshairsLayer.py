from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import pyvista as pv
import typing as tp
from typing import ClassVar

from . import PlotViewLayer
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define
class TargetingCrosshairsLayer(PlotViewLayer):
    _type: ClassVar[str]

    _targetOrCoil: str = 'target'

    _color: str = '#0000ff'
    _opacity: float = 0.5
    _radius: float = 10.
    _offsetRadius: float = 5.
    _lineWidth: float = 4.

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.sigCurrentTargetChanged.connect(lambda: self._redraw(which='initCrosshair'))
        self._coordinator.sigCurrentCoilPositionChanged.connect(lambda: self._redraw(which=['updatePositions', 'crosshairVisibility']))

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        super()._redraw(which=which)

        if not isinstance(which, str):
            # assume parent call above triggered appropriate redraws
            return

        if which == 'all':
            which = ['initCrosshair']
            self._redraw(which=which)
            return

        elif which == 'clearCrosshair':
            actorKey = self._getActorKey('crosshair')
            if actorKey in self._actors:
                actor = self._actors.pop(actorKey)
                self._plotter.remove_actor(actor)

        elif which == 'crosshairVisibility':
            actorKey = self._getActorKey('crosshair')
            if actorKey in self._actors:
                actor = self._actors[actorKey]

                doShow = self._canShow()

                if actor.GetVisibility() != doShow:
                    actor.SetVisibility(doShow)

        elif which == 'initCrosshair':
            actorKey = self._getActorKey('crosshair')

            target = self._coordinator.currentTarget
            if target is None:
                if self._targetOrCoil == 'target':
                    # no active target, cannot plot
                    self._redraw(which='clearCrosshair')
                    return
                elif self._targetOrCoil == 'coil':
                    # use an estimated zOffset until we have a target
                    zOffset = -10.
                else:
                    raise NotImplementedError()
            else:
                # distance from bottom of coil to target (presumably in brain)
                zOffset = -1 * np.linalg.norm(target.entryCoordPlusDepthOffset - target.targetCoord)

            lines = self._getCrosshairLineSegments(radius=self._radius)

            offsetLines = self._getCrosshairLineSegments(radius=self._offsetRadius, zOffset=zOffset)

            depthLine = pv.utilities.lines_from_points(np.asarray([[0, 0, 0], [0, 0, zOffset]]))

            self._actors[actorKey] = addLineSegments(self._plotter,
                                                     concatenateLineSegments([lines, offsetLines, depthLine]),
                                                     name=actorKey,
                                                     color=self._color,
                                                     width=self._lineWidth,
                                                     opacity=self._opacity)

            self._redraw(which=['updatePositions', 'crosshairVisibility'])

        elif which == 'updatePositions':
            if not self._canShow():
                return

            actorKey = self._getActorKey('crosshair')
            if actorKey not in self._actors:
                self._redraw(which='initCrosshair')
                return

            actor = self._actors[actorKey]

            if self._plotInSpace != 'MRI':
                raise NotImplementedError()  # TODO: add necessary transforms for plotting in other spaces below

            if self._targetOrCoil == 'target':
                currentTargetToMRITransform = self._coordinator.currentTarget.coilToMRITransf
                setActorUserTransform(actor, currentTargetToMRITransform)

            elif self._targetOrCoil == 'coil':
                currentCoilToMRITransform = self._coordinator.currentCoilToMRITransform
                setActorUserTransform(actor, currentCoilToMRITransform)

            else:
                raise NotImplementedError()

        else:
            raise NotImplementedError('Unexpected redraw which: {}'.format(which))

    def _canShow(self) -> bool:
        canShow = True
        hasTarget = self._coordinator.currentTarget
        hasCoil = self._coordinator.currentCoilToMRITransform is not None

        if self._plotInSpace != 'MRI':
            raise NotImplementedError()  # TODO: check that we have necessary info to convert to other coordinate spaces

        if self._targetOrCoil == 'target':
            if not hasTarget:
                canShow = False
        elif self._targetOrCoil == 'coil':
            if not hasCoil:
                canShow = False
        else:
            raise NotImplementedError()
        return canShow

    @classmethod
    def _getCircleLines(cls, radius: float, numPts: int = 300) -> pv.PolyData:
        points = np.zeros((numPts, 3))
        theta = np.linspace(0, 2 * np.pi, numPts)
        points[:, 0] = radius * np.cos(theta)
        points[:, 1] = radius * np.sin(theta)
        return pv.utilities.lines_from_points(points)

    @classmethod
    def _getCrosshairLineSegments(cls,
                                  radius: float,
                                  numPtsInCircle: int = 300,
                                  zOffset: float = 0.,
                                  ) -> pv.PolyData:
        circle = cls._getCircleLines(radius=radius, numPts=numPtsInCircle)
        relNotchLength = 0.2
        # TODO: check signs and directions
        topNotch = pv.utilities.lines_from_points(np.asarray([[0, radius, 0], [0, radius * (1 - relNotchLength), 0]]))
        botNotch = pv.utilities.lines_from_points(
            np.asarray([[0, -radius * (1 + relNotchLength), 0], [0, -radius * (1 - relNotchLength), 0]]))
        leftNotch = pv.utilities.lines_from_points(
            np.asarray([[-radius, 0, 0], [-radius * (1 - relNotchLength), 0, 0]]))
        rightNotch = pv.utilities.lines_from_points(np.asarray([[radius, 0, 0], [radius * (1 - relNotchLength), 0, 0]]))

        lines = concatenateLineSegments([circle, botNotch, topNotch, leftNotch, rightNotch])

        lines.points += np.asarray([[0, 0, zOffset]])

        return lines


@attrs.define
class TargetingTargetCrosshairsLayer(TargetingCrosshairsLayer):
    _type: ClassVar[str] = 'TargetingTargetCrosshairs'
    _targetOrCoil: str = 'target'

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class TargetingCoilCrosshairsLayer(TargetingCrosshairsLayer):
    _type: ClassVar[str] = 'TargetingCoilCrosshairs'
    _targetOrCoil: str = 'coil'

    _color: str = '#00ff00'
    _radius: float = 10.
    _offsetRadius: float = 5.
    _lineWidth: float = 8.

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
