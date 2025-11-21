import logging

import attrs
import typing as tp
import os
from qtpy import QtWidgets, QtGui, QtCore

from NaviNIBS.util.Signaler import Signal


logger = logging.getLogger(__name__)


@attrs.define(init=False, slots=False)
class QFileSelectWidget(QtWidgets.QWidget):

    _browseMode: str  # one of ('getSaveFileName', 'getOpenFilename', 'getExistingDirectory')

    _filepath: tp.Optional[str] = None
    _showRelativeTo: tp.Optional[str] = None
    _showRelativePrefix: tp.Optional[str] = None  # if showing relative to path, can show this prefix in displayed QLineEdit to make origin of rel path clear, e.g. '[NaviNIBS]'
    _extFilters: tp.Optional[str] = None
    _browseCaption: tp.Optional[str] = None
    _placeholderText: tp.Optional[str] = None  # text to show in QLineEdit when no filepath is set

    _textWidget: QtWidgets.QLineEdit = attrs.field(init=False)
    _browseBtn: QtWidgets.QPushButton = attrs.field(init=False)

    sigFilepathChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str,)))

    def __init__(self, *args, parent: tp.Optional[QtWidgets.QWidget] = None, **kwargs):
        super().__init__(parent=parent)
        self.__attrs_init__(*args, **kwargs)

    def __attrs_post_init__(self):
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        self._textWidget = QtWidgets.QLineEdit()
        self._textWidget.setReadOnly(True)
        if self._placeholderText is not None:
            self._textWidget.setPlaceholderText(self._placeholderText)
        layout.addWidget(self._textWidget)

        self._browseBtn = QtWidgets.QPushButton('Browse')
        self._browseBtn.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        self._browseBtn.clicked.connect(lambda _: self.browse())
        layout.addWidget(self._browseBtn)

        self.sigFilepathChanged.connect(lambda _: self._updateFilepathDisplay())

        self._updateFilepathDisplay()

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

        newFilepath = os.path.normpath(newFilepath)

        logger.info('Browsed to filepath: {}'.format(newFilepath))
        self.filepath = newFilepath

    @property
    def filepath(self):
        if self._filepath is None:
            return None
        else:
            # convert to always use non-windows file separators for display
            return os.path.normpath(self._filepath)

    @filepath.setter
    def filepath(self, newFilepath: str | None):
        if self._filepath is None and newFilepath is None:
            return
        if self._filepath is not None and newFilepath is not None and \
                os.path.normpath(newFilepath) == os.path.normpath(self._filepath):
            return
        if newFilepath is not None:
            newFilepath = os.path.normpath(newFilepath)
        logger.info('Filepath changed from {} to {}'.format(self._filepath, newFilepath))
        self._filepath = newFilepath
        self.sigFilepathChanged.emit(newFilepath)

    @property
    def showRelativeTo(self):
        if self._showRelativeTo is None:
            return None
        else:
            # convert to always use non-windows file separators for display
            return self._showRelativeTo.replace('\\', '/')

    @showRelativeTo.setter
    def showRelativeTo(self, newPath: tp.Optional[str]):
        if newPath is not None:
            newPath = os.path.normpath(newPath)
        if self._showRelativeTo == newPath:
            return
        self._showRelativeTo = newPath
        self._updateFilepathDisplay()

    @property
    def showRelativePrefix(self):
        return self._showRelativePrefix

    @showRelativePrefix.setter
    def showRelativePrefix(self, newPrefix: tp.Optional[str]):
        if self._showRelativePrefix == newPrefix:
            return
        self._showRelativePrefix = newPrefix
        self._updateFilepathDisplay()

    @property
    def placeholderText(self):
        return self._placeholderText

    @placeholderText.setter
    def placeholderText(self, newPlaceholderText: tp.Optional[str]):
        if self._placeholderText == newPlaceholderText:
            return
        self._placeholderText = newPlaceholderText
        self._textWidget.setPlaceholderText(newPlaceholderText)

    def _updateFilepathDisplay(self):
        if self._filepath is None:
            displayPath = ''
        else:
            if self._showRelativeTo is not None:
                try:
                    displayPath = os.path.relpath(self.filepath, self.showRelativeTo)  # TODO: confirm working as intended
                except ValueError:
                    # if paths are on different drives on Windows, relpath raises ValueError
                    displayPath = self._filepath  # just show absolute path in this case
                else:
                    if self._showRelativePrefix is not None:
                        filesep = '\\' if '\\' in displayPath else '/'
                        displayPath = self._showRelativePrefix + filesep + displayPath
            else:
                displayPath = self._filepath
        self._textWidget.setText(displayPath)
        # TODO: scroll cursor to end of text widget (to show filename, effectively hide nested parent folders first)