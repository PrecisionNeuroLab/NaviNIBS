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
from RTNaBS.util.GUI.QTableWidgetDragRows import QTableWidgetDragRows
from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.SubjectRegistration import Fiducial


logger = logging.getLogger(__name__)


@attrs.define
class FiducialsPanel(MainViewPanel):
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.head-snowflake-outline'))
    _tblWdgt: QTableWidgetDragRows = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[MRISliceView, Surf3DView]] = attrs.field(init=False, factory=dict)
    _surfKey: str = 'skinSurf'
    _fiducialActors: tp.Dict[str, tp.Any] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def canBeEnabled(self) -> bool:
        return self.session is not None and self.session.MRI.isSet and self.session.headModel.isSet

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

        btn = QtWidgets.QPushButton('Add fiducial')
        btn.clicked.connect(self._onAddBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 0)

        btn = QtWidgets.QPushButton('Delete fiducial')
        btn.clicked.connect(self._onDeleteBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 1)

        btn = QtWidgets.QPushButton('Set fiducial')
        btn.clicked.connect(self._onSetBtnClicked)
        btnContainer.layout().addWidget(btn, 2, 0)

        btn = QtWidgets.QPushButton('Goto fiducial')
        btn.clicked.connect(self._onGotoBtnClicked)
        btnContainer.layout().addWidget(btn, 2, 1)

        self._tblWdgt = QTableWidgetDragRows(0, 2)
        self._tblWdgt.setHorizontalHeaderLabels(['Label', 'XYZ'])
        self._tblWdgt.sigDragAndDropReordered.connect(self._onDragAndDropReorderedRows)
        self._tblWdgt.cellChanged.connect(self._onTblCellChanged)
        self._tblWdgt.cellDoubleClicked.connect(self._onTblCellDoubleClicked)
        container.layout().addWidget(self._tblWdgt)

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

        self._onPlannedFiducialsChanged()

    def _onSliceOriginChanged(self, sourceKey: str):
        for key, view in self._views.items():
            if key == sourceKey:
                continue
            view.sliceOrigin = self._views[sourceKey].sliceOrigin

    def _onSessionSet(self):
        super()._onSessionSet()
        self.session.subjectRegistration.fiducials.sigItemsChanged.connect(self._onFiducialsChanged)
        self.session.headModel.sigDataChanged.connect(self._onHeadModelUpdated)

        if self._hasInitialized:
            for key, view in self._views.items():
                view.session = self.session

    def _onDragAndDropReorderedRows(self):
        newOrder = [self._tblWdgt.item(iR, 0).text() for iR in range(self._tblWdgt.rowCount())]
        logger.info('Reordering fiducials: {}'.format(newOrder))
        self.session.subjectRegistration.fiducials.setItems([self.session.subjectRegistration.fiducials[key] for key in newOrder])

    def _onTblCellChanged(self, row: int, col: int):
        if col != 0:
            # ignore
            return
        if self._tblWdgt.dropInProgress:
            # ignore any changes during drag-and-drop processing (these are signals separately)
            return
        newLabel = self._tblWdgt.item(row, col).text()
        oldLabel = list(self.session.subjectRegistration.fiducials.keys())[row]
        if newLabel != oldLabel:
            if newLabel in self.session.subjectRegistration.fiducials:
                logger.warning(f'Tried to change fiducial label from {oldLabel} to {newLabel}, but {newLabel} already exists.')
                return   # TODO: confirm this shows correct behavior of visually rejecting change in GUI

            logger.info('Fiducial label changed from {} to {}'.format(oldLabel, newLabel))
            self.session.subjectRegistration.fiducials[oldLabel].key = newLabel

    def _onTblCellDoubleClicked(self, row: int, col: int):
        coord = list(self.session.subjectRegistration.fiducials.values())[row]
        if coord is not None:
            self._views['3D'].sliceOrigin = coord

    def _onFiducialsChanged(self, fidKeys: list[str], attribs: tp.Optional[list[str]] = None):
        if attribs is None or 'plannedCoord' in attribs:
            self._onPlannedFiducialsChanged()

    def _onPlannedFiducialsChanged(self):
        logger.debug('Planned fiducials changed. Updating table and plots')

        # TODO: do iterative partial updates rather than entirely clearing and repopulating table with every change
        prevSelectedItem = self._tblWdgt.currentItem()
        self._tblWdgt.clearContents()
        self._tblWdgt.setRowCount(len(self.session.subjectRegistration.fiducials))
        for iFid, (label, fid) in enumerate(self.session.subjectRegistration.fiducials.items()):
            item = QtWidgets.QTableWidgetItem(label)
            self._tblWdgt.setItem(iFid, 0, item)
            coord = fid.plannedCoord
            item = QtWidgets.QTableWidgetItem('{:.1f}, {:.1f}, {:.1f}'.format(*coord) if coord is not None else '')
            item.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
            self._tblWdgt.setItem(iFid, 1, item)
        # TODO: restore selected row

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
        curItem = self._tblWdgt.currentItem()
        if curItem is None:
            # no item selected
            return None
        return self._tblWdgt.item(curItem.row(), 0).text()

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
