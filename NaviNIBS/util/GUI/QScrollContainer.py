import attrs
import logging
from qtpy import QtWidgets, QtCore, QtGui

from NaviNIBS.util.GUI.StyleSheets import setStyleSheetForInstanceOnly

logger = logging.getLogger(__name__)


class VerticalScrollArea(QtWidgets.QScrollArea):
    """
    Address issue with behavior of default ScrollArea when never showing horizantal scrollbar but allowing resize: child widgets can be truncated left/right.

    This class overrides the resizeEvent to ensure that the minimum width of the scroll area is always wide enough to show full width of child widgets.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

    def resizeEvent(self, event: QtGui.QResizeEvent):
        width = self.widget().minimumSizeHint().width()
        if self.verticalScrollBar().isVisible():
            width += self.verticalScrollBar().width()
        self.setMinimumWidth(width)
        super().resizeEvent(event)

    def sizeHint(self) -> QtCore.QSize:
        """
        Modify size hint behavior to prevent scrollbar showing in some situations where the widget could be given more room to grow
        """
        sz = self.widget().sizeHint()
        sz.setHeight(sz.height()+10)
        return sz


@attrs.define
class QScrollContainer:
    _allowVerticalScrolling: bool = attrs.field(default=True)
    _allowHorizontalScrolling: bool = attrs.field(default=False)

    _innerContainerLayout: QtWidgets.QLayout = attrs.field(factory=QtWidgets.QVBoxLayout)
    _innerContainer: QtWidgets.QWidget = attrs.field(factory=QtWidgets.QWidget)
    _scrollArea: QtWidgets.QScrollArea = attrs.field(init=False)

    def __attrs_post_init__(self):
        self._innerContainer.setLayout(self._innerContainerLayout)

        if self._allowVerticalScrolling and not self._allowHorizontalScrolling:
            self._scrollArea = VerticalScrollArea()
        else:
            self._scrollArea = QtWidgets.QScrollArea()

            self._scrollArea.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarAsNeeded if self._allowHorizontalScrolling
                else QtCore.Qt.ScrollBarAlwaysOff)
            self._scrollArea.setVerticalScrollBarPolicy(
                QtCore.Qt.ScrollBarAsNeeded if self._allowVerticalScrolling
                else QtCore.Qt.ScrollBarAlwaysOff)

        #setStyleSheetForInstanceOnly(self._scrollArea, 'background: transparent;')
        self._scrollArea.viewport().setAutoFillBackground(False)  # from https://stackoverflow.com/a/79537760

        self._scrollArea.setWidgetResizable(True)

        self._scrollArea.setWidget(self._innerContainer)  # must happen after innerContainer's layout is set
        #setStyleSheetForInstanceOnly(self._innerContainer, 'background-color: transparent;')
        self._scrollArea.widget().setAutoFillBackground(False)

    @property
    def scrollArea(self):
        return self._scrollArea

    @property
    def innerContainer(self):
        return self._innerContainer

    @property
    def innerContainerLayout(self):
        return self._innerContainer.layout()