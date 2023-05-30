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
from RTNaBS.util.pyvista import Actor
from RTNaBS.util.pyvista.plotting import PrimaryLayeredPlotter, SecondaryLayeredPlotter, BackgroundPlotter
from RTNaBS.util.pyvista.PlotInteraction import set_mouse_event_for_picking
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import composeTransform, applyTransform
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class SurfSliceView(MRISliceView):

    _activeSurf: tp.Union[str, tp.List[str]] = 'gmSurf'

    _slicePlotMethod: str = 'cameraClippedVolume'

    _doLayeredPlotters: bool = True
    _primaryPlotter: PrimaryLayeredPlotter = attrs.field(init=False)
    _plotter: SecondaryLayeredPlotter | None = attrs.field(init=False)  # replace parent classes' plotter with layered plotter
    _surfPlotter: SecondaryLayeredPlotter = attrs.field(init=False)

    _opacity: float = 0.5

    _surfColor: tp.Union[str, tp.List[str]] = '#d9a5b2'
    _surfOpacity: tp.Union[float, tp.List[float]] = 0.5
    _surfPlotInitialized: bool = attrs.field(init=False, default=False)
    _surfPlotActors: list[Actor] = attrs.field(init=False, factory=list)

    def __attrs_post_init__(self):

        if self._doLayeredPlotters:
            self._primaryPlotter = PrimaryLayeredPlotter(
                show=False,
                app=QtWidgets.QApplication.instance(),
                doAutoAdjustCameraClippingRange=False,
            )
            self._plotter = self._primaryPlotter.addLayeredPlotter(key='MRISlice', layer=0)
            self._surfPlotter = self._primaryPlotter.addLayeredPlotter(key='SurfSlice', layer=1)
        else:
            self._primaryPlotter = BackgroundPlotter(
                show=False,
                app=QtWidgets.QApplication.instance(),
            )
            self._plotter = self._primaryPlotter
            self._surfPlotter = self._primaryPlotter
        self._primaryPlotter.set_background(self._backgroundColor)

        super().__attrs_post_init__()
        if self._session is not None:
            self._session.headModel.sigDataChanged.connect(self._onHeadModelDataChanged)
        self._surfPlotter.enable_depth_peeling(8)

    @MRISliceView.wdgt.getter
    def wdgt(self) -> QtWidgets.QWidget:
        return self._primaryPlotter.interactor

    @MRISliceView.plotter.getter
    def plotter(self) -> PrimaryLayeredPlotter:
        return self._primaryPlotter

    @property
    def activeSurf(self):
        return self._activeSurf

    @activeSurf.setter
    def activeSurf(self, newKey: str):
        if self._activeSurf == newKey:
            return
        self._clearSurfPlotActors()
        self._activeSurf = newKey
        self._onHeadModelDataChanged(whatChanged=self._activeSurf)

    def _clearPlot(self):
        self._clearSurfPlotActors()
        super()._clearPlot()

    def _clearSurfPlotActors(self):
        for actor in self._surfPlotActors:
            self._surfPlotter.remove_actor(actor)
        self._surfPlotActors.clear()
        self._surfPlotInitialized = False

    def _onHeadModelDataChanged(self, whatChanged: str):
        if whatChanged == self._activeSurf:
            self._clearSurfPlotActors()
            self.updateView()
        else:
            # ignore other changes
            pass

    def updateView(self):

        super().updateView()

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
                    actor = self._surfPlotter.add_mesh(mesh=getattr(self.session.headModel, surfKey),
                                                                 color=surfColors[iSurf % len(surfColors)],
                                                                 opacity=surfOpacities[iSurf % len(surfOpacities)],
                                                                 name=self.label + '_' + surfKey + '_surf',
                                                                 render=False,
                                                                 reset_camera=False
                                                                 )
                    actors.append(actor)
                    self._surfPlotInitialized = True

            self._surfPlotActors.extend(actors)

        if self._slicePlotMethod != 'cameraClippedVolume':
            # clip camera for surface
            self.plotter.camera.clipping_range = (90, 110)

        if self._slicePlotMethod == 'cameraClippedVolume':
            self._primaryPlotter.set_camera_clipping_range((self._cameraOffsetDist * 0.98, self._cameraOffsetDist * 1.02))

        self.plotter.render()


@attrs.define()
class Surf3DView(SurfSliceView):
    _surfOpacity: float = 1.

    _doLayeredPlotters: bool = False

    _pickableSurfs: list[str] | None = None

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        if self._doLayeredPlotters:
            self._primaryPlotter.doAutoAdjustCameraClippingRange = True

    def updateView(self):

        if self.session is None or self.session.MRI.data is None:
            # no data available
            if self._plotterInitialized:
                logger.debug('Clearing plot for {} slice'.format(self.label))
                self._plotter.clear()
                self._surfPlotter.clear()

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

            self.plotter.enable_parallel_projection()
            self.plotter.enable_point_picking(show_message=False,
                                              show_point=False,
                                              pickable_window=True,
                                              callback=lambda newPt: self._onSlicePointChanged())
            set_mouse_event_for_picking(self.plotter, 'RightButtonPressEvent')

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

                    actor = self._surfPlotter.add_mesh(mesh=getattr(self.session.headModel, surfKey),
                                                   color=surfColors[iSurf % len(surfColors)],
                                                   opacity=surfOpacities[iSurf % len(surfOpacities)],
                                                   name=self.label + '_' + surfKey + '_surf',
                                                   render=False,
                                                   reset_camera=False
                                                   )
                    actors.append(actor)

                    self._surfPlotInitialized = True

                    self.plotter.reset_camera()

            self._surfPlotActors.extend(actors)

        if True:
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
                        line = self._surfPlotter.add_lines(pts, color='#11DD11', width=2, name=lineKey)
                        self._lineActors[lineKey] = line
                    else:
                        logger.debug('Moving previous crosshairs')
                        line = self._lineActors[lineKey]
                        pts_pv = pv.lines_from_points(pts)
                        line.GetMapper().SetInputData(pts_pv)

        self._plotterInitialized = True

        self._primaryPlotter.render()

    def _onSlicePointChanged(self):
        pos = self.plotter.picked_point

        if True:
            # ray trace to find point closest to camera
            if self._pickableSurfs is None:
                if isinstance(self._activeSurf, str):
                    surfKeys = [self._activeSurf]
                else:
                    surfKeys = self._activeSurf
            else:
                surfKeys = self._pickableSurfs

            newPos = None

            for iSurf, surfKey in enumerate(surfKeys):
                mesh = getattr(self.session.headModel, surfKey)

                if False:
                    endPoint = pos
                else:
                    # selected pos may be "before" mesh, such that tracing to this endpoint would be insufficient to find mesh intersection
                    # so project even further
                    cameraToCrosshairsDist = np.linalg.norm(self.plotter.camera.position - self._sliceOrigin)
                    dir = np.asarray(pos) - np.asarray(self.plotter.camera.position)
                    dir /= np.linalg.norm(dir)
                    endPoint = pos + dir * cameraToCrosshairsDist * 2

                intersectionPoints, intersectionCells = mesh.ray_trace(
                    origin=self.plotter.camera.position,
                    end_point=endPoint,
                    first_point=True
                )
                if len(intersectionPoints) == 0:
                    continue  # no intersection
                if newPos is not None:
                    # already found an intersection, check if this one is closer
                    if np.linalg.norm(intersectionPoints - self.plotter.camera.position) < np.linalg.norm(newPos - self.plotter.camera.position):
                        newPos = intersectionPoints
                else:
                    newPos = intersectionPoints

            if newPos is None:
                # no intersections found, don't change slice origin
                return

            pos = newPos

        logger.debug('Slice point changed: {} {}'.format(self.label, pos))
        self.sliceOrigin = pos