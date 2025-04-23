import attr
import asyncio
import logging
from PySide6 import QtWidgets, QtGui, QtCore
import pyqtgraph as pg
import typing as tp

from .QWidgetWithCloseSignal import QMainWindowWithCloseSignal
from ..Signaler import Signal


from NaviNIBS.util import exceptionToStr
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError

logger = logging.getLogger(__name__)


@attr.s(auto_attribs=True, eq=False, kw_only=True)
class RunnableAsApp:
    _appName: tp.Optional[str] = None
    _appAsyncPollPeriod = 0.01
    _doRunAsApp: bool = False
    _appLogEveryNLoops: tp.Optional[int] = None
    _theme: str = 'auto'  # auto, light, or dark

    _prevSetTheme: str = attr.ib(init=False, default=None)
    _app: QtWidgets.QApplication = attr.ib(init=False)
    _appIconPath: str | None = attr.ib(init=False, default=None)
    _Win: tp.Callable[..., QMainWindowWithCloseSignal] = attr.ib(default=QMainWindowWithCloseSignal)
    """
    Can specify Win to point to a different MainWindow class (e.g. for docking support)
    """
    _win: QMainWindowWithCloseSignal = attr.ib(init=False, default=None)
    _appIsClosing: bool = attr.ib(init=False, default=False)

    def __attrs_post_init__(self):
        if self._doRunAsApp:
            if self._appName is None:
                self._appName = self.__class__.__name__
            logger.info('Initializing %s' % (self._appName,))

            logger.debug('Initializing GUI window')
            self._app = pg.mkQApp(self._appName)

            if self._appIconPath is not None:

                import platform
                isWin = platform.system() == 'Windows'
                if isWin:
                    # workaround to show icon in Windows task bar
                    # based on https://stackoverflow.com/questions/1551605/how-to-set-applications-taskbar-icon-in-windows-7/1552105#1552105
                    import ctypes
                    myappid = f"NaviNIBS.{self._appName.replace(' ', '')}.subproduct.version"  # arbitrary string
                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

                self._app.setWindowIcon(QtGui.QIcon(self._appIconPath))

            theme = self._theme
            self._theme = None
            self.theme = theme  # trigger setter

            self._win = self._Win()
            self._win.setWindowTitle(self._appName)
            self._win.sigAboutToClose.connect(self._onAppAboutToQuit)

    @property
    def theme(self):
        return self._theme

    @theme.setter
    def theme(self, theme):
        if self._theme == theme:
            return
        self._theme = theme
        if theme.lower() == 'auto':
            import darkdetect
            if darkdetect.isDark():
                theme = 'dark'
            else:
                theme = 'light'
        import qtawesome as qta
        match theme.lower():
            case 'light':
                if self._prevSetTheme is None or self._prevSetTheme == 'light':
                    # no change needed, avoid calling qta.light since it also changes app style
                    pass
                else:
                    qta.light(self._app)
            case 'dark':
                qta.dark(self._app)
            case _:
                raise ValueError(f'Unknown theme: {theme}')

        self._prevSetTheme = theme.lower()

    def _onAppAboutToQuit(self):
        logger.info('About to quit')
        self._appIsClosing = True

    @property
    def appIsClosing(self):
        return self._appIsClosing

    async def _runLoop(self):
        logger.debug('Running %s' % (self.__class__.__name__,))
        if self._appLogEveryNLoops is None:
            counter = 1
        else:
            counter = 0
        while not self._appIsClosing:
            if counter == 0:
                logger.debug('Main loop: process Qt events')
            try:
                self._app.processEvents()
            except Exception as e:
                logger.error(f'Exception while processing Qt events: {exceptionToStr(e)}')
                raise e
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
    def createAndRunAsTask(cls, *args, **kwargs):
        logger.debug('Creating %s' % (cls.__name__,))
        self = cls(*args, doRunAsApp=True, **kwargs)
        asyncio.create_task(asyncTryAndLogExceptionOnError(self._runLoop))
        return self

    @classmethod
    async def createAndRun_async(cls, *args, **kwargs):
        logger.debug('Creating %s' % (cls.__name__,))
        try:
            self = cls(*args, doRunAsApp=True, **kwargs)
        except Exception as e:
            raise e

        await self._runLoop()

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