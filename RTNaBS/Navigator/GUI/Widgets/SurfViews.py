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

from RTNaBS.Navigator.GUI.Widgets.MRIViews import MRISliceView
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import composeTransform, applyTransform
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class SurfSliceView(MRISliceView):

    _activeSurf: tp.Union[str, tp.List[str]] = 'gmSurf'

    _slicePlotMethod: str = 'slicedSurface'

    _surfColor: tp.Union[str, tp.List[str]] = '#d9a5b2'
    _surfOpacity: tp.Union[float, tp.List[float]] = 0.5
    _surfPlotInitialized: bool = attrs.field(init=False, default=False)
    _surfPlotActor: tp.Optional[tp.Any] = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        if self._session is not None:
            self._session.headModel.sigDataChanged.connect(self._onHeadModelDataChanged)
        self.plotter.enable_depth_peeling(4)

    @property
    def activeSurf(self):
        return self._activeSurf

    @activeSurf.setter
    def activeSurf(self, newKey: str):
        if self._activeSurf == newKey:
            return
        self._clearSurfPlotActor()
        self._activeSurf = newKey
        self._onHeadModelDataChanged(whatChanged=self._activeSurf)

    def _clearPlot(self):
        super()._clearPlot()
        self._surfPlotInitialized = False

    def _clearSurfPlotActor(self):
        self._plotter.remove_actor(self._surfPlotActor)
        self._surfPlotActor = None
        self._surfPlotInitialized = False

    def _onHeadModelDataChanged(self, whatChanged: str):
        if whatChanged == self._activeSurf:
            self._clearSurfPlotActor()
            self._updateView()
        else:
            # ignore other changes
            pass

    def _updateView(self):
        super()._updateView()

        if isinstance(self._activeSurf, str):
            surfKeys = [self._activeSurf]
        else:
            surfKeys = self._activeSurf

        if isinstance(self._surfColor, str):
            surfColors = [self._surfColor]
        else:
            surfColors = self._surfColor

        if not isinstance(self._surfOpacity, list):
            surfOpacities = [self._surfOpacity]
        else:
            surfOpacities = self._surfOpacity

        if not self._surfPlotInitialized \
                and self.session is not None:
            actors = []
            for iSurf, surfKey in enumerate(surfKeys):
                if getattr(self.session.headModel, self._activeSurf) is not None:
                    actor = self._plotter.add_mesh(mesh=getattr(self.session.headModel, surfKey),
                                                                 color=surfColors[iSurf % len(surfColors)],
                                                                 opacity=surfOpacities[iSurf % len(surfOpacities)],
                                                                 name=self.label + '_' + surfKey + '_surf',
                                                                 )
                    actors.append(actor)
                    self._surfPlotInitialized = True

            if len(actors) > 0:
                self._surfPlotActor = actors

        self._plotter.camera.clipping_range = (90, 110)

        self._plotter.render()


@attrs.define()
class Surf3DView(SurfSliceView):
    _surfOpacity: float = 1.

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

        if not self._surfPlotInitialized and self.session is not None:

            if isinstance(self._activeSurf, str):
                surfKeys = [self._activeSurf]
            else:
                surfKeys = self._activeSurf

            if isinstance(self._surfColor, str):
                surfColors = [self._surfColor]
            else:
                surfColors = self._surfColor

            if not isinstance(self._surfOpacity, list):
                surfOpacities = [self._surfOpacity]
            else:
                surfOpacities = self._surfOpacity

            actors = []
            for iSurf, surfKey in enumerate(surfKeys):
                if getattr(self.session.headModel, surfKey) is not None:
                    if not self._surfPlotInitialized:
                        logger.debug('Initializing 3D plot')

                    actor = self._plotter.add_mesh(mesh=getattr(self.session.headModel, surfKey),
                                                   color=surfColors[iSurf % len(surfColors)],
                                                   opacity=surfOpacities[iSurf % len(surfOpacities)],
                                                   name=self.label + '_' + surfKey + '_surf',
                                                   )
                    actors.append(actor)

                    self._surfPlotInitialized = True

        logger.debug('Setting crosshairs for {} plot'.format(self.label))
        lineLength = 300  # TODO: scale by image size
        crosshairAxes = 'xyz'
        centerGapLength = 0
        for axis in crosshairAxes:
            mask = np.zeros((1, 3))
            mask[0, 'xyz'.index(axis)] = 1
            for iDir, dir in enumerate((-1, 1)):
                pts = dir*np.asarray([centerGapLength/2, lineLength])[:, np.newaxis] * mask
                if isinstance(self._normal, str):
                    pts += self._sliceOrigin
                else:
                    viewToWorldTransf = composeTransform(self._normal, self._sliceOrigin)
                    pts = applyTransform(viewToWorldTransf, pts)
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

        self._plotter.render()
