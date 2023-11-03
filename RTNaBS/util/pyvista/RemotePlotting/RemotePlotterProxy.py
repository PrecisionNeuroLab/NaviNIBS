from __future__ import annotations

import asyncio
from contextlib import contextmanager
import logging
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

from RTNaBS.util import exceptionToStr, makeStrUnique
from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError
from RTNaBS.util.pyvista.RemotePlotting import ActorRef
from RTNaBS.util.pyvista.RemotePlotting.RemotePlotter import RemotePlotterApp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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


class RemotePlotterProxyBase:
    _camera: RemoteCameraProxy | None = None

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

        if 'mesh' in kwargs and isinstance(kwargs['mesh'], pv.PolyData):
            # clear un-pickleable obbTree field
            # note: this may cause unexpected issues...
            kwargs['mesh'] = kwargs['mesh'].copy()
            kwargs['mesh']._obbTree = None

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

    def _remoteActorMapperCall(self, actor: RemoteActorProxy, fnStr, *args, **kwargs):
        actorRef = ActorRef(actorID=actor.actorID)

        return self._remoteCall('callActorMapperMethod', fnStr, args, kwargs, cmdArgs=(actorRef,))

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

    def remove_actor(self, *args, **kwargs):
        return self._remotePlotterCall('remove_actor', *args, **kwargs)

    def pauseRendering(self):
        return self._remotePlotterCall('pauseRendering')

    def resumeRendering(self):
        return self._remotePlotterCall('resumeRendering')

    def update(self, *args, **kwargs):
        return self._remotePlotterCall('update', *args, **kwargs)

    def update_scalars(self, *args, **kwargs):
        return self._remotePlotterCall('update_scalars', *args, **kwargs)


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

    def __init__(self, parent=None, **kwargs):
        RemotePlotterProxyBase.__init__(self)
        QtWidgets.QWidget.__init__(self, parent=parent)

        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)

        ctx = zmq.Context()
        actx = azmq.Context()
        self._repSocket = actx.socket(zmq.REP)
        repPort = self._repSocket.bind_to_random_port('tcp://127.0.0.1')

        self._reqSocket = ctx.socket(zmq.REQ)
        self._areqSocket = actx.socket(zmq.REQ)
        self._pushSocket = ctx.socket(zmq.PUSH)
        # connect these later
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
        self.remoteProc = mp.Process(target=RemotePlotterApp.createAndRun,
                                     daemon=True,
                                     kwargs=procKwargs)
        self.remoteProc.start()

    async def _sendReqAndRecv_async(self, msg):
        async with self._areqLock:
            self._areqSocket.send_pyobj(msg)
            return await self._areqSocket.recv_pyobj()

    def _sendReqAndRecv(self, msg):
        try:
            self._reqSocket.send_pyobj(msg)
        except TypeError as e:
            logger.error(f'Problem serializing message: {exceptionToStr(e)}')
            raise e
        return self._waitForResp(self._reqSocket)

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
            self._embedWdgt = QtWidgets.QWidget.createWindowContainer(self._embedWin, parent=self)

        layout.removeWidget(tempWdgt)
        tempWdgt.deleteLater()
        layout.addWidget(self._embedWdgt)

        resp = await self._sendReqAndRecv_async(('showWindow',))
        assert resp == 'ack'

        self._isReady.set()

        while True:
            msg = await self._repSocket.recv_pyobj()
            try:
                resp = await self._handleMsg(msg)
            except Exception as e:
                logger.error(f'Exception while handling message: {exceptionToStr(e)}')
                resp = e

            await self._repSocket.send_pyobj(resp)

    def _waitForResp(self, socket: zmq.Socket):
        """
        Note: due to some weirdness with Qt window containering, the remote process can
        deadlock if we don't keep processing Qt events in the main process during cursor
        interaction with the remote plotter window. So make sure to keep processing events
        even while waiting for an otherwise blocking result to come back.
        """
        while True:
            try:
                resp = socket.recv_pyobj(flags=zmq.NOBLOCK)
            except zmq.error.Again:
                # no message available
                QtWidgets.QApplication.instance().processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
                time.sleep(0.001)
            except Exception as e:
                logger.error(f'Unhandled exception in _waitForResp: {exceptionToStr(e)}')
                raise e
            else:
                break
        return resp

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

    def close(self):
        logger.info('Closing')
        asyncio.create_task(asyncTryAndLogExceptionOnError(self.close_async))
        super().close()


@attrs.define(frozen=True)
class RemoteMapper:
    parentActor: RemoteActorProxy

    def SetInputData(self, *args, **kwargs):
        return self.parentActor.plotter._remoteActorMapperCall(
            self.parentActor,
            'SetInputData', *args, **kwargs)
