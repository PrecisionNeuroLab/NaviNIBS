import asyncio
import logging
import numpy as np
import os
import pyperclip
import pytest
import pytest_asyncio
import shutil
import tempfile
import time
import typing as tp


from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from NaviNIBS.Navigator.Model.Session import Session

logger = logging.getLogger(__name__)


doAssertScreenshotsEqual = False  # TODO: debug, delete / set to True / make a test-specific setting


@pytest.fixture
def existingResourcesDataPath():
    """
    Where pre-generated test resource files are stored before being copied for tests
    :return:
    """
    return os.path.join(os.path.dirname(__file__), '..', '..', 'data')


@pytest.fixture
def screenshotsDataSourcePath(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testScreenshots')


@pytest.fixture(scope='session')
def workingDir(request):
    path = request.config.cache.get('workingDir', None)
    if True:
        if path is None:
            path = tempfile.mkdtemp(prefix='NaviNIBS_Tests_')
            request.config.cache.set('workingDir', path)
        # note this directory will not be auto-deleted
        if not os.path.exists(path):
            os.makedirs(path)
        yield path
    else:
        with tempfile.TemporaryDirectory(suffix='NaviNIBS_Test_Session') as path:
            yield path
        # note: directory will be auto-deleted


@pytest_asyncio.fixture
async def navigatorGUIWithoutSession() -> NavigatorGUI:

    if True:
        # make sure specific globals are cleared between tests
        from NaviNIBS.Navigator.TargetingCoordinator import TargetingCoordinator
        TargetingCoordinator._resetSingleton()

    navGUI = NavigatorGUI.createAndRunAsTask()
    if True:
        await raiseMainNavigatorGUI()
    yield navGUI
    navGUI._win.close()


async def openSessionForInteraction(workingDir, sessionKey: str):
    sessionPath = getSessionPath(workingDir, sessionKey)
    NavigatorGUI.createAndRunAsTask(sesFilepath=sessionPath)
    while True:
        await asyncio.sleep(1.)


async def raiseMainNavigatorGUI():
    import multiprocessing as mp
    proc = mp.Process(target=_raiseMainNavigatorGUIWindow)
    proc.start()
    while proc.is_alive():
        await asyncio.sleep(1.)


def _raiseMainNavigatorGUIWindow():
    logger.debug('Finding running NaviNIBS app')
    from pywinauto import Application as PWAApp
    app = PWAApp(backend="uia").connect(title="NaviNIBS Navigator GUI")
    logger.debug('Finding NaviNIBS main window and raising to foreground')
    app.NavigatorGUI.set_focus()
    logger.debug('Done raising NaviNIBS main window')


def test_copyWorkingDirToClipboard(workingDir):
    pyperclip.copy(workingDir)


def getSessionPath(workingDir: str, key: str, ext: str | None = None,
                   deleteIfExists: bool = False):
    sesPath = os.path.join(workingDir, f'Test_{key}.navinibsdir')
    if ext is not None:
        sesPath += ext

    if deleteIfExists and os.path.exists(sesPath):
        logger.debug(f'Deleting existing session at {sesPath}')
        shutil.rmtree(sesPath)

    return sesPath


def getNewSessionPath(workingDir: str, key: str, ext: str | None = None):
    sesPath = getSessionPath(workingDir, key)
    counter = 0
    while os.path.exists(sesPath):
        counter += 1
        sesPath = getSessionPath(workingDir, f'{key}_{counter}', ext)
    return sesPath


def copySessionFolder(workingDir: str, fromPathKey: str, toPathKey: str) -> str:
    """
    Copy session folder from one location to another, deleting the destination if it already exists.

    Returns destination path
    """
    fromPath = getSessionPath(workingDir, fromPathKey)
    toPath = getSessionPath(workingDir, toPathKey)
    if os.path.exists(toPath):
        shutil.rmtree(toPath)
    shutil.copytree(fromPath, toPath)

    return toPath


def assertSavedSessionIsValid(sessionPath: str) -> Session:
    if os.path.isfile(sessionPath):
        ses = Session.loadFromFile(filepath=sessionPath)
    else:
        ses = Session.loadFromFolder(folderpath=sessionPath)

    # load would have thrown exception if there was an issue

    # TODO: maybe do some jsonschema validation here

    return ses


async def waitForever():
    """
    For debug purposes only
    """
    while True:
        await asyncio.sleep(1.)


def captureScreenshot(navigatorGUI: NavigatorGUI, saveToPath: str, wdgt: tp.Any | None = None):
    from PIL import ImageGrab

    if wdgt is None:
        wdgt = navigatorGUI._win
    pos = wdgt.frameGeometry()
    bbox = tuple(x * wdgt.devicePixelRatio() for x in (pos.left(), pos.top(), pos.right(), pos.bottom()))
    logger.info(f'Saving screenshot to {saveToPath}')
    ImageGrab.grab(bbox).save(saveToPath)


def compareImages(img1Path: str, img2Path: str, doAssertEqual: bool = True, diffAmtThreshold: int = 0):
    from PIL import ImageChops, Image
    with Image.open(img1Path) as im1, Image.open(img2Path) as im2:
        if im1.size != im2.size:
            # don't show comparison at all if sizes are different
            if doAssertEqual:
                assert False, 'Images are different sizes'
            else:
                logger.warning('Images are different sizes')
            return
        diff = ImageChops.difference(im1, im2)
        # for some reason first row is consistently very different, so don't include in quantification
        diffAmt = np.sum(np.asarray(diff)[1:, :])
        if diff.getbbox() and diffAmt > diffAmtThreshold:
            im3 = Image.new("RGB", im2.size, (255, 0, 0))
            mask = diff.convert("L").point(lambda x: 127 if x else 0)
            im4 = im2.copy()
            im4.paste(im3, (0, 0), mask)
            toJoin = (im1, im4, im2)
            imJoined = Image.new('RGB', (sum(im.width for im in toJoin), im1.height))
            xOffset = 0
            for im in toJoin:
                imJoined.paste(im, (xOffset, 0))
                xOffset += im.width
            imJoined.show()
        if doAssertEqual:
            assert diffAmt <= diffAmtThreshold, f'Images are different: {diffAmt}'


async def importSimulatedPositionsSnapshot(navigatorGUI: NavigatorGUI, positionsPath: str):
    from addons.NaviNIBS_Simulated_Tools.Navigator.GUI.ViewPanels.SimulatedToolsPanel import SimulatedToolsPanel
    simulatedToolsPanel: SimulatedToolsPanel = navigatorGUI._mainViewPanels['SimulatedToolsPanel']

    if not simulatedToolsPanel._hasInitialized:
        simulatedToolsPanel.finishInitialization()

    await simulatedToolsPanel.importPositionsSnapshot(positionsPath)


async def setSimulatedToolPose(navigatorGUI: NavigatorGUI, key: str, transf: np.ndarray | None):
    from addons.NaviNIBS_Simulated_Tools.Navigator.GUI.ViewPanels.SimulatedToolsPanel import SimulatedToolsPanel
    simulatedToolsPanel: SimulatedToolsPanel = navigatorGUI._mainViewPanels['SimulatedToolsPanel']

    from NaviNIBS.Devices import TimestampedToolPosition

    logger.info(f'Setting simulated tool pose: {key} {transf}')

    position = TimestampedToolPosition(
        time=time.time(),
        transf=transf)

    await simulatedToolsPanel.positionsClient.recordNewPosition_async(key=key, position=position)


