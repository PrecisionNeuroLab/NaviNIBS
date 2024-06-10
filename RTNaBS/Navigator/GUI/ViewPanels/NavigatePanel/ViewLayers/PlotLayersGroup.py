from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import pyvista as pv
import typing as tp
from typing import ClassVar

from . import PlotViewLayer
from NaviNIBS.util.pyvista import Actor, setActorUserTransform, concatenateLineSegments
from NaviNIBS.util.Transforms import applyTransform, concatenateTransforms, invertTransform, composeTransform


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define
class PlotLayersGroup(PlotViewLayer):
    _type: ClassVar[str] = 'Group'
    _layers: tp.Dict[str, PlotViewLayer] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def addLayer(self, cls: tp.Callable[..., PlotViewLayer], key: str, **kwargs):
        assert key not in self._layers
        self._layers[key] = cls(key=key, **kwargs,
                                coordinator=self._coordinator,
                                plotter=self._plotter,
                                plotInSpace=self._plotInSpace)

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        if not isinstance(which, str):
            # assume parent call above triggered appropriate redraws
            return

        if which == 'all':
            which = 'layers'
            self._redraw(which=which)
            return

        if which == 'layers':
            for layer in self._layers.values():
                layer._redraw()

        else:
            raise NotImplementedError

