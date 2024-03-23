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
def headModelDataSourcePath(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testSourceData',
                        'sub-test_T1Seq-SagFSPGRBRAVO_SimNIBS', 'sub-test.msh')


@pytest.mark.asyncio
@pytest.mark.order(after='test_MRI.py::test_setMRIInfo')
async def test_setHeadModel(navigatorGUIWithoutSession: NavigatorGUI,
                          workingDir: str,
                          headModelDataSourcePath: str,
                          screenshotsDataSourcePath: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'SetMRI', 'SetHeadModel')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on head model tab
    navigatorGUI._activateView(navigatorGUI.headModelPanel.key)

    # give time for initialization
    # (TODO: wait for signal to indicate tab is ready instead of waiting fixed time here)
    await asyncio.sleep(10.)

    assert navigatorGUI.activeViewKey == navigatorGUI.headModelPanel.key

    headModelSourceDir, headModelMeshName = os.path.split(headModelDataSourcePath)
    headModelDirName = os.path.split(headModelSourceDir)[1]
    headModelTestDir = os.path.join(sessionPath, '..', headModelDirName)
    if not os.path.exists(headModelTestDir):
        shutil.copytree(headModelSourceDir, headModelTestDir)

    headModelTestSourcePath = os.path.join(headModelTestDir, headModelMeshName)

    navigatorGUI.headModelPanel._filepathWdgt.filepath = headModelTestSourcePath

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    assert os.path.normpath(ses.headModel.filepath) == os.path.normpath(headModelTestSourcePath)

    # TODO: wait for signal to indicate plots have been updated instead of waiting fixed time here
    await asyncio.sleep(60.)
    screenshotPath = os.path.join(sessionPath, 'SetHeadModel.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                  os.path.join(screenshotsDataSourcePath, 'SetHeadModel.png'),
                  doAssertEqual=utils.doAssertScreenshotsEqual)
