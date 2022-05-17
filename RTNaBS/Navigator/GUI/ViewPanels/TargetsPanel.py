from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import logging
import numpy as np
import os
import pathlib
import pytransform3d.rotations as ptr
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
from RTNaBS.util.Transforms import composeTransform, applyTransform
from RTNaBS.Navigator.Model.Session import Session, Target


logger = logging.getLogger(__name__)

Actor = pv._vtk.vtkActor


@attrs.define
class VisualizedTarget:
    """
    Note: this doesn't connect to any change signals from `target`, instead assuming that caller will
    re-instantiate the `VisualizedTarget` for any target changes.
    """
    _target: Target
    _plotter: pv.Plotter
    _style: str
    _color: str = '#0000FF'
    _actors: tp.Dict[str, Actor] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        self.plot()

    def plot(self):
        if self._style == 'line':
            pts_line = np.vstack((self._target.entryCoord, self._target.targetCoord))
            self._actors['line'] = self._plotter.add_lines(pts_line,
                                                           color=self._color,
                                                           width=5,
                                                           label=self._target.key,
                                                           name=self._target.key + 'line',
                                                           )
        else:
            raise NotImplementedError()


@attrs.define
class TargetsPanel(MainViewPanel):
    _treeWdgt: QtWidgets.QTreeWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[MRISliceView, Surf3DView]] = attrs.field(init=False, factory=dict)
    _hasBeenActivated: bool = attrs.field(init=False, default=False)
    _targetActors: tp.Dict[str, VisualizedTarget] = attrs.field(init=False, factory=dict)

    _surfKeys: tp.List[str] = attrs.field(factory=lambda: ['gmSurf', 'skinSurf'])

    def __attrs_post_init__(self):
        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        container = QtWidgets.QGroupBox('Planned targets')
        container.setLayout(QtWidgets.QVBoxLayout())
        container.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.MinimumExpanding)
        self._wdgt.layout().addWidget(container)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        container.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Import targets from file...')
        btn.clicked.connect(self._onImportTargetsBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 0, 1, 2)

        btn = QtWidgets.QPushButton('Add target')
        btn.clicked.connect(self._onAddBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 0)

        btn = QtWidgets.QPushButton('Delete target')
        btn.clicked.connect(self._onDeleteBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 1)

        btn = QtWidgets.QPushButton('Edit target')
        btn.clicked.connect(self._onEditBtnClicked)
        btnContainer.layout().addWidget(btn, 2, 0)

        btn = QtWidgets.QPushButton('Goto target')
        btn.clicked.connect(self._onGotoBtnClicked)
        btnContainer.layout().addWidget(btn, 2, 1)

        self._treeWdgt = QtWidgets.QTreeWidget()
        container.layout().addWidget(self._treeWdgt)

        container = QtWidgets.QWidget()
        container.setLayout(QtWidgets.QGridLayout())
        self._wdgt.layout().addWidget(container)
        for iRow, iCol, key in ((0, 1, 'x'), (0, 0, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            if key in ('x', 'y', 'z'):
                self._views[key] = MRISliceView(label=key, normal=self._getRotMatForCoilAxis(key))
            elif key == '3D':
                self._views[key] = Surf3DView(label=key, normal=np.eye(3),
                                              activeSurf=self._surfKeys,
                                              surfOpacity=[0.9, 0.6])
            else:
                raise NotImplementedError()

            self._views[key].sigSliceTransformChanged.connect(lambda key=key: self._onSliceTransformChanged(sourceKey=key))

            container.layout().addWidget(self._views[key].wdgt, iRow, iCol)

        self.sigPanelActivated.connect(self._onPanelActivated)

    @staticmethod
    def _getRotMatForCoilAxis(axis: str) -> np.ndarray:
        if axis == 'x':
            return ptr.active_matrix_from_extrinsic_euler_yxy([np.pi/2, np.pi/2, 0])
        elif axis == 'y':
            return ptr.active_matrix_from_angle(0, np.pi/2)
        elif axis in ('z', '3D'):
            return np.eye(3)
        else:
            raise NotImplementedError()

    def _onPanelActivated(self):
        # don't initialize computationally-demanding views until panel is activated (viewed)
        for key, view in self._views.items():
            if view.session is None and self.session is not None:
                view.session = self.session
        self._hasBeenActivated = True
        self._onTargetsChanged()

    def _onSliceTransformChanged(self, sourceKey: str):
        logger.debug('Slice {} transform changed'.format(sourceKey))
        crossTransf = self._views[sourceKey].sliceTransform @ composeTransform(np.linalg.pinv(self._getRotMatForCoilAxis(axis=sourceKey)))  # TODO: double check order
        for key, view in self._views.items():
            if key == sourceKey:
                continue
            logger.debug('Updating {} slice transform'.format(key))
            view.sliceTransform = crossTransf @ composeTransform(self._getRotMatForCoilAxis(axis=key))

    def _onSessionSet(self):
        super()._onSessionSet()
        self.session.targets.sigTargetsChanged.connect(self._onTargetsChanged)

        if self._hasBeenActivated:
            for key, view in self._views.items():
                view.session = self.session

    def _getCurrentTargetKey(self) -> tp.Optional[str]:
        curItem = self._treeWdgt.currentItem()
        if curItem is not None:
            raise NotImplementedError()  # TODO
        elif len(self.session.targets) > 0:
            return list(self.session.targets.values())[0].key
        else:
            return None

    def _onTargetsChanged(self, changedTargetKeys: tp.Optional[tp.List[str]] = None):
        logger.debug('Targets changed. Updating tree view and plots')

        # TODO: update tree view

        if self._hasBeenActivated:
            # update views
            for viewKey, view in self._views.items():
                if changedTargetKeys is None:
                    changedTargetKeys = self.session.targets.keys()

                for key in changedTargetKeys:
                    target = self.session.targets[key]
                    if viewKey == '3D':
                        style = 'line'
                    else:
                        style = 'line'

                    self._targetActors[viewKey + target.key] = VisualizedTarget(target=target,
                                                                                plotter=view.plotter,
                                                                                style=style)

            if len(self.session.targets) > 0:
                # change slice camera views to align with selected target
                self._views['3D'].sliceTransform = self.session.targets[self._getCurrentTargetKey()].coilToMRITransf \
                                                   @ composeTransform(ptr.active_matrix_from_angle(0, np.pi)) \
                                                   @ composeTransform(self._getRotMatForCoilAxis(axis='3D'))

                if True:
                    # also set camera position for 3D view to align with target
                    self._views['3D'].plotter.camera.focal_point = self._views['3D'].sliceOrigin
                    self._views['3D'].plotter.camera.position = applyTransform(self._views['3D'].sliceTransform, np.asarray([0, 0, 100]))
                    self._views['3D'].plotter.camera.up = applyTransform(self._views['3D'].sliceTransform,
                                                                               np.asarray([0, 100, 0])) - self._views['3D'].plotter.camera.position

    def _onImportTargetsBtnClicked(self, checked: bool):
        newFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._wdgt,
                                                               'Select targets file to import',
                                                               os.path.dirname(self.session.filepath),
                                                               'json (*.json);; RTNaBS (*.rtnabs)')

        self.session.mergeFromFile(filepath=newFilepath, sections=['targets'])

    def _onAddBtnClicked(self, checked: bool):
        raise NotImplementedError()  # TODO

    def _onDeleteBtnClicked(self, checked: bool):
        raise NotImplementedError()  # TODO

    def _onEditBtnClicked(self, checked: bool):
        raise NotImplementedError()  # TODO

    def _onGotoBtnClicked(self, checked: bool):
        raise NotImplementedError()  # TODO











