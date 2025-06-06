from __future__ import annotations

import asyncio

import attrs
from datetime import datetime
import logging
import os
import pathlib
import pyqtgraph.dockarea.Container as pgdc
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp
from typing import TYPE_CHECKING

from NaviNIBS import __version__
from NaviNIBS.Navigator.GUI.ViewPanels.MainViewPanelWithDockWidgets import MainViewPanelWithDockWidgets
from NaviNIBS.Navigator.GUI.EditWindows.ImportSessionWindow import ImportSessionWindow
from NaviNIBS.util import exceptionToStr
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.GUI.Dock import Dock, DockArea, TContainer
from NaviNIBS.util.GUI.ErrorDialog import raiseErrorDialog
from NaviNIBS.util.GUI.Icons import getIcon
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model.Addons import Addon, installPath as addonBaseInstallPath

if TYPE_CHECKING:
    from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI


logger = logging.getLogger(__name__)


@attrs.define(kw_only=True)
class ManageSessionPanel(MainViewPanelWithDockWidgets):
    _navigatorGUI: NavigatorGUI

    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: getIcon('mdi6.form-select'))

    _autosavePeriod: float = 60  # in sec

    _inProgressBaseDir: tp.Optional[str] = None
    _rootDockArea: DockArea | None = attrs.field(init=False, default=None) # used for tabSaveBtn
    _newSessionBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _tabSaveBtn: QtWidgets.QPushButton | None = attrs.field(init=False, default=None)
    _importBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _saveBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _saveBtnShowsDirty: bool | None = attrs.field(init=False, default=None)
    _saveShortcut: QtWidgets.QShortcut = attrs.field(init=False)
    _saveToFileBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _saveToDirBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _closeBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _addAddonBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _fileDW: Dock = attrs.field(init=False)
    _fileContainer: QtWidgets.QWidget = attrs.field(init=False)
    _infoDW: Dock = attrs.field(init=False)
    _settingsContainer: QtWidgets.QWidget = attrs.field(init=False)
    _infoContainer: QtWidgets.QWidget = attrs.field(init=False)
    _appearanceContainer: QtWidgets.QWidget = attrs.field(init=False)
    _infoWdgts: tp.Dict[str, QtWidgets.QLineEdit] = attrs.field(init=False, factory=dict)
    _themeDropdown: QtWidgets.QComboBox = attrs.field(init=False)
    _fontSizeField: QtWidgets.QSpinBox = attrs.field(init=False)
    _importSessionWindow: ImportSessionWindow | None = attrs.field(init=False, default=None)

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

        btn = QtWidgets.QPushButton(icon=getIcon('mdi6.file-plus'), text='New session')
        self._newSessionBtn = btn
        btn.clicked.connect(lambda checked: self._createNewSession())
        container.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=getIcon('mdi6.folder-open'), text='Load session')
        btn.clicked.connect(lambda checked: self.loadSession())
        container.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=getIcon('mdi6.folder-plus-outline'), text='Copy from session...')
        btn.clicked.connect(lambda checked: self.importSession())
        container.layout().addWidget(btn)
        self._importBtn = btn

        btn = QtWidgets.QPushButton(icon=getIcon('mdi6.file-restore'), text='Recover in-progress session')
        btn.clicked.connect(lambda checked: self._recoverSession())
        container.layout().addWidget(btn)

        btn = QtWidgets.QPushButton(icon=getIcon('mdi6.clipboard-file'), text='Clone session')
        btn.clicked.connect(lambda checked: self._cloneSession())
        container.layout().addWidget(btn)

        container.layout().addSpacing(10)

        btn = QtWidgets.QPushButton(icon=getIcon('mdi6.content-save'), text='Save session')
        btn.clicked.connect(self._onSaveSessionBtnClicked)
        container.layout().addWidget(btn)
        self._saveBtn = btn

        self._saveShortcut = QtWidgets.QShortcut(QtGui.QKeySequence.Save, self._navigatorGUI._win, None, None, QtCore.Qt.ApplicationShortcut)
        self._saveShortcut.activated.connect(self._onSaveSessionShortcutActivated)

        btn = QtWidgets.QPushButton(icon=getIcon('mdi6.content-save-edit-outline'), text='Save session to dir...')
        btn.clicked.connect(lambda checked: self._saveSessionToDir())
        container.layout().addWidget(btn)
        self._saveToDirBtn = btn

        btn = QtWidgets.QPushButton(icon=getIcon('mdi6.content-save-edit'), text='Save session to file...')
        btn.clicked.connect(lambda checked: self._saveSessionToFile())
        container.layout().addWidget(btn)
        self._saveToFileBtn = btn

        container.layout().addSpacing(10)

        btn = QtWidgets.QPushButton(icon=getIcon('mdi6.file-remove'), text='Close session')
        btn.clicked.connect(lambda checked: self._tryVerifyThenCloseSession())
        container.layout().addWidget(btn)
        self._closeBtn = btn

        container.layout().addSpacing(10)

        btn = QtWidgets.QPushButton(icon=getIcon('mdi6.plus'), text='Enable addon')
        btn.clicked.connect(lambda checked: self._addAddon())
        container.layout().addWidget(btn)
        self._addAddonBtn = btn

        container.layout().addStretch()

        title = 'Settings'
        dock = Dock(
            name=self._key + title,
            closable=False,
            title=title,
            affinities=[self._key])
        self._wdgt.addDock(dock, position='right')
        self._infoDW = dock
        container = QtWidgets.QWidget()
        self._settingsContainer = container
        dock.addWidget(container)
        container.setLayout(QtWidgets.QVBoxLayout())

        container = QtWidgets.QGroupBox('Session info')
        self._settingsContainer.layout().addWidget(container)
        container.setLayout(QtWidgets.QFormLayout())
        self._infoContainer = container

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

        container = QtWidgets.QGroupBox('Appearance')
        self._settingsContainer.layout().addWidget(container)
        container.setLayout(QtWidgets.QFormLayout())
        container.layout().setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._appearanceContainer = container

        wdgt = QtWidgets.QComboBox()
        wdgt.addItems(['Auto', 'Light', 'Dark'])
        wdgt.setCurrentIndex(1)
        wdgt.setMinimumWidth(80)
        wdgt.currentIndexChanged.connect(lambda index: self._onThemeDropdownChanged())
        container.layout().addRow('Theme', wdgt)
        self._themeDropdown = wdgt

        wdgt = QtWidgets.QSpinBox()
        wdgt.setRange(4, 64)
        wdgt.setSingleStep(1)
        wdgt.setMinimumWidth(80)
        wdgt.valueChanged.connect(self._onFontSizeFieldChanged)
        wdgt.clear()
        wdgt.lineEdit().setPlaceholderText('Default')
        container.layout().addRow('Font size', wdgt)
        self._fontSizeField = wdgt

        container = QtWidgets.QGroupBox('About')
        self._settingsContainer.layout().addWidget(container)
        container.setLayout(QtWidgets.QFormLayout())
        container.layout().setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        wdgt = QtWidgets.QLabel(__version__)
        self._infoWdgts['version'] = wdgt
        container.layout().addRow('NaviNIBS version', wdgt)

        wdgt = QtWidgets.QPushButton('Open NaviNIBS documentation')
        wdgt.clicked.connect(lambda *args: QtGui.QDesktopServices.openUrl('https://precisionneurolab.github.io/navinibs-docs/'))
        container.layout().addRow('Help', wdgt)

        self._settingsContainer.layout().addStretch()

        self._updateEnabledWdgts()

        self._autosaveTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._autosaveOccasionally))

    def addSaveButtonToDockTabStrip(self, dockArea: DockArea):
        # hack in a save button next to root dock area tab strip

        tCnt = dockArea.topContainer
        if not isinstance(tCnt, TContainer):
            # find shallowest TContainer child

            def findTContainer(child) -> tuple[TContainer | None, int | None]:
                if isinstance(child, TContainer):
                    return child, 0

                if not isinstance(child, pgdc.SplitContainer):
                    return None, None

                tCnt = None
                depth = None
                for i in range(child.count()):
                    tCnt_i, depth_i = findTContainer(child.widget(i))
                    if tCnt_i is not None:
                        if depth is None or depth_i < depth:
                            tCnt = tCnt_i
                            depth = depth_i + 1
                return tCnt, depth

            tCnt, _ = findTContainer(tCnt)

        assert isinstance(tCnt, TContainer)

        if self._rootDockArea is not None:
            assert self._rootDockArea is dockArea
            assert self._tabSaveBtn is not None
            #tCnt.layout.takeAt(tCnt.layout.indexOf(self._tabSaveBtn))
            self._tabSaveBtn = None
        else:
            assert self._tabSaveBtn is None, 'Save button already added to dock tab strip'
            self._rootDockArea = dockArea

        btn = QtWidgets.QPushButton(icon=getIcon('mdi6.content-save'), text='Save')
        btn.clicked.connect(self._onSaveSessionBtnClicked)
        btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
        self._tabSaveBtn = btn

        tCnt.layout.addWidget(btn, 0, 0)  # other widgets in TContainer already start at column index 1

        # update tCnt.stack to span two columns in grid layout
        tCnt.layout.takeAt(tCnt.layout.indexOf(tCnt.stack))
        tCnt.layout.addWidget(tCnt.stack, 1, 0, 1, 2)

        self._updateEnabledWdgts()
        self._updateSaveBtnStyle()

    def _onSessionSet(self):
        self._updateEnabledWdgts()
        if self.session is not None:
            self.session.sigInfoChanged.connect(self._onSessionInfoChanged)
            self.session.sigDirtyKeysChanged.connect(self._updateSaveBtnStyle)
            self.session.miscSettings.sigAttribsChanged.connect(self._onSessionMiscSettingsChanged)
        self._onSessionInfoChanged()
        self._onSessionMiscSettingsChanged()

    def _onThemeDropdownChanged(self):
        theme = self._themeDropdown.currentText()
        self.session.miscSettings.theme = theme.lower()

    def _onFontSizeFieldChanged(self, *args):
        fontSize = self._fontSizeField.value()
        self.session.miscSettings.mainFontSize = fontSize

    def _getNewInProgressSessionDir(self, suffix: str = '') -> str:
        return os.path.join(self._inProgressBaseDir, 'NaviNIBSSession_' + suffix + datetime.today().strftime('%y%m%d%H%M%S'))

    def _updateEnabledWdgts(self):
        wdgts = [self._saveBtn, self._saveToFileBtn, self._saveToDirBtn,
                 self._importBtn,
                 self._closeBtn, self._addAddonBtn,
                 self._infoContainer,
                 self._appearanceContainer]
        if self._tabSaveBtn is not None:
            wdgts.append(self._tabSaveBtn)
        for wdgt in wdgts:
            try:
                wdgt.setEnabled(self.session is not None)
            except Exception as e:
                pass  # widget may have been deleted

    def _updateSaveBtnStyle(self):
        btn = self._tabSaveBtn
        if btn is None:
            return
        isDirty = self.session is not None and len(self.session.dirtyKeys) > 0
        if self._saveBtnShowsDirty is not None and isDirty == self._saveBtnShowsDirty:
            return
        self._saveBtnShowsDirty = isDirty
        palette = self.wdgt.palette()
        if isDirty:
            fg = palette.color(QtGui.QPalette.Active, QtGui.QPalette.Text).name()
        else:
            fg = palette.color(QtGui.QPalette.Disabled, QtGui.QPalette.Text).name()
        try:
            btn.setStyleSheet(f"""
                    QPushButton {{
                        border: none;
                        color: {fg};
                    }}
                    QPushButton:hover {{background-color: rgba(0, 0, 0, 0.1);}}
                    QPushButton:pressed {{background-color: rgba(0, 0, 0, 0.2);}}
                """)
        except Exception as e:
            # button may have been deleted
            pass

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

    def _addAddon(self, addonFilepath: str | None = None):
        if addonFilepath is None:
            addonFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(
                self._wdgt,
                'Select addon configuration file',
                addonBaseInstallPath,
                'Addon configuration file (*.json)')
            if len(addonFilepath) == 0:
                logger.info('Browse addon file cancelled')
                return

        logger.info('Selected addon file: {}'.format(addonFilepath))

        addonDict = dict()
        addonDirPath = os.path.dirname(addonFilepath)
        addonDict['addonInstallPath'] = os.path.relpath(addonDirPath, addonBaseInstallPath)

        addon = Addon.fromDict(addonDict)

        self.session.addons.addItem(addon)

        logger.info(f'Added addon {addon.key}')

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

    def _loadSession(self, sesFilepath: str, unpackedSessionSuffix: str = '') -> Session | None:
        if sesFilepath is None:
            if self.session is not None:
                dir = os.path.dirname(self.session.filepath)
            else:
                if False:
                    raise NotImplementedError()  # TODO: set to location of recent dir if available
                    dir = 'todo'
                else:
                    dir = str(pathlib.Path.home())
            sesFilepath, _ = QtWidgets.QFileDialog.getOpenFileName(self._wdgt, 'Choose session to load', dir, 'Session file (*.navinibs); Config file (*.json)')
            if len(sesFilepath) == 0:
                logger.info('Browse existing session cancelled')
                return None

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
                session = Session.loadFromFile(filepath=sesFilepath, unpackedSessionDir=self._getNewInProgressSessionDir(suffix=unpackedSessionSuffix))
        except Exception as e:
            raiseErrorDialog(f'Problem loading session from {sesFilepath}', exception=e)
            return None

        return session

    def loadSession(self, sesFilepath: tp.Optional[str] = None):
        self._tryVerifyThenCloseSession()

        session = self._loadSession(sesFilepath=sesFilepath)

        if session is None:
            return

        self.sigAboutToFinishLoadingSession.emit(session)
        self.session = session
        try:
            self.sigLoadedSession.emit(self.session)
        except Exception as e:
            logger.error('Problem handling loaded session:\n{}'.format(exceptionToStr(e)))
            raise e

    def importSession(self, sesFilepath: tp.Optional[str] = None):
        logger.info(f'Loading other session for import from {sesFilepath}')
        otherSession = self._loadSession(sesFilepath=sesFilepath, unpackedSessionSuffix='TmpImport')
        assert self._importSessionWindow is None
        self._importSessionWindow = ImportSessionWindow(
            parent=self._navigatorGUI._win,
            session=self.session,
            otherSession=otherSession
        )
        self._importSessionWindow.sigFinished.connect(self._onImportWindowFinished)
        self._importSessionWindow.show()

    def _onImportWindowFinished(self, didAccept: bool):
        assert self._importSessionWindow is not None
        self._importSessionWindow.sigFinished.disconnect(self._onImportWindowFinished)
        self._importSessionWindow = None
        logger.info('Import session window closed')

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

    def _onSessionMiscSettingsChanged(self, whatChanged: tp.Optional[list[str]] = None):
        if whatChanged is None or 'theme' in whatChanged:
            theme = self.session.miscSettings.theme.capitalize()
            self._themeDropdown.setCurrentText(theme)

        if whatChanged is None or 'mainFontSize' in whatChanged:
            fontSize = self.session.miscSettings.mainFontSize
            if fontSize is None:
                self._fontSizeField.clear()
            else:
                self._fontSizeField.setValue(fontSize)

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

    def restoreLayoutIfAvailable(self) -> bool:
        if not super().restoreLayoutIfAvailable():
            return False

        if self._rootDockArea is not None:
            self.addSaveButtonToDockTabStrip(self._rootDockArea)

        return True