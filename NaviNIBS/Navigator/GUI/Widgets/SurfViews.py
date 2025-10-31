from __future__ import annotations

import asyncio

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

from NaviNIBS.Navigator.GUI.Widgets.MRIViews import MRISliceView
from NaviNIBS.util.pyvista import Actor
from NaviNIBS.util.pyvista import (
    DefaultBackgroundPlotter,
    DefaultPrimaryLayeredPlotter,
    DefaultSecondaryLayeredPlotter,
    RemotePlotterProxy
)
from NaviNIBS.util.pyvista.PlotInteraction import set_mouse_event_for_picking
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import composeTransform, applyTransform
from NaviNIBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from NaviNIBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


defaultSurfColor = '#d9a5b2'


@attrs.define()
class SurfSliceView(MRISliceView):

    _activeSurf: tp.Union[str, tp.List[str]] = 'gmSurf'

    _slicePlotMethod: str = 'cameraClippedVolume'

    _doLayeredPlotters: bool = True
    _primaryPlotter: DefaultPrimaryLayeredPlotter = attrs.field(init=False, default=None)
    _plotter: DefaultSecondaryLayeredPlotter | None = attrs.field(init=False, default=None)  # replace parent classes' plotter with layered plotter
    _surfPlotter: DefaultSecondaryLayeredPlotter = attrs.field(init=False)

    _opacity: float = 0.5

    _surfColor: tp.Union[str, tp.List[str]] = defaultSurfColor
    _surfOpacity: tp.Union[float, tp.List[float]] = 0.5
    _surfPlotInitialized: bool = attrs.field(init=False, default=False)
    _surfPlotActors: dict[str, Actor] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):

        if self._primaryPlotter is None:
            if self._doLayeredPlotters:
                self._primaryPlotter = DefaultPrimaryLayeredPlotter(
                    doAutoAdjustCameraClippingRange=False,
                )
                self._plotter = self._primaryPlotter  # will be replaced with secondary plot layer during async init
            else:
                self._primaryPlotter = DefaultBackgroundPlotter()
                self._plotter = self._primaryPlotter


        else:
            pass  # presumably initialized by subclass

        super().__attrs_post_init__()

        if self._session is not None:
            self._session.headModel.sigDataChanged.connect(self._onHeadModelDataChanged)

    async def _finish_init(self):
        if isinstance(self._primaryPlotter, RemotePlotterProxy):
            await self._primaryPlotter.isReadyEvent.wait()

        if self._doLayeredPlotters:
            self._plotter = self._primaryPlotter.addLayeredPlotter(key='MRISlice', layer=0)
            self._surfPlotter = self._primaryPlotter.addLayeredPlotter(key='SurfSlice', layer=1)
        else:
            self._surfPlotter = self._primaryPlotter

        await super()._finish_init()

        with self._primaryPlotter.allowNonblockingCalls():
            self._primaryPlotter.set_background(self._backgroundColor)

        with self._surfPlotter.allowNonblockingCalls():
            self._surfPlotter.enable_depth_peeling(8)

    @MRISliceView.wdgt.getter
    def wdgt(self) -> QtWidgets.QWidget:
        return self._primaryPlotter

    @MRISliceView.plotter.getter
    def plotter(self) -> DefaultPrimaryLayeredPlotter:
        return self._primaryPlotter

    @MRISliceView.session.setter
    def session(self, newSession: Session | None):
        if self._session is not None:
            self._session.headModel.sigDataChanged.disconnect(self._onHeadModelDataChanged)

        MRISliceView.session.fset(self, newSession)

        if self._session is not None:
            self._session.headModel.sigDataChanged.connect(self._onHeadModelDataChanged)

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

    @property
    def surfColor(self):
        return self._surfColor

    def _clearPlot(self):
        self._clearSurfPlotActors()
        super()._clearPlot()

    def _clearSurfPlotActors(self):
        if not self._surfPlotInitialized:
            return
        with self._surfPlotter.allowNonblockingCalls():
            for actor in self._surfPlotActors.values():
                self._surfPlotter.remove_actor(actor)
        self._surfPlotActors.clear()
        self._surfPlotInitialized = False

    def _onHeadModelDataChanged(self, whatChanged: str | None):
        if whatChanged is None or whatChanged in (self._activeSurf, 'all'):
            self._clearSurfPlotActors()
            self.updateView()
        else:
            # ignore other changes
            pass

    def _updateView(self):

        super()._updateView()

        if not self.finishedAsyncInit.is_set():
            # plotter not available yet
            return

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
            actors = dict()
            for iSurf, surfKey in enumerate(surfKeys):
                if getattr(self.session.headModel, surfKey) is not None:
                    actorName = self.label + '_' + surfKey + '_surf'
                    actor = self._surfPlotter.addMesh(mesh=getattr(self.session.headModel, surfKey),
                                                      defaultMeshColor=surfColors[iSurf % len(surfColors)],
                                                      opacity=surfOpacities[iSurf % len(surfOpacities)],
                                                      name=actorName,
                                                      render=False,
                                                      reset_camera=False)

                    actors[actorName] = actor
                    self._surfPlotInitialized = True

            self._surfPlotActors |= actors

        if self._slicePlotMethod == 'cameraClippedVolume':
            with self._primaryPlotter.allowNonblockingCalls():
                self._primaryPlotter.set_camera_clipping_range((self._cameraOffsetDist * 0.98, self._cameraOffsetDist * 1.02))

        with self.plotter.allowNonblockingCalls():
            self.plotter.render()


@attrs.define()
class Surf3DView(SurfSliceView):
    _surfOpacity: float = 1.

    _doLayeredPlotters: bool = False

    _pickableSurfs: list[str] | None = None

    def __attrs_post_init__(self):
        if self._doLayeredPlotters:
            self._primaryPlotter = DefaultPrimaryLayeredPlotter(
                doAutoAdjustCameraClippingRange=True,
            )
        else:
            pass  # let superclass initialize plotter

        super().__attrs_post_init__()

    @property
    def pickableSurfs(self):
        return self._pickableSurfs if self._pickableSurfs is not None else [self._activeSurf]

    @pickableSurfs.setter
    def pickableSurfs(self, newPickableSurfs: list[str] | None):
        if self._pickableSurfs == newPickableSurfs:
            return
        self._pickableSurfs = newPickableSurfs

        if self._surfPlotInitialized:
            pickableActors = []
            for surfKey in self.pickableSurfs:
                actorName = self.label + '_' + surfKey + '_surf'
                if actorName in self._surfPlotActors:
                    pickableActors.append(self._surfPlotActors[actorName])
            self.plotter.pickable_actors = pickableActors

    def _updateView(self):

        if not self.finishedAsyncInit.is_set():
            # plotter not available yet
            return

        if self.session is None or self.session.MRI.data is None:
            # no data available
            if self._plotterInitialized:
                logger.debug('Clearing plot for {} slice'.format(self.label))
                with self._plotter.allowNonblockingCalls():
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

            if not self._plotterPickerInitialized \
                    and self._doEnablePicking and len(self.pickableSurfs) > 0:
                self.plotter.enable_parallel_projection()
                self.plotter.enable_point_picking(show_message=False,
                                                  show_point=False,
                                                  pickable_window=False,
                                                  callback=lambda newPt: self._onSlicePointChanged())
                if isinstance(self.plotter, RemotePlotterProxy):
                    pass  # TODO: enable right button press response for remote plotter
                else:
                    set_mouse_event_for_picking(self.plotter, 'RightButtonPressEvent')
                self._plotterPickerInitialized = True

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

            pickableActors = []
            actors = dict()
            for iSurf, surfKey in enumerate(surfKeys):
                if getattr(self.session.headModel, surfKey) is not None:
                    if not self._surfPlotInitialized:
                        logger.debug('Initializing 3D plot')

                    actorName = self.label + '_' + surfKey + '_surf'
                    actor = self._surfPlotter.addMesh(
                        mesh=getattr(self.session.headModel, surfKey),
                        defaultMeshColor=surfColors[iSurf % len(surfColors)],
                        opacity=surfOpacities[iSurf % len(surfOpacities)],
                        name=actorName,
                        render=False,
                        reset_camera=False)
                    actors[actorName] = actor

                    self._surfPlotInitialized = True

                    self.plotter.reset_camera()
                    if surfKey in self.pickableSurfs:
                        pickableActors.append(actor)

            self._surfPlotActors |= actors
            self._plotter.pickable_actors = pickableActors

        if self._doShowCrosshairs:
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
                        with self._plotter.allowNonblockingCalls():
                            line.SetUseBounds(False)  # don't include for determining camera zoom, etc.
                        self._lineActors[lineKey] = line
                    else:
                        with self._plotter.allowNonblockingCalls():
                            logger.debug('Moving previous crosshairs')
                            line = self._lineActors[lineKey]
                            pts_pv = pv.lines_from_points(pts)
                            line.GetMapper().SetInputData(pts_pv)

        self._plotterInitialized = True

        with self._primaryPlotter.allowNonblockingCalls():
            self._primaryPlotter.render()

    def _onSlicePointChanged(self):
        pos = self.plotter.picked_point

        if True:
            # ray trace to find point closest to camera
            surfKeys = self.pickableSurfs

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