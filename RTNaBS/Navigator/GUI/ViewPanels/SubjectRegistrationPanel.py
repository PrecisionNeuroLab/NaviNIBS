from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import logging
import numpy as np
import os
import pathlib
import pyvista as pv
import pyvistaqt as pvqt
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from . import MainViewPanel
from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Navigator.GUI.Widgets.MRIViews import MRISliceView
from RTNaBS.Navigator.GUI.Widgets.SurfViews import Surf3DView
from RTNaBS.util.pyvista import Actor, setActorUserTransform
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import applyTransform, invertTransform, transformToString, stringToTransform
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.util.GUI.QTableWidgetDragRows import QTableWidgetDragRows
from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define
class SubjectRegistrationPanel(MainViewPanel):
    _surfKey: str = 'skinSurf'

    _fidTblWdgt: QtWidgets.QTableWidget = attrs.field(init=False)
    _headPtsTblWdgt: QtWidgets.QTableWidget = attrs.field(init=False)
    _plotter: pvqt.QtInteractor = attrs.field(init=False)
    _actors: tp.Dict[str, tp.Optional[Actor]] = attrs.field(init=False, factory=dict)

    _positionsClient: ToolPositionsClient = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        sidebar = QtWidgets.QWidget()
        sidebar.setLayout(QtWidgets.QVBoxLayout())
        self._wdgt.layout().addWidget(sidebar)

        fiducialsBox = QtWidgets.QGroupBox('Fiducials')
        fiducialsBox.setLayout(QtWidgets.QVBoxLayout())
        sidebar.layout().addWidget(fiducialsBox)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        fiducialsBox.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Sample fiducial')
        btn.clicked.connect(self._onSampleFidBtnClicked)
        # TODO: set this to only be enabled when necessary tools are visible
        btnContainer.layout().addWidget(btn, 0, 0,)

        btn = QtWidgets.QPushButton('Clear fiducial')
        btn.clicked.connect(self._onClearFidBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 0)
        # TODO: change this to "clear fiducials" when multiple selected

        # TODO: add prev/next fiducial buttons for mapping to foot pedal actions
        # (i.e. without requiring click in fidTbl to select different fiducial)

        self._fidTblWdgt = QtWidgets.QTableWidget(0, 2)
        self._fidTblWdgt.setHorizontalHeaderLabels(['Fiducial', 'Sampled'])
        self._fidTblWdgt.cellDoubleClicked.connect(self._onFidTblCellDoubleClicked)
        fiducialsBox.layout().addWidget(self._fidTblWdgt)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        fiducialsBox.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Align to sampled fiducials')
        btn.clicked.connect(self._onAlignToFidBtnClicked)
        # TODO: set this to only be enabled when at least 3 fiducials are sampled
        btnContainer.layout().addWidget(btn, 0, 0, 1, 2)


        headPtsBox = QtWidgets.QGroupBox('Head shape points')
        headPtsBox.setLayout(QtWidgets.QVBoxLayout())
        sidebar.layout().addWidget(headPtsBox)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        headPtsBox.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Sample head point')
        btn.clicked.connect(self._onSampleHeadPtsBtnClicked)
        # TODO: set this to only be enabled when necessary tools are visible
        btnContainer.layout().addWidget(btn, 0, 0, )

        btn = QtWidgets.QPushButton('Clear head point')
        btn.clicked.connect(self._onClearHeadPtsBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 0)
        # TODO: change this to "clear head points" when multiple selected

        self._headPtsTblWdgt = QtWidgets.QTableWidget(0, 2)
        self._headPtsTblWdgt.setHorizontalHeaderLabels(['Head pt #', 'Dist from surf'])
        self._headPtsTblWdgt.cellDoubleClicked.connect(self._onHeadPtsTblCellDoubleClicked)
        headPtsBox.layout().addWidget(self._headPtsTblWdgt)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        headPtsBox.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Refine with sampled head points')
        btn.clicked.connect(self._onAlignToHeadPtsBtnClicked)
        # TODO: set this to only be enabled when have already aligned to fiducials and when sufficient # head points have been sampled
        btnContainer.layout().addWidget(btn, 0, 0, 1, 2)

        sidebar.layout().addStretch()

        self._plotter = pvqt.BackgroundPlotter(
            show=False,
            app=QtWidgets.QApplication.instance()
        )
        self._wdgt.layout().addWidget(self._plotter.interactor)

        self._positionsClient = ToolPositionsClient()
        self._positionsClient.sigLatestPositionsChanged.connect(lambda: self._redraw(which='pointerPosition'))

    def _onPanelActivated(self):
        super()._onPanelActivated()
        self._redraw(which='all')

    def _onSessionSet(self):
        super()._onSessionSet()
        # TODO: connect relevant session changed signals to _redraw calls

        self.session.headModel.sigDataChanged.connect(lambda which: self._redraw(which='initSurf'))
        self.session.subjectRegistration.sigPlannedFiducialsChanged.connect(lambda: self._redraw(which='initPlannedFids'))
        self.session.subjectRegistration.sigSampledFiducialsChanged.connect(lambda: self._redraw(which='initSampledFids'))
        self.session.subjectRegistration.sigSampledHeadPointsChanged.connect(lambda: self._redraw(which='initHeadPts'))
        self.session.subjectRegistration.sigTrackerToMRITransfChanged.connect(lambda: self._redraw(which=[
            'initSampledFids', 'initHeadPts', 'initSubjectTracker', 'pointerPosition']))

    def _redraw(self, which: tp.Union[str, tp.List[str,...]]):

        logger.debug('redraw {}'.format(which))

        if not self._isActivated:
            return

        if isinstance(which, list):
            for subWhich in which:
                self._redraw(which=subWhich)
            return

        if which == 'all':
            which = ['initSurf', 'initSubjectTracker', 'initPointer', 'initPlannedFids', 'initSampledFids', 'initHeadPts']
            self._redraw(which=which)
            return

        elif which == 'initSurf':
            actorKey = 'surf'
            self._actors[actorKey] = self._plotter.add_mesh(mesh=getattr(self.session.headModel, self._surfKey),
                                                          color='#d9a5b2',
                                                          opacity=0.8,  # TODO: make GUI-configurable
                                                          name=actorKey)

        elif which == 'initSubjectTracker':

            subjectTracker = self.session.tools.subjectTracker
            doShowSubjectTracker = self.session.subjectRegistration.trackerToMRITransf is not None \
                                and subjectTracker is not None \
                                and subjectTracker.trackerSurf is not None
            actorKey = 'subjectTracker'
            if not doShowSubjectTracker:
                if actorKey in self._actors:
                    self._plotter.remove_actor(self._actors[actorKey])
                    self._actors.pop(actorKey)

            else:
                self._actors[actorKey] = self._plotter.add_mesh(mesh=self.session.tools.subjectTracker.trackerSurf,
                                                                color='#aaaaaa',
                                                                opacity=0.6,
                                                                name=actorKey)
                self._redraw(which='subjectTrackerPosition')

        elif which in ('initPointer', 'pointerPosition'):
            pointer = self.session.tools.pointer
            subjectTracker = self.session.tools.subjectTracker
            doShowPointer = self.session.subjectRegistration.trackerToMRITransf is not None \
                            and pointer is not None \
                            and pointer.trackerSurf is not None \
                            and subjectTracker is not None

            actorKey = 'pointer'
            if not doShowPointer:
                if actorKey in self._actors:
                    self._plotter.remove_actor(self._actors[actorKey])
                    self._actors.pop(actorKey)
                return

            if which == 'initPointer':
                self._actors[actorKey] = self._plotter.add_mesh(mesh=self.session.tools.pointer.trackerSurf,
                                                                color='#999999',
                                                                opacity=0.6,
                                                                name=actorKey)
                self._redraw(which='pointerPosition')

            elif which == 'pointerPosition':
                if actorKey not in self._actors:
                    self._redraw(which='initPointer')
                    return

                pointerToCameraTransf = self._positionsClient.latestPositions.get(pointer.key, None)
                subjectTrackerToCameraTransf = self._positionsClient.latestPositions.get(subjectTracker.key, None)

                if pointerToCameraTransf is None or subjectTrackerToCameraTransf is None:
                    # don't have valid info for determining pointer position relative to head tracker
                    if self._actors[actorKey].GetVisibility():
                        self._actors[actorKey].VisibilityOff()
                    return

                if not self._actors[actorKey].GetVisibility():
                    self._actors[actorKey].VisibilityOn()

                pointerToSubjectTrackerTransf = invertTransform(subjectTrackerToCameraTransf) @ pointerToCameraTransf

                setActorUserTransform(
                    self._actors[actorKey],
                    self.session.subjectRegistration.trackerToMRITransf @ pointerToSubjectTrackerTransf @ self.session.tools.pointer.stlToTrackerTransf
                )

            else:
                raise NotImplementedError()

        elif which == 'subjectTrackerPosition':
            actorKey = 'subjectTracker'
            if actorKey not in self._actors:
                # subject tracker hasn't been initialized, maybe due to missing information
                return

            setActorUserTransform(
                self._actors[actorKey],
                self.session.subjectRegistration.trackerToMRITransf @ self.session.tools.subjectTracker.stlToTrackerTransf
            )

        elif which == 'initPlannedFids':

            actorKey = 'plannedFids'

            labels = []
            coords = np.full((len(self.session.subjectRegistration.plannedFiducials), 3), np.nan)
            for iFid, (label, coord) in enumerate(self.session.subjectRegistration.plannedFiducials.items()):
                labels.append(label)
                if coord is not None:
                    coords[iFid, :] = coord

            self._actors[actorKey] = self._plotter.add_point_labels(
                name=actorKey,
                points=coords,
                labels=labels,
                point_color='blue',
                text_color='blue',
                point_size=12,
                shape=None,
                render_points_as_spheres=True,
                reset_camera=False,
                render=False
            )

        elif which == 'initSampledFids':

            actorKey = 'sampledFids'

            doShowSampledFids = len(self.session.subjectRegistration.sampledFiducials) > 0 \
                                and self.session.subjectRegistration.trackerToMRITransf is not None

            if not doShowSampledFids:
                # no sampled fiducials or necessary transform for plotting (yet)
                if actorKey in self._actors:
                    self._plotter.remove_actor(actorKey)
                    self._actors.pop(actorKey)
                return

            labels = []
            coords = np.full((len(self.session.subjectRegistration.sampledFiducials), 3), np.nan)
            for iFid, (label, coord) in enumerate(self.session.subjectRegistration.sampledFiducials.items()):
                labels.append(label)
                if coord is not None:
                    coords[iFid, :] = coord

            coords = applyTransform(self.session.subjectRegistration.trackerToMRITransf, coords)

            self._actors[actorKey] = self._plotter.add_point_labels(
                name=actorKey,
                points=coords,
                labels=labels,
                point_color='green',
                text_color='green',
                point_size=15,
                shape=None,
                render_points_as_spheres=True,
                reset_camera=False,
                render=False
            )

        elif which == 'initHeadPts':

            actorKey = 'headPts'

            doShowHeadPts = self.session.subjectRegistration.sampledHeadPoints is not None \
                            and len(self.session.subjectRegistration.sampledHeadPoints.shape[0]) > 0 \
                            and self.session.subjectRegistration.trackerToMRITransf is not None

            if not doShowHeadPts:
                # no sampled head points or necessary transform (yet)
                if actorKey in self._actors:
                    self._plotter.remove_actor(actorKey)
                    self._actors.pop(actorKey)
                return

            coords = applyTransform(self.session.subjectRegistration.trackerToMRITransf, self.session.subjectRegistration.sampledHeadPoints)

            self._actors[actorKey] = self._plotter.add_points(
                name=actorKey,
                points=coords,
                color='red',
                point_size=10,
                render_points_as_spheres=True,
                reset_camera=False,
                render=False
            )

        else:
            raise NotImplementedError('Unexpected redraw key: {}'.format(which))

