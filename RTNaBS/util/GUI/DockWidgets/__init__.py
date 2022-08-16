import attrs
from enum import Enum
from PyKDDockWidgetsQt6 import KDDockWidgets
from qtpy import QtWidgets, QtCore, QtGui
import typing as tp


@attrs.define(frozen=True)
class DockWidgetOptions:
    notClosable: bool = False
    notDockable: bool = False
    deleteOnClose: bool = False
    mdiNestable: bool = False

    def asOptions(self) -> KDDockWidgets.DockWidget.Option:
        option = KDDockWidgets.DockWidget.Option_None
        if self.notClosable:
            option |= KDDockWidgets.DockWidget.Option_NotClosable

        if self.notDockable:
            option |= KDDockWidgets.DockWidget.Option_NotDockable

        if self.deleteOnClose:
            option |= KDDockWidgets.DockWidget.Option_DeleteOnClose

        if self.mdiNestable:
            option |= KDDockWidgets.DockWidget.Option_MDINestable

        return option


@attrs.define(frozen=True)
class LayoutSaverOptions:
    skip: bool = False

    def asOptions(self) -> KDDockWidgets.DockWidget.LayoutSaverOption:
        option = getattr(KDDockWidgets.DockWidget.LayoutSaverOption, 'None')
        if self.skip:
            option |= KDDockWidgets.DockWidget.LayoutSaverOption.Skip
        return option


@attrs.define(frozen=True)
class MainWindowOptions:
    hasCentralFrame: bool = False
    mdi: bool = False
    hasCentralWidget: bool = False

    def asOptions(self) -> KDDockWidgets.MainWindowOption:
        option = KDDockWidgets.MainWindowOption_None
        if self.hasCentralFrame:
            option |= KDDockWidgets.MainWindowOption_HasCentralFrame
        if self.mdi:
            option |= KDDockWidgets.MainWindowOption_MDI
        if self.hasCentralWidget:
            option |= KDDockWidgets.MainWindowOption_HasCentralWidget
        return option


class DockWidgetLocation(Enum):
    NoneLocation = KDDockWidgets.Location_None
    OnLeft = KDDockWidgets.Location_OnLeft
    OnTop = KDDockWidgets.Location_OnTop
    OnRight = KDDockWidgets.Location_OnRight
    OnBottom = KDDockWidgets.Location_OnBottom


class InitialLayoutOption(KDDockWidgets.InitialOption):
    pass  # TODO


class DockWidget(KDDockWidgets.DockWidget):
    def __init__(self, uniqueName: str, options: tp.Optional[DockWidgetOptions] = None, layoutSaverOptions: tp.Optional[LayoutSaverOptions] = None):
        if options is None:
            options = DockWidgetOptions()
        if layoutSaverOptions is not None:
            raise NotImplementedError  # TODO: add support for layout saver options
        super().__init__(uniqueName, options.asOptions())


class MDIArea(KDDockWidgets.MDIArea):
    def __init__(self, parent: tp.Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)

    def addDockWidget(self, dw: DockWidget, localPt: QtCore.QPoint, addingOption: tp.Optional[InitialLayoutOption] = None):
        if addingOption is not None:
            raise NotImplementedError  # TODO

        super().addDockWidget(dw, localPt)


class MainWindow(KDDockWidgets.MainWindow):
    def __init__(self, uniqueName: str, options: tp.Optional[MainWindowOptions] = None, parent: tp.Optional[QtWidgets.QWidget] = None, flags: tp.Optional[QtCore.Qt.WindowFlags] = None):
        if options is None:
            options = MainWindowOptions(hasCentralFrame=True)

        if flags is not None:
            raise NotImplementedError  # TODO

        super().__init__(uniqueName, options.asOptions(), parent)

    def addDockWidget(self, dockWidget: DockWidget, location: DockWidgetLocation, relativeTo: tp.Optional[DockWidget] = None, initialOption: tp.Optional[InitialLayoutOption] = None):

        super().addDockWidget(dockWidget, location, relativeTo, initialOption)

    def addDockWidgetAsTab(self, dockWidget: DockWidget):
        super().addDockWidgetAsTab(dockWidget)
