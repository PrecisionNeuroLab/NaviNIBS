import asyncio
import logging
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
def mriDataSourcePath(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testSourceData',
                        'sub-test_wt-T1_seq-SagFSPGRBRAVO_MRI.nii.gz')


@pytest.mark.asyncio
@pytest.mark.skip(reason='For troubleshooting')
async def test_openMRISession(workingDir):
    await utils.openSessionForInteraction(workingDir, 'SetMRI')


@pytest.mark.asyncio
@pytest.mark.order(after='test_manageSession.py::test_createSessionViaGUI')
async def test_setMRIInfo(navigatorGUIWithoutSession: NavigatorGUI,
                          workingDir: str,
                          mriDataSourcePath: str,
                          screenshotsDataSourcePath: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'InfoOnly', 'SetMRI')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on MRI tab
    navigatorGUI._activateView(navigatorGUI.mriPanel.key)

    # give time for initialization
    # (TODO: wait for signal to indicate tab is ready instead of waiting fixed time here)
    await asyncio.sleep(10.)

    assert navigatorGUI.activeViewKey == navigatorGUI.mriPanel.key

    mriDataFilename = os.path.split(mriDataSourcePath)[1]
    mriDataTestPath = os.path.join(sessionPath, '..', mriDataFilename)
    if not os.path.exists(mriDataTestPath):
        shutil.copy(mriDataSourcePath, mriDataTestPath)

    # equivalent to clicking browse and selecting MRI data path in GUI
    navigatorGUI.mriPanel._filepathWdgt.filepath = mriDataTestPath

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    assert os.path.normpath(ses.MRI.filepath) == os.path.normpath(mriDataTestPath)

    if True:
        # TODO: wait for signal to indicate plots have been updated instead of waiting fixed time here
        await asyncio.sleep(10.)
        screenshotPath = os.path.join(sessionPath, 'SetMRI.png')
        utils.captureScreenshot(navigatorGUI, screenshotPath)
        pyperclip.copy(str(screenshotPath))

        utils.compareImages(screenshotPath,
                      os.path.join(screenshotsDataSourcePath, 'SetMRI.png'),
                      doAssertEqual=utils.doAssertScreenshotsEqual)