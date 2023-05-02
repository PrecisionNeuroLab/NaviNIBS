from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import json
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
from RTNaBS.Navigator.GUI.Widgets.CollectionTableWidget import FullTargetsTableWidget
from RTNaBS.Navigator.GUI.Widgets.EditTargetWidget import EditTargetWidget
from RTNaBS.util import makeStrUnique
from RTNaBS.util.pyvista import Actor
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.util.GUI.QTableWidgetDragRows import QTableWidgetDragRows
from RTNaBS.util.Transforms import composeTransform, applyTransform, invertTransform, concatenateTransforms
from RTNaBS.Navigator.Model.Session import Session, Target


logger = logging.getLogger(__name__)


@attrs.define
class VisualizedTarget:
    """
    Note: this doesn't connect to any change signals from `target`, instead assuming that caller will
    re-instantiate the `VisualizedTarget` for any target changes.
    """
    _target: Target
    _plotter: pv.Plotter = attrs.field(repr=False)
    _style: str
    _color: str = '#2222FF'
    _actors: tp.Dict[str, Actor] = attrs.field(init=False, factory=dict, repr=False)
    _visible: bool = True  # track this separately from self._target.isVisible to allow temporarily overriding

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

        self._plotter.render()

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

        if not self.visible:
            for actor in self._actors.values():
                actor.VisibilityOff()


@attrs.define
class TargetsPanel(MainViewPanel):
    _key: str = 'Set targets'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.head-flash-outline'))
    _tableWdgt: FullTargetsTableWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[MRISliceView, Surf3DView]] = attrs.field(init=False, factory=dict)
    _targetActors: tp.Dict[str, VisualizedTarget] = attrs.field(init=False, factory=dict)

    _editTargetWdgt: EditTargetWidget = attrs.field(init=False)

    _surfKeys: tp.List[str] = attrs.field(factory=lambda: ['gmSurf', 'skinSurf'])

    _enabledOnlyWhenTargetSelected: list[QtWidgets.QWidget | EditTargetWidget] = attrs.field(init=False, factory=list)

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

    @staticmethod
    def _getRotMatForCoilAxis(axis: str) -> np.ndarray:
        if axis == 'x':
            return ptr.active_matrix_from_extrinsic_euler_yxy([-np.pi/2, np.pi/2, 0])
        elif axis == 'y':
            return ptr.active_matrix_from_angle(0, np.pi/2)
        elif axis in ('z', '3D'):
            return np.eye(3)
        else:
            raise NotImplementedError()

    def _finishInitialization(self):
        # don't initialize computationally-demanding views until panel is activated (viewed)

        super()._finishInitialization()

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
        self._enabledOnlyWhenTargetSelected.append(btn)

        btn = QtWidgets.QPushButton('Duplicate target')
        btn.clicked.connect(self._onDuplicateBtnClicked)
        btnContainer.layout().addWidget(btn, 2, 0)
        self._enabledOnlyWhenTargetSelected.append(btn)

        btn = QtWidgets.QPushButton('Goto target')
        btn.clicked.connect(self._onGotoBtnClicked)
        btnContainer.layout().addWidget(btn, 2, 1)
        self._enabledOnlyWhenTargetSelected.append(btn)

        self._tableWdgt = FullTargetsTableWidget()
        self._tableWdgt.sigCurrentItemChanged.connect(self._onCurrentTargetChanged)
        self._tableWdgt.sigSelectionChanged.connect(self._onSelectionChanged)
        container.layout().addWidget(self._tableWdgt.wdgt)

        self._editTargetWdgt = EditTargetWidget(session=self.session,
                                                getNewTargetCoord=self._getCrosshairCoord,
                                                setTargetCoordButtonLabel='Set from crosshair position',
                                                getNewEntryCoord=self._getCrosshairCoord,
                                                setEntryCoordButtonLabel='Set from crosshair position',
                                                )
        container.layout().addWidget(self._editTargetWdgt.wdgt)

        container = QtWidgets.QWidget()
        container.setLayout(QtWidgets.QGridLayout())
        self._wdgt.layout().addWidget(container)
        for iRow, iCol, key in ((0, 1, 'x'), (0, 0, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            if key in ('x', 'y', 'z'):
                self._views[key] = MRISliceView(label=key, normal=self._getRotMatForCoilAxis(key))
            elif key == '3D':
                self._views[key] = Surf3DView(label=key, normal=np.eye(3),
                                              activeSurf=self._surfKeys,
                                              cameraOffsetDist=50,
                                              surfOpacity=[0.8, 0.5])
            else:
                raise NotImplementedError()

            self._views[key].sigSliceTransformChanged.connect(lambda key=key: self._onSliceTransformChanged(sourceKey=key))

            container.layout().addWidget(self._views[key].wdgt, iRow, iCol)

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

        for widget in self._enabledOnlyWhenTargetSelected:
            logger.debug(f'Disabling {widget}')
            widget.setEnabled(False)

        self._onTargetsChanged()

    def _getCrosshairCoord(self) -> np.ndarray:
        """
        Used by CoordinateWidgets to set target or entry coord from current crosshair position
        """
        return self._views['3D'].sliceOrigin

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

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):
        self.session.targets.sigItemsChanged.connect(self._onTargetsChanged)
        self._tableWdgt.session = self.session

        for key, view in self._views.items():
            view.session = self.session

    def _getCurrentTargetKey(self) -> tp.Optional[str]:
        return self._tableWdgt.currentCollectionItemKey

    def _onSelectionChanged(self, keys: list[str]):
        for widget in self._enabledOnlyWhenTargetSelected:
            logger.debug(f"{'Disabling' if len(keys)==0 is None else 'Enabling'} {widget}")
            widget.setEnabled(len(keys)>0)

    def _onCurrentTargetChanged(self, targetKey: str | None):
        if targetKey is not None:
            self._gotoTarget(targetKey=targetKey)    # go to target immediately whenever selection changes

    def _gotoTarget(self, targetKey: str):
        # change slice camera views to align with selected target
        target = self.session.targets[targetKey]
        extraTransf = np.eye(4)
        if True:
            # align at target depth
            extraTransf[:3, 3] = applyTransform(invertTransform(target.coilToMRITransf), target.targetCoord)
        else:
            # align at coil depth
            pass

        self._views['3D'].sliceTransform = concatenateTransforms([
            composeTransform(self._getRotMatForCoilAxis(axis='3D')),
            extraTransf,
            target.coilToMRITransf])

        if True:
            # also set camera position for 3D view to align with target
            self._views['3D'].plotter.camera.focal_point = self._views['3D'].sliceOrigin
            self._views['3D'].plotter.camera.position = applyTransform(self._views['3D'].sliceTransform, np.asarray([0, 0, 200]))
            self._views['3D'].plotter.camera.up = applyTransform(self._views['3D'].sliceTransform,
                                                                 np.asarray([0, 100, 0])) - self._views[
                                                      '3D'].plotter.camera.position

    def _onTargetsChanged(self, changedTargetKeys: tp.Optional[tp.List[str]] = None, changedTargetAttrs: tp.Optional[tp.List[str]] = None):
        if not self._hasInitialized and not self._isInitializing:
            return

        logger.debug(f'Targets changed. Updating tree view and plots.\nchangedTargetKeys={changedTargetKeys}, \nchangedTargetAttrs={changedTargetAttrs}')

        if changedTargetKeys is None:
            changedTargetKeys = self.session.targets.keys()

        if changedTargetAttrs == ['isVisible']:
            # only visibility changed

            for targetKey in changedTargetKeys:
                target = self._session.targets[targetKey]
                for viewKey in self._views:
                    actorKey = viewKey + targetKey
                    if actorKey in self._targetActors:
                        self._targetActors[actorKey].visible = target.isVisible

        elif changedTargetAttrs == ['isSelected']:
            pass  # selection update will be handled separately

        else:
            # assume anything/everything changed, clear target and start over
            if self._hasInitialized or self._isInitializing:
                # update views
                for viewKey, view in self._views.items():
                    if changedTargetKeys is None:
                        changedTargetKeys = self.session.targets.keys()

                    if viewKey == '3D':
                        style = 'coilLines'
                    else:
                        style = 'lines'

                    for key in changedTargetKeys:
                        try:
                            target = self.session.targets[key]
                        except KeyError as e:
                            # previous key no longer in targets
                            if viewKey + key in self._targetActors:
                                view.plotter.remove_actor(self._targetActors.pop(viewKey + key))
                        else:
                            logger.debug(f'Creating VisualizedTarget for target {target.asDict()} {target.coilToMRITransf}')
                            self._targetActors[viewKey + target.key] = VisualizedTarget(target=target,
                                                                                        plotter=view.plotter,
                                                                                        style=style,
                                                                                        visible=target.isVisible)

                    view.updateView()

                currentTarget = self._getCurrentTargetKey()
                self._onCurrentTargetChanged(currentTarget)

    def _onImportTargetsBtnClicked(self, checked: bool):
        newFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._wdgt,
                                                               'Select targets file to import',
                                                               os.path.dirname(self.session.filepath),
                                                               'json (*.json);; RTNaBS (*.rtnabs)')

        if len(newFilepath) == 0:
            logger.warning('Import cancelled')
            return

        self.session.mergeFromFile(filepath=newFilepath, sections=['targets'])

    def _onAddBtnClicked(self, checked: bool):
        # create new target at current crosshair location, and autoset entry
        targetCoord = self._getCrosshairCoord()
        targetKey = makeStrUnique('Target-1', existingStrs=self.session.targets.keys(), delimiter='-')
        target = Target(targetCoord=targetCoord, key=targetKey, session=self.session)
        target.autosetEntryCoord()
        self.session.targets.addItem(target)
        self._tableWdgt.currentCollectionItemKey = targetKey  # select new target

    def _onDeleteBtnClicked(self, checked: bool):
        raise NotImplementedError()  # TODO

    def _onDuplicateBtnClicked(self, checked: bool):
        raise NotImplementedError()  # TODO

    def _onGotoBtnClicked(self, checked: bool):
        targetKey = self._getCurrentTargetKey()
        if targetKey is not None:
            self._gotoTarget(self._getCurrentTargetKey())
        else:
            logger.warning('No target selected')











