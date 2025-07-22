import logging

import attrs
import numpy as np
import pytransform3d.rotations as ptr
from qtpy import QtWidgets, QtCore, QtGui

from NaviNIBS.util.GUI.Icons import getIcon
from NaviNIBS.util.numpy import array_equalish
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import composeTransform, applyTransform, concatenateTransforms, stringToTransform


logger = logging.getLogger(__name__)


@attrs.define(init=False, slots=False)
class SpatialTransformDisplayWidget(QtWidgets.QWidget):
    """
    Show a 4x4 matrix as a 4x4 grid of labels, with an optional
    edit button that opens a dialog with a separate edit widget.
    """

    _transform: np.ndarray | None = attrs.field(default=None)
    _transformLabel: str | None = None
    _doShowEditButton: bool = attrs.field(default=False)
    _maxDisplayPrecision: int = 3

    _noneLabel: QtWidgets.QLabel = attrs.field(init=False, factory=lambda: QtWidgets.QLabel('None'))
    _valueLabels: list[list[QtWidgets.QLabel]] = attrs.field(init=False, factory=lambda: [[None] * 4 for _ in range(4)])
    _gridContainer: QtWidgets.QWidget = attrs.field(init=False, factory=QtWidgets.QWidget)
    _editDlg: QtWidgets.QDialog | None = attrs.field(init=False, default=None)

    sigTransformChanged: Signal[()] = attrs.field(factory=Signal, init=False)

    def __init__(self, *args, parent: QtWidgets.QWidget | None = None, **kwargs):
        super().__init__(parent=parent)
        self.__attrs_init__(*args, **kwargs)

    def __attrs_post_init__(self):
        rootLayout = QtWidgets.QHBoxLayout()
        rootLayout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(rootLayout)

        self._noneLabel.setVisible(self._transform is None)
        rootLayout.addWidget(self._noneLabel)

        gridLayout = QtWidgets.QGridLayout()
        self._gridContainer.setLayout(gridLayout)
        self._gridContainer.setVisible(self._transform is not None)
        gridLayout.setContentsMargins(0, 0, 0, 0)
        rootLayout.addWidget(self._gridContainer)

        gridLayout.setVerticalSpacing(1)
        gridLayout.setHorizontalSpacing(8)

        for i in range(4):
            for j in range(4):
                label = QtWidgets.QLabel()
                label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
                gridLayout.addWidget(label, i, j)
                self._valueLabels[i][j] = label

        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Maximum, QtWidgets.QSizePolicy.Policy.Maximum)

        self._refreshDisplay()

        if self._doShowEditButton:
            editButton = QtWidgets.QPushButton(icon=getIcon('mdi.pencil'), text='')
            editButton.setToolTip('Edit transform...')
            editButton.clicked.connect(self._onEditButtonClicked)
            rootLayout.addWidget(editButton)

    def _onEditButtonClicked(self, *args):
        if self._editDlg is not None and self._editDlg.isVisible():
            self._editDlg.raise_()
            return

        dlg = QtWidgets.QDialog(self)
        dlg.setModal(True)
        dlg.setWindowTitle(f'Edit {self._transformLabel or "transform"}')
        dlg.setWindowModality(QtGui.Qt.WindowModality.WindowModal)
        dlg.setLayout(QtWidgets.QVBoxLayout())

        wdgt = SpatialTransformEditWidget(transform=self._transform)
        wdgt.sigTransformChanged.connect(lambda: setattr(self, 'transform', wdgt.transform))
        dlg.layout().addWidget(wdgt)

        dlg.show()

        self._editDlg = dlg

    @property
    def transform(self) -> np.ndarray | None:
        return self._transform

    @transform.setter
    def transform(self, value: np.ndarray | None):
        if array_equalish(value, self._transform):
            return

        logger.info(f'Setting {self._transformLabel or "transform"} to {value}')
        self._transform = value
        self._refreshDisplay()
        self.sigTransformChanged.emit()

    def _refreshDisplay(self):
        if self._transform is None:
            self._gridContainer.setVisible(False)
            self._noneLabel.setVisible(True)
            for i in range(4):
                for j in range(4):
                    self._valueLabels[i][j].setText('')
        else:
            assert isinstance(self._transform, np.ndarray) and self._transform.shape == (4, 4), \
                'Transform must be a 4x4 numpy array or None.'

            self._gridContainer.setVisible(True)
            self._noneLabel.setVisible(False)

            # show same precision for all values
            # but round if all can be shown with less precision
            for prec in range(self._maxDisplayPrecision):
                values = self._transform.round(prec)
                if np.all(np.isclose(self._transform, values)):
                    break
            else:
                prec = self._maxDisplayPrecision
                values = self._transform.round(prec)

            formatStr = f'.{prec}f'

            for i in range(4):
                for j in range(4):
                    self._valueLabels[i][j].setText(f'{values[i, j]:{formatStr}}')
        self.sigTransformChanged.emit()


@attrs.define(init=False, slots=False)
class SpatialTransformEditWidget(QtWidgets.QWidget):
    """
    A widget to edit a 4x4 transformation matrix.
    It should be used in a dialog or similar.
    """

    _transform: np.ndarray | None = attrs.field(default=None)

    sigTransformChanged: Signal = attrs.field(factory=Signal, init=False)

    _textField: QtWidgets.QTextEdit = attrs.field(init=False, factory=QtWidgets.QTextEdit)
    _rotateControlsContainer: QtWidgets.QWidget = attrs.field(init=False, factory=QtWidgets.QWidget)

    def __init__(self, *args, parent: QtWidgets.QWidget | None = None, **kwargs):
        super().__init__(parent=parent)
        self.__attrs_init__(*args, **kwargs)

    def __attrs_post_init__(self):
        rootLayout = QtWidgets.QVBoxLayout()
        rootLayout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(rootLayout)

        self._textField.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
        self._textField.setAcceptRichText(False)
        self._textField.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self._textField.textChanged.connect(self._onTextFieldChanged)
        self._textField.setMinimumWidth(500)  # TODO: make these dynamic and font-size dependent rather than hardcoded
        self._textField.setMaximumHeight(100)
        rootLayout.addWidget(self._textField)
        self.sigTransformChanged.connect(self._refreshDisplay)

        formOuterContainer = QtWidgets.QWidget()
        formOuterContainer.setLayout(QtWidgets.QHBoxLayout())
        formOuterContainer.layout().setContentsMargins(0, 0, 0, 0)
        rootLayout.addWidget(formOuterContainer)

        formContainer = QtWidgets.QWidget()
        formLayout = QtWidgets.QFormLayout()
        formContainer.setLayout(formLayout)

        formOuterContainer.layout().addWidget(formContainer)
        formOuterContainer.layout().addStretch()

        # TODO: implement undo/redo stacks and buttons

        btn = QtWidgets.QPushButton(icon=getIcon('mdi6.alpha-i-box-outline'), text='')
        formLayout.addRow('Reset to identity', btn)
        btn.clicked.connect(lambda: setattr(self, 'transform', np.eye(4)))

        # grid of xyz rotate buttons
        gridLayout = QtWidgets.QGridLayout()
        gridLayout.setContentsMargins(0, 0, 0, 0)
        self._rotateControlsContainer.setLayout(gridLayout)
        formLayout.addRow('Rotate 90°', self._rotateControlsContainer)

        for i, axis in enumerate(['x', 'y', 'z']):
            label = QtWidgets.QLabel(axis.upper())
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            gridLayout.addWidget(label, i, 0)
            for j, direction in enumerate(['-', '+']):
                button = QtWidgets.QPushButton(
                    icon=getIcon(f"mdi6.rotate-{'left' if direction == '-' else 'right'}",
                                  color='black'),
                    text='')
                button.setToolTip(f'Rotate around {axis} axis by {direction}90 degrees')
                button.clicked.connect(lambda _, a=axis, d=direction: self._onRotateButtonClicked(a, d))
                gridLayout.addWidget(button, i, j+1)

        self._refreshDisplay()

    @property
    def transform(self):
        return self._transform

    @transform.setter
    def transform(self, value: np.ndarray | None):
        if array_equalish(value, self._transform):
            return

        logger.info(f'Setting transform to {value}')

        self._transform = value
        self.sigTransformChanged.emit()

    def _refreshDisplay(self):
        if self._transform is None:
            text = ''
        else:
            with np.printoptions(
                suppress=True,
            ):
                text = f'{self._transform}'
        if self._textField.toPlainText() != text:
            self._textField.setPlainText(text)
            # TODO: restore cursor position if possible

    def _onTextFieldChanged(self):
        text = self._textField.toPlainText().strip()
        if len(text) == 0:
            self.transform = None
            self._textField.setStyleSheet(
                'QTextEdit {}')
            return

        try:
            # try to parse the text as a numpy array
            newTransform = stringToTransform(text)
        except ValueError as e:
            logger.warning(f'Failed to parse transform from text: {e}')
            self._textField.setStyleSheet(
                'QTextEdit { background-color : red; }')
        else:
            self._textField.setStyleSheet(
                'QTextEdit {}')
            self.transform = newTransform

    def _onRotateButtonClicked(self, axis: str, direction: str):
        transform = np.eye(4)

        match direction:
            case '+':
                angle = np.pi / 2
            case '-':
                angle = -np.pi / 2
            case _:
                raise ValueError(f'Invalid direction: {direction}')

        transform[:3, :3] = ptr.active_matrix_from_angle('xyz'.index(axis), angle)

        if self._transform is not None:
            self.transform = concatenateTransforms([self._transform, transform])
        else:
            self.transform = transform


if __name__ == '__main__':
    import sys
    app = QtWidgets.QApplication(sys.argv)

    mainWidget = QtWidgets.QWidget()
    mainWidget.setLayout(QtWidgets.QVBoxLayout())
    mainWindow = QtWidgets.QMainWindow()
    mainWindow.setCentralWidget(mainWidget)

    transform = composeTransform(ptr.active_matrix_from_angle(0, np.pi/2))
    logger.info(f'Transform:\n{transform}')
    w = SpatialTransformDisplayWidget(doShowEditButton=True, transform=transform)
    mainWidget.layout().addWidget(w)

    transform = composeTransform(ptr.active_matrix_from_angle(0, np.pi/3))
    logger.info(f'Transform:\n{transform}')
    w = SpatialTransformDisplayWidget(doShowEditButton=False, transform=transform)
    mainWidget.layout().addWidget(w)

    mainWindow.show()

    sys.exit(app.exec())