"""
Adapted from https://stackoverflow.com/a/61274630/2388228
"""

from qtpy import QtWidgets, QtCore, QtGui


def preventAnnoyingScrollBehaviour(control: QtWidgets.QWidget) -> None:
    control.setFocusPolicy(QtCore.Qt.StrongFocus)
    control.installEventFilter(_MouseWheelWidgetAdjustmentGuard(control))


class _MouseWheelWidgetAdjustmentGuard(QtCore.QObject):
    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)

    def eventFilter(self, o: QtCore.QObject, e: QtCore.QEvent) -> bool:
        widget: QtWidgets.QWidget = o
        if e.type() == QtCore.QEvent.Wheel and not widget.hasFocus():
            e.ignore()
            return True
        return super().eventFilter(o, e)
