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
from RTNaBS.Navigator.GUI.Widgets.CollectionTableWidget import PlanningFiducialsTableWidget
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.SubjectRegistration import Fiducial


logger = logging.getLogger(__name__)


@attrs.define
class FiducialsPanel(MainViewPanel):
    _key: str = 'Plan fiducials'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.head-snowflake-outline'))
    _tblWdgt: PlanningFiducialsTableWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[MRISliceView, Surf3DView]] = attrs.field(init=False, factory=dict)
    _surfKey: str = 'skinSurf'
    _fiducialActors: tp.Dict[str, tp.Any] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def canBeEnabled(self) -> tuple[bool, str | None]:
        if self.session is None:
            return False, 'No session set'
        if not self.session.MRI.isSet:
            return False, 'No MRI set'
        if not self.session.headModel.isSet:
            return False, 'No head model set'
        return True, None

    def _finishInitialization(self):
        # don't initialize computationally-demanding views until panel is activated (viewed)

        super()._finishInitialization()

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        container = QtWidgets.QGroupBox('Planned fiducials')
        container.setLayout(QtWidgets.QVBoxLayout())
        self._wdgt.layout().addWidget(container)
        container.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.MinimumExpanding)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        container.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Autoset fiducials from head model')
        btn.clicked.connect(self._onAutosetBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 0, 1, 2)

        btn = QtWidgets.QPushButton('Delete fiducial')
        btn.clicked.connect(self._onDeleteBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 1)

        btn = QtWidgets.QPushButton('Set fiducial')
        btn.clicked.connect(self._onSetBtnClicked)
        btnContainer.layout().addWidget(btn, 2, 0)

        btn = QtWidgets.QPushButton('Goto fiducial')
        btn.clicked.connect(self._onGotoBtnClicked)
        btnContainer.layout().addWidget(btn, 2, 1)

        self._tblWdgt = PlanningFiducialsTableWidget()
        self._tblWdgt.sigSelectionChanged.connect(self._onSelectedFiducialsChanged)
        container.layout().addWidget(self._tblWdgt.wdgt)

        container = QtWidgets.QWidget()
        container.setLayout(QtWidgets.QGridLayout())
        self._wdgt.layout().addWidget(container)
        for iRow, iCol, key in ((0, 1, 'x'), (0, 0, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            if key in ('x', 'y', 'z'):
                self._views[key] = MRISliceView(normal=key)
            elif key == '3D':
                self._views[key] = Surf3DView(normal=key, activeSurf=self._surfKey, surfOpacity=0.7)
            else:
                raise NotImplementedError()

            self._views[key].sigSliceOriginChanged.connect(lambda key=key: self._onSliceOriginChanged(sourceKey=key))

            container.layout().addWidget(self._views[key].wdgt, iRow, iCol)

        for key, view in self._views.items():
            if view.session is None and self.session is not None:
                view.session = self.session

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

        self._onPlannedFiducialsChanged()

    def _onSliceOriginChanged(self, sourceKey: str):
        for key, view in self._views.items():
            if key == sourceKey:
                continue
            view.sliceOrigin = self._views[sourceKey].sliceOrigin

    def _onSessionSet(self):
        super()._onSessionSet()

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):
        self.session.subjectRegistration.fiducials.sigItemsChanged.connect(self._onFiducialsChanged)
        self.session.headModel.sigDataChanged.connect(self._onHeadModelUpdated)

        self._tblWdgt.session = self.session

        for key, view in self._views.items():
            view.session = self.session

    def _onTblCellDoubleClicked(self, row: int, col: int):
        coord = list(self.session.subjectRegistration.fiducials.values())[row].plannedCoord
        if coord is not None:
            self._views['3D'].sliceOrigin = coord

    def _onSelectedFiducialsChanged(self, selKeys: list[str]):
        pass

    def _onFiducialsChanged(self, fidKeys: list[str], attribs: tp.Optional[list[str]] = None):
        if attribs is None or 'plannedCoord' in attribs:
            self._onPlannedFiducialsChanged()

    def _onPlannedFiducialsChanged(self):
        logger.debug('Planned fiducials changed. Updating plots')

        labels = []
        coords = np.full((len(self.session.subjectRegistration.fiducials), 3), np.nan)
        for iFid, (label, fid) in enumerate(self.session.subjectRegistration.fiducials.items()):
            coord = fid.plannedCoord
            labels.append(label)
            if coord is not None:
                coords[iFid, :] = coord

        if self._hasInitialized:
            for viewKey, view in self._views.items():
                if viewKey == '3D':
                    self._fiducialActors[viewKey] = view.plotter.add_point_labels(
                        name='plannedFiducials',
                        points=coords,
                        labels=labels,
                        point_color='blue',
                        text_color='blue',
                        point_size=15,
                        shape=None,
                        render_points_as_spheres=True,
                        reset_camera=False,
                        render=True
                    )
                else:
                    self._fiducialActors[viewKey] = view.plotter.add_points(
                        name='plannedFiducials',
                        points=coords,
                        color='blue',
                        opacity=0.85,
                        point_size=15,
                        render_points_as_spheres=True,
                        reset_camera=False,
                        render=True
                    )

                view.plotter.render()

    def _onHeadModelUpdated(self, whatChanged: str):
        if whatChanged is not None and whatChanged != self._surfKey:
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
            if label in subReg.fiducials:
                subReg.fiducials[label].plannedCoord = coords[iLabel, :]
            else:
                subReg.fiducials[label] = Fiducial(key=label,
                                                   plannedCoord=coords[iLabel, :])

        if True:
            # also set approximate nose tip
            downDir = -1 * np.cross(coords[2, :] - coords[0, :], coords[1, :] - coords[0, :])
            downDir /= np.linalg.norm(downDir)
            centerToNoseDir = coords[1, :] + downDir * 20 - (coords[2, :] + coords[0, :]) / 2
            centerToNoseDir /= np.linalg.norm(centerToNoseDir)
            projPts = np.dot(getattr(self.session.headModel, self._surfKey).points, centerToNoseDir)
            iMax = np.argmax(projPts)
            noseCoord = getattr(self.session.headModel, self._surfKey).points[iMax, :]
            label = 'NoseTip'
            if label in subReg.fiducials:
                subReg.fiducials[label].plannedCoord = noseCoord
            else:
                subReg.fiducials[label] = Fiducial(key=label,
                                                   plannedCoord=noseCoord)

        # note: any pre-existing fiducials with non-standard names will remain unchanged

    def _getTblCurrentFiducialKey(self) -> tp.Optional[str]:
        return self._tblWdgt.currentCollectionItemKey

    def _onAddBtnClicked(self, checked: bool):
        raise NotImplementedError()  # TODO

    def _onDeleteBtnClicked(self, checked: bool):
        key = self._getTblCurrentFiducialKey()
        if key is None:
            # no fiducial selected
            return
        logger.info('Deleting {} fiducial'.format(key))
        self.session.subjectRegistration.fiducials.deleteItem(key)

    def _onSetBtnClicked(self, checked: bool):
        key = self._getTblCurrentFiducialKey()
        if key is None:
            # no fiducial selected
            return
        coord = self._views['3D'].sliceOrigin
        logger.info('Setting {} fiducial coord to {}'.format(key, coord))
        self.session.subjectRegistration.fiducials[key].plannedCoord = coord

    def _onGotoBtnClicked(self, checked: bool):
        key = self._getTblCurrentFiducialKey()
        if key is None:
            # no fiducial selected
            return
        coord = self.session.subjectRegistration.fiducials[key].plannedCoord
        if coord is None:
            # no coordinates set for this fiducial
            return
        logger.info('Going to {} at {}'.format(key, coord))
        self._views['3D'].sliceOrigin = coord
