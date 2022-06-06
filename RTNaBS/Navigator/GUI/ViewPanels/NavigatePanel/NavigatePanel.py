from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import logging
import multiprocessing as mp
import numpy as np
import os
import pathlib
import pyvista as pv
import pyvistaqt as pvqt
from pyqtgraph.dockarea import DockArea, Dock
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp
from typing import ClassVar

from .. import MainViewPanel
from .NavigationView import NavigationView, TargetingCrosshairsView
from .TargetingCoordinator import TargetingCoordinator
from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Devices.IGTLinkToolPositionsServer import IGTLinkToolPositionsServer
from RTNaBS.Navigator.Model.Session import Session, Tool, CoilTool, SubjectTracker, Target
from RTNaBS.util.pyvista import Actor, setActorUserTransform, addLineSegments, concatenateLineSegments
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget


logger = logging.getLogger(__name__)


Transform = np.ndarray




@attrs.define
class NavigatePanel(MainViewPanel):
    _targetsTblWdgt: QtWidgets.QTableWidget = attrs.field(init=False)
    _samplesTblWdgt: QtWidgets.QTableWidget = attrs.field(init=False)
    _views: tp.Dict[str, NavigationView] = attrs.field(init=False, factory=dict)
    _viewsDock: DockArea = attrs.field(init=False)

    _coordinator: TargetingCoordinator = attrs.field(init=False)

    _hasInitialized: bool = attrs.field(init=False, default=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        sidebar = QtWidgets.QWidget()
        sidebar.setLayout(QtWidgets.QVBoxLayout())
        sidebar.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.MinimumExpanding)
        self._wdgt.layout().addWidget(sidebar)

        targetsBox = QtWidgets.QGroupBox('Targets')
        targetsBox.setLayout(QtWidgets.QVBoxLayout())
        sidebar.layout().addWidget(targetsBox)

        self._targetsTblWdgt = QtWidgets.QTableWidget()

        self._viewsDock = DockArea()
        self._wdgt.layout().addWidget(self._viewsDock)

    def _onPanelActivated(self):
        super()._onPanelActivated()
        if not self._hasInitialized:
            self._initializePanel()

    def _onSessionSet(self):
        super()._onSessionSet()
        if self._hasInitialized:
            raise NotImplementedError()  # TODO: handle change in session when previously initialized with different session
        else:
            # we'll handle the new session when initializing later
            pass

    def _initializePanel(self):
        assert not self._hasInitialized
        self._hasInitialized = True
        self._coordinator = TargetingCoordinator(session=self._session)
        self._initializeDefaultViews()  # TODO: only do this if not restoring from previously saved config

    def _initializeDefaultViews(self):
        if len(self._views) > 0:
            raise NotImplementedError()  # TODO: clear any previous views from dock and self._views

        self.addView(key='Crosshairs', viewType='TargetingCrosshairs')

        # TODO: set up other default views

    def addView(self, key: str, viewType: str, **kwargs):

        match viewType:
            case 'TargetingCrosshairs':
                View = TargetingCrosshairsView
            case _:
                raise NotImplementedError('Unexpected viewType: {}'.format(viewType))

        # TODO: maybe add optional code here to generate unique key using input as a base key
        assert key not in self._views

        view = View(key=key, coordinator=self._coordinator, **kwargs)

        assert view.key not in self._views
        self._views[view.key] = view
        self._viewsDock.addDock(view.dock)

