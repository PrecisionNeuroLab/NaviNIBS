import asyncio
import attrs
import logging
import pyvista as pv
import pyvistaqt as pvqt
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
#logger.setLevel(logging.INFO)


@attrs.define(frozen=True)
class ActorRef:
    """
    vtk Actors can't be pickled, so we use this class to pass to remote processes,
    and use ActorManager to keep track of which ref refers to which actor locally.
    """
    actorID: str


@attrs.define
class _ActorManager:
    _counter: int = 0
    _actors: dict[ActorRef, Actor] = attrs.field(factory=dict)

    def addActor(self, actor: Actor) -> str:
        self._counter += 1
        actorID = f'<Actor{self._counter}>'
        actorRef = ActorRef(actorID)
        self._actors[actorRef] = actor
        return actorID

    def getActor(self, actorRef: ActorRef) -> Actor:
        return self._actors[actorRef]


class _RemotePlotter(BackgroundPlotter):
    def __init__(self,  reqPort: int, *args, addr='127.0.0.1', **kwargs):
        self._actorManager = _ActorManager()

        self._ctx = azmq.Context()

        self._reqSock = self._ctx.socket(zmq.REQ)
        self._reqSock.connect(f'tcp://{addr}:{reqPort}')

        self._repSock = self._ctx.socket(zmq.REP)
        self._repPort = self._repSock.bind_to_random_port(f'tcp://{addr}')

        BackgroundPlotter.__init__(self, *args, **kwargs)

        self._socketLoopTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._socketLoop))

    def _callPlotMethod(self, msg):
        fn = getattr(super(), msg[0])
        args = msg[1]
        kwargs = msg[2]

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

    async def _socketLoop(self):

        await self._reqSock.send_pyobj(('repPort', self._repPort))
        resp = await self._reqSock.recv_pyobj()
        assert resp == 'ack'

        while True:
            msg = await self._repSock.recv_pyobj()
            logger.debug(f'Received msg: {msg}')
            if not isinstance(msg, tuple):
                await self._sock.send_pyobj(NotImplementedError('Unexpected message format'))

            match msg[0]:
                case 'getWinID':
                    await self._repSock.send_pyobj(self.window().winId())

                case 'showWindow':
                    self.window().show()
                    await self._repSock.send_pyobj('ack')

                case _:
                    try:
                        result = self._callPlotMethod(msg)

                    except Exception as e:
                        logger.error(f'Error calling plot method {msg[0]}: {exceptionToStr(e)}')
                        await self._repSock.send_pyobj(e)

                    else:
                        await self._repSock.send_pyobj(result)


@attrs.define(kw_only=True)
class _RemotePlotterApp(RunnableAsApp):
    _reqPort: int
    _appName: str = 'RemotePlotter'
    _plotterKwargs: dict = attrs.field(factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self._plotter = _RemotePlotter(reqPort=self._reqPort, **self._plotterKwargs)
        self._win.setCentralWidget(self._plotter)

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
    def __init__(self, **kwargs):

        QtWidgets.QWidget.__init__(self)

        import multiprocessing as mp

        ctx = zmq.Context()
        actx = azmq.Context()
        self._repSocket = actx.socket(zmq.REP)
        repPort = self._repSocket.bind_to_random_port('tcp://127.0.0.1')

        self._reqSocket = ctx.socket(zmq.REQ)
        self._areqSocket = actx.socket(zmq.REQ)
        # connect these later

        self._isReady = asyncio.Event()

        self.remoteProc = mp.Process(target=_RemotePlotterApp.createAndRun,
                                     daemon=True,
                                     kwargs=dict(reqPort=repPort, plotterKwargs=kwargs))

        self.remoteProc.start()

        self._socketLoopTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._socketLoop))

    async def _socketLoop(self):

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
        self._embedWin.setFlags(QtCore.Qt.FramelessWindowHint)
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)
        self._embedWdgt = QtWidgets.QWidget.createWindowContainer(self._embedWin, parent=self)
        layout.addWidget(self._embedWdgt)

        await self._areqSocket.send_pyobj(('showWindow',))
        resp = await self._areqSocket.recv_pyobj()
        assert resp == 'ack'

        self._isReady.set()

        while True:
            msg = await self._repSocket.recv_pyobj()
            if msg[0] == 'TODO':
                pass  # TODO: add support for any expected message
            else:
                await self._repSocket.send_pyobj(NotImplementedError('Unexpected message type'))

    def _remoteCall(self, fnStr, *args, **kwargs):
        assert self._isReady.is_set()

        self._reqSocket.send_pyobj((fnStr, args, kwargs))
        resp = self._reqSocket.recv_pyobj()
        logger.debug(f'{fnStr} response: {resp}')
        if isinstance(resp, Exception):
            raise resp
        else:
            return resp

    @property
    def isReadyEvent(self):
        return self._isReady

    def subplot(self, row: int, col: int):
        logger.debug('subplot')
        return self._remoteCall('subplot', row, col)

    def add_mesh(self, *args, **kwargs):
        logger.debug('add_mesh')
        return self._remoteCall('add_mesh', *args, **kwargs)

    def enable_depth_peeling(self, *args, **kwargs):
        logger.debug('enable_depth_peeling')
        return self._remoteCall('enable_depth_peeling', *args, **kwargs)

    def add_axes_at_origin(self, *args, **kwargs):
        logger.debug('add_axes_at_origin')
        return self._remoteCall('add_axes_at_origin', *args, **kwargs)