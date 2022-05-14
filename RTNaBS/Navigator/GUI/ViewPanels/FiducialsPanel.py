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
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from . import MainViewPanel
from RTNaBS.Navigator.GUI.Widgets.MRIViews import MRISliceView
from RTNaBS.Navigator.GUI.Widgets.SurfViews import Surf3DView
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define
class FiducialsPanel(MainViewPanel):
    _tblWdgt: QtWidgets.QTableWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[MRISliceView, Surf3DView]] = attrs.field(init=False, factory=dict)
    _hasBeenActivated: bool = attrs.field(init=False, default=False)
    _surfKey: str = 'skinSurf'
    _fiducialActors: tp.Dict[str, tp.Any] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        container = QtWidgets.QGroupBox('Planned fiducials')
        container.setLayout(QtWidgets.QVBoxLayout())
        container.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.MinimumExpanding)
        self._wdgt.layout().addWidget(container)

        self._tblWdgt = QtWidgets.QTableWidget(0, 2)
        self._tblWdgt.setHorizontalHeaderLabels(['Label', 'XYZ'])
        container.layout().addWidget(self._tblWdgt)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QHBoxLayout())
        container.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Autoset fiducials from head model')
        btn.clicked.connect(self._onAutosetBtnClicked)
        btnContainer.layout().addWidget(btn)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QHBoxLayout())
        container.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Add fiducial')
        btn.clicked.connect(self._onAddBtnClicked)
        btnContainer.layout().addWidget(btn)

        btn = QtWidgets.QPushButton('Set fiducial')
        btn.clicked.connect(self._onSetBtnClicked)
        btnContainer.layout().addWidget(btn)

        btn = QtWidgets.QPushButton('Goto fiducial')
        btn.clicked.connect(self._onGotoBtnClicked)
        btnContainer.layout().addWidget(btn)

        container = QtWidgets.QWidget()
        container.setLayout(QtWidgets.QGridLayout())
        self._wdgt.layout().addWidget(container)
        for iRow, iCol, key in ((0, 1, 'x'), (0, 0, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            if key in ('x', 'y', 'z'):
                self._views[key] = MRISliceView(normal=key)
            elif key == '3D':
                self._views[key] = Surf3DView(normal=key, activeSurf=self._surfKey, opacity=0.7)
            else:
                raise NotImplementedError()

            self._views[key].sigSliceOriginChanged.connect(lambda key=key: self._onSliceOriginChanged(sourceKey=key))

            container.layout().addWidget(self._views[key].wdgt, iRow, iCol)

        self.sigPanelActivated.connect(self._onPanelActivated)

    def _onPanelActivated(self):
        # don't initialize computationally-demanding views until panel is activated (viewed)
        for key, view in self._views.items():
            if view.session is None and self.session is not None:
                view.session = self.session
        self._onPlannedFiducialsChanged()
        self._hasBeenActivated = True

    def _onSliceOriginChanged(self, sourceKey: str):
        for key, view in self._views.items():
            if key == sourceKey:
                continue
            view.sliceOrigin = self._views[sourceKey].sliceOrigin

    def _onSessionSet(self):
        super()._onSessionSet()
        self.session.subjectRegistration.sigPlannedFiducialsChanged.connect(self._onPlannedFiducialsChanged)
        self.session.headModel.sigDataChanged.connect(self._onHeadModelUpdated)

        if self._hasBeenActivated:
            for key, view in self._views.items():
                view.session = self.session

    def _onPlannedFiducialsChanged(self):
        logger.debug('Planned fiducials changed. Updating table and plots')

        # TODO: do iterative partial updates rather than entirely clearing and repopulating table with every change
        prevSelectedItem = self._tblWdgt.currentItem()
        self._tblWdgt.clearContents()
        self._tblWdgt.setRowCount(len(self.session.subjectRegistration.plannedFiducials))
        for iFid, (label, coord) in enumerate(self.session.subjectRegistration.plannedFiducials.items()):
            item = QtWidgets.QTableWidgetItem(label)
            self._tblWdgt.setItem(iFid, 0, item)
            item = QtWidgets.QTableWidgetItem('{:.1f}, {:.1f}, {:.1f}'.format(*coord) if coord is not None else '')
            self._tblWdgt.setItem(iFid, 1, item)
        # TODO: restore selected row

        labels = []
        coords = np.full((len(self.session.subjectRegistration.plannedFiducials), 3), np.nan)
        for iFid, (label, coord) in enumerate(self.session.subjectRegistration.plannedFiducials.items()):
            labels.append(label)
            if coord is not None:
                coords[iFid, :] = coord

        if self._hasBeenActivated:
            for viewKey, view in self._views.items():
                self._fiducialActors[viewKey] = view.plotter.add_point_labels(
                    name='plannedFiducials',
                    points=coords,
                    labels=labels,
                    point_color='blue',
                    text_color='blue',
                    point_size=20,
                    shape=None,
                    render_points_as_spheres=True,
                    reset_camera=False,
                    render=True
                )

    def _onHeadModelUpdated(self, whatChanged: str):
        if whatChanged != self._surfKey:
            return
        # individual view handles re-rendering on update, so don't need to do anything here
        pass

    def _onAutosetBtnClicked(self, checked: bool):
        eegPositions = self.session.headModel.eegPositions
        subReg = self.session.subjectRegistration
        if eegPositions is None:
            raise ValueError('No EEG positions available in head model')

        labelMapping = {'LPA': 'LPA', 'NAS': 'Nz', 'RPA': 'RPA'}

        coords = np.zeros((3, 3))
        for iLabel, (label, altLabel) in enumerate(labelMapping.items()):
            coords[iLabel, :] = eegPositions.loc[altLabel, ['x', 'y', 'z']].values
            subReg.setFiducial(whichSet='planned', whichFiducial=label, coord=coords[iLabel, :])

        if True:
            # also set approximate nose tip
            downDir = -1 * np.cross(coords[2, :] - coords[0, :], coords[1, :] - coords[0, :])
            downDir /= np.linalg.norm(downDir)
            centerToNoseDir = coords[1, :] + downDir * 20 - (coords[2, :] + coords[0, :]) / 2
            centerToNoseDir /= np.linalg.norm(centerToNoseDir)
            projPts = np.dot(getattr(self.session.headModel, self._surfKey).points, centerToNoseDir)
            iMax = np.argmax(projPts)
            noseCoord = getattr(self.session.headModel, self._surfKey).points[iMax, :]
            subReg.setFiducial(whichSet='planned', whichFiducial='NoseTip', coord=noseCoord)

    def _onAddBtnClicked(self, checked: bool):
        raise NotImplementedError()  # TODO

    def _onSetBtnClicked(self, checked: bool):
        raise NotImplementedError()  # TODO

    def _onGotoBtnClicked(self, checked: bool):
        raise NotImplementedError()  # TODO
