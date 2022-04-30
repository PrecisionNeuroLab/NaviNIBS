from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import logging
import os
import pathlib
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from . import MainViewPanel
from RTNaBS.util.Signaler import Signal
from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class CreateOrLoadSessionPanel(MainViewPanel):

    _inProgressBaseDir: tp.Optional[str] = None
    _saveBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _saveAsBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _closeBtn: QtWidgets.QPushButton = attrs.field(init=False)

    sigLoadedSession: Signal = attrs.field(init=False, factory=lambda: Signal((Session,)))
    sigClosedSession: Signal = attrs.field(init=False, factory=lambda: Signal((Session,)))

    def __attrs_post_init__(self):
        self._wdgt.setLayout(QtWidgets.QGridLayout())

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.content-save'), text='Save session')
        btn.clicked.connect(lambda checked: self._saveSession())
        self._wdgt.layout().addWidget(btn, 0, 1)
        self._saveBtn = btn

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.content-save-edit'), text='Save session as...')
        btn.clicked.connect(lambda checked: self._saveSessionAs())
        self._wdgt.layout().addWidget(btn, 1, 1)
        self._saveAsBtn = btn

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-remove'), text='Close session')
        btn.clicked.connect(lambda checked: self._tryVerifyThenCloseSession())
        self._wdgt.layout().addWidget(btn, 2, 1)
        self._closeBtn = btn

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-plus'), text='New session')
        btn.clicked.connect(lambda checked: self._createNewSession())
        self._wdgt.layout().addWidget(btn, 0, 0)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.folder-open'), text='Load session')
        btn.clicked.connect(lambda checked: self.loadSession())
        self._wdgt.layout().addWidget(btn, 1, 0)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-restore'), text='Recover in-progress session')
        btn.clicked.connect(lambda checked: self._recoverSession())
        self._wdgt.layout().addWidget(btn, 2, 0)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.clipboard-file'), text='Clone session')
        btn.clicked.connect(lambda checked: self._cloneSession())
        self._wdgt.layout().addWidget(btn, 3, 0)

        self._updateEnabledBtns()

    def _onSessionSet(self):
        self._updateEnabledBtns()

    def _getNewInProgressSessionDir(self) -> str:
        return os.path.join(self._inProgressBaseDir, 'RTNaBSSession_' + datetime.today().strftime('%y%m%d%H%M%S'))

    def _updateEnabledBtns(self):
        for btn in (self._saveBtn, self._saveAsBtn, self._closeBtn):
            btn.setEnabled(self.session is not None)

    def _saveSession(self):
        self.session.saveToFile()

    def _saveSessionAs(self, sesFilepath: tp.Optional[str] = None):
        if sesFilepath is None:
            prevFilepath = self.session.filepath
            sesFilepath, _ = QtWidgets.QFileDialog.getSaveFileName(self._wdgt,
                                                                   'Save session file',
                                                                   prevFilepath,
                                                                   'Session file (*.rtnabs)')
            if len(sesFilepath) == 0:
                logger.info('Browse save session cancelled')
                return
        logger.info('New session filepath: {}'.format(sesFilepath))
        self.session.filepath = sesFilepath
        self._saveSession()

    def _closeSession(self):
        closedSession = self.session
        self.session = None
        self.sigClosedSession.emit(closedSession)

    def _tryVerifyThenCloseSession(self):
        if self.session is not None:
            if self.session.compressedFileIsDirty:
                raise NotImplementedError()  # TODO: prompt user to confirm whether they want to save or discard changes to previous session
                # TODO: close session to trigger clearing of various GUI components
            self._closeSession()

    def _createNewSession(self, sesFilepath: tp.Optional[str] = None):
        self._tryVerifyThenCloseSession()

        if sesFilepath is None:
            if False:
                raise NotImplementedError()  # TODO: set to location of recent dir if available
                dir = 'todo'
            else:
                dir = str(pathlib.Path.home())
            sesFilepath, _ = QtWidgets.QFileDialog.getSaveFileName(self._wdgt,
                                                                'Create new session file',
                                                                dir,
                                                                'Session file (*.rtnabs)')
            if len(sesFilepath) == 0:
                logger.info('Browse new session cancelled')
                return
        logger.info('New session filepath: {}'.format(sesFilepath))

        self.session = Session.createNew(filepath=sesFilepath, unpackedSessionDir=self._getNewInProgressSessionDir())
        self.sigLoadedSession.emit(self.session)

    def loadSession(self, sesFilepath: tp.Optional[str] = None):
        self._tryVerifyThenCloseSession()

        if sesFilepath is None:
            if False:
                raise NotImplementedError()  # TODO: set to location of recent dir if available
                dir = 'todo'
            else:
                dir = str(pathlib.Path.home())
            sesFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._wdgt, 'Choose session to load', dir, 'Session file (*.rtnabs)')
            if len(sesFilepath) == 0:
                logger.info('Browse existing session cancelled')
                return
        logger.info('Load session filepath: {}'.format(sesFilepath))

        self.session = Session.loadFromFile(filepath=sesFilepath, unpackedSessionDir=self._getNewInProgressSessionDir())
        self.sigLoadedSession.emit(self.session)

    def _recoverSession(self, sesDataDir: tp.Optional[str] = None):
        self._tryVerifyThenCloseSession()

        if sesDataDir is None:
            dir = self._inProgressBaseDir
            sesDataDir = QtWidgets.QFileDialog.getExistingDirectory(self._wdgt, 'Choose unpacked session to load', dir)
            if len(sesDataDir) == 0:
                logger.info('Browse recover session cancelled')
                return
        logger.info('Recover session data dir: {}'.format(sesDataDir))

        self.session = Session.loadFromUnpackedDir(unpackedSessionDir=sesDataDir)
        self.sigLoadedSession.emit(self.session)

    def _cloneSession(self, fromSesFilepath: tp.Optional[str] = None, toSesFilepath: tp.Optional[str] = None):
        if fromSesFilepath is None:
            if False:
                raise NotImplementedError()  # TODO: set to location of recent dir if available
                dir = 'todo'
            else:
                dir = str(pathlib.Path.home())
            fromSesFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._wdgt, 'Choose session to clone', dir,
                                                                "Session file (*.rtnabs)")
            if len(sesFilepath) == 0:
                logger.info('Browse existing session cancelled')
                return

        logger.info('Load session filepath: {}'.format(fromSesFilepath))

        if toSesFilepath is None:
            dir, _ = os.path.split(fromSesFilepath)
            toSesFilepath, _ = QtWidgets.QFileDialog.getSaveFileName(self._wdgt, 'Create save cloned session file', dir, "Session file (*.rtnabs)")
            if len(sesFilepath) == 0:
                logger.info('Browse clone session cancelled')
                return
        logger.info('Cloned session filepath: {}'.format(toSesFilepath))

        logger.debug('Copying session from {} to {}'.format(fromSesFilepath, toSesFilepath))
        shutil.copyfile(fromSesFilepath, toSesFilepath)
        logger.debug('Done copying')

        self.loadSession(sesFilepath=toSesFilepath)