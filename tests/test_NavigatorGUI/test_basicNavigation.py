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

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='BasicNav_NoTarget_NoTools',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

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

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='BasicNav_Target_SubOnly',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    await simulatedToolsPanel.importPositionsSnapshot(positionsDict=positions_coilOnly)

    await asyncio.sleep(1.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='BasicNav_Target_CoilAndSub',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # clear coil position
    await simulatedToolsPanel.clearToolPos(activeCoilKey)

    await asyncio.sleep(1.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='BasicNav_Target_CoilLost',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    await simulatedToolsPanel.importPositionsSnapshot(positionsDict=positions_coilOnly)

    await asyncio.sleep(1.)

    await simulatedToolsPanel.clearToolPos(subTrackerKey)

    await asyncio.sleep(1.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='BasicNav_Target_SubLost',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

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

    extraRot_newToOrigCoil = ptr.active_matrix_from_extrinsic_euler_xyz([np.pi/32, np.pi/32, np.pi/16])
    transf_newToOrigCoil = composeTransform(extraRot_newToOrigCoil, np.array([1, 2, 0]))

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

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='BasicNav_ManualSample_FarTarget',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    random.seed(a=1)
    numSamples = 50
    sampleShifts = np.zeros((numSamples, 6))
    shiftDist = 3.
    rotAngOutOfPlane = np.pi/64
    rotAngInPlane = np.pi/16
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

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='BasicNav_ManualSample_Samples',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    utils.assertSavedSessionIsValid(sessionPath)


@pytest.mark.asyncio
@pytest.mark.order(after='test_basicNavigation')
async def test_basicNavigation_coilChanges(navigatorGUIWithoutSession: NavigatorGUI,
                               workingDir: str,
                               screenshotsDataSourcePath: str,
                               simulatedPositionsBasicNav1Path: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'BasicNavigation', 'BasicNavigationCoilChanges')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(5.)

    for view in navigatorGUI.navigatePanel._views.values():
        if hasattr(view, 'plotter'):
            await view.plotter.isReadyEvent.wait()

    await asyncio.sleep(5.)

    # make sure that "active coil" is always the first (of possibly multiple) active coil in tools list

    coordinator = navigatorGUI.navigatePanel._coordinator
    assert coordinator.activeCoilKey == 'Coil1'

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.toolsPanel.key)

    navigatorGUI.toolsPanel._tblWdgt.currentCollectionItemKey = 'Coil1'

    await asyncio.sleep(10.)

    navigatorGUI.toolsPanel._toolWdgt._isActive.setChecked(False)
    await asyncio.sleep(1.)

    assert coordinator.activeCoilKey is None

    navigatorGUI.session.tools['Coil2'].isActive = True
    await asyncio.sleep(1.)

    assert coordinator.activeCoilKey == 'Coil2'

    navigatorGUI.session.tools['Coil1'].isActive = True
    await asyncio.sleep(1.)

    assert coordinator.activeCoilKey == 'Coil1'

    navigatorGUI.session.tools['Coil1'].isActive = False
    await asyncio.sleep(1.)

    assert coordinator.activeCoilKey == 'Coil2'

    navigatorGUI.session.tools['Coil1'].isActive = True

    # make sure that calibrating coil after navigation, then returning to navigation,
    # doesn't cause any issues
    # TODO


@pytest.mark.asyncio
@pytest.mark.order(after='test_basicNavigation')
async def test_basicNavigation_rapidPoseUpdates(navigatorGUIWithoutSession: NavigatorGUI,
                               workingDir: str,
                               screenshotsDataSourcePath: str,
                               simulatedPositionsBasicNav1Path: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'BasicNavigation', 'BasicNavigationRapidPoseUpdates')

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

    # equivalent to clicking on first target in table
    targetKey = list(navigatorGUI.session.targets.keys())[0]
    navigatorGUI.navigatePanel._targetsTableWdgt.currentCollectionItemKey = targetKey

    await asyncio.sleep(2.)

    random.seed(a=1)
    numSteps = 5000
    shiftDist = 10.
    rotAngOutOfPlane = np.pi/64
    rotAngInPlane = np.pi/8

    finalShift = np.array([shiftDist,
                                   0,
                                   0,
                                   rotAngOutOfPlane,
                                   0,
                                   rotAngInPlane])

    for i in range(numSteps):
        extraRot_newToOrigCoil = ptr.active_matrix_from_extrinsic_euler_xyz(finalShift[3:] * (i + 1) / numSteps)
        transf_newToOrigCoil = composeTransform(extraRot_newToOrigCoil,finalShift[:3] * (i + 1) / numSteps)

        newCoilTrackerPose = concatenateTransforms([
            invertTransform(transf_coilToCoilTracker),
            transf_newToOrigCoil,
            transf_coilToCoilTracker,
            transf_coilTrackerToWorld
        ])

        await utils.setSimulatedToolPose(navigatorGUI, coilTool.trackerKey, newCoilTrackerPose)

        await asyncio.sleep(0.01)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    utils.assertSavedSessionIsValid(sessionPath)


