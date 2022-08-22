from __future__ import annotations

import attrs
import logging
import numpy as np
import pyvista as pv
import pyvistaqt as pvqt
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp
from typing import ClassVar

from RTNaBS.Navigator.GUI.ViewPanels.NavigatePanel.NavigationView import TargetingCoordinator
from RTNaBS.util import classproperty
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments
from RTNaBS.util.pyvista.plotting import BackgroundPlotter


logger = logging.getLogger(__name__)


Transform = np.ndarray


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
class PlotViewLayer(ViewLayer):
    _plotter: BackgroundPlotter  # note this this one plotter may be shared between multiple ViewLayers
    _plotInSpace: str = 'MRI'

    _actors: tp.Dict[str, tp.Optional[Actor]] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self._redraw('all')

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str, ...]] = None):

        #logger.debug('redraw {}'.format(which))

        if which is None:
            which = 'all'

        if not isinstance(which, str):
            for subWhich in which:
                self._redraw(which=subWhich)
            return

    def _getActorKey(self, subKey: str) -> str:
        return self._key + '_' + subKey  # make actor keys unique across multiple layers in the same plotter
