from RTNaBS.util.GUI.DockWidgets.MainWindowWithDocksAndCloseSignal import MainWindowWithDocksAndCloseSignal
from qtpy import QtWidgets, QtCore, QtGui


class DockWidgetsContainer(MainWindowWithDocksAndCloseSignal):
    """
    This is just a thin wrapper around KDDockWidgets.MainWindow, but renamed to make it more clear that it can
    be used as a widget inside another window to enable nested docking.

    Also, add an extra close signal for convenience.
    """
    pass
