import asyncio

import attrs
import logging
import numpy as np
import pyvista as pv
import pyvistaqt as pvqt
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.GUI.QueuedRedrawMixin import QueuedRedrawMixin
from NaviNIBS.util.numpy import array_equalish
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import composeTransform, applyTransform, applyDirectionTransform
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter, RemotePlotterProxy, setActorUserTransform, Actor

logger = logging.getLogger(__name__)


@attrs.define
class MRISliceView(QueuedRedrawMixin):
    _normal: tp.Union[str, np.ndarray] = 'x'  # if an ndarray, should actually be 3x3 transform matrix from view pos to world space, not just 3-elem normal direction
    _label: tp.Optional[str] = None  # if none, will be labelled according to normal; this assumes normal won't change
    _cameraOffsetDist: float = 100  # distance from slice to camera
    _session: tp.Optional[Session] = attrs.field(default=None, repr=False)
    _sliceOrigin: tp.Optional[np.ndarray] = None

    _slicePlotMethod: str = 'cameraClippedVolume'
    _doShowScalarBar: bool = False

    _plotter: DefaultBackgroundPlotter = attrs.field(init=False, default=None)
    _plotterInitialized: bool = attrs.field(init=False, default=False)
    _plotterPickerInitialized: bool = attrs.field(init=False, default=False)
    _lineActors: tp.Dict[str, pv.Line] = attrs.field(init=False, factory=dict)
    _volActor: Actor | None = attrs.field(init=False, factory=dict)

    _backgroundColor: str = '#000000'
    _opacity: float = 0.5

    finishedAsyncInit: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    sigSliceOriginChanged: Signal = attrs.field(init=False, factory=Signal)
    sigNormalChanged: Signal = attrs.field(init=False, factory=Signal)
    sigSliceTransformChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        QueuedRedrawMixin.__attrs_post_init__(self)

        self.sigSliceTransformChanged.connect(self.updateView)
        if self._session is not None:
            self._session.MRI.sigDataChanged.connect(self._onMRIDataChanged)

        if self._plotter is None:
            self._plotter = DefaultBackgroundPlotter()
        else:
            pass  # presumably was initialized by subclass

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._finish_init))

    async def _finish_init(self):
        if isinstance(self._plotter, RemotePlotterProxy):
            await self._plotter.isReadyEvent.wait()

        with self._plotter.allowNonblockingCalls():
            self._plotter.set_background(self._backgroundColor)
            _ = self.plotter.camera  # get camera to get past BasePlotter's reset_camera call

        self.finishedAsyncInit.set()

        self.updateView()

    @property
    def label(self):
        if self._label is None:
            assert isinstance(self._normal, str)
            return self._normal
        else:
            return self._label

    @property
    def wdgt(self):
        return self._plotter

    @property
    def plotter(self):
        return self._plotter

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSession: tp.Optional[Session]):
        if newSession is self._session:
            return

        if self._session is not None:
            self._session.MRI.sigDataChanged.disconnect(self._onMRIDataChanged)
            self._session.MRI.sigClimChanged.disconnect(self._onMRIClimChanged)

        self._session = newSession
        if self._session is not None:
            self._session.MRI.sigDataChanged.connect(self._onMRIDataChanged)
            self._session.MRI.sigClimChanged.connect(self._onMRIClimChanged)

        self.updateView()

    @property
    def sliceOrigin(self):
        return self._sliceOrigin

    @sliceOrigin.setter
    def sliceOrigin(self, newVal: tp.Optional[np.ndarray]):
        if array_equalish(newVal, self._sliceOrigin):
            # no change
            return
        logger.debug(f'Slice origin changed from {self._sliceOrigin} to {newVal}')
        self._sliceOrigin = newVal
        self.sigSliceOriginChanged.emit()
        self.sigSliceTransformChanged.emit()

    @property
    def sliceTransform(self):
        transf = np.eye(4)
        if isinstance(self._normal, str):
            # TODO: double check these
            if self._normal == 'x':
                transf[0:3, 0:3] = np.asarray([[0, 0, 1], [0, 1, 0], [-1, 0, 0]])
            elif self._normal == 'y':
                transf[0:3, 0:3] = np.asarray([[1, 0, 0], [0, 0, -1], [0, 1, 0]])
            elif self._normal == 'z':
                pass
            else:
                raise NotImplementedError()
        else:
            transf[0:3, 0:3] = self._normal

        transf[0:3, 3] = self._sliceOrigin

        return transf

    @sliceTransform.setter
    def sliceTransform(self, newTransf: np.ndarray):
        newOrigin = newTransf[0:3, 3]
        newRot = newTransf[0:3, 0:3]
        assert array_equalish(newTransf[3, :], np.asarray([0, 0, 0, 1]))

        originChanged = not array_equalish(newOrigin, self._sliceOrigin)
        rotChanged = not array_equalish(self._normal, newRot)

        if not originChanged and not rotChanged:
            return

        self._sliceOrigin = newOrigin
        self._normal = newRot

        self.sigSliceTransformChanged.emit()
        if originChanged:
            self.sigSliceOriginChanged.emit()
        if rotChanged:
            self.sigNormalChanged.emit()

    def _onSlicePointChanged(self):
        pos = self.plotter.picked_point
        logger.debug('Slice point changed: {} {}'.format(self.label, pos))
        if True:
            # ignore any out-of-plane offset that can be caused by slice rendering differences
            if isinstance(self._normal, str):
                pos['xyz'.index(self._normal)] = self._sliceOrigin['xyz'.index(self._normal)]
            else:
                normalDir = self._normal @ np.asarray([0, 0, 1])
                pos = pos - np.dot(pos - self._sliceOrigin, normalDir) * normalDir
        self.sliceOrigin = pos

    def _onSliceScrolled(self, change: int):
        logger.debug('Slice scrolled: {} {}'.format(self.label, change))
        offset = np.zeros((3,))
        if isinstance(self._normal, str):
            offset['xyz'.index(self._normal)] = change
        else:
            offset = self._normal @ np.asarray([0, 0, 1]) * change
        pos = self._sliceOrigin + offset
        self.sliceOrigin = pos

    def _onMouseEvent(self, _, event):
        # TODO: check if `obj` has any info about scroll speed to scroll with larger increment

        if event == 'MouseWheelForwardEvent':
            logger.debug('MouseWheelForwardEvent')
            self._onSliceScrolled(change=1)

        elif event == 'MouseWheelBackwardEvent':
            logger.debug('MouseWheelBackwardEvent')
            self._onSliceScrolled(change=-1)
            
    def _onMRIDataChanged(self):
        if self._plotterInitialized:
            self._clearPlot()
        self.updateView()

    def _onMRIClimChanged(self, dim: str):
        if dim == '2D':
            self._queueRedraw(which='updateClim')

    def _updateClim(self):
        if not self.finishedAsyncInit.is_set():
            # plotter not available yet
            return

        if not self._plotterInitialized:
            # initial plot not created yet
            return

        if self.session is not None and self.session.MRI.isSet:
            with self._plotter.allowNonblockingCalls():
                self._plotter.updateScalarBarRangeWithVol(
                    clim=self.session.MRI.clim2D,
                    volumeKey='MRI',
                    volume=self.session.MRI.dataAsUniformGrid
                )
                self._plotter.render()

    def _clearPlot(self):
        logger.debug('Clearing plot for {} slice'.format(self.label))
        with self._plotter.allowNonblockingCalls():
            self._plotter.clear()
        self.sliceOrigin = None
        self._plotterInitialized = False

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str]] = None, **kwargs):
        super()._redraw(which=which, **kwargs)

        if which is None:
            which = 'all'
            self._redraw(which=which, **kwargs)
            return

        if not isinstance(which, str):
            for subWhich in which:
                self._redraw(which=subWhich, **kwargs)
            return

        match which:
            case 'all':
                self._redraw(which=['updateView'])
                return

            case 'updateView':
                self._updateView()

            case 'updateClim':
                self._updateClim()

            case _:
                raise NotImplementedError

    def updateView(self):
        self._queueRedraw(which='updateView')

    def _updateView(self):
        if self.session is None or self.session.MRI.data is None:
            # no data available
            if self._plotterInitialized:
                self._clearPlot()
            return

        # data available, update display
        logger.debug(f'Updating plot for {self.label} slice (slicePlotMethod={self._slicePlotMethod})')

        if self._sliceOrigin is None:
            self.sliceOrigin = (self.session.MRI.data.affine @ np.append(np.asarray(self.session.MRI.data.shape)/2, 1))[:-1]
            return  # prev line will have triggered its own update

        if not self.finishedAsyncInit.is_set():
            # plotter not available yet
            return

        if not self._plotterPickerInitialized:
            logger.debug('Initializing plot for {} slice'.format(self.label))
            with self.plotter.allowNonblockingCalls():
                self.plotter.enable_parallel_projection()
                self.plotter.enable_point_picking(left_clicking=True,
                                                   show_message=False,
                                                   show_point=False,
                                                   pickable_window=True,
                                                   callback=lambda newPt: self._onSlicePointChanged())
                self.plotter.enable_image_style()
                for event in ('MouseWheelForwardEvent', 'MouseWheelBackwardEvent'):
                    self.plotter.addIrenStyleClassObserver(
                        event=event,
                        callback=lambda obj, event: self._onMouseEvent(obj,event))
            self._plotterPickerInitialized = True

        if self._slicePlotMethod == 'slicedSurface':
            # single-slice plot
            slice = self.session.MRI.dataAsUniformGrid.slice(
                normal=applyDirectionTransform(self.session.MRI.scannerToDataTransf, self._normal),
                origin=applyTransform(self.session.MRI.scannerToDataTransf, self._sliceOrigin))
            # this slicing is very slow for some reason
            with self._plotter.allowNonblockingCalls():
                self._plotter.add_mesh(slice,
                                       name='slice',
                                       cmap='gray',
                                       render=False,
                                       reset_camera=False,
                                       show_scalar_bar=self._doShowScalarBar)
                if isinstance(self._normal, str):
                    self.plotter.camera_position = 'xyz'.replace(self._normal, '')
                else:
                    raise NotImplementedError()  # TODO

        elif self._slicePlotMethod == 'cameraClippedVolume':
            # volume plotting with camera clipping
            if not self._plotterInitialized:
                logger.debug('Getting MRI data as uniform grid')
                vol = self.session.MRI.dataAsUniformGrid
                logger.debug('Initializing volume plot of data')
                self._volActor = self._plotter.add_volume(vol,
                                         scalars='MRI',
                                         name='MRI',
                                         mapper='gpu',
                                         clim=self.session.MRI.clim2D,
                                         scalar_bar_args=dict(
                                             title='',
                                             color='white',
                                             vertical=True,
                                             position_x=0.02,
                                             position_y=0.55,
                                             height=0.4,
                                             label_font_size=12
                                         ),
                                         opacity=[0, self._opacity, self._opacity],
                                         cmap='gray',
                                         show_scalar_bar=self._doShowScalarBar,
                                         render=False,
                                         reset_camera=False)
                with self._plotter.allowNonblockingCalls():
                    setActorUserTransform(self._volActor, self.session.MRI.dataToScannerTransf)

        logger.debug('Setting crosshairs for {} plot'.format(self.label))
        lineLength = 300  # TODO: scale by image size
        if isinstance(self._normal, str):
            crosshairAxes = 'xyz'.replace(self._normal, '')
        else:
            crosshairAxes = 'xy'  # will be transformed below
        centerGapLength = 10  # TODO: scale by image size

        offsetDir = np.zeros((3,))
        if isinstance(self._normal, str):
            offsetDir['xyz'.index(self._normal)] = 1
            if self._normal == 'y':
                offsetDir *= -1  # reverse direction for coronal slice to match L/R of other views
        else:
            offsetDir = self._normal @ np.asarray([0, 0, 1])  # TODO: double check

        for axis in crosshairAxes:
            mask = np.zeros((1, 3))
            mask[0, 'xyz'.index(axis)] = 1
            for iDir, dir in enumerate((-1, 0, 1)):
                if dir == 0:
                    pts = np.asarray([centerGapLength / 2, -centerGapLength / 2])[:, np.newaxis] * mask
                    width = 1
                else:
                    pts = dir * np.asarray([centerGapLength / 2, lineLength])[:, np.newaxis] * mask
                    width = 2

                if isinstance(self._normal, str):
                    pts += self._sliceOrigin
                else:
                    viewToWorldTransf = composeTransform(self._normal, self._sliceOrigin)
                    pts = applyTransform(viewToWorldTransf, pts)

                if True:
                    # add z offset to make sure crosshairs appear above other actors
                    pts -= offsetDir * (self._cameraOffsetDist * 0.02)

                lineKey = 'Crosshair_{}_{}_{}'.format(self.label, axis, iDir)
                if not self._plotterInitialized:
                    line = self._plotter.add_lines(pts, color='#11DD11', width=width, name=lineKey)
                    with self._plotter.allowNonblockingCalls():
                        line.SetUseBounds(False)  # don't include for determining camera zoom, etc.
                    self._lineActors[lineKey] = line
                else:
                    with self._plotter.allowNonblockingCalls():
                        logger.debug('Moving previous crosshairs')
                        line = self._lineActors[lineKey]
                        pts_pv = pv.lines_from_points(pts)
                        line.GetMapper().SetInputData(pts_pv)


        if True:
            with self._plotter.allowNonblockingCalls():
                self._plotter.camera.position = offsetDir * self._cameraOffsetDist + self._sliceOrigin
        else:
            # hack to prevent resetting clipping range due to pyvista implementation quirk
            tmp = self._plotter.camera._renderer
            self._plotter.camera._renderer = None
            self._plotter.camera.position = offsetDir * 100 + self._sliceOrigin
            self._plotter.camera._renderer = tmp

        with self.plotter.allowNonblockingCalls():
            self.plotter.camera.focal_point = self._sliceOrigin
            if isinstance(self._normal, str):
                upDir = np.roll(offsetDir, (2, 1, 2)['xyz'.index(self._normal)])
                if self._normal == 'y':
                    upDir *= -1
            else:
                upDir = self._normal @ np.asarray([0, 1, 0])
            self.plotter.camera.up = upDir
            if self._slicePlotMethod == 'cameraClippedVolume':
                self.plotter.camera.clipping_range = (self._cameraOffsetDist * 0.98, self._cameraOffsetDist * 1.02)

            self.plotter.camera.parallel_scale = self._cameraOffsetDist

            self._plotterInitialized = True

            self.plotter.render()


@attrs.define
class MRI3DView(MRISliceView):
    _clim: tp.Tuple[float, float] = (300, 1000)  # TODO: set to auto-initialize instead of hardcoding default

    def __attrs_post_init__(self):
        MRISliceView.__attrs_post_init__(self)

    @property
    def label(self):
        if self._label is None:
            return 'MRI3D'
        else:
            return self._label

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

        if not self.finishedAsyncInit.is_set():
            # plotter not ready
            return

        if not self._plotterInitialized:
            logger.debug('Initializing 3D plot')

            self._volActor = self._plotter.add_volume(self.session.MRI.dataAsUniformGrid.gaussian_smooth(),
                                                scalars='MRI',
                                                scalar_bar_args=dict(
                                                    title='',
                                                    color='white',
                                                    vertical=True,
                                                    position_x=0.02,
                                                    position_y=0.55,
                                                    height=0.4,
                                                    label_font_size=12
                                                ),
                                                name='vol',
                                                clim=self.session.MRI.clim3D,
                                                cmap='gray',
                                                show_scalar_bar=self._doShowScalarBar,
                                                mapper='gpu',
                                                opacity=[0, self._opacity, self._opacity],
                                                shade=False,
                                                render=False,
                                                reset_camera=False)

            with self._plotter.allowNonblockingCalls():
                setActorUserTransform(self._volActor, self.session.MRI.dataToScannerTransf)
                self.plotter.reset_camera()
                # self.plotter.camera.zoom('tight')

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

        with self.plotter.allowNonblockingCalls():
            self.plotter.render()

    def _onMRIClimChanged(self, dim: str):
        if dim == '3D':
            self._queueRedraw(which='updateClim')

    def _updateClim(self):
        if not self.finishedAsyncInit.is_set():
            # plotter not available yet
            return

        if not self._plotterInitialized:
            # initial plot not created yet
            return

        if self.session is not None and self.session.MRI.isSet:
            with self.plotter.allowNonblockingCalls():
                self._plotter.updateScalarBarRangeWithVol(
                    clim=self.session.MRI.clim3D,
                    volumeKey='MRI',
                    volume=self.session.MRI.dataAsUniformGrid.gaussian_smooth()
                )
                self._plotter.render()
