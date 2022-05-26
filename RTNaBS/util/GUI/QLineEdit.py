import logging
from qtpy import QtWidgets, QtCore, QtGui


logger = logging.getLogger(__name__)


class QLineEditWithValidationFeedback(QtWidgets.QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.textEdited.connect(self._onValidityChanged)

    def _onValidityChanged(self, newText):
        validity, _, _ = self.validator().validate(newText, self.cursorPosition())
        logger.debug('checking validity: %s' % validity)
        if validity == QtGui.QValidator.State.Acceptable:
            self.setStyleSheet(
                'QLineEdit, QPlainTextEdit { font-weight : normal; background-color : white; }')
        elif validity == QtGui.QValidator.State.Intermediate:
            self.setStyleSheet(
                'QLineEdit, QPlainTextEdit { font-weight : normal; background-color : red; }')
        elif validity == QtGui.QValidator.State.Invalid:
            self.setStyleSheet(
                'QLineEdit, QPlainTextEdit { font-weight : normal; background-color : red; }')
        else:
            raise NotImplementedError()