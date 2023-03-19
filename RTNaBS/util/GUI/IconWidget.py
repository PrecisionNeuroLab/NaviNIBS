import typing as tp
from qtpy import QtWidgets, QtGui, QtCore


class IconWidget(QtWidgets.QLabel):
    """
    Adapted from https://stackoverflow.com/a/64279374
    """
    _icon: QtGui.QIcon
    _size: QtCore.QSize

    def __init__(self, icon: QtGui.QIcon, size: tp.Optional[QtCore.QSize] = None):
        if size is None:
            size = QtCore.QSize(16, 16)

        self._size = size
        self._icon = icon

        super().__init__()

        self.setPixmap(icon.pixmap(size))

    @property
    def icon(self):
        return self._icon

    @icon.setter
    def icon(self, newIcon: QtGui.QIcon):
        self._icon = newIcon
        self.setPixmap(self._icon.pixmap(self._size))