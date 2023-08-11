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
from pyvistaqt.plotting import BackgroundPlotter as PVQTBackgroundPlotter
from RTNaBS.util.pyvista import Actor

logger = logging.getLogger(__name__)
#logger.setLevel(logging.INFO)


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
    _plotter: _RemotePlotter | _RemotePlotterNoAsync | None = attrs.field(init=False, default=None)

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

    def _callMethod(self, fn, args, kwargs):
        # convert any obvious ActorRefs to Actors
        for iArg in range(len(args)):
            if isinstance(args[iArg], ActorRef):
                args[iArg] = self._actorManager.getActor(args[iArg])
        for key in kwargs:
            if isinstance(kwargs[key], ActorRef):
                kwargs[key] = self._actorManager.getActor(kwargs[key])

        result = fn(*args, **kwargs)

        if isinstance(result, (Actor,
                               vtkmodules.vtkRenderingAnnotation.vtkAxesActor)):
            # Actor is not pickleable, so convert to an ActorRef
            actorRef = self._actorManager.addActor(result)
            result = actorRef

        return result


class _RemotePlotterNoAsync(PVQTBackgroundPlotter):
    def __init__(self, *args, **kwargs):
        PVQTBackgroundPlotter.__init__(self, *args,
                                   **kwargs)

    def setActorUserTransform(self, actor: Actor, transform: np.ndarray):
        # convert from numpy array to vtkTransform
        t = pv._vtk.vtkTransform()
        t.SetMatrix(pv.vtkmatrix_from_array(transform))

        actor.SetUserTransform(t)


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
    _reqPort: int | mpc.PipeConnection
    _repPort: int | mpc.PipeConnection | None = None
    _addr: str = '127.0.0.1'
    _Plotter: tp.ClassVar = _RemotePlotter
    _ctx: azmq.Context = attrs.field(init=False, factory=azmq.Context)
    _reqSock: azmq.Socket | mpc.PipeConnection = attrs.field(init=False)
    _repSock: azmq.Socket | mpc.PipeConnection = attrs.field(init=False)
    _socketLoopTask: asyncio.Task = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        if not isinstance(self._reqPort, mpc.PipeConnection):
            self._reqSock = self._ctx.socket(zmq.REQ)
            self._reqSock.connect(f'tcp://{self._addr}:{self._reqPort}')

            self._repSock = self._ctx.socket(zmq.REP)
            if self._repPort is None:
                self._repPort = self._repSock.bind_to_random_port(f'tcp://{self._addr}')
            else:
                self._repSock.bind(f'tcp://{self._addr}:{self._repPort}')
        else:
            assert isinstance(self._reqPort, mpc.PipeConnection)
            self._repSock = self._repPort
            self._reqSock = self._reqPort

        self._socketLoopTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._socketLoop))

    async def _socketLoop(self):

        if isinstance(self._reqSock, mpc.PipeConnection):
            self._reqSock.send('ready')
            while not self._reqSock.poll():
                await asyncio.sleep(0.1)
            resp = self._reqSock.recv()
            assert resp == 'ack'
        else:
            await self._reqSock.send_pyobj(('repPort', self._repPort))
            resp = await self._reqSock.recv_pyobj()
            assert resp == 'ack'

        while True:
            logger.debug('Awaiting msg')
            if isinstance(self._repSock, mpc.PipeConnection):
                while not self._repSock.poll():
                    await asyncio.sleep(0.1)  # TODO: rewrite to not need to poll
                msg = self._repSock.recv()
            else:
                msg = await self._repSock.recv_pyobj()
            logger.debug(f'Received msg')
            logger.debug(f'Received msg: {msg}')

            async def sendResponse(response):
                if isinstance(self._repSock, mpc.PipeConnection):
                    self._repSock.send(response)
                else:
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

                case 'callActorMethod':
                    try:
                        assert isinstance(msg[1], ActorRef)
                        result = self._callActorMethod(msg[1], msg[2:])

                    except Exception as e:
                        logger.error(f'Error calling actor method: {exceptionToStr(e)}')
                        await sendResponse(e)

                    else:
                        await sendResponse(result)

                case _:
                    try:
                        result = self._callPlotMethod(msg)

                    except Exception as e:
                        logger.error(f'Error calling plot method {msg[0]}: {exceptionToStr(e)}')
                        await sendResponse(e)

                    else:
                        await sendResponse(result)




@attrs.define(kw_only=True)
class _RemotePlotManagerNoAsync(_RemotePlotManagerBase):
    _reqConn: mpc.PipeConnection
    _parentLayout: QtWidgets.QLayout
    _repConn: mpc.PipeConnection
    _Plotter: tp.ClassVar = PVQTBackgroundPlotter
    _connLoopTimer: QtCore.QTimer = attrs.field(init=False)
    _connLoopGenerator: tp.Generator = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._connLoopGenerator = self._connLoop()

        self._connLoopTimer = QtCore.QTimer(self._parentLayout.parentWidget())
        self._connLoopTimer.timeout.connect(lambda: next(self._connLoopGenerator))
        self._connLoopTimer.setInterval(100)
        self._connLoopTimer.start()

    def _test(self):
        self._connLoop()

    def _connLoop(self):
        self._reqConn.send('ready')
        while not self._reqConn.poll():
            yield
        resp = self._reqConn.recv()
        assert resp == 'ack'

        while True:
            logger.debug('Awaiting msg')
            while not self._repConn.poll():
                yield
            msg = self._repConn.recv()
            logger.debug(f'Received msg')
            logger.debug(f'Received msg: {msg}')

            def sendResponse(response):
                self._repConn.send(response)

            if not isinstance(msg, tuple):
                sendResponse(NotImplementedError('Unexpected message format'))
                continue

            match msg[0]:
                case 'noop':
                    sendResponse('ack')

                case 'getWinID':
                    if True:
                        win = self._parentLayout.parentWidget().window()
                    else:
                        win = self._plotter.window()
                    sendResponse(win.winId())

                case 'showWindow':
                    if True:
                        win = self._parentLayout.parentWidget().window()
                    else:
                        win = self._plotter.window()

                    if self._plotter is None:
                        # assume everything else is now ready and we should instantiate plotter
                        self.initPlotter()

                    win.show()
                    sendResponse('ack')

                case 'callActorMethod':
                    try:
                        assert isinstance(msg[1], ActorRef)
                        result = self._callActorMethod(msg[1], msg[2:])

                    except Exception as e:
                        logger.error(f'Error calling actor method: {exceptionToStr(e)}')
                        sendResponse(e)

                    else:
                        sendResponse(result)

                case _:
                    try:
                        result = self._callPlotMethod(msg)

                    except Exception as e:
                        logger.error(f'Error calling plot method {msg[0]}: {exceptionToStr(e)}')
                        sendResponse(e)

                    else:
                        sendResponse(result)



@attrs.define(kw_only=True)
class _RemotePlotterApp(RunnableAsApp):
    _reqPort: int | mpc.PipeConnection
    _repPort: int | mpc.PipeConnection | None = None
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



@attrs.define(kw_only=True)
class _RemotePlotterAppNoAsync:
    _reqPort: int | mpc.PipeConnection
    _repPort: int | mpc.PipeConnection | None = None
    _appName: str = 'RemotePlotter'
    _plotterKwargs: dict = attrs.field(factory=dict)

    _win: QtWidgets.QMainWindow = attrs.field(init=False, factory=QtWidgets.QMainWindow)
    _plotManager: _RemotePlotManagerNoAsync = attrs.field(init=False)
    _rootWdgt: QtWidgets.QWidget = attrs.field(init=False)
    _debugTimer: QtCore.QTimer = attrs.field(init=False)

    def __attrs_post_init__(self):
        wdgt = QtWidgets.QWidget()
        self._rootWdgt = wdgt
        wdgt.setLayout(QtWidgets.QVBoxLayout())
        wdgt.layout().setContentsMargins(0, 0, 0, 0)

        self._plotManager = _RemotePlotManagerNoAsync(reqConn=self._reqPort,
                                               repConn=self._repPort,
                                               parentLayout=wdgt.layout(),
                                               plotterKwargs=self._plotterKwargs)

        self._win.setCentralWidget(wdgt)

        flags = self._win.windowFlags()
        flags |= QtCore.Qt.FramelessWindowHint
        flags |= QtCore.Qt.MSWindowsFixedSizeDialogHint
        flags |= QtCore.Qt.SubWindow
        self._win.setWindowFlags(flags)

    @classmethod
    def createAndRun(cls, *args, **kwargs):
        import pyqtgraph as pg
        import sys
        app = pg.mkQApp('RemotePlotter')
        self = cls(*args, **kwargs)
        sys.exit(app.exec_())





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
    def __init__(self, **kwargs):

        QtWidgets.QWidget.__init__(self)

        doUseZMQ = True

        if doUseZMQ:
            ctx = zmq.Context()
            actx = azmq.Context()
            self._repSocket = actx.socket(zmq.REP)
            repPort = self._repSocket.bind_to_random_port('tcp://127.0.0.1')

            self._reqSocket = ctx.socket(zmq.REQ)
            self._areqSocket = actx.socket(zmq.REQ)
            # connect these later

            procKwargs = dict(reqPort=repPort, plotterKwargs=kwargs)
        else:
            reqPipeA, reqPipeB = mp.Pipe()
            repPipeA, repPipeB = mp.Pipe()

            self._repSocket = repPipeA
            self._reqSocket = reqPipeA
            self._areqSocket = None
            procKwargs = dict(reqPort=repPipeB, repPort=reqPipeB, plotterKwargs=kwargs)

        self._isReady = asyncio.Event()

        if True:
            RemotePlotterApp = _RemotePlotterApp
        else:
            RemotePlotterApp = _RemotePlotterAppNoAsync
        self.remoteProc = mp.Process(target=RemotePlotterApp.createAndRun,
                                     #daemon=True,
                                     kwargs=procKwargs)

        self.remoteProc.start()

        self._socketLoopTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._socketLoop))

    async def _socketLoop(self):

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        tempWdgt = QtWidgets.QLabel()
        tempWdgt.setText('Initializing plotter...')
        layout.addWidget(tempWdgt)

        logger.debug('Waiting for remote to start up')
        if isinstance(self._repSocket, mpc.PipeConnection):
            while not self._repSocket.poll():
                await asyncio.sleep(0.1)  # TODO: rewrite to not poll
            msg = self._repSocket.recv()
            assert msg == 'ready'
        else:
            msg = await self._repSocket.recv_pyobj()
            assert isinstance(msg, tuple)
            # first message sent should be remote's repPort
            assert msg[0] == 'repPort'
            repPort = msg[1]
            logger.debug(f'Got remote repPort: {repPort}')
        if isinstance(self._repSocket, mpc.PipeConnection):
            self._repSocket.send('ack')
        else:
            self._repSocket.send_pyobj('ack')

            self._reqSocket.connect(f'tcp://localhost:{repPort}')
            self._areqSocket.connect(f'tcp://localhost:{repPort}')

        # get win ID and reparent remote window
        if isinstance(self._reqSocket, mpc.PipeConnection):
            self._reqSocket.send(('getWinID',))
            while not self._reqSocket.poll():
                await asyncio.sleep(0.1)
            winID = self._reqSocket.recv()
        else:
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
        del tempWdgt
        layout.addWidget(self._embedWdgt)

        if isinstance(self._reqSocket, mpc.PipeConnection):
            self._reqSocket.send(('showWindow',))
            while not self._reqSocket.poll():
                await asyncio.sleep(0.1)
            resp = self._reqSocket.recv()
        else:
            await self._areqSocket.send_pyobj(('showWindow',))
            resp = await self._areqSocket.recv_pyobj()
        assert resp == 'ack'

        self._isReady.set()

        while True:
            if isinstance(self._repSocket, mpc.PipeConnection):
                while not self._repSocket.poll():
                    await asyncio.sleep(0.1)
                msg = self._repSocket.recv()
            else:
                msg = await self._repSocket.recv_pyobj()
            if msg[0] == 'TODO':
                pass  # TODO: add support for any expected message
            else:
                if isinstance(self._repSocket, mpc.PipeConnection):
                    self._repSocket.send(NotImplementedError('Unexpected message type'))
                else:
                    await self._repSocket.send_pyobj(NotImplementedError('Unexpected message type'))

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

    def _waitForResp(self, socket: mpc.PipeConnection | zmq.Socket):
        """
        Note: due to some weirdness with Qt window containering, the remote process can
        deadlock if we don't keep processing Qt events in the main process during cursor
        interaction with the remote plotter window. So make sure to keep processing events
        even while waiting for an otherwise blocking result to come back.
        """
        if isinstance(socket, mpc.PipeConnection):
            while not socket.poll():
                QtWidgets.QApplication.instance().processEvents()
                time.sleep(0.001)
            resp = socket.recv()
        else:
            while True:
                try:
                    resp = socket.recv_pyobj(flags=zmq.NOBLOCK)
                except zmq.ZMQError:
                    # no message available
                    QtWidgets.QApplication.instance().processEvents()
                    time.sleep(0.001)
                else:
                    break
        return resp

    def _remoteCall(self, fnStr, *args, **kwargs):
        logger.debug(f'Calling {fnStr}')
        assert self._isReady.is_set()

        args = list(args)

        for iArg in range(len(args)):
            if isinstance(args[iArg], RemoteActor):
                # convert from RemoteActor to ActorRef
                args[iArg] = ActorRef(actorID=args[iArg].actorID)

        if isinstance(self._reqSocket, mpc.PipeConnection):
            self._reqSocket.send((fnStr, args, kwargs))
        else:
            self._reqSocket.send_pyobj((fnStr, args, kwargs))
        logger.debug(f'Waiting for response to {fnStr}')

        resp = self._waitForResp(self._reqSocket)

        logger.debug(f'Handling response to {fnStr}')
        return self._handleResp(fnStr, resp)

    def _remoteActorCall(self, actor: RemoteActor, fnStr, *args, **kwargs):
        logger.debug(f'Calling actor.{fnStr}')
        assert self._isReady.is_set()
        actorRef = ActorRef(actorID=actor.actorID)
        if isinstance(self._reqSocket, mpc.PipeConnection):
            self._reqSocket.send(('callActorMethod', actorRef, fnStr, args, kwargs))
        else:
            self._reqSocket.send_pyobj(('callActorMethod', actorRef, fnStr, args, kwargs))
        logger.debug(f'Waiting for response to actor.{fnStr}')

        resp = self._waitForResp(self._reqSocket)

        logger.debug(f'Handling response to actor.{fnStr}')
        return self._handleResp('actor.' + fnStr, resp)

    @property
    def isReadyEvent(self):
        return self._isReady

    # def render(self):
    #     return self._remoteCall('render')

    def subplot(self, row: int, col: int):
        return self._remoteCall('subplot', row, col)

    def add_mesh(self, *args, **kwargs):
        return self._remoteCall('add_mesh', *args, **kwargs)

    def enable_depth_peeling(self, *args, **kwargs):
        return self._remoteCall('enable_depth_peeling', *args, **kwargs)

    def add_axes_at_origin(self, *args, **kwargs):
        return self._remoteCall('add_axes_at_origin', *args, **kwargs)

    def setActorUserTransform(self, actor: RemoteActor, transform: np.ndarray):
        return self._remoteCall('setActorUserTransform', ActorRef(actorID=actor.actorID), transform)

    def reset_camera(self):
        return self._remoteCall('reset_camera')

    def show_grid(self, *args, **kwargs):
        return self._remoteCall('show_grid', *args, **kwargs)

    def remove_actor(self, *args, **kwargs):
        return self._remoteCall('remove_actor', *args, **kwargs)