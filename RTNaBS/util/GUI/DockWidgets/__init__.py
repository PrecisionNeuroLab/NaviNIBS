import attrs
import logging
from enum import Enum
from PyKDDockWidgetsQt6 import KDDockWidgets
from qtpy import QtWidgets, QtCore, QtGui
import typing as tp

logger = logging.getLogger(__name__)


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
    def __init__(self, size: tp.Optional[QtCore.QSize] = None):
        # TODO: add support for InitialVisibilityOption too
        if size is None:
            super().__init__()
        else:
            super().__init__(size)

    def hasPreferredLength(self, o: QtCore.Qt.Orientation) -> bool:
        return super().hasPreferredLength(o)

    def preferredLength(self, o: QtCore.Qt.Orientation) -> int:
        return super().preferredLength(o)

    # TODO: add support for getters related to InitialVisibilityOption


class DockWidget(KDDockWidgets.DockWidget):
    def __init__(self, uniqueName: str, options: tp.Optional[DockWidgetOptions] = None, layoutSaverOptions: tp.Optional[LayoutSaverOptions] = None, title: tp.Optional[str] = None, affinities: tp.Optional[tp.List[str]] = None):
        if options is None:
            options = DockWidgetOptions()
        if layoutSaverOptions is not None:
            raise NotImplementedError  # TODO: add support for layout saver options
        super().__init__(uniqueName, options.asOptions())

        if affinities is not None:
            self.setAffinities(affinities)

        if title is not None:
            self.setTitle(title)

    def setAffinities(self, affinities: tp.List[str]):
        """
         * @brief Sets the affinity names. Dock widgets can only dock into dock widgets of the same affinity.
         *
         * By default the affinity is empty and a dock widget can dock into any main window and into any
         * floating window. Usually you won't ever need to call
         * this function, unless you have requirements where certain dock widgets can only dock into
         * certain other dock widgets and main windows. @sa MainWindowBase::setAffinities().
         *
         * Note: Call this function right after creating your dock widget, before adding to a main window and
         * before restoring any layout.
         *
         * Note: Currently you can only call this function once, to keep the code simple and avoid
         * edge cases. This will only be changed if a good use case comes up that requires changing
         * affinities multiple times.
         """
        super().setAffinities(affinities)

    def setWidget(self, widget: QtWidgets.QWidget):
        super().setWidget(widget)


class MainWindow(KDDockWidgets.MainWindow):
    def __init__(self, uniqueName: str, options: tp.Optional[MainWindowOptions] = None, parent: tp.Optional[QtWidgets.QWidget] = None, flags: tp.Optional[QtCore.Qt.WindowFlags] = None):
        if options is None:
            options = MainWindowOptions()

        if flags is not None:
            raise NotImplementedError  # TODO

        super().__init__(uniqueName, options.asOptions(), parent)

    def addDockWidget(self, dockWidget: DockWidget, location: DockWidgetLocation, relativeTo: tp.Optional[DockWidget] = None, initialOption: tp.Optional[InitialLayoutOption] = None):

        location = location.value

        if initialOption is None:
            if relativeTo is None:
                super().addDockWidget(dockWidget, location)
            else:
                super().addDockWidget(dockWidget, location, relativeTo)
        else:
            super().addDockWidget(dockWidget, location, relativeTo, initialOption)

    def addDockWidgetAsTab(self, dockWidget: DockWidget):
        super().addDockWidgetAsTab(dockWidget)

    def setAffinities(self, affinities: tp.List[str]):
        """
         * @brief Sets the affinities names. Dock widgets can only dock into main windows of the same affinity.
         *
         * By default the affinity is empty and a dock widget can dock into any main window. Usually you
         * won't ever need to call this function, unless you have requirements where certain dock widgets
         * can only dock into certain main windows. @sa DockWidgetBase::setAffinities().
         *
         * Note: Call this function right after creating your main window, before docking any dock widgets
         * into a main window and before restoring any layout.
         *
         * Note: Currently you can only call this function once, to keep the code simple and avoid
         * edge cases. This will only be changed if a good use case comes up that requires changing
         * affinities multiple times.
        """
        super().setAffinities(affinities)

    def layoutEqually(self):
        super().layoutEqually()