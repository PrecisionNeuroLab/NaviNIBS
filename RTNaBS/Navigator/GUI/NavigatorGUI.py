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
from RTNaBS.util.GUI import DockWidgets as dw
from RTNaBS.util.GUI.DockWidgets.MainWindowWithDocksAndCloseSignal import MainWindowWithDocksAndCloseSignal
from RTNaBS.util.Signaler import Signal
from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.GUI.ViewPanels import MainViewPanel
from RTNaBS.Navigator.GUI.ViewPanels.ManageSessionPanel import ManageSessionPanel
from RTNaBS.Navigator.GUI.ViewPanels.MRIPanel import MRIPanel
from RTNaBS.Navigator.GUI.ViewPanels.HeadModelPanel import HeadModelPanel
from RTNaBS.Navigator.GUI.ViewPanels.FiducialsPanel import FiducialsPanel
from RTNaBS.Navigator.GUI.ViewPanels.TargetsPanel import TargetsPanel
from RTNaBS.Navigator.GUI.ViewPanels.ToolsPanel import ToolsPanel
from RTNaBS.Navigator.GUI.ViewPanels.SimulatedToolsPanel import SimulatedToolsPanel
from RTNaBS.Navigator.GUI.ViewPanels.TriggerSettingsPanel import TriggerSettingsPanel
from RTNaBS.Navigator.GUI.ViewPanels.CameraPanel import CameraPanel
from RTNaBS.Navigator.GUI.ViewPanels.SubjectRegistrationPanel import SubjectRegistrationPanel
from RTNaBS.Navigator.GUI.ViewPanels.NavigatePanel import NavigatePanel
from RTNaBS.util import exceptionToStr


logger = logging.getLogger(__name__)


@attrs.define
class NavigatorGUI(RunnableAsApp):
    _appName: str = 'RTNaBS Navigator GUI'

    _sesFilepath: tp.Optional[str] = None  # only used to load session on startup
    _inProgressBaseDir: tp.Optional[str] = None

    _session: tp.Optional[Session] = None

    _Win: tp.Callable[..., MainWindowWithDocksAndCloseSignal] = attrs.field(init=False)
    _win: MainWindowWithDocksAndCloseSignal = attrs.field(init=False)

    _mainViewPanels: tp.Dict[str, MainViewPanel] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        logger.info('Initializing {}'.format(self.__class__.__name__))

        self._Win = lambda: MainWindowWithDocksAndCloseSignal(self._appName, options=dw.MainWindowOptions(hasCentralFrame=True))  # use our own main window instead of RunnableApp's for supporting docking

        super().__attrs_post_init__()

        if self._inProgressBaseDir is None:
            self._inProgressBaseDir = os.path.join(appdirs.user_data_dir(appname='RTNaBS', appauthor=False), 'InProgressSessions')

        self._win.setAffinities(['MainViewPanel'])  # only allow main view panels to dock in this window

        def addViewPanel(panel: MainViewPanel) -> MainViewPanel:
            self._win.addDockWidgetAsTab(panel.dockWdgt)
            self._mainViewPanels[panel.key] = panel
            return panel

        panel = addViewPanel(ManageSessionPanel(key='Manage session', session=self._session,
                                   inProgressBaseDir=self._inProgressBaseDir))
        panel.sigLoadedSession.connect(self._onSessionLoaded)
        panel.sigClosedSession.connect(self._onSessionClosed)

        addViewPanel(MRIPanel(key='Set MRI', session=self._session))

        addViewPanel(HeadModelPanel(key='Set head model', session=self._session))

        addViewPanel(FiducialsPanel(key='Plan fiducials', session=self._session))

        addViewPanel(MainViewPanel(key='Set transforms', session=self._session, icon=qta.icon('mdi6.head-sync-outline')))
        # TODO: set up transforms widget

        addViewPanel(TargetsPanel(key='Set targets', session=self._session))

        #self._toolbarWdgt.addSeparator()  # separate pre-session planning/setup panels from within-session panels

        addViewPanel(ToolsPanel(key='Tools', session=self._session))

        # TODO: dynamically create and add this later only if tools.positionsServerInfo.type is Simulated
        addViewPanel(SimulatedToolsPanel(key='Simulated tools', session=self._session))

        addViewPanel(TriggerSettingsPanel(key='Trigger settings', session=self._session))

        addViewPanel(CameraPanel(key='Camera', session=self._session))

        addViewPanel(SubjectRegistrationPanel(key='Register', session=self._session))

        #self._toolbarWdgt.addSeparator()  # separate pre-session planning/setup panels from within-session panels

        addViewPanel(NavigatePanel(key='Navigate', session=self._session))


        # set initial view widget visibility
        # TODO: default to MRI if new session, otherwise default to something else...
        self._updateEnabledPanels()
        self._activateView('Navigate')  # TODO: debug, delete

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
        self._updateEnabledPanels()
        session.MRI.sigFilepathChanged.connect(self._updateEnabledPanels)
        session.headModel.sigFilepathChanged.connect(self._updateEnabledPanels)
        self.session.subjectRegistration.sigPlannedFiducialsChanged.connect(self._updateEnabledPanels)
        self.session.tools.sigToolsChanged.connect(lambda _: self._updateEnabledPanels())

    def _onSessionClosed(self, prevSession: Session):
        logger.info('Closed session {}'.format(prevSession.filepath))
        self._session = None
        for pane in self._mainViewPanels.values():
            pane.session = None
        self._updateEnabledPanels()

    @property
    def session(self):
        return self._session

    async def _loadAfterSetup(self, filepath):
        await asyncio.sleep(1.)
        logger.info(f'Loading session from {filepath}')
        try:
            self._mainViewPanels['Manage session'].loadSession(sesFilepath=filepath)
        except Exception as e:
            logger.error(exceptionToStr(e))
            raise e
        logger.debug('Done loading session')

    def _updateEnabledPanels(self):

        for key, panel in self._mainViewPanels.items():
            if panel.canBeEnabled():
                if panel.isVisible:
                    if not panel.hasInitialized and not panel.isInitializing:
                        panel.finishInitialization()
            else:
                panel.wdgt.setEnabled(False)

        # TODO: if we just disabled the only active view, change to a useful fallback view
        fallbackViews = ['Manage session', 'Set MRI', 'Set head model', 'Plan fiducials', 'Register']

    @property
    def activeViewKey(self) -> str:
        return list(self._mainViewPanels.keys())[0]
        viewWdgt = self._mainViewStackedWdgt.currentWidget()
        viewKeys = [key for key, val in self._mainViewPanels.items() if val.wdgt is viewWdgt]
        assert len(viewKeys) == 1
        return viewKeys[0]

    def _activateView(self, viewKey: str):

        self._mainViewPanels[viewKey].dockWdgt.raise_()

        logger.info('Switched to view "{}"'.format(viewKey))


if __name__ == '__main__':
    if True:  # TODO: debug, delete or set to False
        if True:
            #sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..', 'data/sub-2003_ses-test4.rtnabs')
            sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..', 'data/sub-2003_ses-test7.rtnabsdir')
        else:
            sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..',
                                       'data/TestSession1.rtnabs')
        NavigatorGUI.createAndRun(sesFilepath=sesFilepath)
    else:
        NavigatorGUI.createAndRun()




