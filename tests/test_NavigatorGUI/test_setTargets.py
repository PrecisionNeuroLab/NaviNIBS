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
def targetsDataSourcePath(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testSourceData',
                        'sub-test_ExampleTargets.json')


@pytest.mark.asyncio
@pytest.mark.order(after='test_planFiducials.py::test_planFiducials')
async def test_setTargets(navigatorGUIWithoutSession: NavigatorGUI,
                          workingDir: str,
                          targetsDataSourcePath: str,
                          screenshotsDataSourcePath: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'PlanFiducials', 'SetTargets')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.setTargetsPanel.key)

    # give time for initialization
    # (TODO: wait for signal to indicate tab is ready instead of waiting fixed time here)
    await asyncio.sleep(60.)

    assert navigatorGUI.activeViewKey == navigatorGUI.setTargetsPanel.key

    screenshotPath = os.path.join(sessionPath, 'SetTargets_Empty.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    # compareImages(screenshotPath,
    #               os.path.join(screenshotsDataSourcePath, 'SetTargets_Empty.png'),
    #               doAssertEqual=doAssertScreenshotsEqual)

    # equivalent to clicking "Import targets from file..." button and browsing to file
    navigatorGUI.setTargetsPanel._importTargetsFromFile(targetsDataSourcePath)

    # equivalent to clicking on corresponding entry in table
    navigatorGUI.setTargetsPanel._tableWdgt.currentCollectionItemKey = 't2-45'

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    assert ses.targets['t2-45'].targetCoord.round(1).tolist() == [-30.1, 32.0, 52.9]

    # TODO: wait for signal to indicate plots have been updated instead of waiting fixed time here
    await asyncio.sleep(60.)
    screenshotPath = os.path.join(sessionPath, 'SetTargets_ImportedAndSelected.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    # TODO: update ground-truth screenshot to not include a new grid target by default immediately
    # after import (currently there due to a grid edit GUI bug that applies grid too aggressively)

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'SetTargets_ImportedAndSelected.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    # TODO: add additional test procedures + assertions for manually editing existing targets
    #  creating new targets, and deleting existing targets
