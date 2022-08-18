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
from RTNaBS.util import exceptionToStr
from RTNaBS.util.GUI import DockWidgets as dw
from RTNaBS.util.GUI.DockWidgets.MainWindowWithDocksAndCloseSignal import MainWindowWithDocksAndCloseSignal
from RTNaBS.util.Signaler import Signal
from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class ManageSessionPanel(MainViewPanel):

    _wdgt: MainWindowWithDocksAndCloseSignal = attrs.field(init=False)

    _inProgressBaseDir: tp.Optional[str] = None
    _saveBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _saveAsBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _closeBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _fileDW: dw.DockWidget = attrs.field(init=False)
    _fileContainer: QtWidgets.QWidget = attrs.field(init=False)
    _infoDW: dw.DockWidget = attrs.field(init=False)
    _infoContainer: QtWidgets.QWidget = attrs.field(init=False)
    _infoWdgts: tp.Dict[str, QtWidgets.QLineEdit] = attrs.field(init=False, factory=dict)

    sigLoadedSession: Signal = attrs.field(init=False, factory=lambda: Signal((Session,)))
    sigClosedSession: Signal = attrs.field(init=False, factory=lambda: Signal((Session,)))

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt = MainWindowWithDocksAndCloseSignal(
            uniqueName=self._key,
            options=dw.MainWindowOptions()
        )
        self._wdgt.setAffinities([self._key])

        title = 'File'
        cdw = dw.DockWidget(
            uniqueName=self._key + title,
            options=dw.DockWidgetOptions(notClosable=True),
            title=title,
            affinities=[self._key])
        self._fileDW = cdw
        container = QtWidgets.QWidget()
        self._fileContainer = container
        container.setLayout(QtWidgets.QVBoxLayout())
        container.setMaximumWidth(300)
        cdw.setWidget(container)
        self._wdgt.addDockWidget(cdw, dw.DockWidgetLocation.OnLeft)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-plus'), text='New session')
        btn.clicked.connect(lambda checked: self._createNewSession())
        container.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.folder-open'), text='Load session')
        btn.clicked.connect(lambda checked: self.loadSession())
        container.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-restore'), text='Recover in-progress session')
        btn.clicked.connect(lambda checked: self._recoverSession())
        container.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.clipboard-file'), text='Clone session')
        btn.clicked.connect(lambda checked: self._cloneSession())
        container.layout().addWidget(btn)

        container.layout().addSpacing(10)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.content-save'), text='Save session')
        btn.clicked.connect(lambda checked: self._saveSession())
        container.layout().addWidget(btn)
        self._saveBtn = btn

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.content-save-edit'), text='Save session as...')
        btn.clicked.connect(lambda checked: self._saveSessionAs())
        container.layout().addWidget(btn)
        self._saveAsBtn = btn

        container.layout().addSpacing(10)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-remove'), text='Close session')
        btn.clicked.connect(lambda checked: self._tryVerifyThenCloseSession())
        container.layout().addWidget(btn)
        self._closeBtn = btn

        container.layout().addStretch()

        title = 'Info'
        cdw = dw.DockWidget(
            uniqueName=self._key + title,
            options=dw.DockWidgetOptions(notClosable=True),
            title=title,
            affinities=[self._key])
        self._wdgt.addDockWidget(cdw, location=dw.DockWidgetLocation.OnRight)
        self._infoDW = cdw
        container = QtWidgets.QWidget()
        self._infoContainer = container
        cdw.setWidget(container)
        container.setLayout(QtWidgets.QFormLayout())

        wdgt = QtWidgets.QLineEdit()
        wdgt.textEdited.connect(lambda text, key='subjectID': self._onInfoTextEdited(key, text))
        self._infoWdgts['subjectID'] = wdgt
        container.layout().addRow('Subject ID', wdgt)
        # TODO: continue here

        wdgt = QtWidgets.QLineEdit()
        wdgt.textEdited.connect(lambda text, key='sessionID': self._onInfoTextEdited(key, text))
        self._infoWdgts['sessionID'] = wdgt
        container.layout().addRow('Session ID', wdgt)

        self._updateEnabledWdgts()

    def _onSessionSet(self):
        self._updateEnabledWdgts()
        if self.session is not None:
            self.session.sigInfoChanged.connect(self._onSessionInfoChanged)
        self._onSessionInfoChanged()

    def _getNewInProgressSessionDir(self) -> str:
        return os.path.join(self._inProgressBaseDir, 'RTNaBSSession_' + datetime.today().strftime('%y%m%d%H%M%S'))

    def _updateEnabledWdgts(self):
        for wdgt in (self._saveBtn, self._saveAsBtn, self._closeBtn, self._infoContainer):
            wdgt.setEnabled(self.session is not None)

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

        try:
            session = Session.loadFromFile(filepath=sesFilepath, unpackedSessionDir=self._getNewInProgressSessionDir())
        except Exception as e:
            logger.warning('Problem loading session from {}:\n{}'.format(sesFilepath, exceptionToStr(e)))
            return

        self.session = session
        try:
            self.sigLoadedSession.emit(self.session)
        except Exception as e:
            logger.error('Problem handling loaded session:\n{}'.format(exceptionToStr(e)))
            raise e

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

    def _onSessionInfoChanged(self):
        if self.session is None:
            for key in ('subjectID', 'sessionID'):
                self._infoWdgts[key].setText('')
        else:
            for key in ('subjectID', 'sessionID'):
                val = getattr(self.session, key)
                self._infoWdgts[key].setText('' if val is None else val)

    def _onInfoTextEdited(self, key: str, text: str):
        if len(text) == 0:
            text = None
        if self.session is not None:
            logger.info('Applying edited value of {} to session: {}'.format(key, text))
            setattr(self.session, key, text)
        else:
            logger.warning('Ignoring edited value of {} since session is closed.'.format(key))
