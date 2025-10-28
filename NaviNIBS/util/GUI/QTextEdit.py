import logging
from qtpy import QtWidgets, QtCore, QtGui

from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.GUI.Icons import getIcon


logger = logging.getLogger(__name__)


class TextEditWithDrafting(QtWidgets.QWidget):
    """
    Wraps a QTextEdit/QPlainTextEdit to provide revert/submit buttons with optional validation.
    """

    textSubmitted: Signal[str]

    def __init__(self, textEdit: QtWidgets.QTextEdit | QtWidgets.QPlainTextEdit | None = None,
                 text: str = '',
                 parent: QtWidgets.QWidget | None = None,
                 validator: QtGui.QValidator | None = None):
        super().__init__(parent)

        self.textSubmitted = Signal((str,))

        if textEdit is None:
            textEdit = QtWidgets.QPlainTextEdit()

        self._textEdit = textEdit
        self._validator = validator

        self._submitButton = QtWidgets.QPushButton(icon=getIcon('mdi6.check'), text='')
        self._revertButton = QtWidgets.QPushButton(icon=getIcon('mdi6.restore'), text='')

        buttonLayout = QtWidgets.QHBoxLayout()
        buttonLayout.addStretch()
        buttonLayout.addWidget(self._revertButton)
        buttonLayout.addWidget(self._submitButton)

        mainLayout = QtWidgets.QVBoxLayout(self)
        mainLayout.setContentsMargins(0, 0, 0, 0)
        mainLayout.addWidget(self._textEdit)
        mainLayout.addLayout(buttonLayout)

        self._submitButton.clicked.connect(self._onSubmit)
        self._revertButton.clicked.connect(self._onRevert)
        self._textEdit.textChanged.connect(self._onTextChanged)

        self._originalText = text
        self._textEdit.setPlainText(text)
        self._updateButtonStates()

    @property
    def text(self):
        return self._originalText

    @text.setter
    def text(self, newText: str):
        self._originalText = newText
        self._textEdit.setPlainText(newText)
        self._updateButtonStates()

    @property
    def draftText(self):
        return self._textEdit.toPlainText()

    def _onSubmit(self):
        text = self._textEdit.toPlainText()
        if self._validator:
            state, _, _ = self._validator.validate(text, 0)
            if state != QtGui.QValidator.Acceptable:
                return  # invalid input, do not submit
        self._originalText = text
        self._updateButtonStates()
        logger.info('About to signal textSubmitted')
        self.textSubmitted.emit(text)

    def _onRevert(self):
        self._textEdit.setPlainText(self._originalText)
        self._updateButtonStates()

    def _onTextChanged(self):
        self._updateButtonStates()

    def _updateButtonStates(self):
        currentText = self._textEdit.toPlainText()
        isModified = currentText != self._originalText
        isValid = True
        if self._validator:
            state, _, _ = self._validator.validate(currentText, 0)
            isValid = (state == QtGui.QValidator.Acceptable)
        self._submitButton.setEnabled(isModified and isValid)
        self._revertButton.setEnabled(isModified)

        self._submitButton.setVisible(isModified)
        self._revertButton.setVisible(isModified)



class AutosizingPlainTextEdit(QtWidgets.QPlainTextEdit):
    """A QPlainTextEdit that automatically resizes its height to fit its content.

    Note: This widget only adjusts its height, not its width.
    """
    def __init__(self, parent=None, minHeight: int | None = None, maxHeight: int| None = None):
        super().__init__(parent)
        self.textChanged.connect(self._updateHeight)
        self._minHeight = minHeight
        self._maxHeight = maxHeight
        self._updateHeight()

        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)

    def _updateHeight(self):
        """Update the height of the widget based on its content."""
        docHeight = self.document().size().height()
        docHeight = int(docHeight * self.fontMetrics().height())  # convert from rows to pixels
        margins = self.contentsMargins()
        newHeight = docHeight + margins.top() + margins.bottom()
        if self._maxHeight is not None:
            newHeight = min(newHeight, self._maxHeight)
        if self._minHeight is not None:
            minHeight = self._minHeight
        else:
            minHeight = self.fontMetrics().height() + margins.top() + margins.bottom()
        newHeight = max(newHeight, minHeight)
        #self.setMaximumHeight(newHeight)

    def sizeHint(self) -> QtCore.QSize:
        size = super().sizeHint()
        docHeight = self.document().size().height()
        docHeight = int(docHeight * self.fontMetrics().height())  # convert from rows to pixels
        margins = self.contentsMargins()
        newHeight = docHeight + margins.top() + margins.bottom()
        if self._maxHeight is not None:
            newHeight = min(newHeight, self._maxHeight)
        if self._minHeight is not None:
            minHeight = self._minHeight
        else:
            if True:
                minHeight = self.fontMetrics().height()
            else:
                minHeight = self.fontMetrics().height() + margins.top() + margins.bottom()
        newHeight = max(newHeight, minHeight)
        size.setHeight(newHeight)
        return size

