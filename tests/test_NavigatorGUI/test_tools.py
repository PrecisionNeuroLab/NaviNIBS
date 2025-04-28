import asyncio
import jsbeautifier
import json
import logging
import os
import pyperclip
import pytest
import shutil

from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from NaviNIBS.Navigator.GUI.ViewPanels.ToolsPanel import CoilToolWidget
from NaviNIBS.util.pyvista import RemotePlotterProxy
from tests.test_NavigatorGUI import utils
from tests.test_NavigatorGUI.utils import (
    existingResourcesDataPath,
    navigatorGUIWithoutSession,
    workingDir,
    screenshotsDataSourcePath)

logger = logging.getLogger(__name__)


@pytest.fixture
def toolsDataSourcePath(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testSourceData',
                        'ExampleTools_Minimal.json')


@pytest.fixture
def simulatedPositionsPath1(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testSourceData',
                        'SimulatedPositions_Example1.json')


@pytest.fixture
def simulatedPositionsCoilCalibrationPath1(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testSourceData',
                        'SimulatedPositions_CoilCalibrationA1.json')


@pytest.mark.asyncio
@pytest.mark.order(after='test_setTargets.py::test_setTargets')
async def test_setTools(navigatorGUIWithoutSession: NavigatorGUI,
                          workingDir: str,
                          toolsDataSourcePath: str,
                          screenshotsDataSourcePath: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'SetTargets', 'SetTools')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.toolsPanel.key)

    # give time for initialization
    await asyncio.sleep(1.)

    assert navigatorGUI.activeViewKey == navigatorGUI.toolsPanel.key

    # equivalent to clicking "Import tools from file..." button and browsing to file
    navigatorGUI.toolsPanel._importToolsFromFile(toolsDataSourcePath)

    # equivalent to clicking on corresponding entry in table
    navigatorGUI.toolsPanel._tblWdgt.currentCollectionItemKey = 'Pointer'

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    assert 'Pointer' in ses.tools

    # wait for plots to initialize
    await asyncio.sleep(1.)
    toolWdgt = navigatorGUI.toolsPanel._toolWdgt
    assert toolWdgt is not None
    await toolWdgt.finishedAsyncInit.wait()
    await asyncio.sleep(1.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='SetTools_ImportedAndSelected',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # TODO: add additional test procedures + assertions for manually editing existing tools
    #  creating new tools, and deleting existing tools, calibrating, etc.


@pytest.mark.asyncio
@pytest.mark.skip(reason='For troubleshooting')
async def test_openToolsSession(workingDir):
    await utils.openSessionForInteraction(workingDir, 'SetTools')


@pytest.mark.asyncio
@pytest.mark.skip(reason='For troubleshooting')
async def test_openSimulatedToolsSession(workingDir):
    await utils.openSessionForInteraction(workingDir, 'SimulateTools')


@pytest.mark.asyncio
@pytest.mark.skip(reason='For troubleshooting')
async def test_openCalibrateCoilSession(workingDir):
    await utils.openSessionForInteraction(workingDir, 'CalibrateCoil')


@pytest.mark.asyncio
@pytest.mark.order(after='test_setTools')
async def test_simulateTools(navigatorGUIWithoutSession: NavigatorGUI,
                             workingDir: str,
                             screenshotsDataSourcePath: str,
                             simulatedPositionsPath1: str):

    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'SetTools', 'SimulateTools')

    # edit session config to enable simulated tools addon
    # TODO: do this via GUI once have support for editing addons in GUI
    if True:
        # add via GUI

        # open session (without addon)
        navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

        await asyncio.sleep(1.)

        from NaviNIBS.Navigator.Model.Addons import installPath as addonBaseInstallPath

        addonConfigPath = os.path.join(addonBaseInstallPath, '..', 'addons', 'NaviNIBS_Simulated_Tools', 'addon_configuration.json')

        navigatorGUI.manageSessionPanel._addAddon(addonConfigPath)

        await asyncio.sleep(5.)

        await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                                sessionPath=sessionPath,
                                                screenshotName='SimulateTools_AddonAdded',
                                                screenshotsDataSourcePath=screenshotsDataSourcePath)

        # equivalent to clicking save button
        navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

        ses = utils.assertSavedSessionIsValid(sessionPath)

    else:
        # add to saved session config before loading session
        addonConfig = dict()
        addonConfig['addonInstallPath'] = '../addons/NaviNIBS_Simulated_Tools/'
        addonConfigName = 'SessionConfig_Addon_NaviNIBS_Simulated_Tools.json'
        addonConfigPath = os.path.join(sessionPath, addonConfigName)
        with open(addonConfigPath, 'w') as f:
            json.dump(addonConfig, f)

        baseConfigPath = os.path.join(sessionPath, 'SessionConfig.json')
        with open(baseConfigPath, 'r+') as f:
            baseConfig = json.load(f)
            if 'addons' not in baseConfig:
                baseConfig['addons'] = []
            baseConfig['addons'].append(addonConfigName)

            del baseConfig['dockWidgetLayouts']  # remove previous saved layouts since they don't include view pane for new addon

            opts = jsbeautifier.default_options()
            opts.indent_size = 2
            beautifier = jsbeautifier.Beautifier(opts)
            f.seek(0)
            f.write(beautifier.beautify(json.dumps(baseConfig)))
            f.truncate()

        # open session
        navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    from addons.NaviNIBS_Simulated_Tools.Navigator.GUI.ViewPanels.SimulatedToolsPanel import SimulatedToolsPanel

    # equivalent to clicking on tab
    simulatedToolsPanel: SimulatedToolsPanel = navigatorGUI._mainViewPanels['SimulatedToolsPanel']
    navigatorGUI._activateView(simulatedToolsPanel.key)

    # give time for initialization
    # (TODO: wait for signal to indicate tab is ready instead of waiting fixed time here)
    await asyncio.sleep(.1)
    await simulatedToolsPanel.finishedAsyncInit.wait()

    assert navigatorGUI.activeViewKey == simulatedToolsPanel.key

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='SimulateTools_Blank',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    await simulatedToolsPanel.importPositionsSnapshot(simulatedPositionsPath1)

    await asyncio.sleep(1.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='SimulateTools_Example1',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # equivalent to clicking "Move tool..." and then clicking on subject tracker
    task = asyncio.create_task(simulatedToolsPanel.selectAndMoveTool(simulatedToolsPanel._actors['CB60Calibration_tool']))

    await asyncio.sleep(1.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='SimulateTools_Example2',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)


@pytest.mark.asyncio
@pytest.mark.order(after='test_simulateTools')
async def test_calibrateCoil(navigatorGUIWithoutSession: NavigatorGUI,
                             workingDir: str,
                             screenshotsDataSourcePath: str,
                             simulatedPositionsPath1: str,
                             simulatedPositionsCoilCalibrationPath1: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'SimulateTools', 'CalibrateCoil')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    await utils.importSimulatedPositionsSnapshot(navigatorGUI, simulatedPositionsPath1)

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.toolsPanel.key)

    # give time for initialization
    # (TODO: wait for signal to indicate tab is ready instead of waiting fixed time here)
    await asyncio.sleep(10.)

    assert navigatorGUI.activeViewKey == navigatorGUI.toolsPanel.key

    # equivalent to clicking on corresponding entry in table
    navigatorGUI.toolsPanel._tblWdgt.currentCollectionItemKey = 'Coil1'

    await asyncio.sleep(10.)

    # click calibrate btn
    coilWdgt: CoilToolWidget = navigatorGUI.toolsPanel._toolWdgt
    coilWdgt._calibrateCoilBtn.click()

    await asyncio.sleep(10.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='CalibrateCoil_1',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath,
                                            wdgt=coilWdgt._calibrationWindow.wdgt)

    await utils.importSimulatedPositionsSnapshot(navigatorGUI, simulatedPositionsCoilCalibrationPath1)

    await asyncio.sleep(0.5)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='CalibrateCoil_2',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath,
                                            wdgt=coilWdgt._calibrationWindow.wdgt)

    # TODO import a simulated positions case where either coil or plate is NOT visible
    # and assert that calibrate button is disabled

    # equivalent to clicking "calibrate" button
    coilWdgt._calibrationWindow._calibrateBtn.click()

    await asyncio.sleep(0.5)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='CalibrateCoil_3',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath,
                                            wdgt=coilWdgt._calibrationWindow.wdgt)

    # TODO: simulate undoing calibration, closing window without changing original calibration

    # equivalent to clicking close button
    coilWdgt._calibrationWindow.wdgt.close()

    await asyncio.sleep(1.0)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='CalibrateCoil_4',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)


