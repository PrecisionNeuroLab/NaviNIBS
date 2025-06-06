import asyncio
import logging
import glob
import os
import pyperclip
import pytest
import shutil

from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
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
@pytest.mark.skip(reason='For troubleshooting')
async def test_openHeadModelSession(workingDir):
    await utils.openSessionForInteraction(workingDir, 'SetHeadModel')


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
    await navigatorGUI.headModelPanel.finishedAsyncInit.wait()

    assert navigatorGUI.activeViewKey == navigatorGUI.headModelPanel.key

    headModelSourceDir, headModelMeshName = os.path.split(headModelDataSourcePath)
    headModelDirName = os.path.split(headModelSourceDir)[1]
    headModelTestDir = os.path.join(sessionPath, '..', headModelDirName)
    if len(glob.glob(os.path.join(headModelTestDir, '*.msh'))) < 1:
        shutil.copytree(headModelSourceDir, headModelTestDir, dirs_exist_ok=True)

    headModelTestSourcePath = os.path.join(headModelTestDir, headModelMeshName)

    navigatorGUI.headModelPanel._filepathWdgt.filepath = headModelTestSourcePath

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    assert os.path.normpath(ses.headModel.filepath) == os.path.normpath(headModelTestSourcePath)

    for view in navigatorGUI.headModelPanel._views.values():
        await view.redrawQueueIsEmpty.wait()
    await asyncio.sleep(1.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='SetHeadModel',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

