from PySide6 import QtWidgets, QtGui, QtCore


class QWidgetWithCloseSignal(QtWidgets.QWidget):

    sigAboutToClose = QtCore.Signal()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.sigAboutToClose.emit()
        return super().closeEvent(event=event)


class QMainWindowWithCloseSignal(QtWidgets.QMainWindow):

    sigAboutToClose = QtCore.Signal()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.sigAboutToClose.emit()
        return super().closeEvent(event)