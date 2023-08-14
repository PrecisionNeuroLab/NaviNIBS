from __future__ import annotations

import asyncio
import logging
import typing as tp

import attrs
import numpy as np
import pyvista as pv
import vtkmodules.vtkRenderingAnnotation
import zmq
from qtpy import QtWidgets, QtCore
from zmq import asyncio as azmq

from RTNaBS.util import exceptionToStr
from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError
from RTNaBS.util.GUI.QAppWithAsyncioLoop import RunnableAsApp

from RTNaBS.util.pyvista import Actor
from RTNaBS.util.pyvista.plotting import BackgroundPlotter
from RTNaBS.util.pyvista.RemotePlotting import ActorRef

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@attrs.define
class ActorManager:
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
class RemotePlotManagerBase:
    _parentLayout: QtWidgets.QLayout
    _plotterArgs: list = attrs.field(factory=list)
    _plotterKwargs: dict = attrs.field(factory=dict)

    _actorManager: ActorManager = attrs.field(init=False, factory=ActorManager)
    _Plotter: tp.ClassVar
    _plotter: RemotePlotter | None = attrs.field(init=False, default=None)

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


class RemotePlotter(BackgroundPlotter):
    def __init__(self, *args, **kwargs):
        BackgroundPlotter.__init__(self, *args,
                                   **kwargs)

    def setActorUserTransform(self, actor: Actor, transform: np.ndarray):
        # convert from numpy array to vtkTransform
        t = pv._vtk.vtkTransform()
        t.SetMatrix(pv.vtkmatrix_from_array(transform))

        actor.SetUserTransform(t)


@attrs.define(kw_only=True)
class RemotePlotManager(RemotePlotManagerBase):
    _reqPort: int
    _repPort: int | None = None
    _addr: str = '127.0.0.1'
    _Plotter: tp.ClassVar = RemotePlotter
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
class RemotePlotterApp(RunnableAsApp):
    _reqPort: int
    _repPort: int | None = None
    _appName: str = 'RemotePlotter'
    _plotterKwargs: dict = attrs.field(factory=dict)

    _plotManager: RemotePlotManager = attrs.field(init=False)
    _rootWdgt: QtWidgets.QWidget = attrs.field(init=False)
    _debugTimer: QtCore.QTimer = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        wdgt = QtWidgets.QWidget()
        self._rootWdgt = wdgt
        wdgt.setLayout(QtWidgets.QVBoxLayout())
        wdgt.layout().setContentsMargins(0, 0, 0, 0)

        self._plotManager = RemotePlotManager(reqPort=self._reqPort,
                                              repPort=self._repPort,
                                              parentLayout=wdgt.layout(),
                                              plotterKwargs=self._plotterKwargs)

        self._win.setCentralWidget(wdgt)

        flags = self._win.windowFlags()
        flags |= QtCore.Qt.FramelessWindowHint
        flags |= QtCore.Qt.MSWindowsFixedSizeDialogHint
        flags |= QtCore.Qt.SubWindow
        self._win.setWindowFlags(flags)
