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
from RTNaBS.Navigator.GUI.ViewPanels.NavigatePanel.TargetingCoordinator import ProjectionSpecification
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments
from RTNaBS.util.Transforms import applyTransform, concatenateTransforms, invertTransform, composeTransform


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define(kw_only=True)
class TargetingAngleErrorLayer(PlotViewLayer):
    _type: ClassVar[str] = 'TargetingAngleError'

    _color: str = '#ff5500'
    _opacity: float = 0.5
    _lineWidth: float = 4.
    _radius: float = 7.5
    _multiplier: float = 1.

    _numArcSegments: int = 180  # TODO: check whether this needs to be reduced to improve render performance

    _angleMetric: str = 'Depth angle error'
    _angleOffset: float = -np.pi / 2
    _plotOnTargetOrCoil: str = 'target'
    _xyDims: tuple[int, int] = (0, 1)  # dimensions defining plane for angle visual

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.sigCurrentTargetChanged.connect(lambda: self._redraw(which='updateAngle'))
        self._coordinator.sigCurrentCoilPositionChanged.connect(lambda: self._redraw(which=['updateAngle']))

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
            pts_line = np.linspace([0, 0, 0], [1, 1, 1], self._numArcSegments + 1)

            self._actors[actorKey] = self._plotter.add_lines(pts_line,
                                                             color=self._color,
                                                             width=self._lineWidth,
                                                             name=actorKey)

            self._redraw(which='updateAngle')

        elif which == 'updateAngle':
            actor = self._actors[actorKey]

            hasTarget = self._coordinator.currentTarget
            hasCoil = self._coordinator.currentCoilToMRITransform is not None
            angle = self._coordinator.currentPoseMetrics.getValueForMetric(label=self._angleMetric)
            hasAngle = not np.isnan(angle)
            angle = np.deg2rad(angle) * self._multiplier

            doShow = hasTarget and hasCoil and hasAngle

            if doShow:
                pts_pv = self._getArcLines(startAngle=self._angleOffset,
                                           endAngle=angle + self._angleOffset,
                                           xyDims=self._xyDims,
                                           radius=self._radius,
                                           numPts=self._numArcSegments+1)
                actor.GetMapper().SetInputData(pts_pv)

                if self._plotOnTargetOrCoil == 'target':
                    currentTargetToMRITransform = self._coordinator.currentTarget.coilToMRITransf
                    setActorUserTransform(actor, currentTargetToMRITransform)

                elif self._plotOnTargetOrCoil == 'coil':
                    currentCoilToMRITransform = self._coordinator.currentCoilToMRITransform
                    setActorUserTransform(actor, currentCoilToMRITransform)

                else:
                    raise NotImplementedError

                if not actor.GetVisibility():
                    actor.VisibilityOn()

            else:
                if actor.GetVisibility():
                    actor.VisibilityOff()

            self._plotter.render()
        else:
            raise NotImplementedError(f'Unexpected redraw which: {which}')

    @classmethod
    def _getArcLines(cls, startAngle: float, endAngle: float,
                     xyDims: tuple[int, int],
                     radius: float, numPts: int = 300,) -> pv.PolyData:
        points = np.zeros((numPts, 3))
        theta = np.linspace(startAngle, endAngle, numPts)
        points[:, xyDims[0]] = radius * np.cos(theta)
        points[:, xyDims[1]] = radius * np.sin(theta)
        return pv.utilities.lines_from_points(points)