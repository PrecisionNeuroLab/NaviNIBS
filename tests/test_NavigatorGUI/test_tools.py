import asyncio
import logging
import os
import pyperclip
import pytest
import shutil

from RTNaBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from tests.test_NavigatorGUI import utils
from tests.test_NavigatorGUI.utils import (
    existingResourcesDataPath,
    navigatorGUIWithoutSession,
    workingDir,
    screenshotsDataSourcePath)

logger = logging.getLogger(__name__)


@pytest.fixture
def toolsDataSourcePath(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testSourceData',
                        'ExampleTools_Minimal.json')


@pytest.mark.asyncio
@pytest.mark.order(after='test_setTargets.py::test_setTargets')
async def test_setTools(navigatorGUIWithoutSession: NavigatorGUI,
                          workingDir: str,
                          toolsDataSourcePath: str,
                          screenshotsDataSourcePath: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'SetTargets', 'SetTools')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.toolsPanel.key)

    # give time for initialization
    # (TODO: wait for signal to indicate tab is ready instead of waiting fixed time here)
    await asyncio.sleep(10.)

    assert navigatorGUI.activeViewKey == navigatorGUI.toolsPanel.key

    # equivalent to clicking "Import tools from file..." button and browsing to file
    navigatorGUI.toolsPanel._importToolsFromFile(toolsDataSourcePath)

    # equivalent to clicking on corresponding entry in table
    navigatorGUI.toolsPanel._tblWdgt.currentCollectionItemKey = 'Pointer'

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    assert 'Pointer' in ses.tools

    # TODO: wait for signal to indicate plots have been updated instead of waiting fixed time here
    await asyncio.sleep(60.)
    screenshotPath = os.path.join(sessionPath, 'SetTools_ImportedAndSelected.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'SetTools_ImportedAndSelected.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    # TODO: add additional test procedures + assertions for manually editing existing tools
    #  creating new tools, and deleting existing tools, calibrating, etc.