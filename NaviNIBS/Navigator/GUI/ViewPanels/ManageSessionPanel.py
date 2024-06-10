from __future__ import annotations

import asyncio

import attrs
from datetime import datetime
import logging
import os
import pathlib
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from NaviNIBS.Navigator.GUI.ViewPanels.MainViewPanelWithDockWidgets import MainViewPanelWithDockWidgets
from NaviNIBS.util import exceptionToStr
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.GUI.Dock import Dock, DockArea
from NaviNIBS.util.GUI.ErrorDialog import raiseErrorDialog
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.Navigator.Model.Session import Session

if tp.TYPE_CHECKING:
    from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI


logger = logging.getLogger(__name__)


@attrs.define(kw_only=True)
class ManageSessionPanel(MainViewPanelWithDockWidgets):
    _navigatorGUI: NavigatorGUI

    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.form-select'))

    _autosavePeriod: float = 60  # in sec

    _inProgressBaseDir: tp.Optional[str] = None
    _newSessionBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _saveBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _saveShortcut: QtWidgets.QShortcut = attrs.field(init=False)
    _saveToFileBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _saveToDirBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _closeBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _fileDW: Dock = attrs.field(init=False)
    _fileContainer: QtWidgets.QWidget = attrs.field(init=False)
    _infoDW: Dock = attrs.field(init=False)
    _infoContainer: QtWidgets.QWidget = attrs.field(init=False)
    _infoWdgts: tp.Dict[str, QtWidgets.QLineEdit] = attrs.field(init=False, factory=dict)
    _autosaveTask: asyncio.Task = attrs.field(init=False)

    sigAboutToFinishLoadingSession: Signal = attrs.field(init=False, factory=lambda: Signal((Session,)))
    sigLoadedSession: Signal = attrs.field(init=False, factory=lambda: Signal((Session,)))
    sigClosedSession: Signal = attrs.field(init=False, factory=lambda: Signal((Session,)))

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        title = 'File'
        dock = Dock(
            name=self._key + title,
            closable=False,
            title=title,
            affinities=[self._key])
        self._fileDW = dock
        container = QtWidgets.QWidget()
        self._fileContainer = container
        container.setLayout(QtWidgets.QVBoxLayout())
        container.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.MinimumExpanding)
        dock.addWidget(container)
        dock.setStretch(1, 10)
        self._wdgt.addDock(dock, position='left')

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-plus'), text='New session')
        self._newSessionBtn = btn
        btn.clicked.connect(lambda checked: self._createNewSession())
        container.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.folder-open'), text='Load session')
        btn.clicked.connect(lambda checked: self.loadSession())
        container.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.folder-plus-outline'), text='Augment session')
        btn.clicked.connect(lambda checked: self.augmentSession())
        container.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-restore'), text='Recover in-progress session')
        btn.clicked.connect(lambda checked: self._recoverSession())
        container.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.clipboard-file'), text='Clone session')
        btn.clicked.connect(lambda checked: self._cloneSession())
        container.layout().addWidget(btn)

        container.layout().addSpacing(10)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.content-save'), text='Save session')
        btn.clicked.connect(self._onSaveSessionBtnClicked)
        container.layout().addWidget(btn)
        self._saveBtn = btn

        self._saveShortcut = QtWidgets.QShortcut(QtGui.QKeySequence.Save, self._navigatorGUI._win, None, None, QtCore.Qt.ApplicationShortcut)
        self._saveShortcut.activated.connect(self._onSaveSessionShortcutActivated)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.content-save-edit-outline'), text='Save session to dir...')
        btn.clicked.connect(lambda checked: self._saveSessionToDir())
        container.layout().addWidget(btn)
        self._saveToDirBtn = btn

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.content-save-edit'), text='Save session to file...')
        btn.clicked.connect(lambda checked: self._saveSessionToFile())
        container.layout().addWidget(btn)
        self._saveToFileBtn = btn

        container.layout().addSpacing(10)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-remove'), text='Close session')
        btn.clicked.connect(lambda checked: self._tryVerifyThenCloseSession())
        container.layout().addWidget(btn)
        self._closeBtn = btn

        container.layout().addStretch()

        title = 'Info'
        dock = Dock(
            name=self._key + title,
            closable=False,
            title=title,
            affinities=[self._key])
        self._wdgt.addDock(dock, position='right')
        self._infoDW = dock
        container = QtWidgets.QWidget()
        self._infoContainer = container
        dock.addWidget(container)
        container.setLayout(QtWidgets.QFormLayout())

        wdgt = QtWidgets.QLineEdit()
        wdgt.setReadOnly(True)
        self._infoWdgts['filepath'] = wdgt
        container.layout().addRow('Session filepath', wdgt)

        wdgt = QtWidgets.QLineEdit()
        wdgt.editingFinished.connect(lambda key='subjectID': self._onInfoTextEdited(key))
        self._infoWdgts['subjectID'] = wdgt
        container.layout().addRow('Subject ID', wdgt)
        # TODO: continue here

        wdgt = QtWidgets.QLineEdit()
        wdgt.editingFinished.connect(lambda key='sessionID': self._onInfoTextEdited(key))
        self._infoWdgts['sessionID'] = wdgt
        container.layout().addRow('Session ID', wdgt)

        self._updateEnabledWdgts()

        self._autosaveTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._autosaveOccasionally))

    def _onSessionSet(self):
        self._updateEnabledWdgts()
        if self.session is not None:
            self.session.sigInfoChanged.connect(self._onSessionInfoChanged)
        self._onSessionInfoChanged()

    def _getNewInProgressSessionDir(self) -> str:
        return os.path.join(self._inProgressBaseDir, 'NaviNIBSSession_' + datetime.today().strftime('%y%m%d%H%M%S'))

    def _updateEnabledWdgts(self):
        for wdgt in (self._saveBtn, self._saveToFileBtn, self._saveToDirBtn, self._closeBtn, self._infoContainer):
            wdgt.setEnabled(self.session is not None)

    def _updateLayoutsBeforeSave(self):
        """
        We don't update serialized layouts on autosaves or other frequent GUI updates, but do want to update
        right before a manual session save.
        """
        self._navigatorGUI.saveLayout()

    def _onSaveSessionBtnClicked(self, checked: bool):
        # if 'alt' modifier is pressed during click, then force save all (ignoring dirty flags)
        self._updateLayoutsBeforeSave()
        if QtWidgets.QApplication.keyboardModifiers() == QtCore.Qt.AltModifier:
            self.session.saveToFile(updateDirtyOnly=False)
        else:
            self.session.saveToFile()

    def _onSaveSessionShortcutActivated(self):
        self._updateLayoutsBeforeSave()
        if QtWidgets.QApplication.keyboardModifiers() == QtCore.Qt.AltModifier:
            self.session.saveToFile(updateDirtyOnly=False)
        else:
            self.session.saveToFile()

    def _saveSessionToFile(self, sesFilepath: tp.Optional[str] = None):
        if sesFilepath is None:
            prevFilepath = self.session.filepath
            sesFilepath, _ = QtWidgets.QFileDialog.getSaveFileName(self._wdgt,
                                                                   'Save session to file',
                                                                   prevFilepath,
                                                                   'Session file (*.navinibs)')
            if len(sesFilepath) == 0:
                logger.info('Browse save session file cancelled')
                return
        logger.info('New session filepath: {}'.format(sesFilepath))
        self.session.filepath = sesFilepath
        self._updateLayoutsBeforeSave()
        self.session.saveToFile()

    def _saveSessionToDir(self, sesFilepath: tp.Optional[str] = None):
        if sesFilepath is None:
            prevFilepath = self.session.filepath
            sesFilepath = QtWidgets.QFileDialog.getExistingDirectory(self._wdgt,
                                                                   'Save session to dir',
                                                                   prevFilepath)
            if len(sesFilepath) == 0:
                logger.info('Browse save session dir cancelled')
                return
        logger.info('New session filepath: {}'.format(sesFilepath))
        self.session.filepath = sesFilepath
        self._updateLayoutsBeforeSave()
        self.session.unpackedSessionDir = sesFilepath  # this will trigger copy to new destination
        self.session.saveToFile()

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


            useFilters = False

            if useFilters:
                sesFilepath, selectedFilter = QtWidgets.QFileDialog.getSaveFileName(self._wdgt,
                                                                    'Create new session file',
                                                                    dir,
                                                                    'Session file (*.navinibs);;Session folder (*)',
                                                                    'Session folder (*)')
            else:
                sesFilepath, selectedFilter = QtWidgets.QFileDialog.getSaveFileName(self._wdgt,
                                                                                    'Create new session file',
                                                                                    dir)

            if len(sesFilepath) == 0:
                logger.info('Browse new session cancelled')
                return

            if useFilters:
                # due to some issue with QFileDialog, if ext filters are specified, then an extension is always
                # appended, even if none was specified and this empty ext matches an existing filter.
                # So strip extension manually if the empty filter was selected.
                # This has the side effect that the .navinibs filter MUST be selected to specify a .navinibs path.
                if selectedFilter == 'Session folder (*)':
                    sesFilepath = os.path.splitext(sesFilepath)[0]

        logger.info('New session filepath: {}'.format(sesFilepath))

        if sesFilepath.endswith('.navinibs'):
            unpackedSessionDir = self._getNewInProgressSessionDir()
        else:
            unpackedSessionDir = sesFilepath
        session = Session.createNew(filepath=sesFilepath, unpackedSessionDir=unpackedSessionDir)
        self.sigAboutToFinishLoadingSession.emit(session)
        self.session = session
        self.sigLoadedSession.emit(self.session)

    def loadSession(self, sesFilepath: tp.Optional[str] = None):
        self._tryVerifyThenCloseSession()

        if sesFilepath is None:
            if False:
                raise NotImplementedError()  # TODO: set to location of recent dir if available
                dir = 'todo'
            else:
                dir = str(pathlib.Path.home())
            sesFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._wdgt, 'Choose session to load', dir, 'Session file (*.navinibs); Config file (*.json)')
            if len(sesFilepath) == 0:
                logger.info('Browse existing session cancelled')
                return

        sesFilepath = os.path.normpath(sesFilepath)

        logger.info('Load session filepath: {}'.format(sesFilepath))

        if sesFilepath.endswith('.json'):
            # assume this is a config file inside a larger session dir
            sesFilepath = os.path.dirname(sesFilepath)

        try:
            if os.path.isdir(sesFilepath):
                # treat as already-unpacked session dir
                session = Session.loadFromFolder(folderpath=sesFilepath)
            else:
                # treat as compressed file
                assert os.path.isfile(sesFilepath)
                session = Session.loadFromFile(filepath=sesFilepath, unpackedSessionDir=self._getNewInProgressSessionDir())
        except Exception as e:
            raiseErrorDialog(f'Problem loading session from {sesFilepath}', exception=e)
            return

        self.sigAboutToFinishLoadingSession.emit(session)
        self.session = session
        try:
            self.sigLoadedSession.emit(self.session)
        except Exception as e:
            logger.error('Problem handling loaded session:\n{}'.format(exceptionToStr(e)))
            raise e

    def augmentSession(self, sesFilepath: tp.Optional[str] = None):
        raise NotImplementedError  # TODO

    def _recoverSession(self, sesDataDir: tp.Optional[str] = None):
        self._tryVerifyThenCloseSession()

        if sesDataDir is None:
            dir = self._inProgressBaseDir
            sesDataDir = QtWidgets.QFileDialog.getExistingDirectory(self._wdgt, 'Choose unpacked session to load', dir)
            if len(sesDataDir) == 0:
                logger.info('Browse recover session cancelled')
                return
        logger.info('Recover session data dir: {}'.format(sesDataDir))

        session = Session.loadFromUnpackedDir(unpackedSessionDir=sesDataDir)
        self.sigAboutToFinishLoadingSession.emit(session)
        self.session = session
        self.sigLoadedSession.emit(self.session)

    def _cloneSession(self, fromSesFilepath: tp.Optional[str] = None, toSesFilepath: tp.Optional[str] = None):
        if fromSesFilepath is None:
            if False:
                raise NotImplementedError()  # TODO: set to location of recent dir if available
                dir = 'todo'
            else:
                dir = str(pathlib.Path.home())
            fromSesFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._wdgt, 'Choose session to clone', dir,
                                                                "Session file (*.navinibs)")
            if len(sesFilepath) == 0:
                logger.info('Browse existing session cancelled')
                return

        fromSesFilepath = os.path.normpath(fromSesFilepath)

        logger.info('Load session filepath: {}'.format(fromSesFilepath))

        if toSesFilepath is None:
            dir, _ = os.path.split(fromSesFilepath)
            toSesFilepath, _ = QtWidgets.QFileDialog.getSaveFileName(self._wdgt, 'Create save cloned session file', dir, "Session file (*.navinibs)")
            if len(sesFilepath) == 0:
                logger.info('Browse clone session cancelled')
                return

        toSesFilepath = os.path.normpath(toSesFilepath)

        logger.info('Cloned session filepath: {}'.format(toSesFilepath))

        logger.debug('Copying session from {} to {}'.format(fromSesFilepath, toSesFilepath))
        shutil.copyfile(fromSesFilepath, toSesFilepath)
        logger.debug('Done copying')

        self.loadSession(sesFilepath=toSesFilepath)

    def _onSessionInfoChanged(self, whatChanged: tp.Optional[list[str]] = None):
        allRelevantKeys = ('filepath', 'subjectID', 'sessionID')
        if whatChanged is None:
            whatChanged = allRelevantKeys
        else:
            whatChanged = tuple(key for key in whatChanged if key in allRelevantKeys)
        if self.session is None:
            for key in whatChanged:
                self._infoWdgts[key].setText('')
        else:
            for key in whatChanged:
                val = getattr(self.session, key)
                self._infoWdgts[key].setText('' if val is None else val)

    def _onInfoTextEdited(self, key: str):
        text = self._infoWdgts[key].text()
        if len(text) == 0:
            text = None
        if self.session is not None:
            logger.info('Applying edited value of {} to session: {}'.format(key, text))
            setattr(self.session, key, text)
        else:
            logger.warning('Ignoring edited value of {} since session is closed.'.format(key))

    async def _autosave(self):
        logger.debug('Trying to autosave')
        if self.session is None:
            logger.debug('Session is None, nothing to autosave.')
        else:
            self.session.saveToUnpackedDir(asAutosave=True)
        logger.debug('Done trying to autosave')

    async def _autosaveOccasionally(self):
        while True:
            await asyncio.sleep(self._autosavePeriod)
            await self._autosave()
