from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import pyvista as pv
import typing as tp
from typing import ClassVar

from . import PlotViewLayer
from NaviNIBS.Navigator.Model.Samples import Sample, Samples
from NaviNIBS.Navigator.Model.Targets import Target, Targets
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter, RemotePlotterProxy
from NaviNIBS.util.pyvista import Actor, setActorUserTransform, concatenateLineSegments


logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define
class VisualizedOrientation:
    """
    Note: this doesn't connect to any change signals from underlying sample, instead assuming that caller will
    re-instantiate for any changes as needed
    """
    _orientation: tp.Union[Sample, Target]
    _plotter: DefaultBackgroundPlotter = attrs.field(repr=False)
    _colorDepthIndicator: str | tuple[float, str]  # string color, or tuple of (float scalar, string colorbar label)
    _colorHandleIndicator: str | tuple[float, str]  # string color, or tuple of (float scalar, string colorbar label)
    _opacity: float
    _lineWidth: float
    _style: str
    _actorKeyPrefix: str

    _actors: tp.Dict[str, Actor] = attrs.field(init=False, factory=dict, repr=False)

    def __attrs_post_init__(self):
        match self._style:
            case 'line':
                raise NotImplementedError  # TODO
            case 'lines':
                # depth axis line plus small handle orientation indicator
                zOffset = -20
                handleLength = 2
                depthLine = pv.lines_from_points(np.asarray([[0, 0, 0], [0, 0, zOffset]]))
                handleLine = pv.lines_from_points(np.asarray([[0, 0, 0], [0, -handleLength, 0]]))

                actorKey = self._actorKeyPrefix + 'depthLine'

                if isinstance(self._colorDepthIndicator, tuple):
                    depthLine[self._colorDepthIndicator[1]] = np.full((depthLine.n_points,), self._colorDepthIndicator[0])
                    scalars = self._colorDepthIndicator[1]
                    scalar_bar_args = {'title': self._colorDepthIndicator[1]}
                    color='k'
                else:
                    scalars = None
                    scalar_bar_args = None
                    color=self._colorDepthIndicator

                self._actors[actorKey] = self._plotter.addLineSegments(
                                                         depthLine,
                                                         name=actorKey,
                                                         color=color,
                                                         scalars=scalars,
                                                         scalar_bar_args=scalar_bar_args,
                                                         width=self._lineWidth,
                                                         opacity=self._opacity,
                                                         userTransform=self._orientation.coilToMRITransf,
                )


                actorKey = self._actorKeyPrefix + 'handleLine'

                if isinstance(self._colorHandleIndicator, tuple):
                    handleLine[self._colorHandleIndicator[1]] = np.full((handleLine.n_points,), self._colorHandleIndicator[0])
                    scalars = self._colorHandleIndicator[1]
                    scalar_bar_args = {'title': self._colorHandleIndicator[1]}
                    color='k'
                else:
                    scalars = None
                    scalar_bar_args = None
                    color=self._colorHandleIndicator

                self._actors[actorKey] = self._plotter.addLineSegments(
                                                         handleLine,
                                                         name=actorKey,
                                                         color=color,
                                                         scalars=scalars,
                                                         scalar_bar_args=scalar_bar_args,
                                                         width=self._lineWidth,
                                                         opacity=self._opacity,
                                                         userTransform=self._orientation.coilToMRITransf,
                )

            case _:
                raise NotImplementedError(f'Unexpected style: {self._style}')

        with self._plotter.allowNonblockingCalls():
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
    _loopTask: asyncio.Task = attrs.field(init=False)
    _hasPendingOrientations: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    _pendingOrientationKeys: set[str] = attrs.field(init=False, factory=set)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._loopTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._loop_drawPendingOrientations))

    def _createVisualizedOrientationForSample(self, key: str) -> VisualizedOrientation:
        isSelected = self.orientations[key].isSelected
        return VisualizedOrientation(
            orientation=self.orientations[key],
            plotter=self._plotter,
            colorHandleIndicator=self._colorHandleIndicatorSelected if isSelected else self._colorHandleIndicator,
            colorDepthIndicator=self._colorDepthIndicatorSelected if isSelected else self._colorDepthIndicator,
            opacity=self._opacity,
            lineWidth=self._lineWidth,
            style=self._style,
            actorKeyPrefix=self._getActorKey(key)
        )

    async def _loop_drawPendingOrientations(self):
        """
        Handle drawing of orientations in async loop here, with a yield to make sure we give
        GUI time to handle other tasks during drawing of many orientations (e.g. drawing hundreds of samples)
        """
        while True:
            await asyncio.sleep(0)
            await self._hasPendingOrientations.wait()
            if len(self._pendingOrientationKeys) == 0:
                self._hasPendingOrientations.clear()
                continue

            key = self._pendingOrientationKeys.pop()

            if self._orientationIsVisible(key):
                logger.debug(f'Instantiating visualized orientation for {key}')
                self._visualizedOrientations[key] = self._createVisualizedOrientationForSample(key)
                for actorKey, actor in self._visualizedOrientations[key].actors.items():
                    self._actors[actorKey] = actor
            else:
                logger.debug(f'Skipping drawing of {key} since it should not be visible')

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
                    with self._plotter.allowNonblockingCalls():
                        for actorKey in self._visualizedOrientations.pop(key).actors:
                            self._plotter.remove_actor(self._actors.pop(actorKey))

                self._pendingOrientationKeys.add(key)
                self._hasPendingOrientations.set()  # tell orientation drawing loop to check for new key(s) to draw


@attrs.define
class SampleOrientationsLayer(OrientationsLayer):
    _type: ClassVar[str] = 'SampleOrientations'

    _colorDepthIndicator: str = '#ba55d3'
    _colorHandleIndicator: str = '#9370db'
    _colorDepthIndicatorSelected: str = '#8b008b'
    _colorHandleIndicatorSelected: str = '#4b0082'

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coordinator.session.samples.sigItemsChanged.connect(self._onSamplesChanged)

    @OrientationsLayer.orientations.getter
    def orientations(self) -> Samples:
        return self._coordinator.session.samples

    def _orientationIsVisible(self, key: str) -> bool:
        return key in self._coordinator.session.samples and \
                self._coordinator.session.samples[key].coilToMRITransf is not None and \
               (self._coordinator.session.samples[key].isVisible or
                self._coordinator.session.samples[key].isSelected)

    def _onSamplesChanged(self, changedKeys: tp.List[str], changedAttrs: tp.Optional[tp.List[str]]):
        self._queueRedraw(which='orientations', changedOrientationKeys=changedKeys)


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

        self._coordinator.session.targets.sigItemsChanged.connect(self._onTargetsChanged)

    @OrientationsLayer.orientations.getter
    def orientations(self) -> Targets:
        return self._coordinator.session.targets

    def _orientationIsVisible(self, key: str) -> bool:
        return key in self._coordinator.session.targets and \
               self._coordinator.session.targets[key].coilToMRITransf is not None and \
               (self._coordinator.session.targets[key].isVisible or
                self._coordinator.session.targets[key].isSelected)

    def _onTargetsChanged(self, changedKeys: tp.List[str], changedAttrs: tp.Optional[tp.List[str]]):
        self._queueRedraw(which='orientations', changedOrientationKeys=changedKeys)
