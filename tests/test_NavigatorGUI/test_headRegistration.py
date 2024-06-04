import asyncio
import logging
import numpy as np
import os
import pyperclip
import pytest
import shutil

from RTNaBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from RTNaBS.util.Transforms import applyTransform, invertTransform, composeTransform, concatenateTransforms
from RTNaBS.util.numpy import array_equalish
from tests.test_NavigatorGUI import utils
from tests.test_NavigatorGUI.utils import (
    existingResourcesDataPath,
    navigatorGUIWithoutSession,
    workingDir,
    screenshotsDataSourcePath)

logger = logging.getLogger(__name__)


@pytest.fixture
def trackerToMRITransf():
    return np.asarray([
        [0.9678884219692884, 0.12098540650006323, 0.22035093381198817, 34.45752550020878],
        [-0.2511741588202068, 0.42996526829785314, 0.8672032114784379, 124.75547193939892],
        [0.010175684682725016, -0.8947024083300377, 0.4465468127415853, 47.33965520789809],
        [0.0, 0.0, 0.0, 1.0]
    ])


@pytest.mark.asyncio
@pytest.mark.order(after='test_tools.py::test_calibrateCoil')
async def test_initialFiducialRegistration(navigatorGUIWithoutSession: NavigatorGUI,
                                           workingDir: str,
                                           screenshotsDataSourcePath: str,
                                           trackerToMRITransf: np.ndarray):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'CalibrateCoil', 'InitialFiducialRegistration')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.subjectRegistrationPanel.key)

    # give time for initialization
    # (TODO: wait for signal to indicate tab is ready instead of waiting fixed time here)
    await asyncio.sleep(10.)

    assert navigatorGUI.activeViewKey == navigatorGUI.subjectRegistrationPanel.key

    if True:  # TODO: debug, set back to True
        screenshotPath = os.path.join(sessionPath, 'HeadRegistration_Empty.png')
        utils.captureScreenshot(navigatorGUI, screenshotPath)
        pyperclip.copy(str(screenshotPath))

        utils.compareImages(screenshotPath,
                            os.path.join(screenshotsDataSourcePath, 'HeadRegistration_Empty.png'),
                            doAssertEqual=utils.doAssertScreenshotsEqual)

    reg = navigatorGUI.session.subjectRegistration
    fids = reg.fiducials

    plannedFidCoords_MRISpace = {key: fid.plannedCoord for key, fid in fids.items() if 'Nose' not in key}
    assert len(plannedFidCoords_MRISpace) == 3

    MRIToTrackerTransf = invertTransform(trackerToMRITransf)

    toSampleFidCoords_trackerSpace = {key: applyTransform(MRIToTrackerTransf, coord)
                                      for key, coord in plannedFidCoords_MRISpace.items()}

    # TODO: set tracker and pointer positions, sample each fiducial
    logger.debug(f'Getting subject tracker pose')
    trackerKey = navigatorGUI.session.tools.subjectTracker.key
    trackerPose_worldSpace = navigatorGUI.subjectRegistrationPanel._positionsClient.getLatestTransf(trackerKey)

    pointerKey = navigatorGUI.session.tools.pointer.key
    for fidKey, coord in toSampleFidCoords_trackerSpace.items():
        logger.debug(f'Registering fiducial {fidKey}')
        pointerPose_trackerSpace = composeTransform(np.eye(3), coord)  # TODO: check sign on coord
        pointerPose_worldSpace = concatenateTransforms((pointerPose_trackerSpace,
                                                       trackerPose_worldSpace))
        await utils.setSimulatedToolPose(navigatorGUI=navigatorGUI,
                                   key=pointerKey,
                                   transf=pointerPose_worldSpace)

        await asyncio.sleep(1.)

        # equivalent to clicking on corresponding entry in table
        navigatorGUI.subjectRegistrationPanel._fidTblWdgt.currentCollectionItemKey = fidKey

        await asyncio.sleep(1.)

        # equivalent to clicking on sample button
        navigatorGUI.subjectRegistrationPanel._sampleFiducialBtn.click()

    await asyncio.sleep(1.)

    # equivalent to clicking on align button
    navigatorGUI.subjectRegistrationPanel._alignToFiducialsBtn.click()

    await asyncio.sleep(1.)

    screenshotPath = os.path.join(sessionPath, 'HeadRegistration_InitialFiducials.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'HeadRegistration_InitialFiducials.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    # new transform should be identical(ish) to planned transform
    assert array_equalish(ses.subjectRegistration.trackerToMRITransf, trackerToMRITransf)

    