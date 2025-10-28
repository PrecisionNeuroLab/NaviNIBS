from __future__ import annotations

import asyncio

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

from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.GUI.Dock import Dock, DockArea
from NaviNIBS.Navigator.GUI.Widgets.MRIViews import MRISliceView
from NaviNIBS.Navigator.GUI.Widgets.SurfViews import Surf3DView
from NaviNIBS.Navigator.GUI.Widgets.CollectionTableWidget import FullTargetsTableWidget
from NaviNIBS.Navigator.GUI.Widgets.EditTargetWidget import EditTargetWidget
from NaviNIBS.Navigator.GUI.Widgets.EditGridWidget import EditGridWidget
from NaviNIBS.Navigator.GUI.ViewPanels.MainViewPanelWithDockWidgets import MainViewPanelWithDockWidgets
from NaviNIBS.util import makeStrUnique
from NaviNIBS.util.GUI.Icons import getIcon
from NaviNIBS.util.GUI.QueuedRedrawMixin import QueuedRedrawMixin
from NaviNIBS.util.pyvista import Actor, RemotePlotterProxy
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import composeTransform, applyTransform, invertTransform, concatenateTransforms
from NaviNIBS.Navigator.Model.Session import Session, Target


logger = logging.getLogger(__name__)


@attrs.define
class VisualizedTarget:
    """
    Note: this doesn't connect to any change signals from `target`, instead assuming that caller will
    re-instantiate the `VisualizedTarget` for any target changes.
    """
    _target: Target
    _plotter: DefaultBackgroundPlotter = attrs.field(repr=False)
    _style: str
    _color: str = '#2222FF'
    _actorKeys: set[str] = attrs.field(init=False, factory=set, repr=False)
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

        with self._plotter.allowNonblockingCalls():
            self._plotter.set_actors_visibility(self._actorKeys, isVisible)

        self._visible = isVisible

        with self._plotter.allowNonblockingCalls():
            self._plotter.render()

    @property
    def style(self):
        return self._style

    @style.setter
    def style(self, style: str):
        if self._style == style:
            return

        self.clearActors()
        self._style = style
        self.plot()

    def plot(self):
        if isinstance(self._plotter, RemotePlotterProxy) and not self._plotter.isReadyEvent.is_set():
            # plotter not ready yet
            return

        thinWidth = 3
        thickWidth = 6
        if self._style == 'line':
            pts_line = np.vstack((self._target.entryCoord, self._target.targetCoord))
            actorKey = self._target.key + 'line'
            self._actorKeys.add(actorKey)
            with self._plotter.allowNonblockingCalls():
                self._plotter.add_lines(pts_line,
                                        color=self._color,
                                        width=thickWidth,
                                        label=self._target.key,
                                        name=actorKey,
                                        )

        elif self._style == 'lines':
            with self._plotter.allowNonblockingCalls():
                pts_line1 = np.vstack((self._target.entryCoord, self._target.targetCoord))
                actorKey = self._target.key + 'line1'
                self._actorKeys.add(actorKey)
                self._plotter.add_lines(pts_line1,
                                        color=self._color,
                                        width=thickWidth,
                                        label=self._target.key,
                                        name=actorKey,
                                        )

                pts_line2 = applyTransform(self._target.coilToMRITransf, np.asarray([[0, -10, 0], [0, 0, 0]]))
                actorKey = self._target.key + 'line2'
                self._actorKeys.add(actorKey)
                self._plotter.add_lines(pts_line2,
                                        color=self._color,
                                        width=thinWidth,
                                        label=self._target.key,
                                        name=actorKey,
                                        )
                pts_line3 = applyTransform(self._target.coilToMRITransf, np.asarray([[0, 0, -50], [0, 0, 10]]))
                actorKey = self._target.key + 'line3'
                self._actorKeys.add(actorKey)
                self._plotter.add_lines(pts_line3,
                                        color=self._color,
                                        width=thinWidth,
                                        label=self._target.key,
                                        name=actorKey,
                                        )

                actorKey = self._target.key + 'target'
                self._actorKeys.add(actorKey)
                self._plotter.add_points(self._target.targetCoord,
                                         color=self._color,
                                         point_size=10.,
                                         render_points_as_spheres=True,
                                         label=self._target.key,
                                         name=actorKey)

        elif self._style == 'coilLines':
            coilDiameter = 10
            coilHandleLength = 7

            theta = np.linspace(0, 2*np.pi, 361)
            circlePts = np.column_stack((coilDiameter/2 * np.cos(theta), coilDiameter/2 * np.sin(theta), np.zeros(theta.shape)))

            with self._plotter.allowNonblockingCalls():
                for wing, dir in (('wing1', 1.), ('wing2', -1.)):
                    if self._target.coilToMRITransf is not None:
                        pts_wing = applyTransform(self._target.coilToMRITransf, circlePts + dir*np.asarray([coilDiameter/2, 0, 0]))
                        actorKey = self._target.key + wing
                        self._actorKeys.add(actorKey)
                        self._plotter.add_lines(pts_wing,
                                                connected=True,
                                                color=self._color,
                                                width=thinWidth,
                                                label=self._target.key,
                                                name=actorKey,
                                                )

                if self._target.coilToMRITransf is not None:
                    pts_line2 = applyTransform(self._target.coilToMRITransf, np.asarray([[0, -coilHandleLength, 0], [0, 0, 0]]))
                    actorKey = self._target.key + 'line2'
                    self._actorKeys.add(actorKey)
                    self._plotter.add_lines(pts_line2,
                                            color=self._color,
                                            width=thinWidth,
                                            label=self._target.key,
                                            name=actorKey,
                                            )
                    if self._target.targetCoord is not None and self._target.entryCoord is not None:
                        depth = np.linalg.norm(self._target.targetCoord - self._target.entryCoord) + self._target.depthOffset
                    else:
                        depth = 50
                    pts_line3 = applyTransform(self._target.coilToMRITransf, np.asarray([[0, 0, -depth], [0, 0, 10]]))
                    actorKey = self._target.key + 'line3'
                    self._actorKeys.add(actorKey)
                    self._plotter.add_lines(pts_line3,
                                            color=self._color,
                                            width=thinWidth,
                                            label=self._target.key,
                                            name=actorKey,
                                            )
                elif self._target.targetCoord is not None and self._target.entryCoord is not None:
                    pts_line2 = np.vstack([self._target.targetCoord, self._target.entryCoord])
                    actorKey = self._target.key + 'line2'
                    self._actorKeys.add(actorKey)
                    self._plotter.add_lines(pts_line2,
                                            color=self._color,
                                            width=thinWidth,
                                            label=self._target.key,
                                            name=actorKey,
                                            )

                actorKey = self._target.key + 'target'
                self._actorKeys.add(actorKey)
                self._plotter.add_points(self._target.targetCoord,
                                         color=self._color,
                                         point_size=10.,
                                         render_points_as_spheres=True,
                                         label=self._target.key,
                                         name=actorKey,
                                         reset_camera=False,
                                         render=False)
        else:
            raise NotImplementedError()

        with self._plotter.allowNonblockingCalls():
            if not self.visible:
                self._plotter.set_actors_visibility(self._actorKeys, False)

            self._plotter.render()

    def clearActors(self):
        with self._plotter.allowNonblockingCalls():
            for actorKey in self._actorKeys:
                self._plotter.remove_actor(actorKey)
            self._actorKeys.clear()
            self._plotter.render()


@attrs.define
class TargetsPanel(MainViewPanelWithDockWidgets, QueuedRedrawMixin):
    _key: str = 'Set targets'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: getIcon('mdi6.head-flash-outline'))
    _tableWdgt: FullTargetsTableWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[MRISliceView, Surf3DView]] = attrs.field(init=False, factory=dict)
    _targetActors: tp.Dict[str, VisualizedTarget] = attrs.field(init=False, factory=dict)

    _editTargetWdgt: EditTargetWidget = attrs.field(init=False)
    _editGridWdgt: EditGridWidget = attrs.field(init=False)
    _editGridDock: Dock = attrs.field(init=False)

    _targetDispStyle_comboBox: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)

    _surfKeys: tp.List[str] = attrs.field(factory=lambda: ['gmSurf', 'skinSurf'])

    _enabledOnlyWhenTargetSelected: list[QtWidgets.QWidget | EditTargetWidget] = attrs.field(init=False, factory=list)

    _redrawTargetKeys: set[str] = attrs.field(init=False, factory=set)

    finishedAsyncInit: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        MainViewPanelWithDockWidgets.__attrs_post_init__(self)
        QueuedRedrawMixin.__attrs_post_init__(self)

    def canBeEnabled(self) -> tuple[bool, str | None]:
        if self.session is None:
            return False, 'No session set'
        if not self.session.MRI.isSet:
            return False, 'No MRI set'
        if not self.session.headModel.skinSurfIsSet:
            return False, 'No skin surface set'
        if not self.session.headModel.gmSurfIsSet:
            return False, 'No gray matter surface set'
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

        dock, container = self._createDockWidget(
            title='Targets',
            layout=QtWidgets.QVBoxLayout()
        )
        dock.setStretch(1, 10)
        container.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        self._wdgt.addDock(dock, position='left')

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

        fieldContainer = QtWidgets.QWidget()
        fieldLayout = QtWidgets.QFormLayout()
        fieldContainer.setLayout(fieldLayout)
        container.layout().addWidget(fieldContainer)
        self._targetDispStyle_comboBox.addItems(['line', 'lines', 'coilLines'])
        self._targetDispStyle_comboBox.setCurrentIndex(2)
        self._targetDispStyle_comboBox.currentIndexChanged.connect(self._onTargetDispStyleChanged)
        fieldLayout.addRow('Target display style:', self._targetDispStyle_comboBox)

        self._editTargetWdgt = EditTargetWidget(session=self.session,
                                                wdgt=QtWidgets.QWidget(),
                                                getNewTargetCoord=self._getCrosshairCoord,
                                                setTargetCoordButtonLabel='Set from crosshair position',
                                                getNewEntryCoord=self._getCrosshairCoord,
                                                setEntryCoordButtonLabel='Set from crosshair position',
                                                )
        dock, _ = self._createDockWidget(
            title='Edit target',
            widget=self._editTargetWdgt.wdgt,
        )
        dock.setStretch(1, 10)
        self._wdgt.addDock(dock, position='bottom')
        dock_editTarget = dock

        self._editGridWdgt = EditGridWidget(session=self.session,
                                            wdgt=QtWidgets.QWidget(),
                                            )
        self._editGridWdgt.sigGridUpdated.connect(self._tableWdgt.resizeColumnsToContents)
        dock, _ = self._createDockWidget(
            title='Edit grid',
            widget=self._editGridWdgt.wdgt,
        )
        dock.setStretch(1, 10)
        self._editGridDock = dock
        if False:
            self._wdgt.addDock(dock, position='bottom')
        else:
            self._wdgt.addDock(dock, position='below', relativeTo=dock_editTarget)

        dock, container = self._createDockWidget(
            title='Target views',
            layout=QtWidgets.QGridLayout()
        )
        self._wdgt.addDock(dock, position='right')

        # TODO: put each view in its own dockwidget instead of a grid layout so they can be moved around

        for iRow, iCol, key in ((0, 1, 'x'), (0, 0, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            if key in ('x', 'y', 'z'):
                self._views[key] = MRISliceView(label=key, normal=self._getRotMatForCoilAxis(key))
            elif key == '3D':
                self._views[key] = Surf3DView(label=key, normal=np.eye(3),
                                              activeSurf=self._surfKeys,
                                              pickableSurfs=[self._surfKeys[0]],  # assuming surfKeys are [gm, skin], make only gm pickable
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

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._finishInitialization_async))

    async def _finishInitialization_async(self):
        for view in self._views.values():
            if isinstance(view.plotter, RemotePlotterProxy):
                await view.plotter.isReadyEvent.wait()

        self._onTargetsChanged()

        self.finishedAsyncInit.set()

    def _getCrosshairCoord(self) -> np.ndarray:
        """
        Used by CoordinateWidgets to set target or entry coord from current crosshair position
        """
        return self._views['3D'].sliceOrigin

    def _onTargetDispStyleChanged(self, index: int):
        for visualizedTarget in self._targetActors.values():
            visualizedTarget.style = self._targetDispStyle_comboBox.currentText()

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
            logger.debug(f"{'Disabling' if len(keys)==0 else 'Enabling'} {widget}")
            widget.setEnabled(len(keys)>0)

    def _onCurrentTargetChanged(self, targetKey: str | None):
        if targetKey is not None:
            self._gotoTarget(targetKey=targetKey)    # go to target immediately whenever selection changes

    def _gotoTarget(self, targetKey: str):

        logger.debug('gotoTarget')

        plotter = self._views['3D'].plotter
        if isinstance(plotter, RemotePlotterProxy) and not plotter.isReadyEvent.is_set():
            return  # plotter not ready yet

        # change slice camera views to align with selected target
        target = self.session.targets[targetKey]
        extraTransf = np.eye(4)
        if True:
            # align at target depth
            extraTransf[:3, 3] = applyTransform(invertTransform(target.coilToMRITransf), target.targetCoord, doCheck=False)
        else:
            # align at coil depth
            pass

        self._views['3D'].sliceTransform = concatenateTransforms([
            composeTransform(self._getRotMatForCoilAxis(axis='3D')),
            extraTransf,
            target.coilToMRITransf])

        if True:
            # also set camera position for 3D view to align with target
            plotter = self._views['3D'].plotter
            with plotter.allowNonblockingCalls():
                plotter.camera.focal_point = self._views['3D'].sliceOrigin
                cameraPos = applyTransform(self._views['3D'].sliceTransform, np.asarray([0, 0, 200]))
                plotter.camera.position = cameraPos
                plotter.camera.up = applyTransform(self._views['3D'].sliceTransform,
                                                                 np.asarray([0, 100, 0])) - cameraPos
                plotter.render()

    def _createVisualForTarget(self, viewKey: str, target: Target):
        logger.debug(f'Creating VisualizedTarget for target {target.asDict()} {target.coilToMRITransf}')
        view = self._views[viewKey]
        style = self._targetDispStyle_comboBox.currentText()
        self._targetActors[viewKey + target.key] = VisualizedTarget(target=target,
                                                                    plotter=view.plotter,
                                                                    style=style,
                                                                    visible=target.isVisible)

    def _onTargetsChanged(self, changedTargetKeys: tp.Optional[tp.List[str]] = None, changedTargetAttrs: tp.Optional[tp.List[str]] = None):
        if not self._hasInitialized and not self._isInitializing:
            return

        # TODO: move this below to only delay visualization initialization, not target table, etc.
        for view in self._views.values():
            if isinstance(view.plotter, RemotePlotterProxy) and not view.plotter.isReadyEvent.is_set():
                # plotter not ready yet
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
                    elif target.isVisible:
                        self._redrawTargetKeys.add(targetKey)
                        self._queueRedraw(which='targets')

        elif changedTargetAttrs == ['isSelected']:
            pass  # selection update will be handled separately

        else:
            # assume anything/everything changed, clear target and start over
            self._redrawTargetKeys.update(changedTargetKeys)
            self._queueRedraw(which='targets')

    def _redraw(self, which: tp.Union[str | None, list[str]] = None, **kwargs):
        super()._redraw(which=which)

        if which is None:
            self._redraw(which='all', **kwargs)
            return

        if not isinstance(which, str):
            for subWhich in which:
                self._redraw(which=subWhich, **kwargs)
            return

        if which == 'all':
            self._redraw(which=['targets'])
            return

        if which == 'targets':
            changedTargetKeys = self._redrawTargetKeys.copy()
            self._redrawTargetKeys.clear()
            if self._hasInitialized or self._isInitializing:
                # update views
                for viewKey, view in self._views.items():
                    for key in changedTargetKeys:
                        try:
                            target = self.session.targets[key]
                        except KeyError as e:
                            # previous key no longer in targets
                            if viewKey + key in self._targetActors:
                                visualizedTarget = self._targetActors.pop(viewKey + key)
                                visualizedTarget.clearActors()
                        else:
                            if viewKey + key in self._targetActors:
                                visualizedTarget = self._targetActors.pop(viewKey + key)
                                visualizedTarget.clearActors()
                            if target.isVisible:
                                self._createVisualForTarget(viewKey=viewKey, target=target)

                    view.updateView()

                currentTarget = self._getCurrentTargetKey()
                self._onCurrentTargetChanged(currentTarget)

    def _onImportTargetsBtnClicked(self, checked: bool):
        newFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._wdgt,
                                                               'Select targets file to import',
                                                               os.path.dirname(self.session.filepath),
                                                               'json (*.json);; NaviNIBS (*.navinibs)')

        if len(newFilepath) == 0:
            logger.warning('Import cancelled')
            return

        self._importTargetsFromFile(newFilepath=newFilepath)

    def _importTargetsFromFile(self, newFilepath: str):

        self.session.mergeFromFile(filepath=newFilepath, sections=['targets'])

        self._tableWdgt.resizeColumnsToContents()  # resize immediately rather than waiting for delayed auto-resize

    def _onAddBtnClicked(self, checked: bool):
        # create new target at current crosshair location, and autoset entry
        targetCoord = self._getCrosshairCoord()
        targetKey = makeStrUnique('Target-1', existingStrs=self.session.targets.keys(), delimiter='-')
        currentTargetKey = self._tableWdgt.currentCollectionItemKey
        if currentTargetKey is not None:
            # match angle from midline of current target for target in new position
            nextTargetAngle = self.session.targets[currentTargetKey].calculatedAngle
        else:
            nextTargetAngle = 0
        target = Target(targetCoord=targetCoord,
                        angle=nextTargetAngle,
                        key=targetKey,
                        session=self.session)
        target.autosetEntryCoord()
        self.session.targets.addItem(target)
        self._tableWdgt.currentCollectionItemKey = targetKey  # select new target

    def _onDeleteBtnClicked(self, checked: bool):
        selectedTargetKeys = self._tableWdgt.selectedCollectionItemKeys
        self.session.targets.deleteItems(selectedTargetKeys)

    def _onDuplicateBtnClicked(self, checked: bool):
        currentTargetKey = self._tableWdgt.currentCollectionItemKey
        assert currentTargetKey is not None
        if '_' in currentTargetKey:
            delim = '_'
        elif ' ' in currentTargetKey:
            delim = ' '
        else:
            delim = '-'
        newTargetKey = makeStrUnique(f'{currentTargetKey}{delim}copy{delim}1', existingStrs=self.session.targets.keys(), delimiter=delim)
        logger.info(f'Duplicating target {currentTargetKey} to {newTargetKey}')
        currentTarget = self.session.targets[currentTargetKey]
        newTarget = currentTarget.asDict()
        newTarget['key'] = newTargetKey
        self.session.targets.addItem(Target.fromDict(newTarget))

    def _onGotoBtnClicked(self, checked: bool):
        targetKey = self._getCurrentTargetKey()
        if targetKey is not None:
            self._gotoTarget(self._getCurrentTargetKey())
        else:
            logger.warning('No target selected')
