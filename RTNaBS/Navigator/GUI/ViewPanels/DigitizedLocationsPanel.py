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
from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Navigator.GUI.Widgets.CollectionTableWidget import DigitizedLocationsTableWidget
from RTNaBS.Navigator.GUI.Widgets.TrackingStatusWidget import TrackingStatusWidget
from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.DigitizedLocations import DigitizedLocation
from RTNaBS.Navigator.Model.Tools import CoilTool, CalibrationPlate
from RTNaBS.util import makeStrUnique
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.pyvista import Actor, setActorUserTransform
from RTNaBS.util.pyvista.plotting import BackgroundPlotter
from RTNaBS.util.Transforms import applyTransform, invertTransform, transformToString, stringToTransform, estimateAligningTransform, concatenateTransforms


logger = logging.getLogger(__name__)


@attrs.define
class DigitizedLocationsPanel(MainViewPanel):
    _key: str = 'Digitize'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.dots-hexagon'))
    _surfKey: str = 'skinSurf'

    _trackingStatusWdgt: TrackingStatusWidget = attrs.field(init=False)

    _sampleLocationBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _clearSampleLocationBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _deleteLocationBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _newLocationFromPointerBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _plotter: BackgroundPlotter = attrs.field(init=False)
    _actors: tp.Dict[str, tp.Optional[Actor]] = attrs.field(init=False, factory=dict)
    _positionsClient: tp.Optional[ToolPositionsClient] = attrs.field(init=False, default=None)
    _tblWdgt: DigitizedLocationsTableWidget = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def canBeEnabled(self) -> tuple[bool, str | None]:
        if self.session is None:
            return False, 'No session set'
        if not self.session.MRI.isSet:
            return False, 'No MRI set'
        if not self.session.headModel.isSet:
            return False, 'No head model set'
        if self.session.tools.subjectTracker is None:
            return False, 'No active subject tracker configured'
        if self.session.tools.pointer is None:
            return False, 'No active pointer tool configured'
        if not self.session.subjectRegistration.isRegistered:
            return False, 'Subject not registered'
        return True, None

    def _finishInitialization(self):
        super()._finishInitialization()

        self._positionsClient = ToolPositionsClient()
        self._positionsClient.sigLatestPositionsChanged.connect(lambda: self._redraw(which=['pointerPosition', 'sampleBtn']))

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        sidebar = QtWidgets.QWidget()
        sidebar.setLayout(QtWidgets.QVBoxLayout())
        sidebar.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.MinimumExpanding)
        self._wdgt.layout().addWidget(sidebar)

        self._trackingStatusWdgt = TrackingStatusWidget(session=self.session,
                                                        hideToolTypes=[CoilTool, CalibrationPlate])
        sidebar.layout().addWidget(self._trackingStatusWdgt.wdgt)

        samplesBox = QtWidgets.QGroupBox('Digitized locations')
        samplesBox.setLayout(QtWidgets.QVBoxLayout())
        sidebar.layout().addWidget(samplesBox)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        samplesBox.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Import montage')
        btn.clicked.connect(self._onImportMontageBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 0)

        btn = QtWidgets.QPushButton('Sample location')
        btn.clicked.connect(self._onSampleLocationBtnClicked)
        btn.setEnabled(False)
        self._sampleLocationBtn = btn
        btnContainer.layout().addWidget(btn, 1, 0)

        btn = QtWidgets.QPushButton('Clear sampled location')
        btn.clicked.connect(self._onClearSampledLocationBtnClicked)
        btn.setEnabled(False)
        btnContainer.layout().addWidget(btn, 2, 0)
        # TODO: change this to "clear sampled locations" when multiple selected
        self._clearSampleLocationBtn = btn

        btn = QtWidgets.QPushButton('Delete row')
        btn.clicked.connect(self._onDeleteLocationBtnClicked)
        btnContainer.layout().addWidget(btn, 3, 0)
        # TODO: change this to "clear sampled locations" when multiple selected
        self._deleteLocationBtn = btn

        self._tblWdgt = DigitizedLocationsTableWidget()
        self._tblWdgt.sigSelectionChanged.connect(self._onSelectedLocationsChanged)
        samplesBox.layout().addWidget(self._tblWdgt.wdgt)

        sidebar.layout().addStretch()

        self._plotter = BackgroundPlotter(
            show=False,
            app=QtWidgets.QApplication.instance()
        )
        self._plotter.enable_depth_peeling(4)
        self._wdgt.layout().addWidget(self._plotter.interactor)

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

    def _onSessionSet(self):
        super()._onSessionSet()
        # TODO: connect relevant session changed signals to _redraw calls

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):
        self.session.headModel.sigDataChanged.connect(lambda which: self._redraw(which='initSurf'))
        self.session.digitizedLocations.sigItemsChanged.connect(self._onLocationsChanged)
        self.session.subjectRegistration.sigTrackerToMRITransfChanged.connect(lambda: self._redraw(which=[
            'initSampledLocations', 'initSubjectTracker', 'initPointer', 'pointerPosition']))

        self._trackingStatusWdgt.session = self.session

        self._tblWdgt.session = self.session
        self._tblWdgt.currentRow = 0

        self._redraw(which='all')

    def _onLocationsChanged(self, dlKeys: list[str], attribs: tp.Optional[list[str]] = None):
        if attribs is None or any(attrib in attribs for attrib in ('sampledCoord', 'color')):
            self._redraw(which='initSampledLocations')
        # TODO: add support for redrawing plannedCoords as needed

    def _onSelectedLocationsChanged(self, selKeys: list[str]):
        if len(selKeys) == 0:
            self._clearSampleLocationBtn.setEnabled(False)
            self._clearSampleLocationBtn.setText('Clear sampled location')
        else:
            numSelLocationsWithSamples = sum(self.session.digitizedLocations[key].sampledCoord is not None for key in selKeys if key is not None)

            if numSelLocationsWithSamples == 0:
                self._clearSampleLocationBtn.setEnabled(False)
                self._clearSampleLocationBtn.setText('Clear sampled location')
            elif numSelLocationsWithSamples == 1:
                self._clearSampleLocationBtn.setEnabled(True)
                self._clearSampleLocationBtn.setText('Clear sampled location')
            else:
                self._clearSampleLocationBtn.setEnabled(True)
                self._clearSampleLocationBtn.setText('Clear sampled locations')

    def _currentTblLocKey(self) -> tp.Optional[str]:
        return self._tblWdgt.currentCollectionItemKey

    def _getPointerCoordInMRISpace(self) -> tp.Optional[np.ndarray]:
        pointerToCameraTransf = self._positionsClient.getLatestTransf(self.session.tools.pointer.key, None)
        subjectTrackerToCameraTransf = self._positionsClient.getLatestTransf(self.session.tools.subjectTracker.key, None)
        subjectTrackerToMRITransf = self.session.subjectRegistration.trackerToMRITransf

        if pointerToCameraTransf is None or subjectTrackerToCameraTransf is None or subjectTrackerToMRITransf is None:
            logger.warning('Tried to sample, but do not have valid positions. Returning.')
            return None

        pointerCoord_MRISpace = applyTransform([self.session.tools.pointer.toolToTrackerTransf,
                                                       pointerToCameraTransf,
                                                       invertTransform(subjectTrackerToCameraTransf),
                                                       subjectTrackerToMRITransf],
                                               np.zeros((3,)))

        return pointerCoord_MRISpace

    def _onImportMontageBtnClicked(self, checked: bool):
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._wdgt,
                                                               'Select montage file to import',
                                                               os.path.dirname(self.session.filepath),
                                                               'xml (*.xml)')

        self.session.digitizedLocations.loadFromXML(filepath)

    def _onSampleLocationBtnClicked(self):

        pointerCoord_MRISpace = self._getPointerCoordInMRISpace()
        if pointerCoord_MRISpace is None:
            return

        logger.info(f'Digitized location in MRI space: {pointerCoord_MRISpace}')

        locKey = self._currentTblLocKey()
        if locKey is None:
            # placeholder new row selected
            locKey = makeStrUnique(baseStr=f'Electrode_{len(self.session.digitizedLocations) + 1}',
                                   existingStrs=self.session.digitizedLocations.keys(),
                                   delimiter='_')
            loc = DigitizedLocation(key=locKey,
                                    sampledCoord=pointerCoord_MRISpace)
            self.session.digitizedLocations.addItem(loc)
        else:
            self.session.digitizedLocations[locKey].sampledCoord = pointerCoord_MRISpace

        if True:
            currentRow = self._tblWdgt.currentRow
            if currentRow == self._tblWdgt.rowCount - 1:
                # already at end of table
                # TODO: auto-advance to prompt about aligning
                pass
            else:
                # advance to next fiducial in table
                self._tblWdgt.currentRow += 1

    def _onClearSampledLocationBtnClicked(self, checked: bool):
        for key in self._tblWdgt.selectedCollectionItemKeys:
            if key is not None:
                self.session.digitizedLocations[key].sampledCoord = None

    def _onDeleteLocationBtnClicked(self, checked: bool):
        for key in self._tblWdgt.selectedCollectionItemKeys:
            if key is not None:
                self.session.digitizedLocations.deleteItem(key)

    def _redraw(self, which: tp.Union[str, tp.List[str,...]]):

        if not self.isVisible:
            return

        logger.debug('redraw {}'.format(which))

        if isinstance(which, list):
            for subWhich in which:
                self._redraw(which=subWhich)
            return

        if which == 'all':
            which = ['initSurf', 'initSubjectTracker', 'initPointer', 'initSampledLocations',
                     'sampleBtn']
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

        elif which in ('sampleBtn',):
            pointer = self.session.tools.pointer
            subjectTracker = self.session.tools.subjectTracker

            allowSampling = False
            if pointer is not None and subjectTracker is not None:
                allowSampling = not any(self._positionsClient.getLatestTransf(key, None) is None for key in (pointer.key, subjectTracker.key))

            if self._sampleLocationBtn.isEnabled() != allowSampling:
                self._sampleLocationBtn.setEnabled(allowSampling)

        elif which in ('initPointer', 'pointerPosition'):
            pointer = self.session.tools.pointer
            subjectTracker = self.session.tools.subjectTracker

            doShowPointer = self.session.subjectRegistration.trackerToMRITransf is not None \
                            and pointer is not None \
                            and (pointer.trackerSurf is not None or pointer.toolSurf is not None) \
                            and subjectTracker is not None

            if not doShowPointer:
                for toolOrTracker in ('tool', 'tracker'):
                    actorKey = 'pointer' + '_' + toolOrTracker
                    if actorKey in self._actors:
                        self._plotter.remove_actor(self._actors[actorKey])
                        self._actors.pop(actorKey)
                return

            if which == 'initPointer':
                for toolOrTracker in ('tool', 'tracker'):
                    actorKey = 'pointer' + '_' + toolOrTracker
                    actorSurf = getattr(self.session.tools.pointer, toolOrTracker + 'Surf')
                    if actorSurf is None:
                        if actorKey in self._actors:
                            self._plotter.remove_actor(self._actors[actorKey])
                            self._actors.pop(actorKey)
                        continue
                    self._actors[actorKey] = self._plotter.add_mesh(mesh=actorSurf,
                                                                    color='#999999',
                                                                    opacity=0.6,
                                                                    name=actorKey)
                self._redraw(which='pointerPosition')

            elif which == 'pointerPosition':
                for toolOrTracker in ('tool', 'tracker'):
                    actorKey = 'pointer' + '_' + toolOrTracker

                    if actorKey not in self._actors:
                        # assume this was because we don't have enough info to show
                        continue

                    pointerToCameraTransf = self._positionsClient.getLatestTransf(pointer.key, None)
                    subjectTrackerToCameraTransf = self._positionsClient.getLatestTransf(subjectTracker.key, None)

                    if pointerToCameraTransf is None or subjectTrackerToCameraTransf is None:
                        # don't have valid info for determining pointer position relative to head tracker
                        if self._actors[actorKey].GetVisibility():
                            self._actors[actorKey].VisibilityOff()
                        continue

                    if not self._actors[actorKey].GetVisibility():
                        self._actors[actorKey].VisibilityOn()

                    if toolOrTracker == 'tool':
                        pointerStlToSubjectTrackerTransf = concatenateTransforms([
                            pointer.toolStlToToolTransf,
                            pointer.toolToTrackerTransf,
                            pointerToCameraTransf,
                            invertTransform(subjectTrackerToCameraTransf)
                        ])
                    elif toolOrTracker == 'tracker':
                        pointerStlToSubjectTrackerTransf = concatenateTransforms([
                            pointer.trackerStlToTrackerTransf,
                            pointerToCameraTransf,
                            invertTransform(subjectTrackerToCameraTransf)
                        ])
                    else:
                        raise NotImplementedError()

                    setActorUserTransform(
                        self._actors[actorKey],
                        concatenateTransforms([
                            pointerStlToSubjectTrackerTransf,
                            self.session.subjectRegistration.trackerToMRITransf
                        ])
                    )
                    self._plotter.render()

            else:
                raise NotImplementedError()

        elif which == 'subjectTrackerPosition':
            actorKey = 'subjectTracker'
            if actorKey not in self._actors:
                # subject tracker hasn't been initialized, maybe due to missing information
                return

            setActorUserTransform(
                self._actors[actorKey],
                self.session.subjectRegistration.trackerToMRITransf @ self.session.tools.subjectTracker.trackerStlToTrackerTransf
            )
            self._plotter.render()

        elif which == 'initSampledLocations':

            actorKeys = ('sampledLocations',)

            sampledLocKeys = [key for key, loc in self.session.digitizedLocations.items() if loc.sampledCoord is not None]
            doShowSampledLocs = len(sampledLocKeys) > 0

            if not doShowSampledLocs:
                # no sampled locations (yet)
                for actorKey in actorKeys:
                    if actorKey in self._actors:
                        self._plotter.remove_actor(actorKey)
                        self._actors.pop(actorKey)
                return

            labels = []
            coords = np.full((len(self.session.digitizedLocations), 3), np.nan)



            for iLoc, (label, loc) in enumerate(self.session.digitizedLocations.items()):
                labels.append(label)
                if loc.sampledCoord is not None:
                    coords[iLoc, :] = loc.sampledCoord

            locColor = list(self.session.digitizedLocations.values())[0].color
            # TODO: add support for per-electrode colors instead of using same for all
            # (will probably require multiple actors, at least one per unique color)

            self._actors[actorKeys[0]] = self._plotter.add_point_labels(
                name=actorKeys[0],
                points=coords,
                labels=labels,
                point_color=locColor,
                text_color=locColor,
                point_size=25,
                shape=None,
                render_points_as_spheres=True,
                reset_camera=False,
                render=True
            )

        else:
            raise NotImplementedError('Unexpected redraw key: {}'.format(which))

        self._plotter.render()