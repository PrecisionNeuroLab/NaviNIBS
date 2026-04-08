import asyncio
import logging
import os
import shutil
import time

import pytest

from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model.Targets import Target
from tests.test_NavigatorGUI import utils
from tests.test_NavigatorGUI.utils import (
    navigatorGUIWithoutSession,
    workingDir,
)

logger = logging.getLogger(__name__)

_autosaveRestoreSessionKey = 'AutosaveRestore'
_testTargetKey = 'TestAutosaveTarget'
_dialogTitle = 'Restore unsaved changes?'


def _setupAutosaveRestoreSession(workingDir: str) -> str:
    """
    Copy BasicNavigationManualSampling to AutosaveRestore, load it, add a distinctive
    target, save it as an autosave only (no regular save), and adjust the main config
    mtime so the autosave appears newer than the last regular save.
    Returns the session folder path.
    """
    sessionPath = utils.copySessionFolder(
        workingDir, 'BasicNavigationManualSampling', _autosaveRestoreSessionKey)

    # Remove any pre-existing autosave files to avoid interference
    autosaveDir = os.path.join(sessionPath, 'autosaved')
    if os.path.isdir(autosaveDir):
        shutil.rmtree(autosaveDir)

    # Load the session directly and add a distinctive traceable target
    session = Session.loadFromFolder(sessionPath)
    session.targets.addItem(Target(key=_testTargetKey))

    # Save as autosave only (no regular save), creating autosaved/autosaved-TIMESTAMP_*.json
    session.saveToUnpackedDir(asAutosave=True)

    # Set the main config mtime to clearly before the autosave timestamp so findAutosaves
    # detects it as newer
    mainConfigPath = os.path.join(sessionPath, Session._sessionConfigFilename + '.json')
    t_before = time.time() - 60
    os.utime(mainConfigPath, (t_before, t_before))

    autosaves = Session.findAutosaves(sessionPath)
    assert len(autosaves) == 1, f'Expected 1 qualifying autosave, got {len(autosaves)}'

    return sessionPath


async def _waitForAutosaveDialog(timeout: float = 30.0):
    from qtpy import QtWidgets
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    logger.debug(f'Waiting for autosave dialog (timeout={timeout}s)')
    while loop.time() < deadline:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if isinstance(widget, QtWidgets.QDialog) \
                    and widget.windowTitle() == _dialogTitle:
                logger.debug('Autosave dialog found')
                return widget
        await asyncio.sleep(0.5)
    raise RuntimeError(f'Autosave restore dialog not found within {timeout}s')


async def _waitForSessionLoaded(manageSessionPanel, timeout: float = 30.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    logger.debug(f'Waiting for session to load (timeout={timeout}s)')
    while loop.time() < deadline:
        if manageSessionPanel.session is not None:
            logger.debug('Session loaded')
            return
        await asyncio.sleep(0.1)
    raise RuntimeError('Session not loaded within timeout')


@pytest.mark.asyncio
@pytest.mark.order(after='test_basicNavigation.py::test_basicNavigation_manualSampling')
async def test_autosaveRestore_decline(navigatorGUIWithoutSession: NavigatorGUI,
                                       workingDir: str):
    """User declines restoration: session should load without the autosaved target."""
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = _setupAutosaveRestoreSession(workingDir)
    logger.info(f'Session path: {sessionPath}')

    logger.info('Calling loadSession')
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath, offerAutosaveRestore=True)
    logger.info('loadSession returned; waiting for autosave dialog')

    dlg = await _waitForAutosaveDialog()
    logger.info('Dialog found; rejecting')
    dlg.reject()
    logger.info('Dialog rejected; waiting for session to load')

    await _waitForSessionLoaded(navigatorGUI.manageSessionPanel)
    logger.info('Session loaded')

    session = navigatorGUI.manageSessionPanel.session
    assert session is not None
    assert _testTargetKey not in session.targets
    logger.info(f'Assertion passed: {_testTargetKey!r} not in session targets')


@pytest.mark.asyncio
@pytest.mark.order(after='test_basicNavigation.py::test_basicNavigation_manualSampling')
async def test_autosaveRestore_accept(navigatorGUIWithoutSession: NavigatorGUI,
                                      workingDir: str):
    """User accepts restoration: session should load with the autosaved target."""
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = _setupAutosaveRestoreSession(workingDir)
    logger.info(f'Session path: {sessionPath}')

    logger.info('Calling loadSession')
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath, offerAutosaveRestore=True)
    logger.info('loadSession returned; waiting for autosave dialog')

    dlg = await _waitForAutosaveDialog()
    logger.info('Dialog found; taking screenshot')

    # Screenshot the dialog then hold for 1 second before accepting
    screenshotPath = os.path.join(sessionPath, 'autosaveRestoreDialog.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath, wdgt=dlg)
    logger.info(f'Screenshot saved to {screenshotPath}; sleeping 1s')
    await asyncio.sleep(5.)

    logger.info('Accepting dialog')
    dlg.accept()
    logger.info('Dialog accepted; waiting for session to load')

    await _waitForSessionLoaded(navigatorGUI.manageSessionPanel)
    logger.info('Session loaded')

    session = navigatorGUI.manageSessionPanel.session
    assert session is not None
    assert _testTargetKey in session.targets
    logger.info(f'Assertion passed: {_testTargetKey!r} in session targets')

    await asyncio.sleep(10.)