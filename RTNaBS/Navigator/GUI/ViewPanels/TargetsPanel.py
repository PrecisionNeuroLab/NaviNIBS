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
from RTNaBS.util.pyvista import Actor
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.util.GUI.QTableWidgetDragRows import QTableWidgetDragRows
from RTNaBS.util.Transforms import composeTransform, applyTransform
from RTNaBS.Navigator.Model.Session import Session, Target


logger = logging.getLogger(__name__)


@attrs.define
class VisualizedTarget:
    """
    Note: this doesn't connect to any change signals from `target`, instead assuming that caller will
    re-instantiate the `VisualizedTarget` for any target changes.
    """
    _target: Target
    _plotter: pv.Plotter
    _style: str
    _color: str = '#2222FF'
    _actors: tp.Dict[str, Actor] = attrs.field(init=False, factory=dict)
    _visible: bool = True

    def __attrs_post_init__(self):
        self.plot()

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, isVisible: bool):
        if self._visible == isVisible:
            return

        if isVisible:
            for actor in self._actors.values():
                actor.VisibilityOn()
        else:
            for actor in self._actors.values():
                actor.VisibilityOff()
        self._visible = isVisible

    def plot(self):
        thinWidth = 3
        thickWidth = 6
        if self._style == 'line':
            pts_line = np.vstack((self._target.entryCoord, self._target.targetCoord))
            self._actors['line'] = self._plotter.add_lines(pts_line,
                                                           color=self._color,
                                                           width=thickWidth,
                                                           label=self._target.key,
                                                           name=self._target.key + 'line',
                                                           )

        elif self._style == 'lines':
            pts_line1 = np.vstack((self._target.entryCoord, self._target.targetCoord))
            self._actors['line1'] = self._plotter.add_lines(pts_line1,
                                                           color=self._color,
                                                           width=thickWidth,
                                                           label=self._target.key,
                                                           name=self._target.key + 'line1',
                                                           )

            pts_line2 = applyTransform(self._target.coilToMRITransf, np.asarray([[0, -10, 0], [0, 0, 0]]))
            self._actors['line2'] = self._plotter.add_lines(pts_line2,
                                                            color=self._color,
                                                            width=thinWidth,
                                                            label=self._target.key,
                                                            name=self._target.key + 'line2',
                                                            )
            pts_line3 = applyTransform(self._target.coilToMRITransf, np.asarray([[0, 0, -50], [0, 0, 10]]))
            self._actors['line3'] = self._plotter.add_lines(pts_line3,
                                                            color=self._color,
                                                            width=thinWidth,
                                                            label=self._target.key,
                                                            name=self._target.key + 'line3',
                                                            )

            self._actors['target'] = self._plotter.add_points(self._target.targetCoord,
                                                              color=self._color,
                                                              point_size=10.,
                                                              render_points_as_spheres=True,
                                                              label=self._target.key,
                                                              name=self._target.key + 'target')

        elif self._style == 'coilLines':
            coilDiameter = 10
            coilHandleLength = 7

            theta = np.linspace(0, 2*np.pi, 361)
            circlePts = np.column_stack((coilDiameter/2 * np.cos(theta), coilDiameter/2 * np.sin(theta), np.zeros(theta.shape)))

            for wing, dir in (('wing1', 1.), ('wing2', -1.)):
                pts_wing = applyTransform(self._target.coilToMRITransf, circlePts + dir*np.asarray([coilDiameter/2, 0, 0]))
                self._actors[wing] = self._plotter.add_lines(pts_wing,
                                                             color=self._color,
                                                             width=thinWidth,
                                                             label=self._target.key,
                                                             name=self._target.key + wing,
                                                             )

            pts_line2 = applyTransform(self._target.coilToMRITransf, np.asarray([[0, -coilHandleLength, 0], [0, 0, 0]]))
            self._actors['line2'] = self._plotter.add_lines(pts_line2,
                                                            color=self._color,
                                                            width=thinWidth,
                                                            label=self._target.key,
                                                            name=self._target.key + 'line2',
                                                            )
            pts_line3 = applyTransform(self._target.coilToMRITransf, np.asarray([[0, 0, -50], [0, 0, 10]]))
            self._actors['line3'] = self._plotter.add_lines(pts_line3,
                                                            color=self._color,
                                                            width=thinWidth,
                                                            label=self._target.key,
                                                            name=self._target.key + 'line3',
                                                            )

            self._actors['target'] = self._plotter.add_points(self._target.targetCoord,
                                                              color=self._color,
                                                              point_size=10.,
                                                              render_points_as_spheres=True,
                                                              label=self._target.key,
                                                              name=self._target.key + 'target')

        else:
            raise NotImplementedError()


@attrs.define
class TargetsPanel(MainViewPanel):
    _treeWdgt: QtWidgets.QTreeWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[MRISliceView, Surf3DView]] = attrs.field(init=False, factory=dict)
    _targetActors: tp.Dict[str, VisualizedTarget] = attrs.field(init=False, factory=dict)
    _treeItems: tp.Dict[str, QtWidgets.QTreeWidgetItem] = attrs.field(init=False, factory=dict)

    _surfKeys: tp.List[str] = attrs.field(factory=lambda: ['gmSurf', 'skinSurf'])

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

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
        self._treeWdgt.setHeaderLabels(['Target', 'Info'])

        container = QtWidgets.QWidget()
        container.setLayout(QtWidgets.QGridLayout())
        self._wdgt.layout().addWidget(container)
        for iRow, iCol, key in ((0, 1, 'x'), (0, 0, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            if key in ('x', 'y', 'z'):
                self._views[key] = MRISliceView(label=key, normal=self._getRotMatForCoilAxis(key))
            elif key == '3D':
                self._views[key] = Surf3DView(label=key, normal=np.eye(3),
                                              activeSurf=self._surfKeys,
                                              surfOpacity=[0.8, 0.5])
            else:
                raise NotImplementedError()

            self._views[key].sigSliceTransformChanged.connect(lambda key=key: self._onSliceTransformChanged(sourceKey=key))

            container.layout().addWidget(self._views[key].wdgt, iRow, iCol)

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

        super()._onPanelActivated()

        for key, view in self._views.items():
            if view.session is None and self.session is not None:
                view.session = self.session
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
        def getRootItem(treeItem: QtWidgets.QTreeWidgetItem) -> QtWidgets.QTreeWidgetItem:
            if treeItem.parent() is None:
                return treeItem
            else:
                return getRootItem(treeItem.parent())

        curItem = self._treeWdgt.currentItem()
        if curItem is not None:
            return getRootItem(curItem).text(0)
        elif len(self.session.targets) > 0:
            return list(self.session.targets.values())[0].key
        else:
            return None

    def _gotoTarget(self, targetKey: str):
        # change slice camera views to align with selected target
        self._views['3D'].sliceTransform = self.session.targets[targetKey].coilToMRITransf \
                                           @ composeTransform(self._getRotMatForCoilAxis(axis='3D'))

        if True:
            # also set camera position for 3D view to align with target
            self._views['3D'].plotter.camera.focal_point = self._views['3D'].sliceOrigin
            self._views['3D'].plotter.camera.position = applyTransform(self._views['3D'].sliceTransform,
                                                                       np.asarray([0, 0, 100]))
            self._views['3D'].plotter.camera.up = applyTransform(self._views['3D'].sliceTransform,
                                                                 np.asarray([0, 100, 0])) - self._views[
                                                      '3D'].plotter.camera.position

    def _onTargetVisibleBtnClicked(self, key: str):
        for viewKey in self._views:
            self._targetActors[viewKey + key].visible = not self._targetActors[viewKey + key].visible
        btn = self._treeWdgt.itemWidget(self._treeItems[key], 1)
        assert isinstance(btn, QtWidgets.QPushButton)
        if self._targetActors['3D' + key].visible:
            btn.setIcon(qta.icon('mdi6.eye'))
        else:
            btn.setIcon(qta.icon('mdi6.eye-off-outline'))

    def _onTargetsChanged(self, changedTargetKeys: tp.Optional[tp.List[str]] = None):
        logger.debug('Targets changed. Updating tree view and plots')

        # update tree view
        # TODO: do incremental tree updates rather than entirely clearing and rebuilding
        prevSelectedItem = self._treeWdgt.currentItem()
        self._treeWdgt.clear()
        self._treeItems = {}
        for iT, (key, target) in enumerate(self.session.targets.items()):
            targetItem = QtWidgets.QTreeWidgetItem([key, ''])
            self._treeItems[key] = targetItem
            for field in ('targetCoord', 'entryCoord', 'angle', 'depthOffset', 'coilToMRITransf'):
                with np.printoptions(precision=2):
                    fieldItem = QtWidgets.QTreeWidgetItem(targetItem, [field, '{}'.format(getattr(target, field))])
        self._treeWdgt.insertTopLevelItems(0, list(self._treeItems.values()))
        for iT, (key, target) in enumerate(self.session.targets.items()):
            btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.eye'), text='')
            btn.setFlat(True)
            btn.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Maximum)
            self._treeWdgt.setItemWidget(self._treeItems[key], 1, btn)
            btn.clicked.connect(lambda checked=False, key=key: self._onTargetVisibleBtnClicked(key=key))

        if self._hasBeenActivated:
            # update views
            for viewKey, view in self._views.items():
                if changedTargetKeys is None:
                    changedTargetKeys = self.session.targets.keys()

                for key in changedTargetKeys:
                    target = self.session.targets[key]
                    if viewKey == '3D':
                        style = 'coilLines'
                    else:
                        style = 'lines'

                    self._targetActors[viewKey + target.key] = VisualizedTarget(target=target,
                                                                                plotter=view.plotter,
                                                                                style=style)

            if len(self.session.targets) > 0:
                self._gotoTarget(self._getCurrentTargetKey())

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
        self._gotoTarget(self._getCurrentTargetKey())











