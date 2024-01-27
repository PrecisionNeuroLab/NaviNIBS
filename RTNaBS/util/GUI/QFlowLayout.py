from enum import Enum
import logging
from qtpy import QtWidgets, QtCore, QtGui

logger = logging.getLogger(__name__)


class QFlowLayout(QtWidgets.QLayout):
    """
    A layout that arranges its children in a grid with a variable number of columns.
    """

    class LayoutMode(Enum):
        minimum = 0  # all items have minimum size
        equalMinimum = 1  # all items have equal size corresponding to minimum size of largest item
        equal = 2  # all items have equal size, filling available space

    def __init__(self, parent: QtWidgets.QWidget | None = None, margin: int = 0, spacing: int = -1, layoutMode: LayoutMode = LayoutMode.equal):
        super().__init__(parent)

        self.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinimumSize)

        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)

        self._itemList: list[QtWidgets.QLayoutItem] = []
        self._spacing = spacing
        self._layoutMode = layoutMode

    def __del__(self):
        self._clear()

    def addItem(self, item: QtWidgets.QLayoutItem) -> None:
        self._itemList.append(item)

    def count(self) -> int:
        return len(self._itemList)

    def itemAt(self, index: int) -> QtWidgets.QLayoutItem | None:
        if 0 <= index < len(self._itemList):
            return self._itemList[index]
        return None

    def takeAt(self, index: int) -> QtWidgets.QLayoutItem | None:
        if 0 <= index < len(self._itemList):
            return self._itemList.pop(index)
        return None

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        height = self._doLayout(QtCore.QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect: QtCore.QRect) -> None:
        super().setGeometry(rect)
        self._doLayout(rect, False)

    def sizeHint(self) -> QtCore.QSize:
        size = self.minimumSize()
        rect = self.contentsRect()
        if rect.width() > 0:
            layoutHeight = self._doLayout(rect, True)
            size.setHeight(layoutHeight)
        return size

    def minimumSize(self) -> QtCore.QSize:
        size = QtCore.QSize()

        for item in self._itemList:
            size = size.expandedTo(item.minimumSize())

        margins = self.contentsMargins()
        size += QtCore.QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

        return size

    def _clear(self) -> None:
        while self.count():
            item = self.takeAt(0)

            if item is not None:
                item.widget().deleteLater()

    def _doLayout(self, rect: QtCore.QRect, testOnly: bool) -> int:
        """
        Returns height
        """
        x = rect.x() + self.contentsMargins().left()
        y = rect.y() + self.contentsMargins().top()
        lineHeight = 0

        match self._layoutMode:
            case QFlowLayout.LayoutMode.minimum:
                for item in self._itemList:
                    wid = item.widget()
                    spaceX = self.spacing() + wid.style().layoutSpacing(QtWidgets.QSizePolicy.PushButton,
                                                                         QtWidgets.QSizePolicy.PushButton,
                                                                         QtCore.Qt.Horizontal)
                    spaceY = self.spacing() + wid.style().layoutSpacing(QtWidgets.QSizePolicy.PushButton,
                                                                         QtWidgets.QSizePolicy.PushButton,
                                                                         QtCore.Qt.Vertical)

                    nextX = x + item.sizeHint().width() + spaceX

                    if nextX - spaceX > rect.right() - self.contentsMargins().right() and lineHeight > 0:
                        x = rect.x() + self.contentsRect().left()
                        y = y + lineHeight + spaceY
                        nextX = x + item.sizeHint().width()
                        lineHeight = 0

                    if not testOnly:
                        item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), item.sizeHint()))

                    x = nextX
                    lineHeight = max(lineHeight, item.sizeHint().height())

            case QFlowLayout.LayoutMode.equalMinimum:
                maxMinWidth = 0
                maxMinHeight = 0
                maxSpaceX = 0
                maxSpaceY = 0
                for item in self._itemList:
                    wid = item.widget()
                    maxMinWidth = max(maxMinWidth, item.sizeHint().width())
                    maxMinHeight = max(maxMinHeight, item.sizeHint().height())
                    maxSpaceX = max(maxSpaceX, self.spacing() + wid.style().layoutSpacing(QtWidgets.QSizePolicy.PushButton,
                                                                            QtWidgets.QSizePolicy.PushButton,
                                                                            QtCore.Qt.Horizontal))
                    maxSpaceY = max(maxSpaceY, self.spacing() + wid.style().layoutSpacing(QtWidgets.QSizePolicy.PushButton,
                                                                                    QtWidgets.QSizePolicy.PushButton,
                                                                                    QtCore.Qt.Vertical))

                for item in self._itemList:
                    wid = item.widget()

                    nextX = x + maxMinWidth + maxSpaceX

                    if nextX - maxSpaceX > rect.right() - self.contentsMargins().right() and lineHeight > 0:
                        x = rect.x() + self.contentsRect().left()
                        y = y + lineHeight + maxSpaceY
                        nextX = x + maxMinWidth + maxSpaceX
                        lineHeight = 0

                    if not testOnly:
                        item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), QtCore.QSize(maxMinWidth, maxMinHeight)))

                    x = nextX
                    lineHeight = max(lineHeight, maxMinHeight)

            case QFlowLayout.LayoutMode.equal:
                maxMinWidth = 0
                maxMinHeight = 0
                maxSpaceX = 0
                maxSpaceY = 0
                for item in self._itemList:
                    wid = item.widget()
                    maxMinWidth = max(maxMinWidth, item.sizeHint().width())
                    maxMinHeight = max(maxMinHeight, item.sizeHint().height())
                    maxSpaceX = max(maxSpaceX, self.spacing() + wid.style().layoutSpacing(QtWidgets.QSizePolicy.PushButton,
                                                                                          QtWidgets.QSizePolicy.PushButton,
                                                                                          QtCore.Qt.Horizontal))
                    maxSpaceY = max(maxSpaceY, self.spacing() + wid.style().layoutSpacing(QtWidgets.QSizePolicy.PushButton,
                                                                                          QtWidgets.QSizePolicy.PushButton,
                                                                                          QtCore.Qt.Vertical))

                numCols = max((rect.width() - 1 - self.contentsMargins().left() - self.contentsMargins().right() - maxMinWidth), 0) // (maxMinWidth + maxSpaceX) + 1
                numRows = (len(self._itemList) - 1) // numCols + 1
                numEmptyLastRow = numCols - (len(self._itemList) % numCols)
                while numEmptyLastRow > numRows:
                    numCols -= 1
                    numEmptyLastRow = numCols - (len(self._itemList) % numCols)

                actualWidth = (rect.width() - 1 - maxSpaceX * (numCols - 1) - self.contentsMargins().left() - self.contentsMargins().right()) // numCols

                for item in self._itemList:
                    wid = item.widget()
                    nextX = x + actualWidth + maxSpaceX

                    if nextX - maxSpaceX > rect.right() - self.contentsMargins().right() and lineHeight > 0:
                        x = rect.x() + self.contentsMargins().left()
                        y = y + lineHeight + maxSpaceY
                        nextX = x + actualWidth + maxSpaceX
                        lineHeight = 0

                    if not testOnly:
                        item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), QtCore.QSize(actualWidth, maxMinHeight)))

                    x = nextX
                    lineHeight = max(lineHeight, maxMinHeight)

            case _:
                raise NotImplementedError('Invalid layout mode')

        return y + lineHeight + self.contentsMargins().bottom() - rect.y()



if __name__ == '__main__':
    import RTNaBS

    app = QtWidgets.QApplication([])
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(QtWidgets.QWidget())
    superLayout = QtWidgets.QVBoxLayout()
    win.centralWidget().setLayout(superLayout)

    for layoutMode in (
            QFlowLayout.LayoutMode.minimum,
            QFlowLayout.LayoutMode.equalMinimum,
            QFlowLayout.LayoutMode.equal,
    ):
        w = QtWidgets.QGroupBox(f'Layout mode: {layoutMode.name}')
        layout = QFlowLayout(layoutMode=layoutMode)
        w.setLayout(layout)
        superLayout.addWidget(w)
        for i in range(13):
            layout.addWidget(QtWidgets.QPushButton(f'Button {i} {"-"*i}'))

        w.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Minimum)

    w = QtWidgets.QGroupBox('QGridLayout')
    layout = QtWidgets.QGridLayout()
    w.setLayout(layout)
    superLayout.addWidget(w)
    k = 0
    for i in range(4):
        for j in range(3):
            layout.addWidget(QtWidgets.QPushButton(f'Button {k} {"-"*k}'), i, j)
            k += 1

    win.show()
    app.exec_()
