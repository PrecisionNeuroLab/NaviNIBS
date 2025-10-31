import asyncio
import logging
import jsbeautifier
import json
import os
import pyperclip
import pytest
import shutil

from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from NaviNIBS.Navigator.GUI.Widgets.EditGridWidget import EditGridWidget
from tests.test_NavigatorGUI import utils
from tests.test_NavigatorGUI.utils import (
    existingResourcesDataPath,
    navigatorGUIWithoutSession,
    workingDir,
    screenshotsDataSourcePath)

logger = logging.getLogger(__name__)


@pytest.fixture
def targetsDataSourcePath(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testSourceData',
                        'sub-test_ExampleTargets.json')


@pytest.mark.asyncio
@pytest.mark.skip(reason='For troubleshooting')
async def test_openSetTargetsSession(workingDir):
    await utils.openSessionForInteraction(workingDir, 'SetTargets')


@pytest.mark.asyncio
@pytest.mark.skip(reason='For troubleshooting')
async def test_openSetTargetGridSession(workingDir):
    await utils.openSessionForInteraction(workingDir, 'SetTargetGrid')


@pytest.mark.asyncio
@pytest.mark.order(after='test_ROIs.py::test_setROIs')
async def test_setTargets(navigatorGUIWithoutSession: NavigatorGUI,
                          workingDir: str,
                          targetsDataSourcePath: str,
                          screenshotsDataSourcePath: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'SetROIs', 'SetTargets')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.setTargetsPanel.key)

    # give time for initialization
    await navigatorGUI.setTargetsPanel.finishedAsyncInit.wait()
    await asyncio.sleep(1.)
    for view in navigatorGUI.setTargetsPanel._views.values():
        await view.redrawQueueIsEmpty.wait()

    assert navigatorGUI.activeViewKey == navigatorGUI.setTargetsPanel.key

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='SetTargets_Empty',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # equivalent to clicking "Import targets from file..." button and browsing to file
    navigatorGUI.setTargetsPanel._importTargetsFromFile(targetsDataSourcePath)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='SetTargets_Imported',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # equivalent to clicking on corresponding entry in table
    navigatorGUI.setTargetsPanel._tableWdgt.currentCollectionItemKey = 't2-45'

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    assert ses.targets['t2-45'].targetCoord.round(1).tolist() == [-30.1, 32.0, 52.9]

    # assert that there are not yet any grid targets
    # (due to a GUI quirk in previous version, grid targets may be created immediately)
    assert not any('grid' in targetKey for targetKey in ses.targets.keys())

    # TODO: wait for signal to indicate plots have been updated instead of waiting fixed time here
    for view in navigatorGUI.setTargetsPanel._views.values():
        await view.redrawQueueIsEmpty.wait()

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='SetTargets_ImportedAndSelected',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # TODO: add additional test procedures + assertions for manually editing existing targets
    #  creating new targets, and deleting existing targets


@pytest.mark.asyncio
@pytest.mark.order(after='test_setTargets')
async def test_setTargetGrid(navigatorGUIWithoutSession: NavigatorGUI,
                          workingDir: str,
                          targetsDataSourcePath: str,
                          screenshotsDataSourcePath: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'SetTargets', 'SetTargetGrid')

    if True:
        # modify M1 target for better use as a grid seed prior to loading
        configPath = os.path.join(sessionPath, 'SessionConfig_Targets.json')
        with open(configPath, 'r+') as f:
            config = json.load(f)
            index = next(i for i, target in enumerate(config) if target['key'] == 'M1')
            config[index]['targetCoord'] = [-35.17, -14.05, 64.45]
            config[index]['entryCoord'] = [-42.31, -12.87, 74.20]
            config[index]['depthOffset'] = 3

            opts = jsbeautifier.default_options()
            opts.indent_size = 2
            beautifier = jsbeautifier.Beautifier(opts)
            f.seek(0)
            f.write(beautifier.beautify(json.dumps(config)))
            f.truncate()

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.setTargetsPanel.key)

    # give time for initialization
    await navigatorGUI.setTargetsPanel.finishedAsyncInit.wait()
    await asyncio.sleep(1.)
    for view in navigatorGUI.setTargetsPanel._views.values():
        await view.redrawQueueIsEmpty.wait()

    assert navigatorGUI.activeViewKey == navigatorGUI.setTargetsPanel.key

    # equivalent to clicking on "Edit grid" tab
    navigatorGUI.setTargetsPanel._editGridDock.raiseDock()

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='SetTargetGrid_Empty',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    gridWdgt: EditGridWidget = navigatorGUI.setTargetsPanel._editGridWdgt

    # equivalent to changing display style in GUI
    navigatorGUI.setTargetsPanel._targetDispStyle_comboBox.setCurrentIndex(1)

    # equivalent to hiding all targets (prior to grid creation) in GUI
    navigatorGUI.session.targets.setWhichTargetsVisible([])

    # equivalent to selecting M1 as seed target
    gridWdgt.seedTarget = navigatorGUI.session.targets['M1']

    # equivalent to setting grid parameters in GUI
    gridWdgt._gridPrimaryAngleWdgt.value = 50.
    gridWdgt._gridPivotDepth.setValue(120.)
    for i in range(0, 2):
        gridWdgt._gridNWdgts[i].setValue(5)
        gridWdgt._gridWidthWdgts[i].setValue(20)
    gridWdgt._gridNeedsUpdate.set()

    await asyncio.sleep(.5)

    assert not gridWdgt._gridNeedsUpdate.is_set()

    for view in navigatorGUI.setTargetsPanel._views.values():
        await view.redrawQueueIsEmpty.wait()

    # equivalent to clicking on corresponding entry in table
    logger.info(f'Targets: {navigatorGUI.session.targets.keys()}')
    navigatorGUI.setTargetsPanel._tableWdgt.currentCollectionItemKey = 'M1 grid point 13'

    navigatorGUI.setTargetsPanel._views['3D'].plotter.camera.zoom(3)  # closer view on head for screenshot

    await asyncio.sleep(.1)
    for view in navigatorGUI.setTargetsPanel._views.values():
        await view.redrawQueueIsEmpty.wait()

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='SetTargetGrid_SpatialGrid',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)



