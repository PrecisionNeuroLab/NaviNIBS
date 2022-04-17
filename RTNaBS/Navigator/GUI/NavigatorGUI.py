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
    _parent: NavigatorGUI
    _wdgt: QtWidgets.QWidget = attrs.field(init=False, factory=QtWidgets.QWidget)

    @property
    def wdgt(self):
        return self._wdgt


@attrs.define()
class _Panel_CreateOrLoadSession(_MainViewPanel):

    _saveBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _saveAsBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _closeBtn: QtWidgets.QPushButton = attrs.field(init=False)

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

        self._parent.sigLoadedSession.connect(lambda session: self._updateEnabledBtns())
        self._parent.sigClosedSession.connect(lambda session: self._updateEnabledBtns())

        self._updateEnabledBtns()

    def _getNewInProgressSessionDir(self) -> str:
        return os.path.join(self._parent._inProgressBaseDir, 'RTNaBSSession_' + datetime.today().strftime(
                    '%y%m%d%H%M%S'))

    def _updateEnabledBtns(self):
        for btn in (self._saveBtn, self._saveAsBtn, self._closeBtn):
            btn.setEnabled(self._parent._session is not None)

    def _saveSession(self):
        self._parent._session.saveToFile()

    def _saveSessionAs(self, sesFilepath: tp.Optional[str] = None):
        if sesFilepath is None:
            prevFilepath = self._parent._session.filepath
            sesFilepath, _ = QtWidgets.QFileDialog.getSaveFileName(self._parent._win,
                                                                'Save session file',
                                                                prevFilepath,
                                                                'Session file (*.rtnabs)')
            if len(sesFilepath) == 0:
                logger.info('Browse save session cancelled')
                return
        logger.info('New session filepath: {}'.format(sesFilepath))
        self._parent._session.filepath = sesFilepath
        self._saveSession()

    def _closeSession(self):
        closedSession = self._parent._session
        self._parent._session = None
        self._parent.sigClosedSession.emit(closedSession)

    def _tryVerifyThenCloseSession(self):
        if self._parent._session is not None:
            if self._parent._session.compressedFileIsDirty:
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
            sesFilepath, _ = QtWidgets.QFileDialog.getSaveFileName(self._parent._win,
                                                                'Create new session file',
                                                                dir,
                                                                'Session file (*.rtnabs)')
            if len(sesFilepath) == 0:
                logger.info('Browse new session cancelled')
                return
        logger.info('New session filepath: {}'.format(sesFilepath))

        self._parent._session = Session.createNew(filepath=sesFilepath, unpackedSessionDir=self._getNewInProgressSessionDir())
        self._parent.sigLoadedSession.emit(self._parent._session)

    def loadSession(self, sesFilepath: tp.Optional[str] = None):
        self._tryVerifyThenCloseSession()

        if sesFilepath is None:
            if False:
                raise NotImplementedError()  # TODO: set to location of recent dir if available
                dir = 'todo'
            else:
                dir = str(pathlib.Path.home())
            sesFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._parent._win, 'Choose session to load', dir, 'Session file (*.rtnabs)')
            if len(sesFilepath) == 0:
                logger.info('Browse existing session cancelled')
                return
        logger.info('Load session filepath: {}'.format(sesFilepath))

        self._parent._session = Session.loadFromFile(filepath=sesFilepath, unpackedSessionDir=self._getNewInProgressSessionDir())
        self._parent.sigLoadedSession.emit(self._parent._session)

    def _recoverSession(self, sesDataDir: tp.Optional[str] = None):
        self._tryVerifyThenCloseSession()

        if sesDataDir is None:
            dir = self._parent._inProgressBaseDir
            sesDataDir = QtWidgets.QFileDialog.getExistingDirectory(self._parent._win, 'Choose unpacked session to load', dir)
            if len(sesDataDir) == 0:
                logger.info('Browse recover session cancelled')
                return
        logger.info('Recover session data dir: {}'.format(sesDataDir))

        self._parent._session = Session.loadFromUnpackedDir(unpackedSessionDir=sesDataDir)
        self._parent.sigLoadedSession.emit(self._parent._session)

    def _cloneSession(self, fromSesFilepath: tp.Optional[str] = None, toSesFilepath: tp.Optional[str] = None):
        if fromSesFilepath is None:
            if False:
                raise NotImplementedError()  # TODO: set to location of recent dir if available
                dir = 'todo'
            else:
                dir = str(pathlib.Path.home())
            fromSesFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._parent._win, 'Choose session to clone', dir,
                                                                "Session file (*.rtnabs)")
            if len(sesFilepath) == 0:
                logger.info('Browse existing session cancelled')
                return

        logger.info('Load session filepath: {}'.format(fromSesFilepath))

        if toSesFilepath is None:
            dir, _ = os.path.split(fromSesFilepath)
            toSesFilepath, _ = QtWidgets.QFileDialog.getSaveFileName(self._parent._win, 'Create save cloned session file', dir, "Session file (*.rtnabs)")
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
        wdgt.textChanged.connect(lambda text, key='subjectID': self._onTextChanged(key, text))
        self._wdgts['subjectID'] = wdgt
        self._wdgt.layout().addRow('Subject ID', wdgt)
        # TODO: continue here

        wdgt = QtWidgets.QLineEdit()
        wdgt.textChanged.connect(lambda text, key='sessionID': self._onTextChanged(key, text))
        self._wdgts['sessionID'] = wdgt
        self._wdgt.layout().addRow('Session ID', wdgt)

        self._parent.sigLoadedSession.connect(self._onSessionLoaded)
        self._parent.sigClosedSession.connect(lambda _: self._onSessionInfoChanged())

    def _onSessionLoaded(self, session: Session):
        session.sigInfoChanged.connect(self._onSessionInfoChanged)
        self._onSessionInfoChanged()

    def _onSessionInfoChanged(self):
        if self._parent.session is None:
            for key in ('subjectID', 'sessionID'):
                self._wdgts[key].setText('')
        else:
            for key in ('subjectID', 'sessionID'):
                val = getattr(self._parent.session, key)
                self._wdgts[key].setText('' if val is None else val)

    def _onTextChanged(self, key: str, text: str):
        if len(text) == 0:
            text = None
        if self._parent.session is not None:
            logger.info('Applying edited value of {} to session: {}'.format(key, text))
            setattr(self._parent.session, key, text)
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

    sigLoadedSession: Signal = attrs.field(factory=lambda: Signal((Session,)))
    sigClosedSession: Signal = attrs.field(factory=lambda: Signal((Session,)))

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

        createViewPanel('New / load', _Panel_CreateOrLoadSession(parent=self), icon=qta.icon('mdi6.folder'))

        createViewPanel('Session info', _Panel_SessionInfo(parent=self), icon=qta.icon('mdi6.form-select'))

        createViewPanel('Set MRI', _MainViewPanel(parent=self), icon=qta.icon('mdi6.image'))
        # TODO: set up MRI widget

        createViewPanel('Set head model', _MainViewPanel(parent=self), icon=qta.icon('mdi6.head-cog-outline'))
        # TODO: set up head model widget

        createViewPanel('Set fiducials', _MainViewPanel(parent=self), icon=qta.icon('mdi6.head-snowflake-outline'))
        # TODO: set up fiducials widget

        createViewPanel('Set transforms', _MainViewPanel(parent=self), icon=qta.icon('mdi6.head-sync-outline'))
        # TODO: set up transforms widget

        createViewPanel('Set targets', _MainViewPanel(parent=self), icon=qta.icon('mdi6.head-flash-outline'))
        # TODO: set up targets widget

        # set initial view widget visibility
        # TODO: default to MRI if new session, otherwise default to something else...
        self._updateEnabledToolbarBtns()
        self._activateView('New / load')

        self.sigLoadedSession.connect(self._onLoadedSession)
        self.sigClosedSession.connect(self._onClosedSession)

        if self._sesFilepath is not None:
            asyncio.create_task(self._loadAfterSetup(filepath=self._sesFilepath))

        if self._doRunAsApp:
            logger.debug('Showing window')
            self._win.show()

    @property
    def session(self):
        return self._session

    async def _loadAfterSetup(self, filepath):
        await asyncio.sleep(1.)
        self._mainViewPanels['New / load'].loadSession(sesFilepath=filepath)

    def _onLoadedSession(self, session: Session):
        logger.info('Loaded session {}'.format(session.filepath))
        assert self._session is not None
        self._updateEnabledToolbarBtns()

    def _onClosedSession(self, session: Session):
        logger.info('Closed session {}'.format(session.filepath))
        assert self._session is None
        self._updateEnabledToolbarBtns()

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




