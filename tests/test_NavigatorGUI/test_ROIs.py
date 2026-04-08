import asyncio
import logging

import numpy as np
import pytest

from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from NaviNIBS.Navigator.GUI.Widgets.EditROIWidget import EditPipelineROIInnerWidget
from NaviNIBS.Navigator.GUI.Widgets import EditROIStageWidgets as StageWidgets
from NaviNIBS.Navigator.Model import ROIs
from NaviNIBS.Navigator.Model.ROIs import AtlasSurfaceParcel
from NaviNIBS.Navigator.Model.ROIs.PipelineROI import PipelineROI
from NaviNIBS.Navigator.Model.ROIs import PipelineROIStages as ROIStages
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages.AddFromSeed import AddFromSeedPoint
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages.AddFromTarget import AddFromTarget

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
@pytest.mark.skip(reason='For troubleshooting')
async def test_openImportParcellationROIsSession(workingDir):
    # with utils.tracer(workingDir, 'ImportParcellationROIs', True):
    await utils.openSessionForInteraction(workingDir, 'ImportParcellationROIs')


@pytest.mark.asyncio
@pytest.mark.skip(reason='For troubleshooting')
async def test_openTargetROIsSession(workingDir):
    # with utils.tracer(workingDir, 'TargetROI', True):
    await utils.openSessionForInteraction(workingDir, 'TargetROI')

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

    # equivalent to clicking on ROIs tab
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
    assert isinstance(roi, PipelineROI)
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
    stageWdgt._typeField.setCurrentText(ROIStages.SelectSurfaceMesh.type)

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
        stageWdgt._typeField.setCurrentText(AddFromSeedPoint.type)

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
    assert isinstance(roi, PipelineROI)
    assert roi.key == newROIKey

    # make changes programmatically instead of through GUI elements
    # keep only first 2 stages
    assert roi.stages.session is not None
    roi.stages[:] = roi.stages[:2]
    assert len(roi.stages) == 2
    surfStage = roi.stages[0]
    assert isinstance(surfStage, ROIStages.SelectSurfaceMesh)
    surfStage.meshKey = 'csfSurf'  # change selected mesh
    addPointsStage = roi.stages[1]
    assert isinstance(addPointsStage, AddFromSeedPoint)
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


@pytest.mark.asyncio
@pytest.mark.order(after='test_headModel.py::test_setHeadModel')
async def test_loadParcellationROIs(
                        workingDir: str,
                       screenshotsDataSourcePath: str
                       ):

    sessionPath = utils.copySessionFolder(workingDir, 'SetCharmHeadModel', 'ROIs_LoadParcellation')

    ses = utils.assertSavedSessionIsValid(sessionPath)

    roiKey = 'dlPFCPart'

    with utils.tracer(workingDir, 'ROIs_LoadParcellation', doOpen=True):
        rois = AtlasSurfaceParcel.AtlasSurfaceParcel.loadROIsFromAtlas(
            session=ses,
            atlasKey='HCPMMP1',
        )
        ses.ROIs.merge(rois)

    roiKey = '8Av'
    roi = ses.ROIs[roiKey]
    assert isinstance(roi, ROIs.SurfaceMeshROI)

    # TODO: create a pipeline ROI merging two or more of the standard atlas parcels

    roi = ses.ROIs[roiKey]
    assert isinstance(roi, PipelineROI)
    roi.process()



@pytest.mark.asyncio
@pytest.mark.order(after='test_headModel.py::test_setHeadModel')
async def test_importSurfaceParcellationROIs(navigatorGUIWithoutSession, #: NavigatorGUI,
                       workingDir: str,
                       screenshotsDataSourcePath: str,
                       exampleCorticalROISeedPointsAndRadii,
                       exampleCSFROISeedPointAndRadius,

                       ):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'SetCharmFSHeadModel', 'ImportParcellationROIs')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on ROIs tab
    navigatorGUI._activateView(navigatorGUI.roisPanel.key)

    # give time for initialization
    await navigatorGUI.roisPanel.finishedAsyncInit.wait()

    assert navigatorGUI.activeViewKey == navigatorGUI.roisPanel.key

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    if False:
        navigatorGUI.roisPanel._onImportAtlasROIs(atlasKey='HCPMMP1')  # similar to initiating import from buttons

        # Select specific left hemisphere ROIs by name
        tree = navigatorGUI.roisPanel._atlasROIsTree
        dlg = navigatorGUI.roisPanel._atlasROIsImportDialog

        assert tree is not None
        assert dlg is not None

        leftHemROIKeys = [
            'L_V1',
            #'L_1',
            'L_3a',
            'L_3b',
            'L_4',
            'L_55b',
            'L_FEF',
            'L_6v',
            'L_8C',
            'L_8Av',
            'L_i6-8',
            'L_s6-8',
            'L_SFL',
            'L_8BL',
            'L_9p',
            'L_9a',
            'L_8Ad',
            'L_p9-46v',
            'L_a9-46v',
            'L_46',
            'L_9-46d',
        ]
        tree.clearSelection()
        for roiKey in leftHemROIKeys:
            found = False
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                if item.text(0) == roiKey + '_ROI':
                    item.setSelected(True)
                    found = True
                    break
            assert found, f'Expected to find atlas parcel {roiKey + "_ROI"!r} in parcel list'

        navigatorGUI.roisPanel._onImportAtlasROIsDialogAccepted()
    else:
        navigatorGUI.roisPanel._onImportAtlasROIs(atlasKey='HCPMMP1_combined')  # similar to initiating import from buttons
        dlg = navigatorGUI.roisPanel._atlasROIsImportDialog
        assert dlg is not None
        # Accept the dialog (call accepted handler then close dialog)
        navigatorGUI.roisPanel._onImportAtlasROIsDialogAccepted()

    navigatorGUI.roisPanel._tableWdgt.resizeColumnsToContents()

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='ParcelROIs',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)
    ses = utils.assertSavedSessionIsValid(sessionPath)
    assert len(ses.ROIs) > 0


@pytest.mark.asyncio
@pytest.mark.order(after='test_setTargets.py::test_setTargets')
async def test_targetROI(navigatorGUIWithoutSession: NavigatorGUI,
                         workingDir: str,
                         screenshotsDataSourcePath: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'SetTargets', 'TargetROI')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # navigate to ROIs panel
    navigatorGUI._activateView(navigatorGUI.roisPanel.key)
    await navigatorGUI.roisPanel.finishedAsyncInit.wait()
    assert navigatorGUI.activeViewKey == navigatorGUI.roisPanel.key

    # add a new ROI and rename it
    initialROICount = len(navigatorGUI.session.ROIs)
    navigatorGUI.roisPanel._addBtn.click()
    assert len(navigatorGUI.session.ROIs) == initialROICount + 1
    originalROIKey = list(navigatorGUI.session.ROIs.keys())[-1]
    newROIKey = 'TargetROI'
    navigatorGUI.session.ROIs[originalROIKey].key = newROIKey

    await asyncio.sleep(0.1)

    navigatorGUI.roisPanel._tableWdgt.resizeColumnsToContents()

    roi = navigatorGUI.session.ROIs[newROIKey]
    assert isinstance(roi, PipelineROI)

    # select the ROI in the table
    navigatorGUI.roisPanel._tableWdgt.currentCollectionItemKey = newROIKey

    await asyncio.sleep(0.5)

    roiWdgt = navigatorGUI.roisPanel._editROIWdgt._roiSpecificInnerWdgt
    assert isinstance(roiWdgt, EditPipelineROIInnerWidget)

    # add SelectSurfaceMesh stage
    roiWdgt._addStageBtn.click()
    stageWdgt = roiWdgt._stageWidgets[0]
    assert isinstance(stageWdgt, StageWidgets.PassthroughStageWidget)
    stageWdgt._typeField.setCurrentText(ROIStages.SelectSurfaceMesh.type)

    await asyncio.sleep(0.1)
    stageWdgt = roiWdgt._stageWidgets[0]
    assert isinstance(stageWdgt, StageWidgets.SelectSurfaceMeshStageWidget)
    stageWdgt._meshComboBox.setCurrentText('gmSurf')

    await asyncio.sleep(0.1)

    # add AddFromTarget stage
    roiWdgt._addStageBtn.click()
    stageWdgt = roiWdgt._stageWidgets[-1]
    assert isinstance(stageWdgt, StageWidgets.PassthroughStageWidget)
    stageWdgt._typeField.setCurrentText(AddFromTarget.type)

    await asyncio.sleep(0.1)
    stageWdgt = roiWdgt._stageWidgets[-1]
    assert isinstance(stageWdgt, StageWidgets.AddFromTargetStageWidget)

    # set target to M1 and initial radii
    stageWdgt._targetCombo.setCurrentText('M1')
    stageWdgt._radiusXField.setValue(15.0)
    stageWdgt._radiusYField.setValue(3.0)

    await asyncio.sleep(0.5)

    navigatorGUI.roisPanel._queueRedraw('cameraPos')
    await asyncio.sleep(0.5)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='TargetROI_1',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # change radii to 10 mm (x) and 5 mm (y)
    stageWdgt._radiusXField.setValue(10.0)
    stageWdgt._radiusYField.setValue(5.0)

    await asyncio.sleep(0.5)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='TargetROI_2',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # change M1 angle to 0 degrees from midline
    navigatorGUI.session.targets['M1'].angle = 0.

    await asyncio.sleep(0.5)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='TargetROI_3',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # change angle back to -45 degrees from midline
    navigatorGUI.session.targets['M1'].angle = -45.

    await asyncio.sleep(0.5)

    # change to frontal target (t2-45)
    stageWdgt._targetCombo.setCurrentText('t2-45')

    await asyncio.sleep(0.5)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='TargetROI_4',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # change the mesh surface from cortex (gmSurf) to skin (skinSurf)
    selectMeshWdgt = roiWdgt._stageWidgets[0]
    assert isinstance(selectMeshWdgt, StageWidgets.SelectSurfaceMeshStageWidget)
    selectMeshWdgt._meshComboBox.setCurrentText('skinSurf')

    await asyncio.sleep(0.5)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='TargetROI_5',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # save and validate
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)
    utils.assertSavedSessionIsValid(sessionPath)

