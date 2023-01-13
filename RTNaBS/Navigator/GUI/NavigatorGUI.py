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
from RTNaBS.Navigator.GUI.ViewPanels.CoordinateSystemsPanel import CoordinateSystemsPanel
from RTNaBS.Navigator.GUI.ViewPanels.FiducialsPanel import FiducialsPanel
from RTNaBS.Navigator.GUI.ViewPanels.TargetsPanel import TargetsPanel
from RTNaBS.Navigator.GUI.ViewPanels.ToolsPanel import ToolsPanel
from RTNaBS.Navigator.GUI.ViewPanels.SimulatedToolsPanel import SimulatedToolsPanel
from RTNaBS.Navigator.GUI.ViewPanels.TriggerSettingsPanel import TriggerSettingsPanel
from RTNaBS.Navigator.GUI.ViewPanels.CameraPanel import CameraPanel
from RTNaBS.Navigator.GUI.ViewPanels.SubjectRegistrationPanel import SubjectRegistrationPanel
from RTNaBS.Navigator.GUI.ViewPanels.NavigatePanel import NavigatePanel
from RTNaBS.Navigator.GUI.ViewPanels.DigitizedLocationsPanel import DigitizedLocationsPanel
from RTNaBS.util import exceptionToStr

logger = logging.getLogger(__name__)


@attrs.define
class NavigatorGUI(RunnableAsApp):
    _appName: str = 'RTNaBS Navigator GUI'
    _theme: str = 'auto'  # auto, light, or dark

    _sesFilepath: tp.Optional[str] = None  # only used to load session on startup
    _inProgressBaseDir: tp.Optional[str] = None

    _session: tp.Optional[Session] = None

    _Win: tp.Callable[..., MainWindowWithDocksAndCloseSignal] = attrs.field(init=False)
    _win: MainWindowWithDocksAndCloseSignal = attrs.field(init=False)

    _mainViewPanels: tp.Dict[str, MainViewPanel] = attrs.field(init=False, factory=dict)

    _logFileHandler: tp.Optional[logging.FileHandler] = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        logger.info('Initializing {}'.format(self.__class__.__name__))

        self._Win = lambda: MainWindowWithDocksAndCloseSignal(self._appName, options=dw.MainWindowOptions(hasCentralFrame=True))  # use our own main window instead of RunnableApp's for supporting docking

        super().__attrs_post_init__()

        if self._inProgressBaseDir is None:
            self._inProgressBaseDir = os.path.join(appdirs.user_data_dir(appname='RTNaBS', appauthor=False), 'InProgressSessions')

        self._win.setAffinities(['MainViewPanel'])  # only allow main view panels to dock in this window

        panel = self._addViewPanel(ManageSessionPanel(key='Manage session', session=self._session,
                                   inProgressBaseDir=self._inProgressBaseDir))
        panel.sigAboutToFinishLoadingSession.connect(self._onSessionAboutToFinishLoading)
        panel.sigLoadedSession.connect(self._onSessionLoaded)
        panel.sigClosedSession.connect(self._onSessionClosed)

        self._addViewPanel(MRIPanel(key='Set MRI', session=self._session))

        self._addViewPanel(HeadModelPanel(key='Set head model', session=self._session))

        self._addViewPanel(FiducialsPanel(key='Plan fiducials', session=self._session))

        self._addViewPanel(MainViewPanel(key='Set transforms', session=self._session, icon=qta.icon('mdi6.head-sync-outline')))
        # TODO: set up transforms widget

        self._addViewPanel(TargetsPanel(key='Set targets', session=self._session))

        #self._toolbarWdgt.addSeparator()  # separate pre-session planning/setup panels from within-session panels

        self._addViewPanel(ToolsPanel(key='Tools', session=self._session))

        # TODO: dynamically create and add this later only if tools.positionsServerInfo.type is Simulated
        #self._addViewPanel(SimulatedToolsPanel(key='Simulated tools', session=self._session))

        self._addViewPanel(TriggerSettingsPanel(key='Trigger settings', session=self._session))

        self._addViewPanel(CameraPanel(key='Camera', session=self._session))

        self._addViewPanel(SubjectRegistrationPanel(key='Register', session=self._session))

        #self._toolbarWdgt.addSeparator()  # separate pre-session planning/setup panels from within-session panels

        self._addViewPanel(NavigatePanel(key='Navigate', session=self._session))

        self._addViewPanel(DigitizedLocationsPanel(key='Digitize', session=self._session))

        # set initial view widget visibility
        # TODO: default to MRI if new session, otherwise default to something else...
        self._updateEnabledPanels()
        self._activateView('Manage session')
        #self._activateView('Navigate')  # TODO: debug, delete

        if self._sesFilepath is not None:
            asyncio.create_task(self._loadAfterSetup(filepath=self._sesFilepath))

        if self._doRunAsApp:
            logger.debug('Showing window')
            self._win.resize(QtCore.QSize(1200, 900))  # TODO: restore previous size if available
            self._win.show()

    def _addViewPanel(self, panel: MainViewPanel) -> MainViewPanel:
        logger.info(f'Adding view panel {panel.key}')
        self._win.addDockWidgetAsTab(panel.dockWdgt)
        self._mainViewPanels[panel.key] = panel
        return panel

    def _onAppAboutToQuit(self):
        super()._onAppAboutToQuit()

        # close each non-visible panel first to prevent them from initializing right before closing
        for panelKey, panel in self._mainViewPanels.items():
            if not panel.isVisible:
                panel.close()

    def _onSessionAboutToFinishLoading(self, session: Session):
        if self._logFileHandler is not None:
            # remove previous session log file handler
            logging.getLogger('').removeHandler(self._logFileHandler)
            self._logFileHandler = None
        self._logFileHandler = logging.FileHandler(
            filename=os.path.join(session.unpackedSessionDir, 'RTNaBS_Log.txt'),
        )
        self._logFileHandler.setFormatter(logging.Formatter(
            fmt='%(asctime)s.%(msecs)03d  %(process)6d %(filename)20s %(lineno)4d %(levelname)5s: %(message)s',
            datefmt='%H:%M:%S'))
        self._logFileHandler.setLevel(logging.DEBUG)  # TODO: set to info instead
        logging.getLogger('').addHandler(self._logFileHandler)

    def _onSessionLoaded(self, session: Session):
        assert session is not None
        logger.info('Loaded session {}'.format(session.filepath))

        self._onAddonsAboutToChange()

        self._session = session

        for pane in self._mainViewPanels.values():
            pane.session = session

        self._onAddonsChanged(triggeredBySessionLoad=True)

        self._updateEnabledPanels()
        session.MRI.sigFilepathChanged.connect(self._updateEnabledPanels)
        session.headModel.sigFilepathChanged.connect(self._updateEnabledPanels)
        session.addons.sigItemsChanged.connect(lambda *args: self._onAddonsChanged())

        self.session.subjectRegistration.fiducials.sigItemsChanged.connect(lambda *args: self._updateEnabledPanels())
        self.session.tools.sigItemsChanged.connect(lambda *args: self._updateEnabledPanels())

    def _onAddonsAboutToChange(self):
        if self._session is not None:
            if len(self._session.addons) > 0:
                pass  # TODO: unload any addons changed not present in new session

    def _onAddonsChanged(self, triggeredBySessionLoad: bool = False):
        needToUpdateEnabledPanels = False
        prevActiveViewKey = self.activeViewKey
        for addonKey, addon in self._session.addons.items():
            for panelKey, ACE_Panel in addon.MainViewPanels.items():
                Panel = ACE_Panel.Class
                if panelKey not in self._mainViewPanels:
                    logger.info(f'Loading addon {addonKey} main view panel {panelKey}')
                    self._addViewPanel(Panel(key=panelKey))  # don't set session until after setting active view below to support deferred panel initialization
                    self._activateView(prevActiveViewKey)  # prevented switching focus to most recent loaded main view panel
                    self._mainViewPanels[panelKey].session = self._session

                    if not triggeredBySessionLoad:
                        self._mainViewPanels[panelKey].session = self._session
                        needToUpdateEnabledPanels = True

                else:
                    logger.info(f'Addon {addonKey} main view panel {panelKey} already loaded, not reloading')

        if needToUpdateEnabledPanels:
            self._updateEnabledPanels()

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
            # sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..', 'data/sub-2003_ses-test8.rtnabsdir')
            sesFilepath = r'D:\KellerLab\RT-TEP\sub-2355\ses-221202\sub-2355_ses-221202_RTNaBS'
        else:
            sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..',
                                       'data/TestSession1.rtnabs')
        NavigatorGUI.createAndRun(sesFilepath=sesFilepath)
    else:
        NavigatorGUI.createAndRun()




