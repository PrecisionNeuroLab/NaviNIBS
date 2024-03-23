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


@pytest.mark.asyncio
@pytest.mark.order(after='test_headModel.py::test_setHeadModel')
async def test_planFiducials(navigatorGUIWithoutSession: NavigatorGUI,
                          workingDir: str,
                          screenshotsDataSourcePath: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'SetHeadModel', 'PlanFiducials')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on plan fiducials tab
    navigatorGUI._activateView(navigatorGUI.planFiducialsPanel.key)

    # give time for initialization
    # (TODO: wait for signal to indicate tab is ready instead of waiting fixed time here)
    await asyncio.sleep(60.)

    assert navigatorGUI.activeViewKey == navigatorGUI.planFiducialsPanel.key

    screenshotPath = os.path.join(sessionPath, 'PlanFiducials_Empty.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                  os.path.join(screenshotsDataSourcePath, 'PlanFiducials_Empty.png'),
                  doAssertEqual=utils.doAssertScreenshotsEqual)

    # equivalent to clicking autoset button
    navigatorGUI.planFiducialsPanel._onAutosetBtnClicked(checked=False)

    # equivalent to clicking on corresponding entry in table
    navigatorGUI.planFiducialsPanel._tblWdgt.currentCollectionItemKey = 'RPA'

    # equivalent to clicking on goto button
    navigatorGUI.planFiducialsPanel._onGotoBtnClicked(checked=False)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    assert ses.subjectRegistration.fiducials.plannedFiducials['RPA'].round(1).tolist() == [76.4, 5.1, -35.5]

    # TODO: wait for signal to indicate plots have been updated instead of waiting fixed time here
    await asyncio.sleep(60.)
    screenshotPath = os.path.join(sessionPath, 'PlanFiducials_Autoset.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'PlanFiducials_Autoset.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    # TODO: add additional test procedures + assertions for manually editing existing fiducials,
    #  creating new fiducials, and deleting existing fiducials