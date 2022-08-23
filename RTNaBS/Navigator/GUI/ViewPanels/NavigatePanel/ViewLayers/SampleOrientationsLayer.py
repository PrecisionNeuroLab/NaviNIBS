from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import pyvista as pv
import typing as tp
from typing import ClassVar

from . import PlotViewLayer
from RTNaBS.Navigator.Model.Samples import Sample
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define
class VisualizedSampleOrientation:
    """
    Note: this doesn't connect to any change signals from underlying sample, instead assuming that caller will
    re-instantiate for any changes as needed
    """
    _sample: Sample
    _plotter: pv.Plotter
    _colorDepthIndicator: str
    _colorHandleIndicator: str
    _opacity: float
    _lineWidth: float
    _style: str
    _actorKeyPrefix: str

    _actors: tp.Dict[str, Actor] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        match self._style:
            case 'line':
                raise NotImplementedError  # TODO
            case 'lines':
                # depth axis line plus small handle orientation indicator
                zOffset = -20
                handleLength = 2
                depthLine = pv.utilities.lines_from_points(np.asarray([[0, 0, 0], [0, 0, zOffset]]))
                handleLine = pv.utilities.lines_from_points(np.asarray([[0, 0, 0], [0, -handleLength, 0]]))
                actorKey = self._actorKeyPrefix + 'depthLine'
                self._actors[actorKey] = addLineSegments(self._plotter,
                                                         depthLine,
                                                         name=actorKey,
                                                         color=self._colorDepthIndicator,
                                                         width=self._lineWidth,
                                                         opacity=self._opacity)

                actorKey = self._actorKeyPrefix + 'handleLine'
                self._actors[actorKey] = addLineSegments(self._plotter,
                                                         handleLine,
                                                         name=actorKey,
                                                         color=self._colorHandleIndicator,
                                                         width=self._lineWidth,
                                                         opacity=self._opacity)

            case _:
                raise NotImplementedError(f'Unexpected style: {self._style}')

        for actor in self._actors.values():
            setActorUserTransform(actor, self._sample.coilToMRITransf)

    @property
    def actors(self):
        return self._actors


@attrs.define
class SampleOrientationsLayer(PlotViewLayer):
    _type: ClassVar[str] = 'SampleOrientations'

    _colorDepthIndicator: str = '#ba55d3'
    _colorHandleIndicator: str = '#9370db'
    _colorDepthIndicatorSelected: str = '#8b008b'
    _colorHandleIndicatorSelected: str = '#4b0082'
    _opacity: float = 0.5
    _lineWidth: float = 3.
    _style: str = 'lines'

    _visualizedOrientations: tp.Dict[str, VisualizedSampleOrientation] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.session.samples.sigSamplesChanged.connect(self._onSamplesChanged)

    def _sampleIsVisible(self, key: str) -> bool:
        """
        Whether sample should be visible (rendered). Could be because it is marked as always visible, or because it is currently selected.
        """
        return key in self._coordinator.session.samples and \
                self._coordinator.session.samples[key].coilToMRITransf is not None and \
               (self._coordinator.session.samples[key].isVisible or
                self._coordinator.session.samples[key].isSelected)

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None, changedSampleKeys: tp.Optional[tp.List[str]] = None):
        super()._redraw(which=which)

        if not isinstance(which, str):
            # assume parent call above triggered appropriate redraws
            return

        if which == 'all':
            which = ['samples']
            self._redraw(which=which, changedSampleKeys=changedSampleKeys)

        elif which == 'samples':
            if changedSampleKeys is None:
                changedSampleKeys = list(set(self._visualizedOrientations.keys()) | set(self._coordinator.session.samples.keys()))

            for key in changedSampleKeys:
                if key in self._visualizedOrientations:
                    for actorKey in self._visualizedOrientations.pop(key).actors:
                        self._plotter.remove_actor(self._actors.pop(actorKey))

                if self._sampleIsVisible(key):
                    isSelected = self._coordinator.session.samples[key].isSelected
                    self._visualizedOrientations[key] = VisualizedSampleOrientation(
                        sample=self._coordinator.session.samples[key],
                        plotter=self._plotter,
                        colorHandleIndicator=self._colorHandleIndicatorSelected if isSelected else self._colorHandleIndicator,
                        colorDepthIndicator=self._colorDepthIndicatorSelected if isSelected else self._colorDepthIndicator,
                        opacity=self._opacity,
                        lineWidth=self._lineWidth,
                        style=self._style,
                        actorKeyPrefix=self._getActorKey(key)
                    )
                    for actorKey, actor in self._visualizedOrientations[key].actors.items():
                        self._actors[actorKey] = actor

    def _onSamplesChanged(self, changedKeys: tp.List[str], changedAttrs: tp.Optional[tp.List[str]]):
        self._redraw(which='samples', changedSampleKeys=changedKeys)

