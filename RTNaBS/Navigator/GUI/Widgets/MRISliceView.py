import attrs
import logging
import numpy as np
import pyvista as pv
import pyvistaqt as pvqt
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.util.Signaler import Signal


logger = logging.getLogger(__name__)


@attrs.define()
class MRISliceView:
    _normal: tp.Union[str, np.ndarray] = 'x'
    _label: tp.Optional[str] = None  # if none, will be labelled according to normal; this assumes normal won't change
    _clim: tp.Tuple[float, float] = (300, 2000)  # TODO: set to auto-initialize instead of hardcoding default
    _session: tp.Optional[Session] = None
    _sliceOrigin: tp.Optional[np.ndarray] = None

    _slicePlotMethod: str = 'cameraClippedVolume'

    _plotter: pvqt.QtInteractor = attrs.field(init=False)
    _plotterInitialized: bool = attrs.field(init=False, default=False)
    _lineActors: tp.Dict[str, pv.Line] = attrs.field(init=False, factory=dict)

    _backgroundColor: str = '#000000'

    sigSliceOriginChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        self.sigSliceOriginChanged.connect(self._updateView)
        if self._session is not None:
            self._session.MRI.sigDataChanged.connect(self._onMRIDataChanged)

        self._plotter = pvqt.BackgroundPlotter(
            show=False,
            app=QtWidgets.QApplication.instance()
        )
        self._plotter.set_background(self._backgroundColor)

        self._updateView()

    @property
    def label(self):
        if self._label is None:
            assert isinstance(self._normal, str)
            return self._normal
        else:
            return self._label

    @property
    def wdgt(self):
        return self._plotter.interactor

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

        self._session = newSession
        if self._session is not None:
            self._session.MRI.sigDataChanged.connect(self._onMRIDataChanged)

        self._updateView()

    @property
    def sliceOrigin(self):
        return self._sliceOrigin

    @sliceOrigin.setter
    def sliceOrigin(self, newVal: tp.Optional[np.ndarray]):
        if np.array_equal(newVal, self._sliceOrigin):
            # no change
            return
        self._sliceOrigin = newVal
        self.sigSliceOriginChanged.emit()

    def _onSlicePointChanged(self):
        pos = self._plotter.picked_point
        logger.debug('Slice point changed: {} {}'.format(self.label, pos))
        if True:
            # ignore any out-of-plane offset that can be caused by slice rendering differences
            if isinstance(self._normal, str):
                pos['xyz'.index(self._normal)] = self._sliceOrigin['xyz'.index(self._normal)]
            else:
                raise NotImplementedError()  # TODO
        self.sliceOrigin = pos

    def _onSliceScrolled(self, change: int):
        logger.debug('Slice scrolled: {} {}'.format(self.label, change))
        offset = np.zeros((3,))
        if isinstance(self._normal, str):
            offset['xyz'.index(self._normal)] = change
        else:
            raise NotImplementedError()  # TODO
        pos = self._sliceOrigin + offset
        self.sliceOrigin = pos

    def _onMouseEvent(self, obj, event):
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
        self._updateView()

    def _clearPlot(self):
        logger.debug('Clearing plot for {} slice'.format(self.label))
        self._plotter.clear()
        self.sliceOrigin = None
        self._plotterInitialized = False

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
            self.sliceOrigin = (self.session.MRI.data.affine @ np.append(np.asarray(self.session.MRI.data.shape)/2, 1))[:-1]
            return  # prev line will have triggered its own update

        if not self._plotterInitialized:
            logger.debug('Initializing plot for {} slice'.format(self.label))
            self._plotter.enable_parallel_projection()
            self._plotter.enable_point_picking(left_clicking=True,
                                               show_message=False,
                                               show_point=False,
                                               callback=lambda newPt: self._onSlicePointChanged())
            self._plotter.enable_image_style()
            for event in ('MouseWheelForwardEvent', 'MouseWheelBackwardEvent'):
                self._plotter.iren._style_class.AddObserver(event, lambda obj, event: self._onMouseEvent(obj,event))

        if self._slicePlotMethod == 'slicedSurface':
            # single-slice plot
            slice = self.session.MRI.dataAsUniformGrid.slice(normal=self._normal, origin=self._sliceOrigin)  # this is very slow for some reason
            self._plotter.add_mesh(slice,
                                         name='slice',
                                         cmap='gray')
            if isinstance(self._normal, str):
                self._plotter.camera_position = 'xyz'.replace(self._normal, '')
            else:
                raise NotImplementedError()  # TODO

        elif self._slicePlotMethod == 'cameraClippedVolume':
            # volume plotting with camera clipping
            if not self._plotterInitialized:
                logger.debug('Getting MRI data as uniform grid')
                vol = self.session.MRI.dataAsUniformGrid
                logger.debug('Initializing volume plot of data')
                self._plotter.add_volume(vol,
                                         scalars='MRI',
                                         name='MRI',
                                         mapper='gpu',
                                         clim=self._clim,
                                         cmap='gray')

        logger.debug('Setting crosshairs for {} plot'.format(self.label))
        lineLength = 300  # TODO: scale by image size
        if isinstance(self._normal, str):
            crosshairAxes = 'xyz'.replace(self._normal, '')
        else:
            raise NotImplementedError()  # TODO
        centerGapLength = 10  # TODO: scale by image size

        for axis in crosshairAxes:
            mask = np.zeros((1, 3))
            mask[0, 'xyz'.index(axis)] = 1
            for iDir, dir in enumerate((-1, 1)):
                pts = dir * np.asarray([centerGapLength / 2, lineLength])[:, np.newaxis] * mask + self._sliceOrigin
                lineKey = 'Crosshair_{}_{}_{}'.format(self.label, axis, iDir)
                if not self._plotterInitialized:
                    line = self._plotter.add_lines(pts, color='#11DD11', width=2, name=lineKey)
                    self._lineActors[lineKey] = line
                else:
                    logger.debug('Moving previous crosshairs')
                    line = self._lineActors[lineKey]
                    pts_pv = pv.lines_from_points(pts)
                    line.GetMapper().SetInputData(pts_pv)

        offsetDir = np.zeros((3,))
        if isinstance(self._normal, str):
            offsetDir['xyz'.index(self._normal)] = 1
        else:
            raise NotImplementedError()  #TODO
        if True:
            self._plotter.camera.position = offsetDir * 100 + self._sliceOrigin
        else:
            # hack to prevent resetting clipping range due to pyvista implementation quirk
            tmp = self._plotter.camera._renderer
            self._plotter.camera._renderer = None
            self._plotter.camera.position = offsetDir * 100 + self._sliceOrigin
            self._plotter.camera._renderer = tmp

        self._plotter.camera.focal_point = self._sliceOrigin
        if isinstance(self._normal, str):
            self._plotter.camera.up = np.roll(offsetDir, (2, 1, 2)['xyz'.index(self._normal)])
        else:
            raise NotImplementedError()  # TODO
        if self._slicePlotMethod == 'cameraClippedVolume':
            self._plotter.camera.clipping_range = (99, 102)
        self._plotter.camera.parallel_scale = 90

        self._plotterInitialized = True