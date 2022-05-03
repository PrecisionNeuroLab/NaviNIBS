import logging

import attrs
import typing as tp
import os
from qtpy import QtWidgets, QtGui, QtCore

from RTNaBS.util.Signaler import Signal


logger = logging.getLogger(__name__)


@attrs.define(init=False, slots=False)
class QFileSelectWidget(QtWidgets.QWidget):

    _browseMode: str

    _filepath: tp.Optional[str] = None
    _showRelativeTo: tp.Optional[str] = None
    _extFilters: tp.Optional[str] = None
    _browseCaption: tp.Optional[str] = None

    _textWidget: QtWidgets.QLineEdit = attrs.field(init=False)
    _browseBtn: QtWidgets.QPushButton = attrs.field(init=False)

    sigFilepathChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str,)))

    def __init__(self, *args, parent: tp.Optional[QtWidgets.QWidget] = None, **kwargs):
        super().__init__(parent=parent)
        self.__attrs_init__(*args, **kwargs)

    def __attrs_post_init__(self):
        layout = QtWidgets.QHBoxLayout()
        self.setLayout(layout)

        self._textWidget = QtWidgets.QLineEdit()
        self._textWidget.setReadOnly(True)
        layout.addWidget(self._textWidget)

        self._browseBtn = QtWidgets.QPushButton('Browse')
        self._browseBtn.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        self._browseBtn.clicked.connect(lambda _: self.browse())
        layout.addWidget(self._browseBtn)

        self.sigFilepathChanged.connect(lambda _: self._updateFilepathDisplay())

    def browse(self):
        if self._browseCaption is None:
            browseCaption = ''  # TODO: do mode-dependent auto caption (e.g. 'Select save path')
        else:
            browseCaption = self._browseCaption

        if self._extFilters is None:
            extFilters = ''  # TODO: check if necessary
        else:
            extFilters = self._extFilters

        prevFilepath = self._filepath
        if prevFilepath is None and self._showRelativeTo is not None:
            prevFilepath = self._showRelativeTo

        logger.info('Showing browse dialog')
        if self._browseMode == 'getSaveFileName':
            newFilepath, _ = QtWidgets.QFileDialog.getSaveFileName(self,
                                                                   browseCaption,
                                                                   prevFilepath,
                                                                   extFilters)
        elif self._browseMode == 'getOpenFilename':
            newFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self,
                                                                   browseCaption,
                                                                   prevFilepath,
                                                                   extFilters)
        elif self._browseMode == 'getExistingDirectory':
            assert self._extFilters is None
            newFilepath, _ = QtWidgets.QFileDialog.getExistingDirectory(self,
                                                                   browseCaption,
                                                                   prevFilepath)
        else:
            raise NotImplementedError('Unexpected browseMode: {}'.format(self._browseMode))

        if len(newFilepath) == 0:
            logger.info('Browse cancelled')
            return

        logger.info('Browsed to filepath: {}'.format(newFilepath))
        self.filepath = newFilepath

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, newFilepath: str):
        if newFilepath == self._filepath:
            return
        logger.info('Filepath changed from {} to {}'.format(self._filepath, newFilepath))
        self._filepath = newFilepath
        self.sigFilepathChanged.emit(newFilepath)

    @property
    def showRelativeTo(self):
        return self._showRelativeTo

    @showRelativeTo.setter
    def showRelativeTo(self, newPath: tp.Optional[str]):
        if self._showRelativeTo == newPath:
            return
        self._showRelativeTo = newPath
        self._updateFilepathDisplay()

    def _updateFilepathDisplay(self):
        if self._filepath is None:
            displayPath = ''
        else:
            if self._showRelativeTo is not None:
                displayPath = os.path.relpath(self._filepath, self._showRelativeTo)  # TODO: confirm working as intended
            else:
                displayPath = self._filepath
        self._textWidget.setText(displayPath)
        # TODO: scroll cursor to end of text widget (to show filename, effectively hide nested parent folders first)