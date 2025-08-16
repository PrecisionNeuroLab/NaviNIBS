from __future__ import annotations

import asyncio
from contextlib import nullcontext
import logging
import typing as tp
from typing import ClassVar

import attrs
import numpy as np
import pyvista as pv
import vtkmodules.vtkRenderingAnnotation
import zmq
from qtpy import QtWidgets, QtCore
from zmq import asyncio as azmq

from NaviNIBS.util import exceptionToStr
from NaviNIBS.util import ZMQAsyncioFix
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.GUI.QAppWithAsyncioLoop import RunnableAsApp
from NaviNIBS.util.logging import createLogFileHandler
from NaviNIBS.util.pyvista import Actor
from NaviNIBS.util.pyvista.plotting import BackgroundPlotter
from NaviNIBS.util.pyvista.RemotePlotting import ActorRef

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# logger.setLevel(logging.DEBUG)


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
    _Plotter: ClassVar
    _plotter: RemotePlotter | None = attrs.field(default=None)

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

    async def _handleMsg(self, msg):
        """
        :return: response to message
        """
        if not isinstance(msg, tuple):
            raise NotImplementedError('Unexpected message format')

        match msg[0]:
            case 'noop':
                return 'ack'

            case 'quit':
                asyncio.create_task(asyncTryAndLogExceptionOnError(self._closeAfterDelay))
                return 'ack'

            case 'getWinID':
                if True:
                    win = self._parentLayout.parentWidget().window()
                else:
                    win = self._plotter.window()
                return win.winId()

            case 'showWindow':
                if True:
                    win = self._parentLayout.parentWidget().window()
                else:
                    win = self._plotter.window()

                if self._plotter is None:
                    # assume everything else is now ready and we should instantiate plotter
                    self.initPlotter()

                win.show()
                return 'ack'

            case 'plotterGet':
                assert isinstance(msg[1], str)
                assert len(msg) == 4 and len(msg[2]) == 0 and len(msg[3]) == 0
                return getattr(self._plotter, msg[1])

            case 'callPlotterMethod':
                return self._callPlotMethod(msg[1:])

            case 'callActorMethod':
                return self._callActorMethod(msg[1], msg[2:])

            case 'callActorMapperMethod':
                return self._callMapperMethod(msg[2:], msg[1])

            case 'actorMapperGet':
                assert isinstance(msg[1], ActorRef)
                assert isinstance(msg[2], str)
                assert len(msg) == 5 and len(msg[3]) == 0 and len(msg[4]) == 0
                return getattr(self._actorManager.getActor(msg[1]).GetMapper(), msg[2])

            case 'actorMapperSet':
                assert isinstance(msg[1], ActorRef)
                assert isinstance(msg[2], str)
                assert len(msg) == 5
                assert len(msg[3]) == 1
                assert len(msg[4]) == 0
                setattr(self._actorManager.getActor(msg[1]).GetMapper(), msg[2], msg[3][0])
                return None

            case 'callMapperMethod':
                return self._callMapperMethod(msg[1:], None)

            case 'mapperGet':
                assert isinstance(msg[1], str)
                assert len(msg) == 4 and len(msg[2]) == 0 and len(msg[3]) == 0
                return getattr(self._plotter.mapper, msg[1])

            case 'mapperSet':
                assert isinstance(msg[1], str)
                assert len(msg) == 4
                assert len(msg[2]) == 1
                assert len(msg[3]) == 0
                setattr(self._plotter.mapper, msg[1], msg[2][0])
                return None

            case 'cameraGet':
                assert isinstance(msg[1], str)
                assert len(msg) == 4 and len(msg[2]) == 0 and len(msg[3]) == 0
                return getattr(self._plotter.camera, msg[1])

            case 'cameraSet':
                assert isinstance(msg[1], str)
                assert len(msg) == 4
                assert len(msg[2]) == 1
                assert len(msg[3]) == 0
                setattr(self._plotter.camera, msg[1], msg[2][0])
                return None

            case 'cameraCall':
                return self._callCameraMethod(msg[1:])

            case 'queryProperty':
                assert isinstance(msg[1], str)
                self._queryProperty(msg[1])
                return None

            case _:
                raise NotImplementedError(f'Unexpected message type: {msg[0]}')

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

    def _callMapperMethod(self, msg, actor: ActorRef | None):
        if actor is not None:
            actor = self._actorManager.getActor(actor)
            mapper = actor.GetMapper()
        else:
            mapper = self._plotter.mapper  # only set by some plotting functions (e.g. add_volume)
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
        logger.debug(f'calling method {fn} {args} {kwargs}')
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
                               vtkmodules.vtkRenderingCore.vtkActor2D,
                               vtkmodules.vtkRenderingAnnotation.vtkAxesActor,
                               pv._vtk.vtkVolume)):
            # Actor is not pickleable, so convert to an ActorRef
            actorRef = self._actorManager.addActor(result)
            result = actorRef

        logger.debug(f'result of calling method {fn}: {result}')

        return result

    def _executeCallback(self, callbackKey: str, *args, **kwargs):
        raise NotImplementedError  # to be implemented by subclass

    async def _closeAfterDelay(self):
        await asyncio.sleep(0.1)
        self._parentLayout.parentWidget().window().close()


class RemotePlotterMixin:
    def setActorUserTransform(self, actor: Actor, transform: np.ndarray):
        # convert from numpy array to vtkTransform
        t = pv._vtk.vtkTransform()
        t.SetMatrix(pv.vtkmatrix_from_array(transform))

        actor.SetUserTransform(t)


class RemotePlotter(BackgroundPlotter, RemotePlotterMixin):
    def __init__(self, *args, **kwargs):
        BackgroundPlotter.__init__(self, *args, **kwargs)
        RemotePlotterMixin.__init__(self)


@attrs.define(kw_only=True)
class RemotePlotManager(RemotePlotManagerBase):
    _reqPort: int
    _repPort: int | None = None
    _pullPort: int | None = None
    _addr: str = '127.0.0.1'
    _Plotter: ClassVar = RemotePlotter
    _ctx: azmq.Context = attrs.field(init=False, factory=azmq.Context)
    _reqSock: azmq.Socket = attrs.field(init=False)
    _repSock: azmq.Socket = attrs.field(init=False)
    _pullSock: azmq.Socket = attrs.field(init=False)
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

        self._pullSock = self._ctx.socket(zmq.PULL)
        if self._pullPort is None:
            self._pullPort = self._pullSock.bind_to_random_port(f'tcp://{self._addr}')
        else:
            self._pullSock.bind(f'tcp://{self._addr}:{self._pullPort}')

        self._socketLoopTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._socketLoop))

    async def _socketLoop(self):

        async with self._reqLock:
            await self._reqSock.send_pyobj(('ports', dict(rep=self._repPort,
                                                          pull=self._pullPort)))
            resp = await self._reqSock.recv_pyobj()
            assert resp == 'ack'

        if False:
            self.initPlotter()

        poller = azmq.Poller()
        poller.register(self._pullSock, zmq.POLLIN)
        poller.register(self._repSock, zmq.POLLIN)
        while True:
            logger.debug('Awaiting msg')
            socks = dict(await poller.poll())
            plotterWasInitialized = self.plotter is not None
            with self.plotter.renderingPaused() if plotterWasInitialized else nullcontext():
                while len(socks) > 0 and (plotterWasInitialized or self.plotter is None):
                    if self._pullSock in socks:
                        # note: this ordering has the effect of emptying pull queue before checking rep
                        # queue, which is what we want to deplete all pending non-blocking requests before
                        # we respond to a blocking request (which was presumably sent later by
                        # the single client)
                        msg = await self._pullSock.recv_pyobj()
                        replyOnSock = None  # push-pull is unidirectional (nonblocking command, no return)
                    elif self._repSock in socks:
                        msg = await self._repSock.recv_pyobj()
                        replyOnSock = self._repSock
                    else:
                        raise NotImplementedError

                    logger.debug(f'Received msg: {msg}')

                    try:
                        resp = await self._handleMsg(msg)
                    except Exception as e:
                        logger.error(f'Exception while handling msg {msg}: {exceptionToStr(e)}')
                        resp = e

                    if replyOnSock is not None:
                        logger.debug(f'Sending response: {resp}')
                        await replyOnSock.send_pyobj(resp)
                    else:
                        logger.debug(f'Non-blocking request complete, dropping response: {resp}')

                    if True:
                        # wait to render until after processed all pending requests
                        socks = dict(await poller.poll(timeout=0))
                    else:
                        socks = dict()

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
    _logFilepath: str | None = None
    _plotterKwargs: dict = attrs.field(factory=dict)

    _plotManager: RemotePlotManager = attrs.field(init=False, default=None)
    _rootWdgt: QtWidgets.QWidget = attrs.field(init=False)
    _debugTimer: QtCore.QTimer = attrs.field(init=False)
    _logFileHandler: logging.FileHandler = attrs.field(init=False)

    def __attrs_post_init__(self):
        if self._logFilepath is not None:
            self._logFileHandler = createLogFileHandler(self._logFilepath)
            logging.getLogger('').addHandler(self._logFileHandler)

        logger.debug(f'Initializing {self.__class__.__name__}')
        super().__attrs_post_init__()
        wdgt = QtWidgets.QWidget()
        self._rootWdgt = wdgt
        wdgt.setLayout(QtWidgets.QVBoxLayout())
        wdgt.layout().setContentsMargins(0, 0, 0, 0)

        self._win.setCentralWidget(wdgt)

        flags = self._win.windowFlags()
        flags |= QtCore.Qt.FramelessWindowHint
        flags |= QtCore.Qt.MSWindowsFixedSizeDialogHint
        flags |= QtCore.Qt.SubWindow
        self._win.setWindowFlags(flags)

        self._initPlotManager()

    def _initPlotManager(self):
        assert self._plotManager is None
        logger.debug('Initializing RemotePlotManager')
        self._plotManager = RemotePlotManager(reqPort=self._reqPort,
                                              repPort=self._repPort,
                                              parentLayout=self._rootWdgt.layout(),
                                              plotterKwargs=self._plotterKwargs)
