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
from RTNaBS.Navigator.GUI.Widgets.MRISliceView import MRISliceView
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class SurfSliceView(MRISliceView):

    _activeSurf: str = 'gmSurf'

    _slicePlotMethod: str = 'slicedSurface'

    _surfColor: str = '#d9a5b2'
    _surfPlotInitialized: bool = attrs.field(init=False, default=False)
    _surfPlotActor: tp.Optional[tp.Any] = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        if self._session is not None:
            self._session.headModel.sigDataChanged.connect(self._onHeadModelDataChanged)

    @property
    def activeSurf(self):
        return self._activeSurf

    @activeSurf.setter
    def activeSurf(self, newKey: str):
        if self._activeSurf == newKey:
            return
        self._activeSurf = newKey
        self._onHeadModelDataChanged(whatChanged=self._activeSurf)

    def _clearPlot(self):
        super()._clearPlot()
        self._surfPlotInitialized = False

    def _onHeadModelDataChanged(self, whatChanged: str):
        if whatChanged == self._activeSurf:
            self._plotter.remove_actor(self._surfPlotActor)
            self._surfPlotActor = None
            self._surfPlotInitialized = False
            self._updateView()
        else:
            # ignore other changes
            pass

    def _updateView(self):
        super()._updateView()

        if not self._surfPlotInitialized \
                and self.session is not None \
                and getattr(self.session.headModel, self._activeSurf) is not None:
            self._surfPlotActor = self._plotter.add_mesh(mesh=getattr(self.session.headModel, self._activeSurf),
                                                         color=self._surfColor,
                                                         opacity=0.5,
                                                         name=self.label + '_surf',
                                                         )
            self._surfPlotInitialized = True

        self._plotter.camera.clipping_range = (90, 110)


@attrs.define()
class Surf3DView(SurfSliceView):
    def _updateView(self):
        if self.session is None or self.session.MRI.data is None:
            # no data available
            if self._plotterInitialized:
                logger.debug('Clearing plot for {} slice'.format(self.label))
                self._plotter.clear()

                self.sliceOrigin = None
                self._plotterInitialized = False
            return

        # data available, update display
        logger.debug('Updating plot for {} slice'.format(self.label))
        if self._sliceOrigin is None:
            self.sliceOrigin = (self.session.MRI.data.affine @ np.append(np.asarray(self.session.MRI.data.shape) / 2,
                                                                         1))[:-1]
            return  # prev line will have triggered its own update

        if not self._surfPlotInitialized \
                and self.session is not None \
                and getattr(self.session.headModel, self._activeSurf) is not None:
            logger.debug('Initializing 3D plot')
            self._surfPlotActor = self._plotter.add_mesh(mesh=getattr(self.session.headModel, self._activeSurf),
                                                         color=self._surfColor,
                                                         opacity=1,
                                                         name=self.label + '_surf',
                                                         )
            self._surfPlotInitialized = True

        logger.debug('Setting crosshairs for {} plot'.format(self.label))
        lineLength = 300  # TODO: scale by image size
        crosshairAxes = 'xyz'
        centerGapLength = 0
        for axis in crosshairAxes:
            mask = np.zeros((1, 3))
            mask[0, 'xyz'.index(axis)] = 1
            for iDir, dir in enumerate((-1, 1)):
                pts = dir*np.asarray([centerGapLength/2, lineLength])[:, np.newaxis] * mask + self._sliceOrigin
                lineKey = 'Crosshair_{}_{}_{}'.format(self.label, axis, iDir)
                if not self._plotterInitialized:
                    line = self._plotter.add_lines(pts, color='#11DD11', width=2, name=lineKey)
                    self._lineActors[lineKey] = line
                else:
                    logger.debug('Moving previous crosshairs')
                    line = self._lineActors[lineKey]
                    pts_pv = pv.lines_from_points(pts)
                    line.GetMapper().SetInputData(pts_pv)

        self._plotterInitialized = True


@attrs.define()
class HeadModelPanel(MainViewPanel):
    _filepathWdgt: QFileSelectWidget = attrs.field(init=False)
    _activeSurfWidget: QtWidgets.QListWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[SurfSliceView, Surf3DView]] = attrs.field(init=False, factory=dict)
    _hasBeenActivated: bool = attrs.field(init=False, default=False)

    def __attrs_post_init__(self):
        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        wdgt = QFileSelectWidget(browseMode='getOpenFilename',
                                 extFilters='Gmsh (*.msh)')
        wdgt.sigFilepathChanged.connect(self._onBrowsedNewFilepath)
        self._wdgt.layout().addWidget(wdgt)
        self._filepathWdgt = wdgt

        containerWdgt = QtWidgets.QWidget()
        containerWdgt.setLayout(QtWidgets.QFormLayout())
        self._activeSurfWidget = QtWidgets.QListWidget()
        self._activeSurfWidget.itemSelectionChanged.connect(lambda *args, **kwargs: self._onSurfSelectionChanged())
        containerWdgt.layout().addRow('Surfaces', self._activeSurfWidget)
        self._wdgt.layout().addWidget(containerWdgt)

        containerWdgt = QtWidgets.QWidget()
        containerLayout = QtWidgets.QGridLayout()
        containerWdgt.setLayout(containerLayout)
        self._wdgt.layout().addWidget(containerWdgt)
        for iRow, iCol, key in ((0, 0, 'x'), (0, 1, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            if key in ('x', 'y', 'z'):
                self._views[key] = SurfSliceView(normal=key)
            elif key == '3D':
                self._views[key] = Surf3DView(normal=key)
            else:
                raise NotImplementedError()

            self._views[key].sigSliceOriginChanged.connect(lambda key=key: self._onSliceOriginChanged(sourceKey=key))

            containerLayout.addWidget(self._views[key].wdgt, iRow, iCol)

        self.sigPanelActivated.connect(self._onPanelActivated)

    def _onPanelActivated(self):
        # don't initialize computationally-demanding views until panel is activated (viewed)
        for key, view in self._views.items():
            if view.session is None and self.session is not None:
                view.session = self.session
        self._hasBeenActivated = True

    def _onSliceOriginChanged(self, sourceKey: str):
        for key, view in self._views.items():
            if key == sourceKey:
                continue
            view.sliceOrigin = self._views[sourceKey].sliceOrigin

    def _onSessionSet(self):
        super()._onSessionSet()
        self._updateFilepath()
        self._updateRelativeToPath()
        self.session.sigInfoChanged.connect(self._updateRelativeToPath)
        self.session.headModel.sigFilepathChanged.connect(self._updateFilepath)
        self.session.headModel.sigDataChanged.connect(self._onHeadModelUpdated)

        if self._hasBeenActivated:
            for key, view in self._views.items():
                view.session = self.session

    def _onHeadModelUpdated(self, whatChanged: str):
        prevSelected = self._activeSurfWidget.selectedItems()
        if len(prevSelected) > 0:
            prevSelectedKey = prevSelected[0].text()
        else:
            prevSelectedKey = None
        self._activeSurfWidget.clear()
        if self.session is not None and self.session.headModel.isSet:
            for key in self.session.headModel.surfKeys:
                self._activeSurfWidget.addItem(key)
            if prevSelectedKey is not None:
                selectKey = prevSelectedKey
            else:
                selectKey = 'gmSurf'
            self._activeSurfWidget.setCurrentItem(self._activeSurfWidget.findItems(selectKey, QtCore.Qt.MatchExactly)[0])

    def _onSurfSelectionChanged(self):
        selected = self._activeSurfWidget.selectedItems()
        if len(selected) == 0:
            return
        selectedKey = selected[0].text()
        for key, view in self._views.items():
            view.activeSurf = selectedKey

    def _updateFilepath(self):
        self._filepathWdgt.filepath = self.session.headModel.filepath

    def _updateRelativeToPath(self):
        self._filepathWdgt.showRelativeTo = os.path.dirname(self.session.filepath)

    def _onBrowsedNewFilepath(self, newFilepath: str):
        self.session.headModel.filepath = newFilepath