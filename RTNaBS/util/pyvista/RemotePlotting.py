from __future__ import annotations
import asyncio
import attrs
import logging
import multiprocessing as mp
from multiprocessing import connection as mpc
import numpy as np
import pyvista as pv
import pyvistaqt as pvqt
import time
import vtkmodules.vtkRenderingAnnotation
from qtpy import QtGui, QtCore, QtWidgets
import typing as tp
import zmq
import zmq.asyncio as azmq

from RTNaBS.util import ZMQAsyncioFix, exceptionToStr
from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError
from RTNaBS.util.GUI.QAppWithAsyncioLoop import RunnableAsApp
from RTNaBS.util.pyvista.plotting import BackgroundPlotter
from RTNaBS.util.pyvista import Actor

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@attrs.define(frozen=True)
class MapperRef:
    parentActor: RemoteActor

    def SetInputData(self, *args, **kwargs):
        return self.parentActor.plotter._remoteActorMapperCall(
            self.parentActor,
            'SetInputData', *args, **kwargs)


@attrs.define(frozen=True)
class ActorRef:
    """
    vtk Actors can't be pickled, so we use this class to pass to remote processes,
    and use ActorManager to keep track of which ref refers to which actor locally.
    """
    actorID: str


@attrs.define(frozen=True)
class RemoteActor:
    actorID: str
    plotter: EmbeddedRemotePlotter

    def SetUserTransform(self, transform: vtkmodules.vtkCommonTransforms.vtkTransform):

        # convert to ndarray since vtkTransform is not pickleable
        transform_ndarray = pv.array_from_vtkmatrix(transform.GetMatrix())

        return self.plotter.setActorUserTransform(self, transform_ndarray)

    def GetVisibility(self) -> bool:
        import time
        return self.plotter._remoteActorCall(self, 'GetVisibility')

    def GetMapper(self):
        return MapperRef(self)


@attrs.define
class RemoteCamera:
    _plotter: EmbeddedRemotePlotter

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


@attrs.define
class _ActorManager:
    _counter: int = 0
    _actors: dict[ActorRef, Actor] = attrs.field(factory=dict)

    def addActor(self, actor: Actor) -> ActorRef:
        self._counter += 1
        actorID = f'<Actor{self._counter}>'
        actorRef = ActorRef(actorID)
        self._actors[actorRef] = actor
        return actorRef

    def getActor(self, actorRef: ActorRef) -> Actor:
        return self._actors[actorRef]


@attrs.define(kw_only=True)
class _RemotePlotManagerBase:
    _parentLayout: QtWidgets.QLayout
    _plotterArgs: list = attrs.field(factory=list)
    _plotterKwargs: dict = attrs.field(factory=dict)

    _actorManager: _ActorManager = attrs.field(init=False, factory=_ActorManager)
    _Plotter: tp.ClassVar
    _plotter: _RemotePlotter | None = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        pass

    def initPlotter(self):
        # don't do this automatically to allow deferred widget instantiation
        assert self._plotter is None
        logger.info('Initializing plotter')
        self._plotter = self._Plotter(*self._plotterArgs,
                                       #parent=self._parentLayout.parentWidget(),
                                       **self._plotterKwargs)
        logger.debug('Adding plotter to parent layout')
        self._parentLayout.addWidget(self._plotter)

    @property
    def plotter(self):
        return self._plotter

    def _queryProperty(self, key: str) -> None:
        """
        Call a getter without returning result.

        Useful in rare circumstances like getting plotter camera to get
        forced camera reset out of the way, without trying to actually
        serialize the camera object
        """
        assert self._plotter is not None
        fn = getattr
        args = (self._plotter, key)
        self._callMethod(fn, args, {})
        return

    def _callPlotMethod(self, msg):
        assert self._plotter is not None
        fn = getattr(self._plotter, msg[0])
        args = list(msg[1])
        kwargs = msg[2]

        return self._callMethod(fn, args, kwargs)

    def _callActorMethod(self, actor: ActorRef, msg):
        actor = self._actorManager.getActor(actor)
        fn = getattr(actor, msg[0])
        args = list(msg[1])
        kwargs = msg[2]

        return self._callMethod(fn, args, kwargs)

    def _callActorMapperMethod(self, actor: ActorRef, msg):
        actor = self._actorManager.getActor(actor)
        mapper = actor.GetMapper()
        fn = getattr(mapper, msg[0])
        args = list(msg[1])
        kwargs = msg[2]

        return self._callMethod(fn, args, kwargs)

    def _callCameraMethod(self, msg):
        assert self._plotter is not None
        fn = getattr(self._plotter.camera, msg[0])
        args = list(msg[1])
        kwargs = msg[2]

        return self._callMethod(fn, args, kwargs)

    def _callMethod(self, fn, args, kwargs):
        # convert any obvious ActorRefs to Actors
        for iArg in range(len(args)):
            if isinstance(args[iArg], ActorRef):
                args[iArg] = self._actorManager.getActor(args[iArg])
        for key in kwargs:
            if isinstance(kwargs[key], ActorRef):
                kwargs[key] = self._actorManager.getActor(kwargs[key])

        # convert any callback keys in kwargs to callbacks
        if 'callback' in kwargs:
            callbackKey = kwargs['callback']
            callbackFn = lambda *args, **kwargs: self._executeCallback(callbackKey, *args, **kwargs)
            kwargs['callback'] = callbackFn

        # call method
        result = fn(*args, **kwargs)

        # convert any Actors to ActorRefs
        if isinstance(result, (Actor,
                               vtkmodules.vtkRenderingAnnotation.vtkAxesActor,
                               pv._vtk.vtkVolume)):
            # Actor is not pickleable, so convert to an ActorRef
            actorRef = self._actorManager.addActor(result)
            result = actorRef

        return result

    def _executeCallback(self, callbackKey: str, *args, **kwargs):
        raise NotImplementedError  # to be implemented by subclass


class _RemotePlotter(BackgroundPlotter):
    def __init__(self, *args, **kwargs):
        BackgroundPlotter.__init__(self, *args,
                                   **kwargs)

    def setActorUserTransform(self, actor: Actor, transform: np.ndarray):
        # convert from numpy array to vtkTransform
        t = pv._vtk.vtkTransform()
        t.SetMatrix(pv.vtkmatrix_from_array(transform))

        actor.SetUserTransform(t)


@attrs.define(kw_only=True)
class _RemotePlotManager(_RemotePlotManagerBase):
    _reqPort: int
    _repPort: int | None = None
    _addr: str = '127.0.0.1'
    _Plotter: tp.ClassVar = _RemotePlotter
    _ctx: azmq.Context = attrs.field(init=False, factory=azmq.Context)
    _reqSock: azmq.Socket = attrs.field(init=False)
    _repSock: azmq.Socket = attrs.field(init=False)
    _reqLock: asyncio.Lock = attrs.field(init=False, factory=asyncio.Lock)
    _socketLoopTask: asyncio.Task = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._reqSock = self._ctx.socket(zmq.REQ)
        self._reqSock.connect(f'tcp://{self._addr}:{self._reqPort}')

        self._repSock = self._ctx.socket(zmq.REP)
        if self._repPort is None:
            self._repPort = self._repSock.bind_to_random_port(f'tcp://{self._addr}')
        else:
            self._repSock.bind(f'tcp://{self._addr}:{self._repPort}')

        self._socketLoopTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._socketLoop))

    async def _socketLoop(self):

        async with self._reqLock:
            await self._reqSock.send_pyobj(('repPort', self._repPort))
            resp = await self._reqSock.recv_pyobj()
            assert resp == 'ack'

        if True:
            self.initPlotter()

        while True:
            logger.debug('Awaiting msg')
            msg = await self._repSock.recv_pyobj()
            logger.debug(f'Received msg: {msg}')

            async def sendResponse(response):
                await self._repSock.send_pyobj(response)

            if not isinstance(msg, tuple):
                await sendResponse(NotImplementedError('Unexpected message format'))
                continue

            match msg[0]:
                case 'noop':
                    await sendResponse('ack')

                case 'getWinID':
                    if True:
                        win = self._parentLayout.parentWidget().window()
                    else:
                        win = self._plotter.window()
                    await sendResponse(win.winId())

                case 'showWindow':
                    if True:
                        win = self._parentLayout.parentWidget().window()
                    else:
                        win = self._plotter.window()

                    if self._plotter is None:
                        # assume everything else is now ready and we should instantiate plotter
                        self.initPlotter()

                    win.show()
                    await sendResponse('ack')

                case 'plotterGet':
                    try:
                        assert isinstance(msg[1], str)
                        assert len(msg) == 4 and len(msg[2]) == 0 and len(msg[3]) == 0
                        result = getattr(self._plotter, msg[1])
                    except Exception as e:
                        logger.error(f'Error getting plotter attribute: {exceptionToStr(e)}')
                        await sendResponse(e)
                    else:
                        await sendResponse(result)

                case 'callPlotterMethod':
                    try:
                        result = self._callPlotMethod(msg[1:])

                    except Exception as e:
                        logger.error(f'Error calling plot method {msg[0]}: {exceptionToStr(e)}')
                        await sendResponse(e)

                    else:
                        await sendResponse(result)

                case 'callActorMethod':
                    try:
                        assert isinstance(msg[1], ActorRef)
                        result = self._callActorMethod(msg[1], msg[2:])

                    except Exception as e:
                        logger.error(f'Error calling actor method: {exceptionToStr(e)}')
                        await sendResponse(e)

                    else:
                        await sendResponse(result)

                case 'callActorMapperMethod':
                    try:
                        assert isinstance(msg[1], ActorRef)
                        result = self._callActorMapperMethod(msg[1], msg[2:])
                    except Exception as e:
                        logger.error(f'Error calling actor mapper method: {exceptionToStr(e)}')
                        await sendResponse(e)
                    else:
                        await sendResponse(result)

                case 'cameraGet':
                    try:
                        assert isinstance(msg[1], str)
                        assert len(msg) == 4 and len(msg[2]) == 0 and len(msg[3]) == 0
                        result = getattr(self._plotter.camera, msg[1])
                    except Exception as e:
                        logger.error(f'Error getting camera property: {exceptionToStr(e)}')
                        await sendResponse(e)

                    else:
                        await sendResponse(result)

                case 'cameraSet':
                    try:
                        assert isinstance(msg[1], str)
                        assert len(msg) == 4
                        assert len(msg[2]) == 1
                        assert len(msg[3]) == 0
                        setattr(self._plotter.camera, msg[1], msg[2][0])
                    except Exception as e:
                        logger.error(f'Error setting camera property: {exceptionToStr(e)}')
                        await sendResponse(e)
                    else:
                        await sendResponse(None)

                case 'cameraCall':
                    try:
                        result = self._callCameraMethod(msg[1:])
                    except Exception as e:
                        logger.error(f'Error calling camera method: {exceptionToStr(e)}')
                        await sendResponse(e)
                    else:
                        await sendResponse(result)

                case 'queryProperty':
                    try:
                        assert isinstance(msg[1], str)
                        self._queryProperty(msg[1])
                    except Exception as e:
                        logger.error(f'Error querying property: {exceptionToStr(e)}')
                        await sendResponse(e)
                    else:
                        await sendResponse(None)

                case _:
                    raise NotImplementedError(f'Unexpected message type: {msg[0]}')

    def _executeCallback(self, callbackKey: str, *args, **kwargs):
        logger.debug(f'Queuing async task for callback {callbackKey}')
        asyncio.create_task(asyncTryAndLogExceptionOnError(self._executeCallbackAsync, callbackKey, *args, **kwargs))

    async def _executeCallbackAsync(self, callbackKey: str, *args, **kwargs):
        async with self._reqLock:
            await self._reqSock.send_pyobj(('callback', callbackKey, args, kwargs))
            resp = await self._reqSock.recv_pyobj()
            assert resp == 'ack'


@attrs.define(kw_only=True)
class _RemotePlotterApp(RunnableAsApp):
    _reqPort: int
    _repPort: int | None = None
    _appName: str = 'RemotePlotter'
    _plotterKwargs: dict = attrs.field(factory=dict)

    _plotManager: _RemotePlotManager = attrs.field(init=False)
    _rootWdgt: QtWidgets.QWidget = attrs.field(init=False)
    _debugTimer: QtCore.QTimer = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        wdgt = QtWidgets.QWidget()
        self._rootWdgt = wdgt
        wdgt.setLayout(QtWidgets.QVBoxLayout())
        wdgt.layout().setContentsMargins(0, 0, 0, 0)

        self._plotManager = _RemotePlotManager(reqPort=self._reqPort,
                                               repPort=self._repPort,
                                               parentLayout=wdgt.layout(),
                                               plotterKwargs=self._plotterKwargs)

        self._win.setCentralWidget(wdgt)

        flags = self._win.windowFlags()
        flags |= QtCore.Qt.FramelessWindowHint
        flags |= QtCore.Qt.MSWindowsFixedSizeDialogHint
        flags |= QtCore.Qt.SubWindow
        self._win.setWindowFlags(flags)


class EmbeddedRemotePlotter(QtWidgets.QWidget):
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

    _camera: RemoteCamera | None = None

    @attrs.define
    class CallbackRegistry:
        _callbacks: dict[str, tp.Callable] = attrs.field(factory=dict)

        def register(self, name: str, func: tp.Callable):
            assert name not in self._callbacks
            self._callbacks[name] = func

        def callback(self, name: str, *args, **kwargs):
            return self._callbacks[name](*args, **kwargs)

    _callbackRegistry: CallbackRegistry

    def __init__(self, **kwargs):

        QtWidgets.QWidget.__init__(self)

        self._callbackRegistry = self.CallbackRegistry()

        ctx = zmq.Context()
        actx = azmq.Context()
        self._repSocket = actx.socket(zmq.REP)
        repPort = self._repSocket.bind_to_random_port('tcp://127.0.0.1')

        self._reqSocket = ctx.socket(zmq.REQ)
        self._areqSocket = actx.socket(zmq.REQ)
        # connect these later

        procKwargs = dict(reqPort=repPort, plotterKwargs=kwargs)

        self._isReady = asyncio.Event()

        self.remoteProc = mp.Process(target=_RemotePlotterApp.createAndRun,
                                     daemon=True,
                                     kwargs=procKwargs)

        self.remoteProc.start()

        self._socketLoopTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._socketLoop))

    @property
    def picked_point(self):
        return self._remotePlotterGet('picked_point')

    async def _socketLoop(self):

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        tempWdgt = QtWidgets.QLabel()
        tempWdgt.setText('Initializing plotter...')
        layout.addWidget(tempWdgt)

        logger.debug('Waiting for remote to start up')
        msg = await self._repSocket.recv_pyobj()
        assert isinstance(msg, tuple)
        # first message sent should be remote's repPort
        assert msg[0] == 'repPort'
        repPort = msg[1]
        logger.debug(f'Got remote repPort: {repPort}')

        self._repSocket.send_pyobj('ack')

        self._reqSocket.connect(f'tcp://localhost:{repPort}')
        self._areqSocket.connect(f'tcp://localhost:{repPort}')

        # get win ID and reparent remote window

        await self._areqSocket.send_pyobj(('getWinID',))
        winID = await self._areqSocket.recv_pyobj()

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

        await self._areqSocket.send_pyobj(('showWindow',))
        resp = await self._areqSocket.recv_pyobj()
        assert resp == 'ack'

        self._isReady.set()

        while True:
            msg = await self._repSocket.recv_pyobj()
            match msg[0]:
                case 'callback':
                    try:
                        callbackKey = msg[1]
                        args = msg[2]
                        kwargs = msg[3]
                        callback = self._callbackRegistry._callbacks[callbackKey]
                        resp = callback(*args, **kwargs)
                        assert resp is None  # return values not supported due to asynchronous exchange
                    except Exception as e:
                        logger.error(f'Exception while executing callback: {exceptionToStr(e)}')
                        await self._repSocket.send_pyobj(e)
                    else:
                        await self._repSocket.send_pyobj('ack')
                case _:
                    await self._repSocket.send_pyobj(NotImplementedError(f'Unexpected message type: {msg[0]}'))

    def _handleResp(self, label, resp):
        logger.debug(f'{label} response: {resp}')
        if isinstance(resp, Exception):
            logger.error(f'{exceptionToStr(resp)}')
            raise resp
        elif isinstance(resp, ActorRef):
            # convert result to a RemoteActor
            return RemoteActor(actorID=resp.actorID, plotter=self)
        else:
            return resp

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

    def _remoteCall(self, cmdKey, fnStr, args: tuple = (), kwargs: dict | None = None, cmdArgs: tuple = ()):
        if kwargs is None:
            kwargs = dict()

        logger.debug(f'{cmdKey} {fnStr}')
        assert self._isReady.is_set()

        args = list(args)

        for iArg in range(len(args)):
            if isinstance(args[iArg], RemoteActor):
                # convert from RemoteActor to ActorRef
                args[iArg] = ActorRef(actorID=args[iArg].actorID)

        if 'callback' in kwargs:
            # convert from callback function to key matching new entry
            # in callback registry
            callbackFn = kwargs['callback']
            callbackKey = fnStr
            self._callbackRegistry.register(callbackKey, callbackFn)  # this requires a unique key
            kwargs['callback'] = callbackKey

        self._reqSocket.send_pyobj((cmdKey, *cmdArgs, fnStr, args, kwargs))
        logger.debug(f'Waiting for response to {fnStr}')

        resp = self._waitForResp(self._reqSocket)

        logger.debug(f'Handling response to {fnStr}')
        return self._handleResp(fnStr, resp)

    def _remotePlotterCall(self, fnStr, *args, **kwargs):
        return self._remoteCall('callPlotterMethod', fnStr, args, kwargs)

    def _remotePlotterGet(self, key: str):
        return self._remoteCall('plotterGet', key)

    def _remoteActorCall(self, actor: RemoteActor, fnStr, *args, **kwargs):
        actorRef = ActorRef(actorID=actor.actorID)

        return self._remoteCall('callActorMethod', fnStr, args, kwargs, cmdArgs=(actorRef,))

    def _remoteActorMapperCall(self, actor: RemoteActor, fnStr, *args, **kwargs):
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

    @property
    def isReadyEvent(self):
        return self._isReady

    @property
    def camera(self):
        if self._camera is None:
            self._remoteQueryProperty('camera')
            self._camera = RemoteCamera(plotter=self)
        return self._camera

    def render(self, *args, **kwargs):
        if len(args) == 0 and len(kwargs) == 0:
            return self._remotePlotterCall('render')
        else:
            # this is a call from the Qt event loop, so we need to forward it to the remote process
            return QtWidgets.QWidget.render(self, *args, **kwargs)

    def subplot(self, row: int, col: int):
        return self._remotePlotterCall('subplot', row, col)

    def add_mesh(self, *args, **kwargs):
        return self._remotePlotterCall('add_mesh', *args, **kwargs)

    def add_volume(self, *args, **kwargs):
        return self._remotePlotterCall('add_volume', *args, **kwargs)

    def add_lines(self, *args, **kwargs):
        return self._remotePlotterCall('add_lines', *args, **kwargs)

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

    def add_axes_at_origin(self, *args, **kwargs):
        return self._remotePlotterCall('add_axes_at_origin', *args, **kwargs)

    def setActorUserTransform(self, actor: RemoteActor, transform: np.ndarray):
        return self._remotePlotterCall('setActorUserTransform', ActorRef(actorID=actor.actorID), transform)

    def reset_camera(self):
        return self._remotePlotterCall('reset_camera')

    def show_grid(self, *args, **kwargs):
        return self._remotePlotterCall('show_grid', *args, **kwargs)

    def remove_actor(self, *args, **kwargs):
        return self._remotePlotterCall('remove_actor', *args, **kwargs)