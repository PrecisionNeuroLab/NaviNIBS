from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import logging
import os
import pathlib
import pyqtgraph as pg
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from RTNaBS.util.GUI.QAppWithAsyncioLoop import RunnableAsApp
from RTNaBS.util.Signaler import Signal
from RTNaBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define()
class _MainViewPanel:
    _session: tp.Optional[Session] = None
    _wdgt: QtWidgets.QWidget = attrs.field(init=False, factory=QtWidgets.QWidget)

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newVal: tp.Optional[session]):
        self._session = newVal
        self._onSessionSet()

    def _onSessionSet(self):
        pass  # to be implemented by subclass


@attrs.define()
class _Panel_CreateOrLoadSession(_MainViewPanel):

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


@attrs.define()
class _Panel_SessionInfo(_MainViewPanel):
    _wdgts: tp.Dict[str, QtWidgets.QLineEdit] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        self._wdgt.setLayout(QtWidgets.QFormLayout())

        wdgt = QtWidgets.QLineEdit()
        wdgt.textEdited.connect(lambda text, key='subjectID': self._onTextEdited(key, text))
        self._wdgts['subjectID'] = wdgt
        self._wdgt.layout().addRow('Subject ID', wdgt)
        # TODO: continue here

        wdgt = QtWidgets.QLineEdit()
        wdgt.textEdited.connect(lambda text, key='sessionID': self._onTextEdited(key, text))
        self._wdgts['sessionID'] = wdgt
        self._wdgt.layout().addRow('Session ID', wdgt)

    def _onSessionSet(self):
        if self.session is not None:
            self.session.sigInfoChanged.connect(self._onSessionInfoChanged)
        self._onSessionInfoChanged()

    def _onSessionInfoChanged(self):
        if self.session is None:
            for key in ('subjectID', 'sessionID'):
                self._wdgts[key].setText('')
        else:
            for key in ('subjectID', 'sessionID'):
                val = getattr(self.session, key)
                self._wdgts[key].setText('' if val is None else val)

    def _onTextEdited(self, key: str, text: str):
        if len(text) == 0:
            text = None
        if self.session is not None:
            logger.info('Applying edited value of {} to session: {}'.format(key, text))
            setattr(self.session, key, text)
        else:
            logger.warning('Ignoring edited value of {} since session is closed.'.format(key))


@attrs.define()
class NavigatorGUI(RunnableAsApp):
    _appName: str = 'RTNaBS Navigator GUI'

    _sesFilepath: tp.Optional[str] = None
    _inProgressBaseDir: tp.Optional[str] = None
    _session: tp.Optional[Session] = None

    _mainViewStackedWdgt: QtWidgets.QStackedWidget = attrs.field(init=False)
    _mainViewPanels: tp.Dict[str, _MainViewPanel] = attrs.field(init=False, factory=dict)
    _toolbarWdgt: QtWidgets.QToolBar = attrs.field(init=False)
    _toolbarBtnActions: tp.Dict[str, QtWidgets.QAction] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        logger.info('Initializing {}'.format(self.__class__.__name__))

        super().__attrs_post_init__()

        if self._inProgressBaseDir is None:
            self._inProgressBaseDir = os.path.join(appdirs.user_data_dir(appname='RTNaBS', appauthor=False), 'InProgressSessions')

        rootWdgt = QtWidgets.QWidget()
        rootWdgt.setLayout(QtWidgets.QVBoxLayout())
        self._win.setCentralWidget(rootWdgt)

        self._toolbarWdgt = QtWidgets.QToolBar()
        self._toolbarWdgt.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        rootWdgt.layout().addWidget(self._toolbarWdgt)

        self._mainViewStackedWdgt = QtWidgets.QStackedWidget()
        rootWdgt.layout().addWidget(self._mainViewStackedWdgt)

        def createViewPanel(key: str, panel: _MainViewPanel, icon: tp.Optional[QtGui.QIcon]=None):
            self._mainViewPanels[key] = panel
            self._mainViewStackedWdgt.addWidget(panel.wdgt)
            self._toolbarBtnActions[key] = self._toolbarWdgt.addAction(key) if icon is None else self._toolbarWdgt.addAction(icon, key)
            self._toolbarBtnActions[key].setCheckable(True)
            self._toolbarBtnActions[key].triggered.connect(lambda checked=False, key=key: self._activateView(viewKey=key))

        panel = _Panel_CreateOrLoadSession(session=self._session,
                                           inProgressBaseDir=self._inProgressBaseDir)
        createViewPanel('New / load', panel, icon=qta.icon('mdi6.folder'))
        panel.sigLoadedSession.connect(self._onSessionLoaded)
        panel.sigClosedSession.connect(self._onSessionClosed)

        createViewPanel('Session info', _Panel_SessionInfo(session=self._session), icon=qta.icon('mdi6.form-select'))

        createViewPanel('Set MRI', _MainViewPanel(session=self._session), icon=qta.icon('mdi6.image'))
        # TODO: set up MRI widget

        createViewPanel('Set head model', _MainViewPanel(session=self._session), icon=qta.icon('mdi6.head-cog-outline'))
        # TODO: set up head model widget

        createViewPanel('Set fiducials', _MainViewPanel(session=self._session), icon=qta.icon('mdi6.head-snowflake-outline'))
        # TODO: set up fiducials widget

        createViewPanel('Set transforms', _MainViewPanel(session=self._session), icon=qta.icon('mdi6.head-sync-outline'))
        # TODO: set up transforms widget

        createViewPanel('Set targets', _MainViewPanel(session=self._session), icon=qta.icon('mdi6.head-flash-outline'))
        # TODO: set up targets widget

        # set initial view widget visibility
        # TODO: default to MRI if new session, otherwise default to something else...
        self._updateEnabledToolbarBtns()
        self._activateView('New / load')

        if self._sesFilepath is not None:
            asyncio.create_task(self._loadAfterSetup(filepath=self._sesFilepath))

        if self._doRunAsApp:
            logger.debug('Showing window')
            self._win.show()

    def _onSessionLoaded(self, session: Session):
        assert session is not None
        logger.info('Loaded session {}'.format(session.filepath))
        self._session = session
        for pane in self._mainViewPanels.values():
            pane.session = session
        self._updateEnabledToolbarBtns()

    def _onSessionClosed(self, prevSession: Session):
        logger.info('Closed session {}'.format(prevSession.filepath))
        self._session = None
        for pane in self._mainViewPanels.values():
            pane.session = None
        self._updateEnabledToolbarBtns()

    @property
    def session(self):
        return self._session

    async def _loadAfterSetup(self, filepath):
        await asyncio.sleep(1.)
        self._mainViewPanels['New / load'].loadSession(sesFilepath=filepath)

    def _updateEnabledToolbarBtns(self):

        for btn in self._toolbarBtnActions.values():
            btn.setEnabled(False)

        activeKeys = []

        activeKeys.append('New / load')

        if self._session is not None:
            activeKeys += ['Session info', 'Set MRI']
            if self._session.MRI is not None:
                activeKeys += ['Set head model']
                if self._session.headModel is not None:
                    activeKeys += ['Set fiducials', 'Set transforms', 'Set targets']

        for key in activeKeys:
            self._toolbarBtnActions[key].setEnabled(True)

        if self.activeViewKey not in activeKeys:
            fallbackViews = ['New / load', 'Session info', 'Set MRI', 'Set head model']
            changeToView = ''
            while changeToView not in activeKeys:
                changeToView = fallbackViews.pop()
            self._activateView(changeToView)

    @property
    def activeViewKey(self) -> str:
        viewWdgt = self._mainViewStackedWdgt.currentWidget()
        viewKeys = [key for key, val in self._mainViewPanels.items() if val.wdgt is viewWdgt]
        assert len(viewKeys) == 1
        return viewKeys[0]

    def _activateView(self, viewKey: str):
        toolbarAction = self._toolbarBtnActions[viewKey]
        toolbarBtn = self._toolbarWdgt.widgetForAction(toolbarAction)

        prevViewKey = self.activeViewKey
        self._toolbarBtnActions[prevViewKey].setChecked(False)
        self._toolbarBtnActions[viewKey].setChecked(True)

        panel = self._mainViewPanels[viewKey]
        self._mainViewStackedWdgt.setCurrentWidget(panel.wdgt)

        logger.info('Switched to view "{}"'.format(viewKey))


if __name__ == '__main__':
    if True:  # TODO: debug, delete or set to False
        NavigatorGUI.createAndRun(sesFilepath=r'C:\Users\chris\Desktop\tmp6\TestSession1.rtnabs')
    else:
        NavigatorGUI.createAndRun()




