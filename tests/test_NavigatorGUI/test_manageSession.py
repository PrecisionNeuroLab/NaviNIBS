import asyncio
import os
import pyperclip
import pytest
from pytestqt.qtbot import QtBot
import shutil
import logging
from qtpy import QtCore

from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from tests.test_NavigatorGUI import utils
from tests.test_NavigatorGUI.utils import (
    existingResourcesDataPath,
    navigatorGUIWithoutSession,
    workingDir,
    screenshotsDataSourcePath)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.skip(reason='Requires user input')
async def test_createNewSessionFolderWithUserInput(navigatorGUIWithoutSession: NavigatorGUI,
                                                   workingDir: str):
    newSessionPath = utils.getNewSessionPath(workingDir, 'New')
    assert not os.path.exists(newSessionPath)

    # copy to clipboard
    pyperclip.copy(newSessionPath)

    logger.info(f'When modal dialog opens, paste the following path and press enter: {newSessionPath}')
    navigatorGUIWithoutSession.manageSessionPanel._newSessionBtn.click()

    navigatorGUI = navigatorGUIWithoutSession

    pathsEqual = lambda a, b: os.path.normpath(a) == os.path.normpath(b)
    assert pathsEqual(navigatorGUI.session.filepath, newSessionPath)
    assert pathsEqual(navigatorGUI.session.unpackedSessionDir, newSessionPath),\
        "With a filepath input without .navinibs extension, session should be directly saved as an unpacked directory"


@pytest.mark.asyncio
@pytest.mark.skip(reason='Requires user input')
async def test_createNewSessionFileWithUserInput(navigatorGUIWithoutSession: NavigatorGUI,
                                                 workingDir: str):
    newSessionPath = utils.getNewSessionPath(workingDir, 'New', '.navinibs')
    assert not os.path.exists(newSessionPath)

    # copy to clipboard
    pyperclip.copy(newSessionPath)

    logger.info(f'When modal dialog opens, paste the following path and press enter: {newSessionPath}')
    navigatorGUIWithoutSession.manageSessionPanel._newSessionBtn.click()

    navigatorGUI = navigatorGUIWithoutSession

    pathsEqual = lambda a, b: os.path.normpath(a) == os.path.normpath(b)
    assert pathsEqual(navigatorGUI.session.filepath, newSessionPath)
    assert not pathsEqual(navigatorGUI.session.unpackedSessionDir, newSessionPath),\
        "With a filepath input with .navinibs extension, session should not be directly saved as an unpacked directory"


@pytest.mark.asyncio
async def test_createSessionViaGUI(navigatorGUIWithoutSession: NavigatorGUI,
                                   workingDir: str,
                                   screenshotsDataSourcePath: str):
    sessionPath = utils.getSessionPath(workingDir, 'InfoOnly', deleteIfExists=True)
    assert not os.path.exists(sessionPath)

    await asyncio.sleep(5.)

    assert navigatorGUIWithoutSession._win.isVisible()

    # try autosave
    # (at this time, session is None, but autosave should not generate error)
    await navigatorGUIWithoutSession.manageSessionPanel._autosave()

    await asyncio.sleep(1.)

    # create new session

    # resize window to smaller size so that screenshots are more readable when used in documentation
    navigatorGUIWithoutSession._win.resize(QtCore.QSize(1200, 800))

    await asyncio.sleep(1.)

    screenshotPath = os.path.join(workingDir, 'NoSession.png')
    utils.captureScreenshot(navigatorGUIWithoutSession, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'NoSession.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    # note: can't click and test new session file dialog due it being modal
    # so test one level lower
    navigatorGUIWithoutSession.manageSessionPanel._createNewSession(
        sesFilepath=sessionPath,
    )

    navigatorGUI = navigatorGUIWithoutSession

    pathsEqual = lambda a, b: os.path.normpath(a) == os.path.normpath(b)
    assert pathsEqual(navigatorGUI.session.filepath, sessionPath)
    assert pathsEqual(navigatorGUI.session.unpackedSessionDir, sessionPath), \
        "With a filepath input without .navinibs extension, session should be directly saved as an unpacked directory"

    await asyncio.sleep(1.)

    subjectID = 'test subject'
    sessionID = 'test session'

    wdgt = navigatorGUI.manageSessionPanel._infoWdgts['subjectID']
    QtBot.mouseDClick(wdgt, QtCore.Qt.MouseButton.LeftButton)
    QtBot.keyClicks(wdgt, subjectID)
    QtBot.keyClick(wdgt, QtCore.Qt.Key.Key_Enter)
    await asyncio.sleep(1.)
    assert navigatorGUI.session.subjectID == subjectID

    await asyncio.sleep(1.)

    wdgt = navigatorGUI.manageSessionPanel._infoWdgts['sessionID']
    QtBot.mouseDClick(wdgt, QtCore.Qt.MouseButton.LeftButton)
    QtBot.keyClicks(wdgt, sessionID)
    if False:
        QtBot.keyClick(wdgt, QtCore.Qt.Key.Key_Tab)
    else:
        QtBot.keyClick(wdgt, QtCore.Qt.Key.Key_Enter)
    await asyncio.sleep(1.)
    assert navigatorGUI.session.sessionID == sessionID

    await asyncio.sleep(1.)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    # TODO: break this apart into separate steps, save and reload after each

    # TODO: verify contents of saved files

    utils.assertSavedSessionIsValid(sessionPath)

    screenshotPath = os.path.join(sessionPath, 'CreateSession.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'CreateSession.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

