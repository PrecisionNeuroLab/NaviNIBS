from __future__ import annotations

import asyncio

import appdirs
import attrs
import contextlib
import darkdetect
from datetime import datetime
import logging
import os
import pathlib
import pyvista as pv
import pyqtgraph as pg
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError
from RTNaBS.util.GUI.QAppWithAsyncioLoop import RunnableAsApp
from RTNaBS.util.GUI.Dock import Dock, DockArea
from RTNaBS.util.GUI.ErrorDialog import asyncTryAndRaiseDialogOnError
from RTNaBS.util.Signaler import Signal
from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.DockWidgetLayouts import DockWidgetLayout
from RTNaBS.Navigator.GUI.ViewPanels import MainViewPanel
from RTNaBS.Navigator.GUI.ViewPanels.MainViewPanelWithDockWidgets import MainViewPanelWithDockWidgets
from RTNaBS.Navigator.GUI.ViewPanels.ManageSessionPanel import ManageSessionPanel
from RTNaBS.Navigator.GUI.ViewPanels.MRIPanel import MRIPanel
from RTNaBS.Navigator.GUI.ViewPanels.HeadModelPanel import HeadModelPanel
from RTNaBS.Navigator.GUI.ViewPanels.CoordinateSystemsPanel import CoordinateSystemsPanel
from RTNaBS.Navigator.GUI.ViewPanels.FiducialsPanel import FiducialsPanel
from RTNaBS.Navigator.GUI.ViewPanels.TargetsPanel import TargetsPanel
from RTNaBS.Navigator.GUI.ViewPanels.ToolsPanel import ToolsPanel
from RTNaBS.Navigator.GUI.ViewPanels.SimulatedToolsPanel import \
    SimulatedToolsPanel
from RTNaBS.Navigator.GUI.ViewPanels.TriggerSettingsPanel import TriggerSettingsPanel
from RTNaBS.Navigator.GUI.ViewPanels.CameraPanel import CameraPanel
from RTNaBS.Navigator.GUI.ViewPanels.SubjectRegistrationPanel import SubjectRegistrationPanel
from RTNaBS.Navigator.GUI.ViewPanels.NavigatePanel import NavigatePanel
from RTNaBS.Navigator.GUI.ViewPanels.DigitizedLocationsPanel import DigitizedLocationsPanel
from RTNaBS.util import exceptionToStr

logger = logging.getLogger(__name__)


@attrs.define
class NavigatorGUI(RunnableAsApp):
    _appName: str = 'NaviNIBS Navigator GUI'
    _theme: str = 'auto'  # auto, light, or dark

    _sesFilepath: tp.Optional[str] = None  # only used to load session on startup
    _inProgressBaseDir: tp.Optional[str] = None

    _session: tp.Optional[Session] = None

    _rootDockArea: DockArea = attrs.field(init=False)

    _mainViewPanels: tp.Dict[str, MainViewPanel] = attrs.field(init=False, factory=dict)

    _logFileHandler: tp.Optional[logging.FileHandler] = attrs.field(init=False, default=None)

    _restoringLayoutLock: asyncio.Lock = attrs.field(init=False, factory=asyncio.Lock)

    def __attrs_post_init__(self):
        logger.info('Initializing {}'.format(self.__class__.__name__))

        thisDir = pathlib.Path(__file__).parent
        iconPath = os.path.join(thisDir, 'resources', 'NaviNIBSIcon.ico')
        assert os.path.exists(iconPath)
        self._appIconPath = iconPath

        super().__attrs_post_init__()

        if self._inProgressBaseDir is None:
            self._inProgressBaseDir = os.path.join(appdirs.user_data_dir(appname='NaviNIBS', appauthor=False), 'InProgressSessions')

        self._rootDockArea = DockArea(affinities=['MainViewPanel'])
        self._rootDockArea.setContentsMargins(2, 2, 2, 2)
        self._win.setCentralWidget(self._rootDockArea)

        # set pyvista theme according to current colors
        if darkdetect.isDark() and False:
            pv.set_plot_theme('dark')
        else:
            pv.set_plot_theme('default')
        pv.global_theme.background = self._win.palette().color(QtGui.QPalette.Base).name()
        pv.global_theme.font.color = self._win.palette().color(QtGui.QPalette.Text).name()

        panel = self._addViewPanel(ManageSessionPanel(key='Manage session',
                                                      session=self._session,
                                                      inProgressBaseDir=self._inProgressBaseDir,
                                                      navigatorGUI=self),)
        panel.sigAboutToFinishLoadingSession.connect(self._onSessionAboutToFinishLoading)
        panel.sigLoadedSession.connect(self._onSessionLoaded)
        panel.sigClosedSession.connect(self._onSessionClosed)

        self._addViewPanel(MRIPanel(session=self._session))

        self._addViewPanel(HeadModelPanel(session=self._session))

        self._addViewPanel(FiducialsPanel(session=self._session))

        #self._addViewPanel(CoordinateSystemsPanel(session=self._session))
        # TODO: set up transforms widget

        self._addViewPanel(TargetsPanel(session=self._session))

        #self._toolbarWdgt.addSeparator()  # separate pre-session planning/setup panels from within-session panels

        self._addViewPanel(ToolsPanel(session=self._session))

        # TODO: dynamically create and add this later only if tools.positionsServerInfo.type is Simulated
        # self._addViewPanel(SimulatedToolsPanel(key='Simulated tools', session=self._session))

        self._addViewPanel(TriggerSettingsPanel(session=self._session))

        self._addViewPanel(CameraPanel(session=self._session))

        self._addViewPanel(SubjectRegistrationPanel(session=self._session))

        #self._toolbarWdgt.addSeparator()  # separate pre-session planning/setup panels from within-session panels

        self._addViewPanel(NavigatePanel(session=self._session))

        self._addViewPanel(DigitizedLocationsPanel(session=self._session))

        # set initial view widget visibility
        # TODO: default to MRI if new session, otherwise default to something else...
        self._updateEnabledPanels()
        self._activateView('Manage session')
        #self._activateView('Navigate')  # TODO: debug, delete

        if self._sesFilepath is not None:
            asyncio.create_task(asyncTryAndRaiseDialogOnError(self._loadAfterSetup, filepath=self._sesFilepath))

        if self._doRunAsApp:
            logger.debug('Showing window')
            self._win.resize(QtCore.QSize(1600, 1100))  # TODO: restore previous size if available
            self._win.show()

    def _addViewPanel(self, panel: MainViewPanel) -> MainViewPanel:
        logger.info(f'Adding view panel {panel.key}')
        self._rootDockArea.addDock(panel.dockWdgt, position='below')
        self._mainViewPanels[panel.key] = panel
        if isinstance(panel, MainViewPanelWithDockWidgets):
            panel.sigAboutToRestoreLayout.connect(self._onAboutToRestorePanelLayout)
            panel.sigRestoredLayout.connect(self._onRestoredPanelLayout)
        return panel

    def _saveRootLayout(self):
        # save root layout

        key = 'NavigatorGUI'
        layout = self.session.dockWidgetLayouts.get(key, None)
        if layout is None:
            layout = DockWidgetLayout(
                key=key,
                affinities=['MainViewPanel'],
            )
            self.session.dockWidgetLayouts.addItem(layout)

        layout.state = self._rootDockArea.saveState()

    def saveLayout(self):
        self._saveRootLayout()

        # save each initialized panel's layout
        for mainViewPanel in self.mainViewPanels.values():
            if isinstance(mainViewPanel, MainViewPanelWithDockWidgets) and mainViewPanel.hasInitialized:
                mainViewPanel.saveLayout()

    async def _restoreRootLayout(self, needsLock: bool = True):
        # restore root layout

        await asyncio.sleep(0.01)
        layout = self.session.dockWidgetLayouts.get('NavigatorGUI', None)
        if layout is not None and layout.state is not None:

            async with self._restoringLayoutLock if needsLock else contextlib.nullcontext():

                with contextlib.ExitStack() as stack:
                    blocks = [stack.enter_context(panel.sigPanelShown.blocked()) for panel in self.mainViewPanels.values()]

                    winSize = self._win.size()
                    logger.debug(f'About to restore root layout when winSize={winSize}')

                    try:
                        self._rootDockArea.restoreState(layout.state)
                    except ValueError:
                        # sometimes can get errors during restore if other parts of layout have changed
                        logger.error('Unable to restore root dock area state')

                for panel in self.mainViewPanels.values():
                    # catch up with signals that may have been blocked above
                    await asyncio.sleep(0.01)
                    panel.wdgt.updateGeometry()
                    # if panel.dockWdgt.isCurrentTab():
                    #     panel.sigPanelShown.emit()
                    # else:
                    #     panel.sigPanelHidden.emit()

                await asyncio.sleep(0.01)
                winSize = self._win.size()
                logger.debug(f'Restored root layout when winSize={winSize}')
                # jiggle window size to force layout to update
                # TODO: find way to force layout update without jiggle that is visible to user
                self._win.resize(winSize - QtCore.QSize(10, 10))  # TODO: restore previous size if available
                await asyncio.sleep(0.01)
                self._win.resize(winSize)
                await asyncio.sleep(0.01)
                logger.debug(f'After restored root layout, winSize={winSize}')

    def _onAboutToRestorePanelLayout(self):
        if self._restoringLayoutLock.locked():
            return  # ignore if in the middle of a whole-app restore
        self._saveRootLayout()

    def _onRestoredPanelLayout(self):
        if self._restoringLayoutLock.locked():
            return  # ignore if in the middle of a whole-app restore
        asyncio.create_task(asyncTryAndLogExceptionOnError(self._restoreRootLayout))

    async def restoreLayoutIfAvailable(self):
        async with self._restoringLayoutLock:
            await self._restoreRootLayout(needsLock=False)

            # restore each initialized panel's layout
            didRestoreAPanel = False
            for mainViewPanel in self.mainViewPanels.values():
                if isinstance(mainViewPanel, MainViewPanelWithDockWidgets) and mainViewPanel.hasInitialized:
                    if hasattr(mainViewPanel, 'finishedAsyncInitialization'):
                        if True:
                            if not mainViewPanel.finishedAsyncInitialization.is_set():
                                continue  # assume it will restore itself when initialized
                        else:
                            await mainViewPanel.finishedAsyncInitialization.wait()

                        continue  # TODO: debug, delete
                    if mainViewPanel.restoreLayoutIfAvailable():
                        didRestoreAPanel = True

            if didRestoreAPanel and False:
                # restore root layout again
                await self._restoreRootLayout(needsLock=False)

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
            filename=os.path.join(session.unpackedSessionDir, 'NaviNIBS_Log.txt'),
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

        asyncio.create_task(asyncTryAndLogExceptionOnError(self.restoreLayoutIfAvailable))

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

            if addon.needsToInstantiateExtras:
                addon.instantiateExtras(navigatorGUI=self, session=self._session)

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

    @property
    def mainViewPanels(self):
        return self._mainViewPanels

    @property
    def manageSessionPanel(self) -> ManageSessionPanel:
        return self._mainViewPanels['Manage session']

    @property
    def mriPanel(self) -> MRIPanel:
        return self._mainViewPanels['Set MRI']

    @property
    def headModelPanel(self) -> HeadModelPanel:
        return self._mainViewPanels['Set head model']

    @property
    def planFiducialsPanel(self) -> FiducialsPanel:
        return self._mainViewPanels['Plan fiducials']

    async def _loadAfterSetup(self, filepath):
        await asyncio.sleep(1.)
        logger.info(f'Loading session from {filepath}')
        self._mainViewPanels['Manage session'].loadSession(sesFilepath=filepath)
        logger.debug('Done loading session')

    def _updateEnabledPanels(self):

        for key, panel in self._mainViewPanels.items():
            if panel.canBeEnabled()[0]:
                if panel.isVisible:
                    if not panel.hasInitialized and not panel.isInitializing:
                        panel.finishInitialization()
            else:
                panel.wdgt.setEnabled(False)

        # TODO: if we just disabled the only active view, change to a useful fallback view
        fallbackViews = ['Manage session', 'Set MRI', 'Set head model', 'Plan fiducials', 'Register']

    @property
    def activeViewKey(self) -> str | None:
        """
        Note: This doesn't account for "active" views is secondary windows, split views, etc.
        """
        try:
            return self._rootDockArea.topContainer.stack.currentWidget().name()
        except:
            # can happen if root is not a tabbed container (?)
            return None

    def _activateView(self, viewKey: str):

        self._mainViewPanels[viewKey].dockWdgt.raiseDock()

        logger.info('Switched to view "{}"'.format(viewKey))


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--sesFilepath', type=str, default=None)
    args = parser.parse_args()

    if args.sesFilepath is None:
        if True:  # TODO: debug, delete or set to False
            if True:
                #sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..', 'data/sub-2003_ses-test4.rtnabs')
                # sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..', 'data/sub-2003_ses-test8.rtnabsdir')
                # sesFilepath = r'G:\My Drive\KellerLab\RTNaBS\data\sub-2355\ses-230209\sub-2355_ses-230209_RTNaBS'
                sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..', 'data/sub-2003_ses-test9.rtnabsdir')
                sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..', 'data/sub-1_ses-demo.navinibsdir')
                sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..', 'data/sub-1_ses-cobotDemo.navinibsdir')
            else:
                sesFilepath = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', '..',
                                           'data/TestSession1.rtnabs')
            NavigatorGUI.createAndRun(sesFilepath=sesFilepath)
        else:
            NavigatorGUI.createAndRun()
    else:
        NavigatorGUI.createAndRun(sesFilepath=args.sesFilepath)



