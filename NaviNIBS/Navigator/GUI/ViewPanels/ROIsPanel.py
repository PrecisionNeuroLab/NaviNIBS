from __future__ import annotations

import asyncio
import logging
import os
import typing as tp

import attrs
import distinctipy
import numpy as np
import pyvista as pv
from qtpy import QtWidgets, QtGui, QtCore

from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.GUI.Dock import Dock, DockArea
from NaviNIBS.Navigator.GUI.Widgets.SurfViews import Surf3DView
from NaviNIBS.Navigator.GUI.Widgets.CollectionTableWidget import ROIsTableWidget
from NaviNIBS.Navigator.GUI.Widgets.EditROIWidget import EditROIWidget
from NaviNIBS.Navigator.GUI.ViewPanels.MainViewPanelWithDockWidgets import MainViewPanelWithDockWidgets
from NaviNIBS.util import makeStrUnique
from NaviNIBS.util.GUI.Icons import getIcon
from NaviNIBS.util.GUI.QueuedRedrawMixin import QueuedRedrawMixin
from NaviNIBS.util.pyvista import Actor, RemotePlotterProxy
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import composeTransform, applyTransform, invertTransform, concatenateTransforms
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model import ROIs

if DefaultBackgroundPlotter is RemotePlotterProxy or tp.TYPE_CHECKING:
    from NaviNIBS.util.pyvista.RemotePlotting.RemotePlotterProxy import RemotePolyDataProxy, RemotePlotterProxyBase


logger = logging.getLogger(__name__)


@attrs.define
class VisualizedROI:
    _roi: ROIs.ROI
    _session: Session
    _linked3DView: Surf3DView
    _opacity: float = 0.85

    _meshKey: str | None = attrs.field(init=False, default=None)
    _mesh: pv.PolyData | None = attrs.field(init=False, default=None)
    _meshActor: Actor | None = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        self._initMesh()

        self._roi.sigItemChanged.connect(self._onROIChanged)

    @property
    def scalarsKey(self):
        return 'ROI_' + self._roi.key + '_rgba'

    def _onROIChanged(self, key: str, changedAttrs: list[str] | None = None):
        if changedAttrs is None:
            # assume everything changed
            self._initMesh()
            return

        if 'isVisible' in changedAttrs:
            self._refreshMeshVisibility()

        if 'color' in changedAttrs or 'autoColor' in changedAttrs:
            self._refreshMeshColor()

        if isinstance(self._roi, ROIs.SurfaceMeshROI):
            if 'meshKey' in changedAttrs:
                self._initMesh()
            else:
                if 'meshVertexIndices' in changedAttrs:
                    self._refreshMeshOpacity()

        elif isinstance(self._roi, ROIs.PipelineROI):
            if 'output' in changedAttrs or 'stages' in changedAttrs:
                newOutput = self._roi.getOutput()
                if not isinstance(newOutput, ROIs.SurfaceMeshROI):
                    raise NotImplementedError(f'Visualization for ROI type {type(newOutput)} not implemented')
                if newOutput.meshKey != self._meshKey:
                    self._initMesh()
                else:
                    # assume only thing that changed was vertex membership within existing mesh
                    self._refreshMeshOpacity()

        else:
            raise NotImplementedError(f'Visualization for ROI type {type(self._roi)} not implemented')

    def _initMesh(self):

        logger.info(f'Initializing mesh for visualizing ROI {self._roi.key}')

        if self._mesh is not None:
            # clear previous mesh, meshKey, meshActor
            self.clear()

        if not self._roi.isVisible:
            return  # nothing to do

        if isinstance(self._roi, ROIs.PipelineROI):
            roi = self._roi.getOutput()
        else:
            roi = self._roi

        if not isinstance(roi, ROIs.SurfaceMeshROI):
            raise NotImplementedError(f'Visualization for ROI type {type(roi)} not implemented')

        if roi.meshKey is None:
            logger.warning(f'ROI {roi.key} has no meshKey set, cannot visualize')
            return

        if len(roi.meshVertexIndices) == 0:
            # nothing to visualize
            return

        self._meshKey = roi.meshKey
        # TODO: subscribe to head model changes in this mesh

        self._mesh = pv.PolyData(getattr(self._session.headModel, roi.meshKey),
                                 deep=True)

        self._setMeshRGBA()

        if isinstance(self._linked3DView.plotter, RemotePlotterProxyBase):
            # wrap mesh as a RemotePolyDataProxy so that future updates
            # to mesh scalars are reflected in remote plotter
            self._mesh = self._linked3DView.plotter.registerPolyData(
                polyData=self._mesh,
                id=self._roi.key + '_VisualizedROIMesh',  # specify ID so that previous mesh gets overwritten on re-adding
            )

        self._meshActor = self._linked3DView.plotter.addMesh(
            self._mesh,
            scalars=self.scalarsKey,
            rgba=True,
            name=f'ROI_{self._roi.key}',
            pickable=False,
            specular=0.5,
            diffuse=0.5,
            ambient=0.5,
        )

    def _refreshMeshVisibility(self):
        logger.info(f'Refreshing mesh visibility for visualizing ROI {self._roi.key}')
        if self._meshActor is not None:
            with self._linked3DView.plotter.allowNonblockingCalls():
                self._meshActor.SetVisibility(self._roi.isVisible)
                self._linked3DView.plotter.render()
        elif self._roi.isVisible:
            self._initMesh()

    def _refreshMeshColor(self):
        logger.info(f'Refreshing mesh color for visualizing ROI {self._roi.key}')
        if self._meshActor is None:
            self._initMesh()
        else:
            self._setMeshRGBA()
            self._linked3DView.plotter.render()

    def _refreshMeshOpacity(self):
        logger.info(f'Refreshing mesh opacity for visualizing ROI {self._roi.key}')
        if self._meshActor is None:
            self._initMesh()
        else:
            self._setMeshRGBA()
            self._linked3DView.plotter.render()

    def _setMeshRGBA(self):
        if isinstance(self._roi, ROIs.PipelineROI):
            roi = self._roi.getOutput()
        else:
            assert isinstance(self._roi, ROIs.SurfaceMeshROI)
            roi = self._roi

        if roi.color is None:
            assert roi.autoColor is not None, 'autoColor should have been set before visualization'
            color = roi.autoColor
        else:
            color = roi.color[0:3]  # ignore any pre-set alpha
        color = list(color)

        # convert color from rgbf (0-1) to rgba (0-255 integer)
        rgbaColor = [round(255 * c) for c in color]

        logger.debug(f'rgbaColor: {rgbaColor}')

        # will store rgba value (0-255 uint8) per vertex
        # TODO: possibly cache this array so that it doesn't need to reallocated with each update
        newRGBA = np.full((self._mesh.n_points, 4), 0, dtype=np.uint8)

        newRGBA[:, 0:3] = rgbaColor[0:3]

        newRGBA[roi.meshVertexIndices, 3] = round(255 * self._opacity)  # set alpha for ROI vertices

        # do full assignment rather than subset above to trigger remote observer properly
        self._mesh[self.scalarsKey] = newRGBA

    def clear(self):
        if self._meshActor is not None:
            # clear previous mesh, meshKey, meshActor
            logger.info(f'Clearing visualized ROI {self._roi.key}')
            self._meshKey = None
            self._mesh = None
            self._linked3DView.plotter.remove_actor(self._meshActor)
            self._meshActor = None


@attrs.define
class ROIsPanel(MainViewPanelWithDockWidgets, QueuedRedrawMixin):
    _key: str = 'Set ROIs'

    _doMoveCameraToActiveROI: bool = True

    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: getIcon('mdi6.map'))
    _tableWdgt: ROIsTableWidget = attrs.field(init=False)
    _surfView: Surf3DView = attrs.field(init=False)
    _editROIWdgt: EditROIWidget = attrs.field(init=False)
    _visualizedROIs: dict[str, VisualizedROI] = attrs.field(init=False, factory=dict)
    _enabledOnlyWhenROISelected: list[QtWidgets.QWidget] = attrs.field(init=False, factory=list)

    _redrawROIKeys: set[str] = attrs.field(init=False, factory=set)

    finishedAsyncInit: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        MainViewPanelWithDockWidgets.__attrs_post_init__(self)
        QueuedRedrawMixin.__attrs_post_init__(self)

    def canBeEnabled(self) -> tuple[bool, str | None]:
        if self.session is None:
            return False, 'No session loaded'
        if not self.session.MRI.isSet:
            return False, 'MRI not set'
        if not self.session.headModel.isSet:
            return False, 'Head model not set'
        return True, None

    def _refreshROIAutoColors(self):
        autoROIs = [roi for roi in self.session.ROIs.values() if roi.color is None and roi.isVisible]
        if len(autoROIs) == 0:
            return  # no need to update

        logger.info('Refreshing auto ROI colors')

        fixedROIs = [roi for roi in self.session.ROIs.values() if roi.color is not None and roi.isVisible]

        excludeColors = [[0., 0., 0.], [1., 1., 1.]]

        if isinstance(self._surfView.surfColor, str):
            meshColors = [self._surfView.surfColor]
        else:
            meshColors = self._surfView.surfColor
        # convert meshColor in form '#d9a5b2' into rgb 0-1
        meshColors = [QtGui.QColor(colorStr).getRgbF()[0:3] for colorStr in meshColors]
        excludeColors.extend(meshColors)

        fixedROIColors = [list(roi.color) for roi in fixedROIs]
        excludeColors.extend(fixedROIColors)

        autoROIColors = distinctipy.get_colors(len(autoROIs),
                                               exclude_colors=excludeColors,
                                               rng=0)
        for iROI, roi in enumerate(autoROIs):
            roi.autoColor = autoROIColors[iROI]

    def _finishInitialization(self):
        super()._finishInitialization()

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        dock, container = self._createDockWidget(
            title='ROIs',
            layout=QtWidgets.QVBoxLayout(),
        )
        dock.setStretch(1, 10)
        container.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        self._wdgt.addDock(dock, position='left')

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        container.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Import ROIs from file...')
        btn.clicked.connect(self._onImportROIsBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 0, 1, 2)

        btn = QtWidgets.QPushButton('Add ROI')
        btn.clicked.connect(self._onAddBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 0)

        btn = QtWidgets.QPushButton('Delete ROI')
        btn.clicked.connect(self._onDeleteBtnClicked)
        btnContainer.layout().addWidget(btn, 1, 1)
        self._enabledOnlyWhenROISelected.append(btn)

        btn = QtWidgets.QPushButton('Duplicate ROI')
        btn.clicked.connect(self._onDuplicateBtnClicked)
        btnContainer.layout().addWidget(btn, 2, 0)
        self._enabledOnlyWhenROISelected.append(btn)

        self._tableWdgt = ROIsTableWidget()
        self._tableWdgt.sigCurrentItemChanged.connect(self._onCurrentROIChanged)
        self._tableWdgt.sigSelectionChanged.connect(self._onSelectionChanged)
        container.layout().addWidget(self._tableWdgt.wdgt)

        surfKeys = ['gmSurf', 'skinSurf']
        surfOpacities = [0.8, 0.5]

        self._surfView = Surf3DView(label='Head model', normal=np.eye(3),
                                    backgroundColor=None,
                                    activeSurf=surfKeys,
                                    doEnablePicking=False,  # let individual ROI edit widgets enable on demand
                                    doShowCrosshairs=False,  # let individual ROI edit widgets render on demand
                                    cameraOffsetDist=50,
                                    surfOpacity=surfOpacities)

        self._editROIWdgt = EditROIWidget(session=self.session,
                                          wdgt=QtWidgets.QWidget(),
                                          linkTo3DView=self._surfView)
        dock, _ = self._createDockWidget(
            title='Edit ROI',
            widget=self._editROIWdgt.wdgt,
        )
        dock.setStretch(2, 10)
        self._wdgt.addDock(dock, position='right')
        dock_editROI = dock

        dock, container = self._createDockWidget(
            title='Head model',
            layout=QtWidgets.QVBoxLayout(),
        )
        self._wdgt.addDock(dock, position='right')

        # TODO: add GUI control of which mesh surfaces are visible

        container.layout().addWidget(self._surfView.wdgt)

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

        for widget in self._enabledOnlyWhenROISelected:
            logger.debug(f'Disabling {widget}')
            widget.setEnabled(False)

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._finishInitialization_async))

    async def _finishInitialization_async(self):
        for view in [self._surfView]:
            if isinstance(view.plotter, RemotePlotterProxy):
                await view.plotter.isReadyEvent.wait()

        self._onROIsChanged()

        self.finishedAsyncInit.set()

    def _onSessionSet(self):
        super()._onSessionSet()

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):
        self.session.ROIs.sigItemsChanged.connect(self._onROIsChanged)
        self._tableWdgt.session = self.session
        # self._editROIWdgt.roisModel = self._tableWdgt.model  # share models to keep selection in sync

        for view in [self._surfView]:
            view.session = self.session

    def _onSelectionChanged(self, keys: list[str]):
        self._queueRedraw(which='cameraPos')
        for widget in self._enabledOnlyWhenROISelected:
            # logger.debug(f"{'Disabling' if len(keys)==0 else 'Enabling'} {widget}")
            widget.setEnabled(len(keys)>0)
        currentKey = self._tableWdgt.currentCollectionItemKey
        if currentKey is None:
            self._editROIWdgt.roiComboBox.currentIndex = -1
        else:
            self._editROIWdgt.roiComboBox.setCurrentText(currentKey)

    def _onROIsChanged(self, changedKeys: list[str] | None = None, changedAttrs: list[str] | None = None):
        if not self._hasInitialized and not self._isInitializing:
            return

        for view in [self._surfView]:
            if isinstance(view.plotter, RemotePlotterProxy) and not view.plotter.isReadyEvent.is_set():
                # plotter not ready yet
                return

        logger.debug(f'onROIsChanged: {changedKeys}, {changedAttrs}')

        if changedAttrs is not None:
            # remove attrs that can be handled by existing visualizations or can be ignored
            changedAttrs = changedAttrs.copy()
            for changedAttr in ['output', 'autoColor', 'stages']:
                try:
                    changedAttrs.remove(changedAttr)
                except ValueError:
                    pass
            if len(changedAttrs) == 0:
                return

        if changedKeys is None:
            changedKeys = self.session.ROIs.keys()

        if changedAttrs is None or 'color' in changedAttrs:
            self._queueRedraw(which='ROIAutoColors')

        if changedAttrs == ['isVisible']:
            # only visibility changed
            self._queueRedraw(which='ROIAutoColors')
            for roiKey in changedKeys:
                roi = self.session.ROIs[roiKey]
                if roiKey in self._visualizedROIs:
                    pass  # let existing visualized ROI update itself
                elif roi.isVisible:
                    self._redrawROIKeys.add(roiKey)
                    self._queueRedraw(which='ROIs')

        elif changedAttrs == ['isSelected']:
            self._queueRedraw(which='cameraPos')
            # TODO: change opacity for selected / deselected ROIs to make it clear which are active
            pass  # selection update will be handled separately

        else:
            # assume anything/everything changed, clear ROI and start over
            self._redrawROIKeys.update(changedKeys)
            self._queueRedraw(which='ROIs')

    def _redraw(self, which: str | None | list[str] = None, **kwargs):
        super()._redraw(which=which)

        if isinstance(self._surfView.plotter, RemotePlotterProxy) and not self._surfView.plotter.isReadyEvent.is_set():
            # remote plotter not ready yet
            return

        if which is None:
            self._redraw(which='all', **kwargs)
            return

        if not isinstance(which, str):
            for subWhich in which:
                self._redraw(which=subWhich, **kwargs)
            return

        if which == 'all':
            self._redraw(which=['ROIAutoColors', 'ROIs', 'cameraPos'])
            return

        if which == 'ROIAutoColors':
            self._refreshROIAutoColors()

        if which == 'ROIs':
            changedROIKeys = self._redrawROIKeys.copy()
            self._redrawROIKeys.clear()
            if self._hasInitialized or self._isInitializing:
                # update view
                for key in changedROIKeys:
                    try:
                        roi = self.session.ROIs[key]
                    except KeyError:
                        # previous key no longer in ROIs
                        if key in self._visualizedROIs:
                            visualizedROI = self._visualizedROIs.pop(key)
                            visualizedROI.clear()
                    else:
                        if key in self._visualizedROIs:
                            visualizedROI = self._visualizedROIs.pop(key)
                            visualizedROI.clear()

                        if roi.isVisible:
                            # create new visualization
                            if True:  # TODO: debug, set to true / remove conditional
                                visualizedROI = VisualizedROI(roi=roi,
                                                              session=self.session,
                                                              linked3DView=self._surfView)
                                self._visualizedROIs[key] = visualizedROI

                self._surfView.updateView()

                currentROIKey = self._getCurrentROIKey()
                self._onCurrentROIChanged(currentROIKey)

        if which == 'cameraPos':
            if self._doMoveCameraToActiveROI:
                currentROIKey = self._getCurrentROIKey()
                if currentROIKey is not None:
                    try:
                        roi = self.session.ROIs[currentROIKey]
                    except KeyError:
                        pass
                    else:
                        if isinstance(roi, ROIs.PipelineROI):
                            roi = roi.getOutput()

                        if isinstance(roi, ROIs.SurfaceMeshROI):
                            roiCenter = roi.seedCoord
                        else:
                            roiCenter = None

                        if roiCenter is not None:
                            plotter = self._surfView.plotter

                            try:
                                headCenter = self.session.coordinateSystems['MNI_SimNIBS12DoF'].transformFromThisToWorld(np.asarray([0, 0, 0]))
                            except KeyError:
                                # TODO: try estimating center from fiducials if they're available instead
                                headCenter = np.array([0, 0, 0])  # may be wrong depending on MRI alignment

                            cameraDistance = np.linalg.norm(
                                np.asarray(plotter.camera.position) - np.asarray(plotter.camera.focal_point))
                            with plotter.allowNonblockingCalls():
                                plotter.camera.focal_point = roiCenter
                                lookDirection = roiCenter - headCenter
                                lookDirection /= np.linalg.norm(lookDirection)
                                plotter.camera.position = roiCenter + lookDirection * cameraDistance
                                plotter.render()

    def _getCurrentROIKey(self) -> str | None:
        return self._tableWdgt.currentCollectionItemKey

    def _onCurrentROIChanged(self, roiKey: str | None):
        logger.debug(f'Current ROI changed to {roiKey}')

    def _onImportROIsBtnClicked(self, checked: bool):
        newFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._wdgt,
                                                               'Select ROIs file to import',
                                                               os.path.dirname(self.session.filepath),
                                                               'json (*.json);; NaviNIBS (*.navinibs)')

        if len(newFilepath) == 0:
            logger.warning('Import cancelled')
            return

        self._importROIsFromFile(newFilepath=newFilepath)

    def _importROIsFromFile(self, newFilepath: str):

        self.session.mergeFromFile(filepath=newFilepath, sections=['ROIs'])

        self._tableWdgt.resizeColumnsToContents()  # resize immediately rather than waiting for delayed auto-resize


    def _onAddBtnClicked(self, checked: bool):
        roiKey = makeStrUnique('ROI-1', existingStrs=self.session.ROIs.keys(), delimiter='-')
        roi = ROIs.PipelineROI(key=roiKey)
        self.session.ROIs.addItem(roi)
        self._tableWdgt.currentCollectionItemKey = roiKey  # select new ROI

    def _onDeleteBtnClicked(self, checked: bool):
        selectedROIKeys = self._tableWdgt.selectedCollectionItemKeys
        self.session.ROIs.deleteItems(selectedROIKeys)

    def _onDuplicateBtnClicked(self, checked: bool):
        currentROIKey = self._tableWdgt.currentCollectionItemKey
        assert currentROIKey is not None
        if '_' in currentROIKey:
            delim = '_'
        elif ' ' in currentROIKey:
            delim = ' '
        else:
            delim = '-'
        newROIKey = makeStrUnique(f'{currentROIKey}{delim}copy{delim}1', existingStrs=self.session.ROIs.keys(), delimiter=delim)
        logger.info(f'Duplicating ROI {currentROIKey} to {newROIKey}')
        currentROI = self.session.ROIs[currentROIKey]
        newROI = currentROI.asDict()
        newROI['key'] = newROIKey
        newROI['session'] = self._session
        self.session.ROIs.addItem(self.session.ROIs.roiFromDict(newROI))

