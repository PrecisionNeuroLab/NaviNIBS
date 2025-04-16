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
    await navigatorGUI.planFiducialsPanel.finishedAsyncInit.wait()
    await asyncio.sleep(.1)
    for view in navigatorGUI.planFiducialsPanel._views.values():
        await view.redrawQueueIsEmpty.wait()

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

    # TODO: trigger fiducials table column size refresh or wait for it to automatically refresh

    for view in navigatorGUI.planFiducialsPanel._views.values():
        await view.redrawQueueIsEmpty.wait()
    await asyncio.sleep(1.)

    screenshotPath = os.path.join(sessionPath, 'PlanFiducials_Autoset.png')
    await utils.raiseMainNavigatorGUI()
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'PlanFiducials_Autoset.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    # TODO: add additional test procedures + assertions for manually editing existing fiducials,
    #  creating new fiducials, and deleting existing fiducials