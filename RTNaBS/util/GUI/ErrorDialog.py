import typing as tp
import logging

logger = logging.getLogger(__name__)


def raiseErrorDialog(title: str = 'Error',
                     message: tp.Optional[str] = None,
                     exception: tp.Optional[Exception] = None,
                     breakpointOnButton: tp.Optional[str] = 'Open',
                     buttons: tp.Optional[tp.Tuple[str, ...]] = None) -> str:

    if buttons is None:
        if __debug__ and breakpointOnButton is not None:
            buttons = ('Ignore', breakpointOnButton, 'Abort')
        else:
            buttons = ('Ignore', 'Abort')

    # lazy import to only import if error occurs (since caller may not otherwise use any GUI components)
    from PySide6 import QtWidgets, QtCore
    from .. import exceptionToStr

    if message is None:
        if exception is not None:
            message = exceptionToStr(exception)
        else:
            message = 'Error'

    if QtCore.QCoreApplication.instance() is None:
        if False:
            logger.error('Requested error dialog, but Qt is not running. Raising error immediately.')
            if exception is not None:
                raise exception
            else:
                raise RuntimeError()
        else:
            logger.info('Requested error dialog, but Qt is not running. Creating Qt app to run here.')
            try:
                import pyqtgraph as pg
            except ModuleNotFoundError as e:
                logger.error('Unable to display dialog, will raise instead.\n"%s": %s' % (title, message))
                raise exception if exception is not None else RuntimeError()

            app = pg.mkQApp()
            kwargs = dict(title=title, message=message, exception=exception, buttons=buttons)
            return raiseErrorDialog(**kwargs)


    logger.error('Displaying error dialog "%s": %s' % (title, message))

    msgBox = QtWidgets.QMessageBox()
    msgBox.setWindowTitle(title)
    msgBox.setText(message)

    btns = QtWidgets.QMessageBox.NoButton | QtWidgets.QMessageBox.NoButton
    for btnKey in buttons:
        btns |= getattr(QtWidgets.QMessageBox, btnKey)  # assume keys are enum keys

    msgBox.setStandardButtons(btns)
    msgBox.setDefaultButton(getattr(QtWidgets.QMessageBox, buttons[0]))  # use first button as the default
    msgBox.setWindowState(
        (msgBox.windowState() & ~QtCore.Qt.WindowState.WindowMinimized)
        | QtCore.Qt.WindowState.WindowActive)
    ret = msgBox.exec()

    retKey = None
    for btnKey in buttons:
        if ret == getattr(QtWidgets.QMessageBox, btnKey):
            retKey = btnKey
            break

    assert retKey is not None

    if retKey == 'Abort':
        if exception is not None:
            raise exception
        else:
            raise RuntimeError('%s: %s' % (title, message))
    elif __debug__ and breakpointOnButton is not None and retKey == breakpointOnButton:
        breakpoint()
    else:
        logger.info('User selected "%s"' % retKey)
        return retKey


