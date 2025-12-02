import asyncio
import logging

import numpy as np
import pytest


from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from NaviNIBS.Navigator.GUI.Widgets.EditROIWidget import EditPipelineROIInnerWidget
from NaviNIBS.Navigator.GUI.Widgets import EditROIStageWidgets as StageWidgets
from NaviNIBS.Navigator.Model import ROIs

from tests.test_NavigatorGUI import utils
from tests.test_NavigatorGUI.utils import (
    existingResourcesDataPath,
    navigatorGUIWithoutSession,
    workingDir,
    screenshotsDataSourcePath)

logger = logging.getLogger(__name__)


@pytest.fixture
def exampleCorticalROISeedPointsAndRadii():
    return np.asarray([
        [-33.4, -15.4, 65.2],
        [-26.5, -17.9, 68.4],
        [-35.8, -7.2, 63.1],
    ]), [7, 7, 8]

@pytest.fixture
def exampleCSFROISeedPointAndRadius():
    return np.asarray([-33.5, 57.4, 63]), 20

@pytest.mark.asyncio
@pytest.mark.skip(reason='For troubleshooting')
async def test_openSetROIsSession(workingDir):
    await utils.openSessionForInteraction(workingDir, 'SetROIs')


@pytest.mark.asyncio
@pytest.mark.order(after='test_planFiducials.py::test_planFiducials')
async def test_setROIs(navigatorGUIWithoutSession: NavigatorGUI,
                       workingDir: str,
                       screenshotsDataSourcePath: str,
                       exampleCorticalROISeedPointsAndRadii,
                       exampleCSFROISeedPointAndRadius,

                       ):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'PlanFiducials', 'SetROIs')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on head model tab
    navigatorGUI._activateView(navigatorGUI.roisPanel.key)

    # give time for initialization
    await navigatorGUI.roisPanel.finishedAsyncInit.wait()

    assert navigatorGUI.activeViewKey == navigatorGUI.roisPanel.key

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='ROIs_Empty',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    logger.info('Adding ROI')
    navigatorGUI.roisPanel._addBtn.click()

    assert len(navigatorGUI.session.ROIs) == 1
    originalROIKey = list(navigatorGUI.session.ROIs.keys())[-1]
    newROIKey = 'M1'
    navigatorGUI.session.ROIs[originalROIKey].key = newROIKey

    await asyncio.sleep(0.1)

    navigatorGUI.roisPanel._tableWdgt.resizeColumnsToContents()

    roi = navigatorGUI.session.ROIs[newROIKey]
    assert isinstance(roi, ROIs.PipelineROI)
    assert roi.key == newROIKey

    # equivalent to clicking on corresponding entry in table
    navigatorGUI.roisPanel._tableWdgt.currentCollectionItemKey = newROIKey

    await asyncio.sleep(0.5)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='ROIs_1',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # click add stage
    roiWdgt = navigatorGUI.roisPanel._editROIWdgt._roiSpecificInnerWdgt
    assert isinstance(roiWdgt, EditPipelineROIInnerWidget)
    roiWdgt._addStageBtn.click()

    # change stage to select surface mesh
    assert len(roiWdgt._stageWidgets) == 1
    stageWdgt = roiWdgt._stageWidgets[0]
    assert isinstance(stageWdgt, StageWidgets.PassthroughStageWidget)
    stageWdgt._typeField.setCurrentText(ROIs.SelectSurfaceMesh.type)

    # set surface mesh
    await asyncio.sleep(0.1)
    assert len(roiWdgt._stageWidgets) == 1
    stageWdgt = roiWdgt._stageWidgets[0]
    assert isinstance(stageWdgt, StageWidgets.SelectSurfaceMeshStageWidget)
    # set surface mesh to cortex
    stageWdgt._meshComboBox.setCurrentText('gmSurf')

    await asyncio.sleep(0.1)

    # add a passthrough stage that will stay at the end as a test
    roiWdgt._addStageBtn.click()
    assert len(roiWdgt._stageWidgets) == 2

    for pt, radius in zip(*exampleCorticalROISeedPointsAndRadii):
        logger.info(f'Adding cortical ROI seed point at {pt} with radius {radius}')

        # insert before last stage
        lastWdgt = roiWdgt._stageWidgets[-1]
        assert isinstance(lastWdgt, StageWidgets.PassthroughStageWidget)
        lastWdgt._insertStageBtn.click()

        # change to AddFromSeedPoint stage
        stageWdgt = roiWdgt._stageWidgets[-2]
        stageWdgt._typeField.setCurrentText(ROIs.AddFromSeedPoint.type)

        await asyncio.sleep(0.1)
        stageWdgt = roiWdgt._stageWidgets[-2]
        assert isinstance(stageWdgt, StageWidgets.AddFromSeedPointStageWidget)

        stageWdgt._stage.seedPoint = pt

        await asyncio.sleep(0.5)

        stageWdgt._radiusField.setValue(radius)

        await asyncio.sleep(0.5)

    navigatorGUI.roisPanel._queueRedraw('cameraPos')  # reorient camera to see ROI

    await asyncio.sleep(0.5)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='ROIs_2',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # delete passthrough widget at end as a test
    numSeedPoints = len(exampleCorticalROISeedPointsAndRadii[0])
    assert len(roiWdgt._stageWidgets) == 2 + numSeedPoints
    assert len(roi.stages) == 2 + numSeedPoints
    lastWdgt = roiWdgt._stageWidgets[-1]
    assert isinstance(lastWdgt, StageWidgets.PassthroughStageWidget)
    lastWdgt._deleteStageBtn.click()
    await asyncio.sleep(0.5)
    assert len(roiWdgt._stageWidgets) == 1 + numSeedPoints
    assert len(roi.stages) == 1 + numSeedPoints

    # save
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='ROIs_3',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    assert ses.ROIs[newROIKey].key == newROIKey
    assert len(ses.ROIs[newROIKey].stages) == 1 + numSeedPoints

    # TODO: duplicate this ROI, edit it, delete it, etc.

    navigatorGUI.roisPanel._duplicateBtn.click()

    await asyncio.sleep(0.5)

    assert len(navigatorGUI.session.ROIs) == 2
    originalROIKey = list(navigatorGUI.session.ROIs.keys())[-1]
    newROIKey = 'PFC'
    navigatorGUI.session.ROIs[originalROIKey].key = newROIKey

    await asyncio.sleep(0.5)

    roi = navigatorGUI.session.ROIs[newROIKey]
    assert roi.session is not None
    assert isinstance(roi, ROIs.PipelineROI)
    assert roi.key == newROIKey

    # make changes programmatically instead of through GUI elements
    # keep only first 2 stages
    assert roi.stages.session is not None
    roi.stages[:] = roi.stages[:2]
    assert len(roi.stages) == 2
    surfStage = roi.stages[0]
    assert isinstance(surfStage, ROIs.SelectSurfaceMesh)
    surfStage.meshKey = 'csfSurf'  # change selected mesh
    addPointsStage = roi.stages[1]
    assert isinstance(addPointsStage, ROIs.AddFromSeedPoint)
    addPointsStage.seedPoint = exampleCSFROISeedPointAndRadius[0]  # change seed point
    addPointsStage.radius = exampleCSFROISeedPointAndRadius[1]  # change radius

    await asyncio.sleep(1)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='ROIs_4',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    roi.isVisible = False

    await asyncio.sleep(0.5)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='ROIs_5',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)
    ses = utils.assertSavedSessionIsValid(sessionPath)
