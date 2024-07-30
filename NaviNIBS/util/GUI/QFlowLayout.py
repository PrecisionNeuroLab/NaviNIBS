from enum import Enum
import logging
from qtpy import QtWidgets, QtCore, QtGui

from NaviNIBS.util.Signaler import Signal


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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

        self._prevWidth: int | None = None
        self._prevHeight: int | None = None

        self.sigLayoutChanged: Signal[()] = Signal()

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

    def expandingDirections(self):
        return QtCore.Qt.Orientations()

    def setGeometry(self, rect: QtCore.QRect) -> None:
        super().setGeometry(rect)
        height = self._doLayout(rect, False)
        if self._prevHeight != height:
            self._prevHeight = height
            self.update()
            # self.invalidate()
            self.sigLayoutChanged.emit()

    def sizeHint(self) -> QtCore.QSize:
        size = self.minimumSize()
        rect = self.geometry()
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

        if True:
            width = self.contentsRect().width() + margins.left() + margins.right()
            if width <= 0 and self._prevWidth is not None:
                width = self._prevWidth
            size.setHeight(self.heightForWidth(width))

        return size

    def clear(self):
        self._clear()

    def _clear(self) -> None:
        while self.count():
            item = self.takeAt(0)

            if item is not None:
                if item.widget() is not None:
                    item.widget().deleteLater()

    def _doLayout(self, rect: QtCore.QRect, testOnly: bool) -> int:
        """
        Returns height
        """

        if not testOnly and rect.width() > 0:
            self._prevWidth = rect.width()

        x = rect.x() + self.contentsMargins().left()
        y = rect.y() + self.contentsMargins().top()
        lineHeight = 0

        match self._layoutMode:
            case QFlowLayout.LayoutMode.minimum:
                maxSpaceY = 0
                for item in self._itemList:
                    wid = item.widget()
                    spaceX = self.spacing() + wid.style().layoutSpacing(QtWidgets.QSizePolicy.PushButton,
                                                                          QtWidgets.QSizePolicy.PushButton,
                                                                         QtCore.Qt.Horizontal)
                    spaceY = self.spacing() + wid.style().layoutSpacing(QtWidgets.QSizePolicy.PushButton,
                                                                         QtWidgets.QSizePolicy.PushButton,
                                                                         QtCore.Qt.Vertical)
                    maxSpaceY = max(maxSpaceY, spaceY)

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

            case QFlowLayout.LayoutMode.equal | QFlowLayout.LayoutMode.equalMinimum:
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

                if rect.width() == 0 and testOnly:
                    # special case: being tested without assigned size
                    return self.contentsMargins().top() + maxMinHeight + self.contentsMargins().bottom()

                try:
                    numCols = max((rect.width() - 1 - self.contentsMargins().left() - self.contentsMargins().right() - maxMinWidth), 0) // (maxMinWidth + maxSpaceX) + 1
                except ZeroDivisionError:
                    numCols = 1
                numRows = (len(self._itemList) - 1) // numCols + 1
                numEmptyLastRow = numCols * numRows - len(self._itemList)
                while numCols > 1 and numEmptyLastRow >= numRows:
                    numCols -= 1
                    numRows = (len(self._itemList) - 1) // numCols + 1
                    numEmptyLastRow = numCols * numRows - len(self._itemList)

                if not testOnly and self._layoutMode == QFlowLayout.LayoutMode.equalMinimum:
                    actualWidth = maxMinWidth
                else:
                    actualWidth = (rect.width() - 1 - maxSpaceX * (numCols - 1) - self.contentsMargins().left() - self.contentsMargins().right()) // numCols
                logger.debug(f'actualWidth: {actualWidth}')
                if actualWidth < 0:
                    logger.error('negative width')

                numInRow = 0
                numActualRows = 1
                for item in self._itemList:
                    wid = item.widget()
                    nextX = x + actualWidth + maxSpaceX

                    numInRow += 1

                    if numInRow > numCols or (nextX - maxSpaceX > rect.right() - self.contentsMargins().right() + 1 and lineHeight > 0):
                        x = rect.x() + self.contentsMargins().left()
                        y = y + lineHeight + maxSpaceY
                        nextX = x + actualWidth + maxSpaceX
                        lineHeight = 0
                        numInRow = 1
                        numActualRows += 1

                    if not testOnly:
                        item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), QtCore.QSize(actualWidth, maxMinHeight)))

                    x = nextX
                    lineHeight = max(lineHeight, maxMinHeight)

                logger.debug(f'numActualRows: {numActualRows}')

            case _:
                raise NotImplementedError('Invalid layout mode')

        logger.debug(f'lineHeight: {lineHeight}')

        if rect.width() < 0 and self._layoutMode != QFlowLayout.LayoutMode.equal:
            # calculated y is not meaningful
            return rect.y() + self.contentsMargins().top() + lineHeight + maxSpaceY + self.contentsMargins().bottom() - rect.y()
        else:
            height = y + lineHeight + self.contentsMargins().bottom() - rect.y()

        logger.debug(f'height: {height}')

        return height



if __name__ == '__main__':
    import NaviNIBS

    app = QtWidgets.QApplication([])
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(QtWidgets.QWidget())
    superLayout = QtWidgets.QVBoxLayout()
    win.centralWidget().setLayout(superLayout)

    for layoutMode in (
            QFlowLayout.LayoutMode.minimum,
            QFlowLayout.LayoutMode.equal,
            QFlowLayout.LayoutMode.equalMinimum,
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

    superLayout.addStretch()

    win.show()

    win.adjustSize()

    app.exec_()
