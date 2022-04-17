import attr
import asyncio
import logging
from PySide6 import QtWidgets, QtGui, QtCore
import pyqtgraph as pg
import typing as tp

from .QWidgetWithCloseSignal import QMainWindowWithCloseSignal
from ..Signaler import Signal

logger = logging.getLogger(__name__)


@attr.s(auto_attribs=True, eq=False, kw_only=True)
class RunnableAsApp:
    _appName: tp.Optional[str] = None
    _appAsyncPollPeriod = 0.01
    _doRunAsApp: bool = False
    _appLogEveryNLoops: tp.Optional[int] = None

    _app: QtGui.QGuiApplication = attr.ib(init=False)
    _win: QMainWindowWithCloseSignal = attr.ib(init=False)
    _appWasClosed: bool = attr.ib(init=False, default=False)

    def __attrs_post_init__(self):
        if self._doRunAsApp:
            if self._appName is None:
                self._appName = self.__class__.__name__
            logger.info('Initializing %s' % (self._appName,))

            logger.debug('Initializing GUI window')
            self._app = pg.mkQApp(self._appName)
            self._win = QMainWindowWithCloseSignal()
            self._win.setWindowTitle(self._appName)
            self._win.sigAboutToClose.connect(self._onAppAboutToQuit)

    def _onAppAboutToQuit(self):
        logger.info('About to quit')
        self._appWasClosed = True

    @classmethod
    async def createAndRun_async(cls, *args, **kwargs):
        logger.debug('Creating %s' % (cls.__name__,))
        try:
            self = cls(*args, doRunAsApp=True, **kwargs)
        except Exception as e:
            raise e
        logger.debug('Running %s' % (cls.__name__,))
        if self._appLogEveryNLoops is None:
            counter = 1
        else:
            counter = 0
        while not self._appWasClosed:
            if counter == 0:
                logger.debug('Main loop: process Qt events')
            self._app.processEvents()
            if counter == 0:
                logger.debug('Main loop: async')
            await asyncio.sleep(self._appAsyncPollPeriod)
            if counter == 0:
                logger.debug('Main loop: end')
            # b/c doing QT event processing ourselves, for some reason the aboutToQuit signal is never emitted,
            # so check (infrequently) whether window has been closed and quit manually
            if self._appLogEveryNLoops is not None:
                counter = (counter + 1) % self._appLogEveryNLoops

    @classmethod
    def createAndRun(cls, *args, **kwargs):
        if True:
            from ..AsyncRunner import asyncioRunAndHandleExceptions
            asyncioRunAndHandleExceptions(cls.createAndRun_async, *args, **kwargs)
        else:
            asyncio.run(cls.createAndRun_async(*args, **kwargs), debug=True)


@attr.s(auto_attribs=True, eq=False)
class TestGUI(RunnableAsApp):
    _appName: str = 'Test GUI'

    def __attrs_post_init__(self):
        logger.info('Initializing TestGUI')

        super().__attrs_post_init__()

        self._wdgt = QtWidgets.QWidget()

        if self._doRunAsApp:
            self._win.setCentralWidget(self._wdgt)
            logger.info('Showing window')
            self._win.show()

    def _onAppAboutToQuit(self):
        self.cleanup()
        super()._onAppAboutToQuit()

    def cleanup(self):
        logger.info('Cleanup')


if __name__ == '__main__':
    TestGUI.createAndRun()