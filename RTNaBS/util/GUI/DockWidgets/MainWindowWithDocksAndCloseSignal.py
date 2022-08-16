from . import MainWindow
from qtpy import QtWidgets, QtCore, QtGui


class MainWindowWithDocksAndCloseSignal(MainWindow):
    sigAboutToClose = QtCore.Signal()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.sigAboutToClose.emit()
        return super().closeEvent(event)
