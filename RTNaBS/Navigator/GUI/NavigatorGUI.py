from __future__ import annotations
import appdirs
import attrs
import logging
import os
import pathlib
import pyqtgraph as pg
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from RTNaBS.util.GUI.QAppWithAsyncioLoop import RunnableAsApp
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

    def __attrs_post_init__(self):
        self._wdgt.setLayout(QtWidgets.QVBoxLayout())
        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-plus'), text='New session')
        btn.clicked.connect(lambda checked: self._createNewSession())
        self._wdgt.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-document-edit'), text='Load session')
        btn.clicked.connect(lambda checked: self._loadSession())
        self._wdgt.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-restore'), text='Recover in-progress session')
        btn.clicked.connect(lambda checked: self._recoverSession())
        self._wdgt.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=qta.icon('mdi6.file-replace'), text='Clone session')
        btn.clicked.connect(lambda checked: self._cloneSession())
        self._wdgt.layout().addWidget(btn)

    def _createNewSession(self, sesFilepath: tp.Optional[str] = None):
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

        raise NotImplementedError()  # TODO

    def _loadSession(self, sesFilepath: tp.Optional[str] = None):
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
        raise NotImplementedError()  # TODO

    def _recoverSession(self, sesDataDir: tp.Optional[str] = None):
        if sesDataDir is None:
            dir = self._parent._inProgressDir
            sesDataDir = QtWidgets.QFileDialog.getExistingDirectory(self._parent._win, 'Choose unpacked session to load', dir)
            if len(sesDataDir) == 0:
                logger.info('Browse recover session cancelled')
                return
        logger.info('Recover session data dir: {}'.format(sesDataDir))

        raise NotImplementedError()  # TODO

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

        self._loadSession(sesFilepath=toSesFilepath)


@attrs.define()
class _Panel_SessionInfo(_MainViewPanel):
    def __attrs_post_init__(self):
        self._wdgt.setLayout(QtWidgets.QFormLayout())
        self._wdgt.layout().addRow('Subject ID', QtWidgets.QLineEdit())
        # TODO: continue here


@attrs.define()
class NavigatorGUI(RunnableAsApp):
    _appName: str = 'RTNaBS Navigator GUI'

    _sesFilepath: tp.Optional[str] = None
    _inProgressDir: tp.Optional[str] = None

    _mainViewStackedWdgt: QtWidgets.QStackedWidget = attrs.field(init=False)
    _mainViewPanels: tp.Dict[str, _MainViewPanel] = attrs.field(init=False, factory=dict)
    _toolbarWdgt: QtWidgets.QToolBar = attrs.field(init=False)
    _toolbarBtnActions: tp.Dict[str, QtWidgets.QAction] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        logger.info('Initializing {}'.format(self.__class__.__name__))

        super().__attrs_post_init__()

        if self._inProgressDir is None:
            self._inProgressDir = os.path.join(appdirs.user_data_dir(appname='RTNaBS', appauthor=False), 'InProgressSessions')

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

        createViewPanel('New / load', _Panel_CreateOrLoadSession(parent=self), icon=qta.icon('mdi6.file'))

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
        self._activateView('New / load')

        if self._doRunAsApp:
            logger.debug('Showing window')
            self._win.show()

    def _activateView(self, viewKey: str):
        toolbarAction = self._toolbarBtnActions[viewKey]
        toolbarBtn = self._toolbarWdgt.widgetForAction(toolbarAction)

        prevViewWdgt = self._mainViewStackedWdgt.currentWidget()
        prevViewKeys = [key for key, val in self._mainViewPanels.items() if val.wdgt is prevViewWdgt]
        assert len(prevViewKeys) == 1
        prevViewKey = prevViewKeys[0]
        self._toolbarBtnActions[prevViewKey].setChecked(False)
        self._toolbarBtnActions[viewKey].setChecked(True)

        panel = self._mainViewPanels[viewKey]
        self._mainViewStackedWdgt.setCurrentWidget(panel.wdgt)

        logger.info('Switched to view "{}"'.format(viewKey))




if __name__ == '__main__':
    NavigatorGUI.createAndRun()




