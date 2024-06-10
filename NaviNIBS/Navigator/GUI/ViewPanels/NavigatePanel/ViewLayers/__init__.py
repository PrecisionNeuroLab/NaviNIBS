from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import pyvista as pv
import pyvistaqt as pvqt
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp
from typing import ClassVar

from NaviNIBS.Navigator.GUI.ViewPanels.NavigatePanel.NavigationView import TargetingCoordinator
from NaviNIBS.util import classproperty

from NaviNIBS.util.GUI.QueuedRedrawMixin import QueuedRedrawMixin
from NaviNIBS.util.pyvista import Actor, setActorUserTransform, concatenateLineSegments
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter, RemotePlotterProxy



logger = logging.getLogger(__name__)


Transform = np.ndarray


@attrs.define
class LegendEntry:
    _label: str
    _color: tp.Any

    """
    Note: attributes below are shared by entire legend, so each entry just provides its preference but may not 
    actually be granted these values. Or in the future, may implement multiple / more flexible pyvista legends
    to support showing these on a per-entry basis
    """
    _bcolor: tp.Any | None = None
    _border: bool = False
    _loc: str = 'upper right'
    _face: str | pv.PolyData | None = 'triangle'

    _legendActor: pv.Actor | None = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        pass

    @property
    def label(self):
        return self._label

    @property
    def color(self):
        return self._color

    @property
    def bcolor(self):
        return

    @property
    def border(self):
        return self._border

    @property
    def loc(self):
        return self._loc

    @property
    def face(self):
        return self._face


@attrs.define
class ViewLayer:
    _key: str
    _type: ClassVar[str]
    _coordinator: TargetingCoordinator

    def __attrs_post_init__(self):
        pass

    @classproperty
    def type(cls):
        return cls._type


@attrs.define
class PlotViewLayer(ViewLayer, QueuedRedrawMixin):
    _plotter: DefaultBackgroundPlotter  # note that this one plotter may be shared between multiple ViewLayers
    _plotInSpace: str = 'MRI'

    _actors: dict[str, Actor | None] = attrs.field(init=False, factory=dict)
    _legendEntries: list[LegendEntry] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        ViewLayer.__attrs_post_init__(self)
        QueuedRedrawMixin.__attrs_post_init__(self)
        self._redraw('all')

    @property
    def legendEntries(self):
        return self._legendEntries

    def _registerLegendEntry(self, entry: LegendEntry):
        self._legendEntries.append(entry)

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):
        QueuedRedrawMixin._redraw(self, which=which)

        if isinstance(self._plotter, RemotePlotterProxy) and not self._plotter.isReadyEvent.is_set():
            # remote plotter not ready yet
            return

        #logger.debug('redraw {}'.format(which))

        if which is None:
            which = 'all'
            self._redraw(which=which)
            return

        if not isinstance(which, str):
            for subWhich in which:
                self._redraw(which=subWhich)
            return

        # subclass should handle the rest

    def _getActorKey(self, subKey: str) -> str:
        return self._key + '_' + subKey  # make actor keys unique across multiple layers in the same plotter
