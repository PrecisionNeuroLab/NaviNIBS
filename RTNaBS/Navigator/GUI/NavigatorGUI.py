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
from RTNaBS.Navigator.GUI.ViewPanels import MainViewPanel
from RTNaBS.Navigator.GUI.ViewPanels.ManageSessionPanel import ManageSessionPanel
from RTNaBS.Navigator.GUI.ViewPanels.MRIPanel import MRIPanel
from RTNaBS.Navigator.GUI.ViewPanels.HeadModelPanel import HeadModelPanel
from RTNaBS.Navigator.GUI.ViewPanels.FiducialsPanel import FiducialsPanel
from RTNaBS.Navigator.GUI.ViewPanels.TargetsPanel import TargetsPanel
from RTNaBS.Navigator.GUI.ViewPanels.ToolsPanel import ToolsPanel
from RTNaBS.Navigator.GUI.ViewPanels.CameraPanel import CameraPanel
from RTNaBS.Navigator.GUI.ViewPanels.SubjectRegistrationPanel import SubjectRegistrationPanel
from RTNaBS.util import exceptionToStr

logger = logging.getLogger(__name__)


@attrs.define()
class NavigatorGUI(RunnableAsApp):
    _appName: str = 'RTNaBS Navigator GUI'

    _sesFilepath: tp.Optional[str] = None  # only used to load session on startup
    _inProgressBaseDir: tp.Optional[str] = None

    _session: tp.Optional[Session] = None

    _mainViewStackedWdgt: QtWidgets.QStackedWidget = attrs.field(init=False)
    _mainViewPanels: tp.Dict[str, MainViewPanel] = attrs.field(init=False, factory=dict)
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

        def createViewPanel(key: str, panel: MainViewPanel, icon: tp.Optional[QtGui.QIcon]=None):
            self._mainViewPanels[key] = panel
            self._mainViewStackedWdgt.addWidget(panel.wdgt)
            self._toolbarBtnActions[key] = self._toolbarWdgt.addAction(key) if icon is None else self._toolbarWdgt.addAction(icon, key)
            self._toolbarBtnActions[key].setCheckable(True)
            self._toolbarBtnActions[key].triggered.connect(lambda checked=False, key=key: self._activateView(viewKey=key))

        panel = ManageSessionPanel(session=self._session,
                                   inProgressBaseDir=self._inProgressBaseDir)
        createViewPanel('Manage session', panel, icon=qta.icon('mdi6.form-select'))
        panel.sigLoadedSession.connect(self._onSessionLoaded)
        panel.sigClosedSession.connect(self._onSessionClosed)

        createViewPanel('Set MRI', MRIPanel(session=self._session), icon=qta.icon('mdi6.image'))

        createViewPanel('Set head model', HeadModelPanel(session=self._session), icon=qta.icon('mdi6.head-cog-outline'))

        createViewPanel('Plan fiducials', FiducialsPanel(session=self._session), icon=qta.icon('mdi6.head-snowflake-outline'))

        createViewPanel('Set transforms', MainViewPanel(session=self._session), icon=qta.icon('mdi6.head-sync-outline'))
        # TODO: set up transforms widget

        createViewPanel('Set targets', TargetsPanel(session=self._session), icon=qta.icon('mdi6.head-flash-outline'))

        self._toolbarWdgt.addSeparator()  # separate pre-session planning/setup panels from within-session panels

        createViewPanel('Tools', ToolsPanel(session=self._session), icon=qta.icon('mdi6.hammer-screwdriver'))

        createViewPanel('Camera', CameraPanel(session=self._session), icon=qta.icon('mdi6.cctv'))

        createViewPanel('Register', SubjectRegistrationPanel(session=self._session), icon=qta.icon('mdi6.head-snowflake'))

        # set initial view widget visibility
        # TODO: default to MRI if new session, otherwise default to something else...
        self._updateEnabledToolbarBtns()
        self._activateView('Manage session')

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
        session.MRI.sigFilepathChanged.connect(self._updateEnabledToolbarBtns)
        session.headModel.sigFilepathChanged.connect(self._updateEnabledToolbarBtns)
        self.session.subjectRegistration.sigPlannedFiducialsChanged.connect(self._updateEnabledToolbarBtns)
        self.session.tools.sigToolsChanged.connect(lambda _: self._updateEnabledToolbarBtns())

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
        self._mainViewPanels['Manage session'].loadSession(sesFilepath=filepath)

    def _updateEnabledToolbarBtns(self):

        for btn in self._toolbarBtnActions.values():
            btn.setEnabled(False)

        activeKeys = []

        activeKeys.append('Manage session')

        if self._session is not None:
            activeKeys += ['Set MRI', 'Camera', 'Tools']
            if self._session.MRI.isSet:
                activeKeys += ['Set head model']
                if self._session.headModel.isSet:
                    activeKeys += ['Plan fiducials', 'Set transforms', 'Set targets']

                    if self._session.tools.subjectTracker is not None and self._session.tools.pointer is not None:
                        if self._session.subjectRegistration.hasMinimumPlannedFiducials:
                            activeKeys += ['Register']

        for key in activeKeys:
            self._toolbarBtnActions[key].setEnabled(True)

        if self.activeViewKey not in activeKeys:
            fallbackViews = ['Manage session', 'Set MRI', 'Set head model']
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

        prevPanel = self._mainViewPanels[prevViewKey]
        prevPanel.sigPanelDeactivated.emit()

        panel.sigPanelActivated.emit()


if __name__ == '__main__':
    if True:  # TODO: debug, delete or set to False
        if True:
            sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..', 'data/sub-2003_ses-test2.rtnabs')
        else:
            sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..',
                                       'data/TestSession1.rtnabs')
        NavigatorGUI.createAndRun(sesFilepath=sesFilepath)
    else:
        NavigatorGUI.createAndRun()




