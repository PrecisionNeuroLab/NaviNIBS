from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import pyvista as pv
import typing as tp
from typing import ClassVar

from . import PlotViewLayer
from RTNaBS.Navigator.Model.Samples import Sample, Samples
from RTNaBS.Navigator.Model.Targets import Target, Targets
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define
class VisualizedOrientation:
    """
    Note: this doesn't connect to any change signals from underlying sample, instead assuming that caller will
    re-instantiate for any changes as needed
    """
    _orientation: tp.Union[Sample, Target]
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
            setActorUserTransform(actor, self._orientation.coilToMRITransf)

    @property
    def actors(self):
        return self._actors


@attrs.define
class OrientationsLayer(PlotViewLayer):
    _type: ClassVar[str] = 'Orientations'
    _colorDepthIndicator: str = '#ba55d3'
    _colorHandleIndicator: str = '#9370db'
    _colorDepthIndicatorSelected: str = '#8b008b'
    _colorHandleIndicatorSelected: str = '#4b0082'
    _opacity: float = 0.5
    _lineWidth: float = 3.
    _style: str = 'lines'

    _visualizedOrientations: tp.Dict[str, VisualizedOrientation] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _orientationIsVisible(self, key: str) -> bool:
        """
        Whether sample or target should be visible (rendered). Could be because it is marked as always visible, or because it is currently selected.
        """
        raise NotImplementedError  # should be implemented by subclass

    @property
    def orientations(self) -> tp.Union[Samples, Targets]:
        raise NotImplementedError  # should be implemented by subclass

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None, changedOrientationKeys: tp.Optional[tp.List[str]] = None):
        super()._redraw(which=which)

        if not isinstance(which, str):
            # assume parent call above triggered appropriate redraws
            return

        if which == 'all':
            which = ['orientations']
            self._redraw(which=which, changedOrientationKeys=changedOrientationKeys)

        elif which == 'orientations':
            if changedOrientationKeys is None:
                changedOrientationKeys = list(set(self._visualizedOrientations.keys()) | set(self.orientations.keys()))

            for key in changedOrientationKeys:
                if key in self._visualizedOrientations:
                    for actorKey in self._visualizedOrientations.pop(key).actors:
                        self._plotter.remove_actor(self._actors.pop(actorKey))

                if self._orientationIsVisible(key):
                    isSelected = self.orientations[key].isSelected
                    logger.debug(f'Instantiating visualized orientation for {key}')
                    self._visualizedOrientations[key] = VisualizedOrientation(
                        orientation=self.orientations[key],
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

@attrs.define
class SampleOrientationsLayer(OrientationsLayer):
    _type: ClassVar[str] = 'SampleOrientations'

    _colorDepthIndicator: str = '#ba55d3'
    _colorHandleIndicator: str = '#9370db'
    _colorDepthIndicatorSelected: str = '#8b008b'
    _colorHandleIndicatorSelected: str = '#4b0082'

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.session.samples.sigSamplesChanged.connect(self._onSamplesChanged)

    @OrientationsLayer.orientations.getter
    def orientations(self) -> Samples:
        return self._coordinator.session.samples

    def _orientationIsVisible(self, key: str) -> bool:
        return key in self._coordinator.session.samples and \
                self._coordinator.session.samples[key].coilToMRITransf is not None and \
               (self._coordinator.session.samples[key].isVisible or
                self._coordinator.session.samples[key].isSelected)

    def _onSamplesChanged(self, changedKeys: tp.List[str], changedAttrs: tp.Optional[tp.List[str]]):
        self._redraw(which='orientations', changedOrientationKeys=changedKeys)


@attrs.define
class TargetOrientationsLayer(OrientationsLayer):
    _type: ClassVar[str] = 'TargetOrientations'

    _colorDepthIndicator: str = '#2a25e3'
    _colorHandleIndicator: str = '#2320eb'
    _colorDepthIndicatorSelected: str = '#2b20ab'
    _colorHandleIndicatorSelected: str = '#2b20a2'
    _lineWidth: float = 4.5

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.session.targets.sigTargetsChanged.connect(self._onTargetsChanged)

    @OrientationsLayer.orientations.getter
    def orientations(self) -> Targets:
        return self._coordinator.session.targets

    def _orientationIsVisible(self, key: str) -> bool:
        return key in self._coordinator.session.targets and \
               self._coordinator.session.targets[key].coilToMRITransf is not None and \
               (self._coordinator.session.targets[key].isVisible or
                self._coordinator.session.targets[key].isSelected)

    def _onTargetsChanged(self, changedKeys: tp.List[str], changedAttrs: tp.Optional[tp.List[str]]):
        self._redraw(which='orientations', changedOrientationKeys=changedKeys)
