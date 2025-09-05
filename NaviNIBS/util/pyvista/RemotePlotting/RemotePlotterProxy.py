from __future__ import annotations

import asyncio
from contextlib import contextmanager
import logging
import logging.handlers
import multiprocessing as mp
import time
import typing as tp

import attrs
import numpy as np
import pyvista as pv
import vtkmodules.vtkCommonTransforms
import zmq
from qtpy import QtWidgets, QtGui, QtCore
from zmq import asyncio as azmq

from NaviNIBS.util import exceptionToStr, makeStrUnique
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.pyvista.RemotePlotting import ActorRef
from NaviNIBS.util.pyvista.RemotePlotting.RemotePlotter import RemotePlotterApp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# logger.setLevel(logging.DEBUG)



@attrs.define
class RemoteActorProxy:
    _actorID: str
    _plotter: RemotePlotterProxyBase

    _mapper: RemoteMapper | None = attrs.field(init=False, default=None)
    _visibility: bool | None = attrs.field(init=False, default=None)

    @property
    def actorID(self):
        return self._actorID

    @property
    def plotter(self):
        return self._plotter

    @property
    def mapper(self):
        return self.GetMapper()

    def SetUserTransform(self, transform: vtkmodules.vtkCommonTransforms.vtkTransform):

        # convert to ndarray since vtkTransform is not pickleable
        transform_ndarray = pv.array_from_vtkmatrix(transform.GetMatrix())

        return self._plotter.setActorUserTransform(self, transform_ndarray)

    def GetVisibility(self) -> bool:
        if self._visibility is None:
            with self._plotter.disallowNonblockingCalls():
                self._visibility = self._plotter._remoteActorCall(self, 'GetVisibility')
        return self._visibility  # by using cached version, we assume we will be the only one to ever change visibility

    def GetMapper(self):
        if self._mapper is None:
            self._mapper = RemoteMapper(self)
        return self._mapper

    def SetVisibility(self, visibility: bool):
        self._visibility = visibility
        return self._plotter._remoteActorCall(self, 'SetVisibility', visibility)

    def VisibilityOn(self):
        self._visibility = True
        return self._plotter._remoteActorCall(self, 'VisibilityOn')

    def VisibilityOff(self):
        self._visibility = False
        return self._plotter._remoteActorCall(self, 'VisibilityOff')

    def SetUseBounds(self, useBounds: bool):
        return self._plotter._remoteActorCall(self, 'SetUseBounds', useBounds)


@attrs.define
class RemoteCameraProxy:
    _plotter: RemotePlotterProxyBase

    @property
    def position(self):
        return self._plotter._remoteCameraGet('position')

    @position.setter
    def position(self, value):
        self._plotter._remoteCameraSet('position', value)

    @property
    def focal_point(self):
        return self._plotter._remoteCameraGet('focal_point')

    @focal_point.setter
    def focal_point(self, value):
        self._plotter._remoteCameraSet('focal_point', value)

    @property
    def up(self):
        return self._plotter._remoteCameraGet('up')

    @up.setter
    def up(self, value):
        self._plotter._remoteCameraSet('up', value)

    @property
    def clipping_range(self):
        return self._plotter._remoteCameraGet('clipping_range')

    @clipping_range.setter
    def clipping_range(self, value):
        self._plotter._remoteCameraSet('clipping_range', value)

    @property
    def parallel_scale(self):
        return self._plotter._remoteCameraGet('parallel_scale')

    @parallel_scale.setter
    def parallel_scale(self, value):
        self._plotter._remoteCameraSet('parallel_scale', value)

    @property
    def view_angle(self):
        return self._plotter._remoteCameraGet('view_angle')

    @view_angle.setter
    def view_angle(self, value):
        self._plotter._remoteCameraSet('view_angle', value)

    def enable_parallel_projection(self):
        self._plotter._remoteCameraCall('enable_parallel_projection')

    def zoom(self, value):
        self._plotter._remoteCameraCall('zoom', value)


class RemotePlotterProxyBase:
    _camera: RemoteCameraProxy | None = None
    _mapper: RemoteMapper | None = None

    _doQueueCallsAndReturnImmediately: bool = False
    _queuedCalls: list[tuple[str, str, tuple, dict | None, tuple]]

    @attrs.define
    class CallbackRegistry:
        _callbacks: dict[str, tp.Callable] = attrs.field(factory=dict)

        def register(self, func: tp.Callable, key: str | None = None) -> str:
            if key is None:
                key = makeStrUnique(baseStr='Callback',
                                    existingStrs=self._callbacks.keys())
            else:
                assert key not in self._callbacks
            self._callbacks[key] = func
            return key

        def callback(self, name: str, *args, **kwargs):
            return self._callbacks[name](*args, **kwargs)

    _callbackRegistry: CallbackRegistry

    def __init__(self):
        logger.debug(f'Initializing {self.__class__.__name__}')
        self._callbackRegistry = self.CallbackRegistry()

        self._isReady = asyncio.Event()

        self._queuedCalls = []

    @property
    def picked_point(self):
        return self._remotePlotterGet('picked_point')

    @property
    def camera(self):
        if self._camera is None:
            self._remoteQueryProperty('camera')
            self._camera = RemoteCameraProxy(plotter=self)
        return self._camera

    @property
    def mapper(self):
        if self._mapper is None:
            self._mapper = RemoteMapper(parentPlotter=self)
        return self._mapper

    @contextmanager
    def allowNonblockingCalls(self):
        """
        Within this context, most commands will be pushed out to remote for execution, and we won't wait for the return value (i.e. we will immediately return None).

        Some commands may still block.

        Multiple calls are guaranteed to be executed in order by the remote plotter. If multiple non-blocking calls are made right before a blocking call, the blocking call will block until all preceding non-blocking calls are complete before starting.
        """
        prevVal = self._doQueueCallsAndReturnImmediately
        self._doQueueCallsAndReturnImmediately = True
        yield
        self._doQueueCallsAndReturnImmediately = prevVal

    @contextmanager
    def disallowNonblockingCalls(self):
        prevVal = self._doQueueCallsAndReturnImmediately
        self._doQueueCallsAndReturnImmediately = False
        yield
        self._doQueueCallsAndReturnImmediately = prevVal

    async def _sendReqAndRecv_async(self, msg):
        raise NotImplementedError  # to be implemented by subclass

    def _sendReqAndRecv(self, msg):
        raise NotImplementedError  # to be implemented by subclass

    def _sendReqNonblocking(self, msg) -> None:
        raise NotImplementedError  # to be implemented by subclass

    def _prepareForCall(self, cmdKey: str, fnStr: str, args: tuple = (), kwargs: dict | None = None, cmdArgs: tuple = ()):
        if kwargs is None:
            kwargs = dict()

        logger.debug(f'{cmdKey} {fnStr}')
        assert self._isReady.is_set()

        args = list(args)

        for iArg in range(len(args)):
            if isinstance(args[iArg], RemoteActorProxy):
                # convert from RemoteActor to ActorRef
                args[iArg] = ActorRef(actorID=args[iArg].actorID)

        if 'callback' in kwargs:
            # convert from callback function to key matching new entry
            # in callback registry
            callbackFn = kwargs['callback']
            callbackKey = self._callbackRegistry.register(callbackFn)
            kwargs['callback'] = callbackKey

        if 'mesh' in kwargs and isinstance(kwargs['mesh'], pv.PolyData) and hasattr(kwargs['mesh'], '_obbTree'):
            # clear un-pickleable obbTree field
            # note: this may cause unexpected issues...
            kwargs['mesh'] = kwargs['mesh'].copy()
            kwargs['mesh']._obbTree = None

        logger.debug(f'prepared for call {cmdKey} {fnStr}')

        return cmdKey, *cmdArgs, fnStr, args, kwargs

    async def _remoteCall_async(self, cmdKey: str, fnStr: str, args: tuple = (), kwargs: dict | None = None, cmdArgs: tuple = ()):
        req = self._prepareForCall(cmdKey, fnStr, args, kwargs, cmdArgs)

        resp = await self._sendReqAndRecv_async(req)

        return self._handleResp(fnStr, resp)

    def _remoteCall(self, cmdKey: str, fnStr: str, args: tuple = (), kwargs: dict | None = None, cmdArgs: tuple = ()):
        req = self._prepareForCall(cmdKey, fnStr, args, kwargs, cmdArgs)

        if self._doQueueCallsAndReturnImmediately:
            self._sendReqNonblocking(req)
            return None
        else:
            resp = self._sendReqAndRecv(req)
            logger.debug(f'Waiting for response to {fnStr}')
            return self._handleResp(fnStr, resp)

    def _handleResp(self, label, resp):
        logger.debug(f'{label} response: {resp}')
        if isinstance(resp, Exception):
            logger.error(f'{exceptionToStr(resp)}')
            raise resp
        elif isinstance(resp, ActorRef):
            # convert result to a RemoteActor
            return RemoteActorProxy(actorID=resp.actorID, plotter=self)
        else:
            return resp

    async def _handleMsg(self, msg):
        match msg[0]:
            case 'callback':
                callbackKey = msg[1]
                args = msg[2]
                kwargs = msg[3]
                callback = self._callbackRegistry._callbacks[callbackKey]
                resp = callback(*args, **kwargs)
                assert resp is None
                return 'ack'
            case _:
                raise NotImplementedError(f'Unexpected message type: {msg[0]}')

    def _remotePlotterCall(self, fnStr, *args, **kwargs):
        return self._remoteCall('callPlotterMethod', fnStr, args, kwargs)

    async def _remotePlotterCall_async(self, fnStr, *args, **kwargs):
        return await self._remoteCall_async('callPlotterMethod', fnStr, args, kwargs)

    def _remotePlotterGet(self, key: str):
        return self._remoteCall('plotterGet', key)

    def _remoteActorCall(self, actor: RemoteActorProxy, fnStr, *args, **kwargs):
        actorRef = ActorRef(actorID=actor.actorID)

        return self._remoteCall('callActorMethod', fnStr, args, kwargs, cmdArgs=(actorRef,))

    def _remoteActorMapperCall(self, actor: RemoteActorProxy, fnStr: str, *args, **kwargs):
        actorRef = ActorRef(actorID=actor.actorID)
        return self._remoteCall('callActorMapperMethod', fnStr, args, kwargs, cmdArgs=(actorRef,))

    def _remoteActorMapperGet(self, actor: RemoteActorProxy, key: str):
        actorRef = ActorRef(actorID=actor.actorID)
        return self._remoteCall('actorMapperGet', key, cmdArgs=(actorRef,))

    def _remoteActorMapperSet(self, actor: RemoteActorProxy, key: str, value):
        actorRef = ActorRef(actorID=actor.actorID)
        return self._remoteCall('actorMapperSet', key, (value,), cmdArgs=(actorRef,))

    def _remoteMapperCall(self, fnStr: str, *args, **kwargs):
        return self._remoteCall('callMapperMethod', fnStr, args, kwargs)

    def _remoteMapperGet(self, key: str):
        return self._remoteCall('mapperGet', key)

    def _remoteMapperSet(self, key: str, value):
        return self._remoteCall('mapperSet', key, (value,))

    def _remoteCameraGet(self, key: str):
        return self._remoteCall('cameraGet', key)

    def _remoteCameraSet(self, key: str, value):
        return self._remoteCall('cameraSet', key, (value,))

    def _remoteCameraCall(self, fnStr, *args, **kwargs):
        return self._remoteCall('cameraCall', fnStr, args, kwargs)

    def _remoteQueryProperty(self, key: str) -> None:
        return self._remoteCall('queryProperty', key)

    def render(self):
        return self._remotePlotterCall('render')

    def subplot(self, row: int, col: int):
        return self._remotePlotterCall('subplot', row, col)

    def add_mesh(self, *args, **kwargs):
        return self._remotePlotterCall('add_mesh', *args, **kwargs)

    def addMesh(self, *args, **kwargs):
        return self._remotePlotterCall('addMesh', *args, **kwargs)

    def add_volume(self, *args, **kwargs):
        return self._remotePlotterCall('add_volume', *args, **kwargs)

    def add_lines(self, *args, **kwargs):
        return self._remotePlotterCall('add_lines', *args, **kwargs)

    async def add_lines_async(self, *args, **kwargs):
        return await self._remotePlotterCall_async('add_lines', *args, **kwargs)

    def addLineSegments(self, *args, **kwargs):
        return self._remotePlotterCall('addLineSegments', *args, **kwargs)

    def add_points(self, *args, **kwargs):
        return self._remotePlotterCall('add_points', *args, **kwargs)

    async def add_points_async(self, *args, **kwargs):
        return await self._remotePlotterCall_async('add_points', *args, **kwargs)

    def add_point_labels(self, *args, **kwargs):
        return self._remotePlotterCall('add_point_labels', *args, **kwargs)

    def add_axes_marker(self, *args, **kwargs):
        return self._remotePlotterCall('add_axes_marker', *args, **kwargs)

    def addIrenStyleClassObserver(self, *args, **kwargs):
        return self._remotePlotterCall('addIrenStyleClassObserver', *args, **kwargs)

    def clear(self, *args, **kwargs):
        return self._remotePlotterCall('clear', *args, **kwargs)

    def enable_depth_peeling(self, *args, **kwargs):
        return self._remotePlotterCall('enable_depth_peeling', *args, **kwargs)

    def enable_parallel_projection(self):
        self._remotePlotterCall('enable_parallel_projection')

    def enable_point_picking(self, *args, **kwargs):
        self._remotePlotterCall('enable_point_picking', *args, **kwargs)

    def enable_image_style(self, *args, **kwargs):
        self._remotePlotterCall('enable_image_style', *args, **kwargs)

    def set_background(self, *args, **kwargs):
        return self._remotePlotterCall('set_background', *args, **kwargs)

    def set_camera_clipping_range(self, *args, **kwargs):
        return self._remotePlotterCall('set_camera_clipping_range', *args, **kwargs)

    def reset_camera_clipping_range(self, *args, **kwargs):
        return self._remotePlotterCall('reset_camera_clipping_range', *args, **kwargs)

    def reset_scalar_bar_ranges(self, *args, **kwargs):
        return self._remotePlotterCall('reset_scalar_bar_ranges', *args, **kwargs)

    def add_axes_at_origin(self, *args, **kwargs):
        return self._remotePlotterCall('add_axes_at_origin', *args, **kwargs)

    def setActorUserTransform(self, actor: RemoteActorProxy, transform: np.ndarray):
        return self._remotePlotterCall('setActorUserTransform', ActorRef(actorID=actor.actorID), transform)

    def reset_camera(self):
        return self._remotePlotterCall('reset_camera')

    def show_grid(self, *args, **kwargs):
        return self._remotePlotterCall('show_grid', *args, **kwargs)

    def showGrid(self, *args, **kwargs):
        return self._remotePlotterCall('showGrid', *args, **kwargs)

    def remove_actor(self, *args, **kwargs):
        return self._remotePlotterCall('remove_actor', *args, **kwargs)

    def pauseRendering(self):
        return self._remotePlotterCall('pauseRendering')

    def maybeResumeRendering(self):
        return self._remotePlotterCall('maybeResumeRendering')

    def resumeRendering(self):
        return self._remotePlotterCall('resumeRendering')

    @contextmanager
    def renderingPaused(self):
        self.pauseRendering()
        yield
        self.maybeResumeRendering()

    def update(self, *args, **kwargs):
        return self._remotePlotterCall('update', *args, **kwargs)

    def update_scalars(self, *args, **kwargs):
        return self._remotePlotterCall('update_scalars', *args, **kwargs)

    def update_scalar_bar_range(self, *args, **kwargs):
        return self._remotePlotterCall('update_scalar_bar_range', *args, **kwargs)

    def updateScalarBarRangeWithVol(self, *args, **kwargs):
        return self._remotePlotterCall('updateScalarBarRangeWithVol', *args, **kwargs)


class RemotePlotterProxy(RemotePlotterProxyBase, QtWidgets.QWidget):
    """
    There are issues with instantiating multiple pyvista BackgroundPlotters in the same
    process, related to opengl window binding and threaded render contexts. To work around
    this, we create a separate process for each  plotter, embed it in the primary Qt process
    using QWidget.createWindowContainer(), and communicate with it via zmq sockets. This is
     very inefficient (due to multiprocesing overhead), but has the side benefit of
      parallelizing some of the rendering workload.

    Note that this requires serializing / deserializing communication between the main process
    and the plotter. Only a limited subset of plotter methods are currently supported, and
    code working with the results of plotter calls (e.g. actors) may need to be further
     adapted by the caller.
    """

    _RemotePlotterApp: tp.Type[RemotePlotterApp] | None = None

    def __init__(self, parent=None, **kwargs):
        RemotePlotterProxyBase.__init__(self)
        QtWidgets.QWidget.__init__(self, parent=parent)

        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)

        logger.debug(f'Initializing rep socket')
        ctx = zmq.Context()
        actx = azmq.Context()
        self._repSocket = actx.socket(zmq.REP)
        repPort = self._repSocket.bind_to_random_port('tcp://127.0.0.1')
        logger.debug(f'Rep socket bound to port {repPort}')

        logger.debug(f'Initializing req and push sockets')
        self._reqSocket = ctx.socket(zmq.REQ)
        self._reqSocketReqPending: bool = False
        self._areqSocket = actx.socket(zmq.REQ)
        self._pushSocket = ctx.socket(zmq.PUSH)
        # connect these later
        logger.debug(f'Req and push sockets initialized, not connected yet')

        self._areqLock = asyncio.Lock()

        procKwargs = dict(reqPort=repPort, plotterKwargs=kwargs)
        self.remoteProc = None
        self._startRemoteProc(procKwargs, **kwargs)

        self._socketLoopTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._socketLoop))

    @property
    def isReadyEvent(self):
        return self._isReady

    def _startRemoteProc(self, procKwargs, **kwargs):
        assert self.remoteProc is None

        logger.debug('Preparing to start remote plotter process')

        if self._RemotePlotterApp is None:
            self._RemotePlotterApp = RemotePlotterApp

        if True:
            # set log filepath of remote proc based on filepath of root logger file handler
            handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.QueueHandler)]
            if len(handlers) > 0:
                logFilepath = handlers[-1].listener.handlers[0].baseFilename
                procKwargs = procKwargs.copy()
                procKwargs['logFilepath'] = logFilepath

        procKwargs['theme'] = 'light' if self.palette().color(QtGui.QPalette.Base).value() > 128 else 'dark'
        # TODO: add support for dynamically updating theme on palette changes later

        self.remoteProc = mp.Process(target=self._RemotePlotterApp.createAndRun,
                                     daemon=True,
                                     kwargs=procKwargs)
        logger.debug('Starting remote plotter process')
        self.remoteProc.start()

    async def _sendReqAndRecv_async(self, msg):
        async with self._areqLock:
            logger.debug(f'Sending async msg {msg}')
            self._areqSocket.send_pyobj(msg)
            logger.debug(f'Awaiting reply')
            resp = await self._areqSocket.recv_pyobj()
            logger.debug(f'Received reply {resp}')
            return resp

    def _sendReqAndRecv(self, msg):
        """
        Note: due to some weirdness with Qt window containering, the remote process can
        deadlock if we don't keep processing Qt events in the main process during cursor
        interaction with the remote plotter window. So make sure to keep processing events
        even while waiting for an otherwise blocking result to come back.
        """
        if self._reqSocketReqPending:
            if True:
                logger.error(f'Previous request still pending, not sending this: {msg}')
                return
            else:
                logger.warning('Waiting for a previous request to finish before sending this one')
                # note: if previous request was outside of QEventLoop, we will be stuck here and never get its response
                while self._reqSocketReqPending:
                    logger.debug('Waiting...')
                    QtWidgets.QApplication.instance().processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
                    time.sleep(0.001)

        try:
            logger.debug(f'Sending msg {msg}')
            self._reqSocket.send_pyobj(msg)
        except TypeError as e:
            logger.error(f'Problem serializing message: {exceptionToStr(e)}')
            raise e

        logger.debug('Waiting for response')
        self._reqSocketReqPending = True
        while True:
            try:
                resp = self._reqSocket.recv_pyobj(flags=zmq.NOBLOCK)
            except zmq.error.Again:
                # no message available
                # logger.debug('No message available, waiting...')
                QtWidgets.QApplication.instance().processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
                time.sleep(0.001)
            except Exception as e:
                logger.error(f'Unhandled exception in _waitForResp: {exceptionToStr(e)}')
                raise e
            else:
                break
        self._reqSocketReqPending = False
        logger.debug(f'Received reply {resp}')
        return resp

    def _sendReqNonblocking(self, msg):
        self._pushSocket.send_pyobj(msg)
        return None

    async def _socketLoop(self):
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        tempWdgt = QtWidgets.QLabel()
        tempWdgt.setText('Initializing plotter...')
        tempWdgt.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(tempWdgt)

        logger.debug('Waiting for remote to start up')
        msg = await self._repSocket.recv_pyobj()
        assert isinstance(msg, tuple)
        # first message sent should be remote's repPort and pullPort
        assert msg[0] == 'ports'
        portsDict = msg[1]
        logger.debug(f'Got remote ports: {portsDict}')

        self._repSocket.send_pyobj('ack')

        self._reqSocket.connect(f'tcp://localhost:{portsDict["rep"]}')
        self._areqSocket.connect(f'tcp://localhost:{portsDict["rep"]}')
        self._pushSocket.connect(f'tcp://localhost:{portsDict["pull"]}')

        # get win ID and reparent remote window
        winID = await self._sendReqAndRecv_async(('getWinID',))

        self._embedWin = QtGui.QWindow.fromWinId(winID)
        assert self._embedWin != 0
        #self._embedWin.setFlags(QtCore.Qt.FramelessWindowHint)
        if False:  # TODO: debug, delete or set to False
            self._embedWdgt = QtWidgets.QWidget()  # placeholder
        else:
            try:
                self._embedWdgt = QtWidgets.QWidget.createWindowContainer(self._embedWin, parent=self)
            except RuntimeError as e:
                # if this widget was deleted while awaiting remote initialization, will
                # raise a runtime error here
                logger.warning('Problem while creating window container, giving up')
                self.remoteProc.terminate()
                return

        self._embedWdgt.setVisible(False)

        tempWdgt.setVisible(False)
        layout.removeWidget(tempWdgt)
        tempWdgt.deleteLater()
        layout.addWidget(self._embedWdgt)

        resp = await self._sendReqAndRecv_async(('showWindow',))
        assert resp == 'ack'

        self._embedWdgt.setVisible(True)

        self._isReady.set()

        while True:
            msg = await self._repSocket.recv_pyobj()
            try:
                resp = await self._handleMsg(msg)
            except Exception as e:
                logger.error(f'Exception while handling message: {exceptionToStr(e)}')
                resp = e

            await self._repSocket.send_pyobj(resp)

    def render(self, *args, **kwargs):
        if len(args) == 0 and len(kwargs) == 0:
            return RemotePlotterProxyBase.render(self)
        else:
            return QtWidgets.QWidget.render(self, *args, **kwargs)

    async def close_async(self):
        logger.info('Closing')
        await self._isReady.wait()
        await self._sendReqAndRecv_async(('quit',))
        self._socketLoopTask.cancel()
        self.remoteProc.terminate()
        logger.debug('Closed')

    def close(self):
        logger.info('Closing')
        asyncio.create_task(asyncTryAndLogExceptionOnError(self.close_async))
        super().close()
        logger.debug('Closed')


@attrs.define
class RemoteMapper:
    _parentActor: RemoteActorProxy | None = None
    _parentPlotter: RemotePlotterProxy | None = None

    def SetInputData(self, *args, **kwargs):
        if self._parentActor is None:
            raise NotImplementedError('Only supported when mapper created from a specific parent actor')

        return self._parentActor.plotter._remoteActorMapperCall(
            self._parentActor,
            'SetInputData', *args, **kwargs)

    @property
    def scalar_range(self):
        if self._parentActor is None:
            return self._parentPlotter._remoteMapperGet('scalar_range')
        else:
            return self._parentActor.plotter._remoteActorMapperGet(self._parentActor, 'scalar_range')

    @scalar_range.setter
    def scalar_range(self, value: tuple):
        if self._parentActor is None:
            self._parentPlotter._remoteMapperSet('scalar_range', value)
        else:
            self._parentActor.plotter._remoteActorMapperSet(self._parentActor, 'scalar_range', value)

    def update(self):
        if self._parentActor is None:
            return self._parentPlotter._remoteMapperCall('update')
        else:
            return self._parentActor.plotter._remoteActorMapperCall(self._parentActor, 'update')