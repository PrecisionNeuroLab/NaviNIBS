from __future__ import annotations
import typing as tp
import pyqtgraph.dockarea as pgd
from pyqtgraph.dockarea.Dock import DockLabel as pgdDockLabel, VerticalLabel as pgdVerticalLabel
import pyqtgraph.dockarea.DockDrop as pgdd
import pyqtgraph.dockarea.Container as pgdc
from qtpy import QtWidgets, QtGui, QtCore
import weakref

from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.GUI.StyleSheets import setStyleSheetForInstanceOnly



borderWidth = '2px'


class LabelScrollArea(QtWidgets.QScrollArea):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

    def sizeHint(self) -> QtCore.QSize:
        """
        Modify size hint behavior to prevent scrollbar showing in some situations where the widget could be given more room to grow
        """
        sz = self.widget().sizeHint()
        sz.setWidth(sz.width()+10)
        return sz


class AltTContainer(QtWidgets.QTabWidget, pgdc.TContainer):
    def __init__(self, area):
        QtWidgets.QTabWidget.__init__(self)
        pgdc.Container.__init__(self, area)

        if True:

            r = '5px'
            if self.parent() is None:
                palette = self.palette()
            else:
                palette = self.parent().palette()

            fg = '#444444'
            bg = '#dddddd'
            borderColor = '#bbbbbb'
            border = borderColor
            borderBottom = borderColor
            thisBorderWidth = '0px'

            setStyleSheetForInstanceOnly(self.tabBar(), f"""
                background-color : {bg};
                color : {fg};
                border-top-right-radius: {r};
                border-top-left-radius: {r};
                border-bottom-right-radius: 0px;
                border-bottom-left-radius: 0px;
                border-width: {thisBorderWidth};
                border-bottom: {thisBorderWidth} solid {borderBottom};
                border-top: {thisBorderWidth} solid {border};
                border-left: {thisBorderWidth} solid {border};
                border-right: {thisBorderWidth} solid {border};
                padding-left: 1px;
                padding-right: 1px;
                """, selectorSuffix='::tab')

            fg = palette.color(QtGui.QPalette.Active, QtGui.QPalette.Text).name()
            bg = '#bbbbbb'
            borderColor = '#bbbbbb'
            border = borderColor
            borderBottom = bg
            thisBorderWidth = borderWidth

            setStyleSheetForInstanceOnly(self.tabBar(), f"""
                            background-color : {bg};
                            color : {fg};
                            border-width: {thisBorderWidth};
                            border-bottom: {thisBorderWidth} solid {borderBottom};
                            border-top: {thisBorderWidth} solid {border};
                            border-left: {thisBorderWidth} solid {border};
                            border-right: {thisBorderWidth} solid {border};
                            """,
                                         selectorSuffix='::tab::selected')
        else:
            self.setStyleSheet(f"""
                QTabBar::tab:selected {{ 
                  background: #bbbbbb; 
                }}
                """)



    def _insertItem(self, item, index):
        if not isinstance(item, Dock):
            raise Exception("Tab containers may hold only docks, not other containers.")

        self.insertTab(index,
                       item,
                       item.label.icon.pixmap(),
                       item.label.text)

    def raiseDock(self, dock):
        index = self.indexOf(dock)
        self.setCurrentIndex(index)


class StackedWidget(pgdc.StackedWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.currentChanged.connect(lambda *args: self._onCurrentChanged(*args))

    def sizeHint(self) -> QtCore.QSize:
        """
        Modify size hint to return current widget size hint instead of maximum size
        """
        if self.currentWidget() is not None:
            return self.currentWidget().sizeHint()
        else:
            return super().sizeHint()

    def minimumSizeHint(self) -> QtCore.QSize:
        """
        Modify size hint to return current widget size hint instead of maximum size
        """
        if self.currentWidget() is not None:
            return self.currentWidget().minimumSizeHint()
        else:
            return super().minimumSizeHint()

    def _onCurrentChanged(self, index: int):
        if self.currentWidget() is not None:
            self.setSizePolicy(self.currentWidget().sizePolicy())
            self.adjustSize()

    def childEvent(self, ev):
        super().childEvent(ev)
        self.container.childEvent_(ev)


class TContainer(pgdc.TContainer):
    def __init__(self, area):
        QtWidgets.QWidget.__init__(self)
        pgdc.Container.__init__(self, area)
        self.layout = QtWidgets.QGridLayout()
        self.layout.setSpacing(0)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self.layout)

        self.hTabLayout = QtWidgets.QHBoxLayout()
        self.hTabBox = QtWidgets.QWidget()
        self.hTabBox.setLayout(self.hTabLayout)
        self.hTabLayout.setSpacing(2)
        self.hTabLayout.setContentsMargins(0, 0, 0, 0)

        if False:
            self.hTabBoxScroll = LabelScrollArea()
            setStyleSheetForInstanceOnly(self.hTabBoxScroll, 'background: transparent;')
            self.hTabBox.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.hTabBoxScroll.setWidgetResizable(True)
            self.hTabBoxScroll.setWidget(self.hTabBox)
            self.hTabBoxScroll.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Maximum)
            self.layout.addWidget(self.hTabBoxScroll, 0, 1)
        else:
            self.layout.addWidget(self.hTabBox, 0, 1)

        self.stack = StackedWidget(container=self)
        self.layout.addWidget(self.stack, 1, 1)
        self.stack.currentChanged.connect(self._onStackCurrentChanged)

        self.setLayout(self.layout)
        for n in ['count', 'widget', 'indexOf']:
            setattr(self, n, getattr(self.stack, n))

    def _insertItem(self, item, index):
        prevIndex = self.stack.currentIndex()
        if index <= prevIndex or prevIndex == -1:
            prevIndex += 1
        if not isinstance(item, Dock):
            raise Exception("Tab containers may hold only docks, not other containers.")
        self.stack.insertWidget(index, item)
        self.hTabLayout.insertWidget(index, item.label)
        #QtCore.QObject.connect(item.label, QtCore.SIGNAL('clicked'), self.tabClicked)
        item.label.sigClicked.connect(self.tabClicked)
        if True:
            # don't show new item by default
            item.label.setDim(True)
            self.raiseDock(self.stack.widget(prevIndex))
        else:
            self.tabClicked(item.label)

    def _onStackCurrentChanged(self, index: int):
        if self.stack.currentWidget() is not None:
            self.setSizePolicy(self.stack.currentWidget().sizePolicy())
            self.adjustSize()

    def restoreState(self, state):
        super().restoreState(state)
        self.tabClicked(self.stack.widget(state['index']).label)


class HContainer(pgdc.HContainer):
    def __init__(self, area):
        super().__init__(area)

        if False:
            self.setStyleSheet(f"""
                QSplitter::handle {{
                    background-color: '#ff0000';
                }}
                QSplitter::handle:horizontal {{
                    width: 4px;
                }}
                QSplitter::handle:vertical {{
                    height: 4px;
                }}
            """)

    def _insertItem(self, item, index):
        super()._insertItem(item, index)
        self.setCollapsible(index, False)  # don't allow collapsing


class VContainer(pgdc.VContainer):
    def __init__(self, area):
        super().__init__(area)

        if False:
            self.setStyleSheet(f"""
                QSplitter::handle {{
                    background-color: '#ff0000';
                }}
                QSplitter::handle:horizontal {{
                    width: 4px;
                }}
                QSplitter::handle:vertical {{
                    height: 4px;
                }}
            """)


    def _insertItem(self, item, index):
        super()._insertItem(item, index)
        self.setCollapsible(index, False)  # don't allow collapsing


class DockLabel(QtWidgets.QFrame):
    _icon: QtWidgets.QLabel | None = None
    _iconSrc: QtGui.QIcon | None = None
    _iconFn: tp.Callable[..., QtGui.QIcon] | None = None
    _label: QtWidgets.QLabel
    _closeButton: QtWidgets.QToolButton | None = None
    _dock: Dock
    _dim: bool = False
    _iconSize: tuple[int, int] | None = None

    _styleUpdateInProgress: bool = False

    sigClicked = QtCore.Signal(object, object)
    sigCloseClicked = QtCore.Signal()

    def __init__(self, text: str, dock: Dock, showCloseButton: bool,
                 icon: QtGui.QIcon | None = None,
                 iconFn: tp.Callable[..., QtGui.QIcon] | None = None,
                 iconSize: tuple[int, int] | None = None,
                 **kwargs):
        QtWidgets.QFrame.__init__(self)
        self._dock = dock
        self._iconSize = iconSize
        self.setLayout(QtWidgets.QHBoxLayout())
        self.layout().setContentsMargins(1, 1, 1, 1)
        self.layout().setSpacing(1)

        if icon is not None or iconFn is not None:
            self._iconSrc = icon
            self._iconFn = iconFn
            self._icon = QtWidgets.QLabel(self)
            self.layout().addWidget(self._icon)
            self._icon.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)

        self._label = QtWidgets.QLabel(text, self)
        self._label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.layout().addWidget(self._label)

        if showCloseButton:
            self._closeButton = QtWidgets.QToolButton(self)
            self._closeButton.setIcon(QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_TitleBarCloseButton))
            self._closeButton.clicked.connect(self.sigCloseClicked)
            self.layout().addWidget(self._closeButton)

        self.updateStyle()

        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)

    @property
    def dim(self):
        return self._dim

    @property
    def icon(self):
        return self._icon

    @property
    def text(self):
        return self._label.text()

    def setDim(self, d):
        if self.dim != d:
            self._dim = d
            self.updateStyle()

    @property
    def dock(self):
        return self._dock

    def setOrientation(self, o: str):
        if o == 'auto':
            return
        elif o == 'vertical':
            raise NotImplementedError
        elif o == 'horizontal':
            return
        else:
            raise NotImplementedError

    def event(self, event: QtCore.QEvent):
        if event.type() == QtCore.QEvent.Type.Polish:
            self.updateStyle()
        elif event.type() == QtCore.QEvent.Type.PaletteChange:
            if not self._styleUpdateInProgress:
                QtCore.QTimer.singleShot(0, self.updateStyle)
        elif event.type() == QtCore.QEvent.Type.ApplicationPaletteChange:
            self.updateStyle()
        return super().event(event)

    def updateStyle(self):
        self._styleUpdateInProgress = True

        if self._icon is not None:
            if self._iconFn is not None:
                icon = self._iconFn()
            else:
                icon = self._iconSrc

            iconSize = self._iconSize
            if iconSize is None:
                # calculate iconSize from font size
                iconSize = tuple(1.25 * self._label.fontMetrics().height() for i in range(2))
            self._icon.setPixmap(icon.pixmap(*iconSize))

        r = '5px'
        if self.parent() is None:
            palette = self.palette()
        else:
            palette = self.parent().palette()
        if self.dim:
            self._label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            self._label.setMinimumWidth(1)
            if palette.color(QtGui.QPalette.Base).value() > 128:
                # light theme
                fg = '#333333'
                bg = '#dddddd'
                hbg = '#cccccc'
                borderColor = '#bbbbbb'
            else:
                # dark theme
                fg = '#cccccc'
                bg = '#444444'
                hbg = '#555555'
                borderColor = '#666666'

            border = borderColor
            borderBottom = borderColor
            thisBorderWidth = '0px'
        else:
            self._label.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Maximum)
            self._label.setMinimumWidth(0)

            if palette.color(QtGui.QPalette.Base).value() > 128:
                # light theme
                fg = palette.color(QtGui.QPalette.Active, QtGui.QPalette.Text).name()
                bg = '#bbbbbb'
                borderColor = '#bbbbbb'
            else:
                # dark theme
                fg = '#eeeeee'
                bg = '#666666'
                borderColor = '#666666'

            hbg = bg
            border = borderColor
            borderBottom = bg
            thisBorderWidth = borderWidth

        self.hStyle = f"""
            background-color : {bg};
            color : {fg};
            border-top-right-radius: {r};
            border-top-left-radius: {r};
            border-bottom-right-radius: 0px;
            border-bottom-left-radius: 0px;
            border-width: {thisBorderWidth};
            border-bottom: {thisBorderWidth} solid {borderBottom};
            border-top: {thisBorderWidth} solid {border};
            border-left: {thisBorderWidth} solid {border};
            border-right: {thisBorderWidth} solid {border};
            padding-left: 1px;
            padding-right: 1px;
        """
        if False:
            # (for some reason, this causes crash when mouse hovers over the label, so disabled for now)
            setStyleSheetForInstanceOnly(self, f"""
                background-color: {hbg};
                """, selectorSuffix=':hover')
        setStyleSheetForInstanceOnly(self, self.hStyle)

        setStyleSheetForInstanceOnly(self._label, f""" 
            font-weight: {400 if self.dim else 500};
            color: {fg};
            """)
        self._styleUpdateInProgress = False

    def mousePressEvent(self, ev):
        lpos = ev.position() if hasattr(ev, 'position') else ev.localPos()
        self.pressPos = lpos
        self.mouseMoved = False
        ev.accept()

    def mouseMoveEvent(self, ev):
        if not self.mouseMoved:
            lpos = ev.position() if hasattr(ev, 'position') else ev.localPos()
            self.mouseMoved = (lpos - self.pressPos).manhattanLength() > QtWidgets.QApplication.startDragDistance()

        if self.mouseMoved and ev.buttons() == QtCore.Qt.MouseButton.LeftButton:
            self.dock.startDrag()
        ev.accept()

    def mouseReleaseEvent(self, ev):
        ev.accept()
        if not self.mouseMoved:
            self.sigClicked.emit(self, ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.LeftButton:
            self.dock.float()


class DockDrop(pgdd.DockDrop):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def dragEnterEvent(self, ev):
        src = ev.source()
        if isinstance(src, Dock):
            if isinstance(self.dndWidget, DockArea):
                existingAffinities: set = set()
                for dock in self.dndWidget.docks.values():
                    existingAffinities |= set(dock.affinities if dock.affinities is not None else [None])
            elif isinstance(self.dndWidget, Dock):
                existingAffinities: set = set(self.dndWidget.affinities if self.dndWidget.affinities is not None else [None])
            else:
                raise NotImplementedError

            if len(existingAffinities) == 0:
                doesMatch = True

            else:
                if src.affinities is None:
                    doesMatch = None in existingAffinities
                else:
                    doesMatch = len(existingAffinities & set(src.affinities)) > 0

            if doesMatch:
                ev.accept()
            else:
                ev.ignore()
        else:
            ev.ignore()


class Dock(pgd.Dock):
    _affinities: list[str] | None = None

    _styleUpdateInProgress: bool = False

    sigFocused: Signal
    """
    This signal must be emitted by slot connected to root focusObjectChanged to function.
    """
    sigShown: Signal[()]
    sigHidden: Signal[()]
    sigResized: Signal[()]

    # noinspection PyMissingConstructor
    def __init__(self, name, title: str = None, area=None, size=(10, 10), widget=None, hideTitle=False, autoOrientation=False, closable=False, affinities: list[str] | None = None,
                 icon: QtGui.QIcon | None = None,
                 iconFn: tp.Callable[..., QtGui.QIcon] | None = None):
        # completely override parent class init to specify different DockDrop class

        QtWidgets.QWidget.__init__(self)
        self.dockdrop = DockDrop(self)
        self._container = None
        self._name = name
        self.area = area
        if title is None:
            title = self._name
        self.label = (
            DockLabel(title, self, closable, icon=icon, iconFn=iconFn))
        if closable:
            self.label.sigCloseClicked.connect(self.close)
        self.labelHidden = False
        self.moveLabel = True  ## If false, the dock is no longer allowed to move the label.
        self.autoOrient = autoOrientation
        self.orientation = 'horizontal'
        # self.label.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
        self.topLayout = QtWidgets.QGridLayout()
        self.topLayout.setContentsMargins(0, 0, 0, 0)
        self.topLayout.setSpacing(0)
        self.setLayout(self.topLayout)
        self.topLayout.addWidget(self.label, 0, 1)
        self.widgetArea = QtWidgets.QWidget()
        self.topLayout.addWidget(self.widgetArea, 1, 1)
        self.layout = QtWidgets.QGridLayout()
        self.layout.setContentsMargins(2, 2, 2, 2)
        self.layout.setSpacing(0)
        self.widgetArea.setLayout(self.layout)
        #self.widgetArea.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.widgets = []
        self.currentRow = 0
        # self.titlePos = 'top'
        self.dockdrop.raiseOverlay()

        self.setStretch(*size)

        if widget is not None:
            self.addWidget(widget)

        if hideTitle:
            self.hideTitleBar()

        if affinities is not None:
            self._affinities = affinities

        self.sigFocused = Signal()
        self.sigShown = Signal()
        self.sigHidden = Signal()
        self.sigResized = Signal()

        self.updateStyle()

    def event(self, event: QtCore.QEvent):
        if event.type() == QtCore.QEvent.Type.Polish:
            self.updateStyle()
        elif event.type() == QtCore.QEvent.Type.PaletteChange:
            if not self._styleUpdateInProgress:
                QtCore.QTimer.singleShot(0, self.updateStyle)
        elif event.type() == QtCore.QEvent.Type.ApplicationPaletteChange:
            self.updateStyle()
        return super().event(event)

    def updateStyle(self):
        self._styleUpdateInProgress = True

        if self.parent() is None:
            palette = self.palette()
        else:
            palette = self.parent().palette()
        if palette.color(QtGui.QPalette.Base).value() > 128:
            # light theme
            borderColor = '#bbbbbb'
        else:
            # dark theme
            borderColor = '#666666'

        self.hStyle = f"""
        Dock > QWidget {{
            border: {borderWidth} solid {borderColor};
            border-radius: 5px;
            border-top-left-radius: 0px;
            border-top-right-radius: 0px;
            border-top-width: {borderWidth};
        }}"""
        self.vStyle = f"""
        Dock > QWidget {{
            border: {borderWidth} solid {borderColor};
            border-radius: 5px;
            border-top-left-radius: 0px;
            border-bottom-left-radius: 0px;
            border-left-width: 0px;
        }}"""
        self.nStyle = """
        Dock > QWidget {
            border: 2px solid #000;
            border-radius: 5px;
        }"""
        self.dragStyle = """
        Dock > QWidget {
            border: 4px solid #00F;
            border-radius: 5px;
        }"""
        self.setAutoFillBackground(False)
        self.widgetArea.setStyleSheet(self.hStyle)

        super().updateStyle()

        self._styleUpdateInProgress = False

    def setEnabled(self, doEnable: bool):
        self.label.setEnabled(doEnable)
        super().setEnabled(doEnable)

    def implements(self, name=None):
        if name is None:
            return ['dock', 'dockWithAffinities']
        else:
            return name in ('dock', 'dockWithAffinities')

    @property
    def affinities(self):
        return self._affinities

    def dragEnterEvent(self, *args):
        self.dockdrop.dragEnterEvent(*args)

    def dragMoveEvent(self, *args):
        self.dockdrop.dragMoveEvent(*args)

    def dragLeaveEvent(self, *args):
        self.dockdrop.dragLeaveEvent(*args)

    def dropEvent(self, *args):
        self.dockdrop.dropEvent(*args)

    def raiseDock(self):
        if self.container() is not None and self.container().type() == 'tab':
            super().raiseDock()

    def hideEvent(self, *args):
        super().hideEvent(*args)
        self.sigHidden.emit()

    def showEvent(self, *args):
        super().showEvent(*args)
        self.sigShown.emit()

    def resizeEvent(self, *args):
        super().resizeEvent(*args)
        self.sigResized.emit()


class DockArea(pgd.DockArea):
    # noinspection PyMissingConstructor

    _affinities: list[str] | None = None

    def __init__(self, parent=None, temporary=False, home=None, affinities: list[str] | None = None):
        # completely override parent class init to specify different DockDrop class

        pgdc.Container.__init__(self, self)
        QtWidgets.QWidget.__init__(self, parent=parent)
        self.dockdrop = DockDrop(self)
        self.dockdrop.removeAllowedArea('center')
        self.layout = QtWidgets.QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.setLayout(self.layout)
        self.docks = weakref.WeakValueDictionary()
        self.topContainer = None
        self.dockdrop.raiseOverlay()
        self.temporary = temporary
        self.tempAreas = []
        self.home = home
        self._affinities = affinities

    @property
    def affinities(self):
        return self._affinities

    def addDock(self, dock: Dock, position='bottom',
                relativeTo: Dock | None = None):
        if self._affinities is not None:
            assert dock.affinities is not None
            assert any(x in self._affinities for x in dock.affinities)

        super().addDock(dock, position=position, relativeTo=relativeTo)
        if dock.affinities is not None:
            for affinity in dock.affinities:
                if affinity in self.docks:
                    self.moveDock(dock, 'above', self.docks[affinity])
                    break

    def dragEnterEvent(self, *args):
        self.dockdrop.dragEnterEvent(*args)

    def dragMoveEvent(self, *args):
        self.dockdrop.dragMoveEvent(*args)

    def dragLeaveEvent(self, *args):
        self.dockdrop.dragLeaveEvent(*args)

    def dropEvent(self, *args):
        self.dockdrop.dropEvent(*args)

    def makeContainer(self, typ):
        if typ == 'vertical':
            new = VContainer(self)
        elif typ == 'horizontal':
            new = HContainer(self)
        elif typ == 'tab':
            if True:
                new = TContainer(self)
            else:
                new = AltTContainer(self)
        else:
            raise ValueError("typ must be one of 'vertical', 'horizontal', or 'tab'")
        return new

    def buildFromState(self, state, docks, root, depth=0, missing='error'):
        # override parent to disable apoptosing that can cause issues in some situations

        typ, contents, state = state
        pfx = "  " * depth
        if typ == 'dock':
            try:
                obj = docks[contents]
                del docks[contents]
            except KeyError:
                if missing == 'error':
                    raise Exception('Cannot restore dock state; no dock with name "%s"' % contents)
                elif missing == 'create':
                    obj = Dock(name=contents)
                elif missing == 'ignore':
                    return
                else:
                    raise ValueError('"missing" argument must be one of "error", "create", or "ignore".')

        else:
            obj = self.makeContainer(typ)

        root.insert(obj, 'after')
        # print pfx+"Add:", obj, " -> ", root

        if typ != 'dock':
            for o in contents:
                self.buildFromState(o, docks, obj, depth + 1, missing=missing)
            if False:
                # remove this container if possible. (there are valid situations when a restore will
                # generate empty containers, such as when using missing='ignore')
                obj.apoptose(propagate=False)
            obj.restoreState(state)  ## this has to be done later?

    def restoreState(self, state, missing='error', extraPosition='bottom', extraNeighbor=None):
        """
        Similar to superclass, but allows specifying neighbor for extra relative position
        """
        ## 1) make dict of all docks and list of existing containers
        containers, docks = self.findAll()
        oldTemps = self.tempAreas[:]
        # print "found docks:", docks

        ## 2) create container structure, move docks into new containers
        if state['main'] is not None:
            self.buildFromState(state['main'], docks, self, missing=missing)

        ## 3) create floating areas, populate
        for s in state['float']:
            a = self.addTempArea()
            a.buildFromState(s[0]['main'], docks, a, missing=missing)
            a.win.setGeometry(*s[1])
            a.apoptose()  # ask temp area to close itself if it is empty

        ## 4) Add any remaining docks to a float
        for d in docks.values():
            if extraPosition == 'float':
                assert extraNeighbor is None
                a = self.addTempArea()
                a.addDock(d, 'below')
            else:
                self.moveDock(d, extraPosition, extraNeighbor)

        # print "\nKill old containers:"
        ## 5) kill old containers
        for c in containers:
            c.close()
        for a in oldTemps:
            a.apoptose()


