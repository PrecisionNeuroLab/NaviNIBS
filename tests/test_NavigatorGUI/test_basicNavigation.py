import asyncio
import json
import logging
import numpy as np
import os
import pyperclip
import pytest
import pytransform3d.transformations as ptt
import pytransform3d.rotations as ptr
import random
import shutil

from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from NaviNIBS.util.Transforms import applyTransform, invertTransform, composeTransform, concatenateTransforms
from NaviNIBS.util.numpy import array_equalish
from tests.test_NavigatorGUI import utils
from tests.test_NavigatorGUI.utils import (
    existingResourcesDataPath,
    navigatorGUIWithoutSession,
    workingDir,
    screenshotsDataSourcePath)

logger = logging.getLogger(__name__)


@pytest.fixture
def simulatedPositionsBasicNav1Path(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testSourceData',
                        'SimulatedPositions_BasicNavigation1.json')


@pytest.mark.asyncio
@pytest.mark.skip(reason='For troubleshooting')
async def test_openBasicNavigationSession(workingDir):
    await utils.openSessionForInteraction(workingDir, 'BasicNavigation')


@pytest.mark.asyncio
@pytest.mark.skip(reason='For troubleshooting')
async def test_openBasicNavigationSession(workingDir):
    await utils.openSessionForInteraction(workingDir, 'BasicNavigationManualSampling')


@pytest.mark.asyncio
@pytest.mark.order(after='test_headRegistration.py::test_acquireHeadPoints')
async def test_basicNavigation(navigatorGUIWithoutSession: NavigatorGUI,
                               workingDir: str,
                               screenshotsDataSourcePath: str,
                               simulatedPositionsBasicNav1Path: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'HeadPointAcquisition', 'BasicNavigation')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(5.)  # give time to restore any previous simulated positions  (TODO: handle this differently to speed up test)

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.cameraPanel.key)

    await navigatorGUI.cameraPanel._mainCameraView.plotter.isReadyEvent.wait()

    await asyncio.sleep(1.)

    from addons.NaviNIBS_Simulated_Tools.Navigator.GUI.ViewPanels.SimulatedToolsPanel import SimulatedToolsPanel
    simulatedToolsPanel: SimulatedToolsPanel = navigatorGUI._mainViewPanels['SimulatedToolsPanel']
    simulatedToolsPanel.clearAllPositions()

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.navigatePanel.key)

    await asyncio.sleep(5.)  # TODO: make dynamic instead of fixed time

    # equivalent to dragging camera panel to left of main dock and resizing
    navigatorGUI._rootDockArea.moveDock(navigatorGUI.cameraPanel.dockWdgt,
                                        'left', navigatorGUI.navigatePanel.dockWdgt)

    navigatorGUI.cameraPanel.dockWdgt.setStretch(x=5, y=10)

    for view in navigatorGUI.navigatePanel._views.values():
        if hasattr(view, 'plotter'):
            await view.plotter.isReadyEvent.wait()

    await asyncio.sleep(2.)

    screenshotPath = os.path.join(sessionPath, 'BasicNav_NoTarget_NoTools.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'BasicNav_NoTarget_NoTools.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    with open(simulatedPositionsBasicNav1Path, 'r') as f:
        positionsDict: dict[str, dict] = json.load(f)

    subTrackerKey = navigatorGUI.session.tools.subjectTracker.trackerKey
    activeCoilKey = navigatorGUI.navigatePanel._coordinator.activeCoilTool.key
    activeCoilTrackerKey = navigatorGUI.navigatePanel._coordinator.activeCoilTool.trackerKey

    positions_subOnly = {k: v for k, v in positionsDict.items() if k in (subTrackerKey,)}
    assert len(positions_subOnly) == 1
    positions_coilOnly = {k: v for k, v in positionsDict.items() if k in (activeCoilTrackerKey,)}
    assert len(positions_coilOnly) == 1

    await simulatedToolsPanel.importPositionsSnapshot(positionsDict=positions_subOnly)

    # equivalent to clicking on first target
    navigatorGUI.navigatePanel._targetsTableWdgt.currentCollectionItemKey = list(navigatorGUI.session.targets.keys())[0]

    await asyncio.sleep(5.)

    screenshotPath = os.path.join(sessionPath, 'BasicNav_Target_SubOnly.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'BasicNav_Target_SubOnly.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    await simulatedToolsPanel.importPositionsSnapshot(positionsDict=positions_coilOnly)

    await asyncio.sleep(1.)

    screenshotPath = os.path.join(sessionPath, 'BasicNav_Target_CoilAndSub.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'BasicNav_Target_CoilAndSub.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    # clear coil position
    await simulatedToolsPanel.clearToolPos(activeCoilKey)

    await asyncio.sleep(1.)

    screenshotPath = os.path.join(sessionPath, 'BasicNav_Target_CoilLost.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'BasicNav_Target_CoilLost.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    await simulatedToolsPanel.importPositionsSnapshot(positionsDict=positions_coilOnly)

    await asyncio.sleep(1.)

    await simulatedToolsPanel.clearToolPos(subTrackerKey)

    await asyncio.sleep(1.)

    screenshotPath = os.path.join(sessionPath, 'BasicNav_Target_SubLost.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'BasicNav_Target_SubLost.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    await simulatedToolsPanel.importPositionsSnapshot(positionsDict=positions_subOnly)

    await asyncio.sleep(1.)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    utils.assertSavedSessionIsValid(sessionPath)


@pytest.mark.asyncio
@pytest.mark.order(after='test_basicNavigation')
async def test_basicNavigation_manualSampling(navigatorGUIWithoutSession: NavigatorGUI,
                               workingDir: str,
                               screenshotsDataSourcePath: str,
                               simulatedPositionsBasicNav1Path: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'BasicNavigation', 'BasicNavigationManualSampling')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(5.)

    for view in navigatorGUI.navigatePanel._views.values():
        if hasattr(view, 'plotter'):
            await view.plotter.isReadyEvent.wait()

    await asyncio.sleep(5.)

    from addons.NaviNIBS_Simulated_Tools.Navigator.GUI.ViewPanels.SimulatedToolsPanel import SimulatedToolsPanel
    simulatedToolsPanel: SimulatedToolsPanel = navigatorGUI._mainViewPanels['SimulatedToolsPanel']

    coilTool = navigatorGUI.navigatePanel._coordinator.activeCoilTool
    transf_coilTrackerToWorld = navigatorGUI.navigatePanel._coordinator._positionsClient.getLatestTransf(coilTool.trackerKey)
    transf_coilToCoilTracker = navigatorGUI.navigatePanel._coordinator.activeCoilTool.toolToTrackerTransf

    extraRot_newToOrigCoil = ptr.active_matrix_from_extrinsic_euler_xyz([np.pi/32, np.pi/32, np.pi/8])
    transf_newToOrigCoil = composeTransform(extraRot_newToOrigCoil, np.array([3, 4, 0]))

    newCoilTrackerPose = concatenateTransforms([
        invertTransform(transf_coilToCoilTracker),
        transf_newToOrigCoil,
        transf_coilToCoilTracker,
        transf_coilTrackerToWorld
    ])
    newCoilTrackerPose_first = newCoilTrackerPose  # to return to later

    await utils.setSimulatedToolPose(navigatorGUI, coilTool.trackerKey, newCoilTrackerPose)

    # equivalent to clicking on first target in table
    targetKey = list(navigatorGUI.session.targets.keys())[0]
    navigatorGUI.navigatePanel._targetsTableWdgt.currentCollectionItemKey = targetKey

    await asyncio.sleep(2.)

    screenshotPath = os.path.join(sessionPath, 'BasicNav_ManualSample_FarTarget.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'BasicNav_ManualSample_FarTarget.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    random.seed(a=1)
    numSamples = 50
    sampleShifts = np.zeros((numSamples, 6))
    shiftDist = 5.
    rotAngOutOfPlane = np.pi/64
    rotAngInPlane = np.pi/8
    for i in range(numSamples):
        sampleShifts[i, :] = np.array([random.gauss(sigma=shiftDist),
                                    random.gauss(sigma=shiftDist),
                                    0,
                                    random.gauss(sigma=rotAngOutOfPlane),
                                    random.gauss(sigma=rotAngOutOfPlane),
                                    random.gauss(sigma=rotAngInPlane),])

    for i in range(numSamples):
        extraRot_newToOrigCoil = ptr.active_matrix_from_extrinsic_euler_xyz(sampleShifts[i, 3:])
        transf_newToOrigCoil = composeTransform(extraRot_newToOrigCoil, sampleShifts[i, :3])

        newCoilTrackerPose = concatenateTransforms([
            invertTransform(transf_coilToCoilTracker),
            transf_newToOrigCoil,
            transf_coilToCoilTracker,
            transf_coilTrackerToWorld
        ])

        await utils.setSimulatedToolPose(navigatorGUI, coilTool.trackerKey, newCoilTrackerPose)

        await asyncio.sleep(0.5)

        # click on sample button
        navigatorGUI.navigatePanel._sampleBtn.click()

        await asyncio.sleep(0.5)

    await utils.setSimulatedToolPose(navigatorGUI, coilTool.trackerKey, newCoilTrackerPose_first)

    navigatorGUI.navigatePanel._samplesTableWdgt.resizeColumnsToContents()

    await asyncio.sleep(2.)

    screenshotPath = os.path.join(sessionPath, 'BasicNav_ManualSample_Samples.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    utils.compareImages(screenshotPath,
                        os.path.join(screenshotsDataSourcePath, 'BasicNav_ManualSample_Samples.png'),
                        doAssertEqual=utils.doAssertScreenshotsEqual)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    utils.assertSavedSessionIsValid(sessionPath)




