import asyncio
import os
import pyperclip
import pytest
import pytest_asyncio
from pytestqt.qtbot import QtBot
from pytest_lazy_fixtures import lf
import shutil
import tempfile
import logging
from qtpy import QtCore


from RTNaBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from RTNaBS.Navigator.Model.Session import Session

logger = logging.getLogger(__name__)


@pytest.fixture
def existingResourcesDataPath():
    """
    Where pre-generated test resource files are stored before being copied for tests
    :return:
    """
    return os.path.join(os.path.dirname(__file__), '..', 'data')


@pytest.fixture
def screenshotsDataSourcePath(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testScreenshots')


@pytest.fixture
def mriDataSourcePath(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'sub-test_wt-T1_seq-SagFSPGRBRAVO_MRI.nii.gz')


@pytest.fixture(scope='session')
def workingDir(request):
    path = request.config.cache.get('workingDir', None)
    if True:
        if path is None:
            path = tempfile.mkdtemp(prefix='NaviNIBS_Tests')
            request.config.cache.set('workingDir', path)
        # note this directory will not be auto-deleted
        yield path
    else:
        with tempfile.TemporaryDirectory(suffix='NaviNIBS_Test_Session') as path:
            yield path
        # note: directory will be auto-deleted


def test_copyWorkingDirToClipboard(workingDir):
    pyperclip.copy(workingDir)


def getSessionPath(workingDir: str, key: str, ext: str | None = None,
                   deleteIfExists: bool = False):
    sesPath = os.path.join(workingDir, f'TestSession_{key}_NaviNIBS')
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

def assertSavedSessionIsValid(sessionPath: str):
    if os.path.isfile(sessionPath):
        ses = Session.loadFromFile(filepath=sessionPath)
    else:
        ses = Session.loadFromFolder(folderpath=sessionPath)

    # load would have thrown exception if there was an issue

    # TODO: maybe do some jsonschema validation here


async def waitForever():
    """
    For debug purposes only
    """
    while True:
        await asyncio.sleep(1.)


def captureScreenshot(navigatorGUI: NavigatorGUI, saveToPath: str):
    from PIL import ImageGrab

    pos = navigatorGUI._win.frameGeometry()
    bbox = tuple(x * navigatorGUI._win.devicePixelRatio() for x in (pos.left(), pos.top(), pos.right(), pos.bottom()))
    logger.info(f'Saving screenshot to {saveToPath}')
    ImageGrab.grab(bbox).save(saveToPath)


def assertImagesEqualish(img1Path: str, img2Path: str):
    from PIL import ImageChops, Image
    with Image.open(img1Path) as im1, Image.open(img2Path) as im2:
        diff = ImageChops.difference(im1, im2)
        if diff.getbbox():
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
        # TODO: add some tolerance for permissible differences (with configurable threshold)
        assert not diff.getbbox()


@pytest_asyncio.fixture
async def navigatorGUIWithoutSession() -> NavigatorGUI:
    return NavigatorGUI.createAndRunAsTask()


@pytest.mark.asyncio
async def test_openSession(workingDir):
    sessionKey = 'setMRI'
    sessionPath = getSessionPath(workingDir, sessionKey)
    NavigatorGUI.createAndRunAsTask(sesFilepath=sessionPath)
    while True:
        await asyncio.sleep(1.)


@pytest.mark.asyncio
async def test_createNewSessionFolderWithUserInput(navigatorGUIWithoutSession: NavigatorGUI,
                                                   workingDir: str):
    newSessionPath = getNewSessionPath(workingDir, 'New')
    assert not os.path.exists(newSessionPath)

    # copy to clipboard
    pyperclip.copy(newSessionPath)

    logger.info(f'When modal dialog opens, paste the following path and press enter: {newSessionPath}')
    navigatorGUIWithoutSession.manageSessionPanel._newSessionBtn.click()

    navigatorGUI = navigatorGUIWithoutSession

    pathsEqual = lambda a, b: os.path.normpath(a) == os.path.normpath(b)
    assert pathsEqual(navigatorGUI.session.filepath, newSessionPath)
    assert pathsEqual(navigatorGUI.session.unpackedSessionDir, newSessionPath),\
        "With a filepath input without .navinibs extension, session should be directly saved as an unpacked directory"


@pytest.mark.asyncio
async def test_createNewSessionFileWithUserInput(navigatorGUIWithoutSession: NavigatorGUI,
                                                 workingDir: str):
    newSessionPath = getNewSessionPath(workingDir, 'New', '.navinibs')
    assert not os.path.exists(newSessionPath)

    # copy to clipboard
    pyperclip.copy(newSessionPath)

    logger.info(f'When modal dialog opens, paste the following path and press enter: {newSessionPath}')
    navigatorGUIWithoutSession.manageSessionPanel._newSessionBtn.click()

    navigatorGUI = navigatorGUIWithoutSession

    pathsEqual = lambda a, b: os.path.normpath(a) == os.path.normpath(b)
    assert pathsEqual(navigatorGUI.session.filepath, newSessionPath)
    assert not pathsEqual(navigatorGUI.session.unpackedSessionDir, newSessionPath),\
        "With a filepath input with .navinibs extension, session should not be directly saved as an unpacked directory"


@pytest.mark.asyncio
async def test_createSessionViaGUI(navigatorGUIWithoutSession: NavigatorGUI,
                                   workingDir: str):
    sessionPath = getSessionPath(workingDir, 'InfoOnly', deleteIfExists=True)
    assert not os.path.exists(sessionPath)

    await asyncio.sleep(5.)

    assert navigatorGUIWithoutSession._win.isVisible()

    # try autosave
    # (at this time, session is None, but autosave should not generate error)
    await navigatorGUIWithoutSession.manageSessionPanel._autosave()

    await asyncio.sleep(1.)

    # create new session

    # note: can't click and test new session file dialog due it being modal
    # so test one level lower
    navigatorGUIWithoutSession.manageSessionPanel._createNewSession(
        sesFilepath=sessionPath,
    )

    navigatorGUI = navigatorGUIWithoutSession

    pathsEqual = lambda a, b: os.path.normpath(a) == os.path.normpath(b)
    assert pathsEqual(navigatorGUI.session.filepath, sessionPath)
    assert pathsEqual(navigatorGUI.session.unpackedSessionDir, sessionPath), \
        "With a filepath input without .navinibs extension, session should be directly saved as an unpacked directory"

    await asyncio.sleep(1.)

    subjectID = 'test subject'
    sessionID = 'test session'

    wdgt = navigatorGUI.manageSessionPanel._infoWdgts['subjectID']
    QtBot.mouseDClick(wdgt, QtCore.Qt.MouseButton.LeftButton)
    QtBot.keyClicks(wdgt, subjectID)
    QtBot.keyClick(wdgt, QtCore.Qt.Key.Key_Enter)
    assert navigatorGUI.session.subjectID == subjectID

    await asyncio.sleep(1.)

    wdgt = navigatorGUI.manageSessionPanel._infoWdgts['sessionID']
    QtBot.mouseDClick(wdgt, QtCore.Qt.MouseButton.LeftButton)
    QtBot.keyClicks(wdgt, sessionID)
    QtBot.keyClick(wdgt, QtCore.Qt.Key.Key_Tab)
    assert navigatorGUI.session.sessionID == sessionID

    await asyncio.sleep(1.)

    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    # TODO: break this apart into separate steps, save and reload after each

    # TODO: verify contents of saved files

    assertSavedSessionIsValid(sessionPath)


@pytest.mark.asyncio
@pytest.mark.order(after='test_createSessionViaGUI')
async def test_setMRIInfo(navigatorGUIWithoutSession: NavigatorGUI,
                          workingDir: str,
                          mriDataSourcePath: str,
                          screenshotsDataSourcePath: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = copySessionFolder(workingDir, 'InfoOnly', 'SetMRI')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # set MRI
    navigatorGUI._activateView(navigatorGUI.mriPanel.key)

    await asyncio.sleep(10.)

    assert navigatorGUI.activeViewKey == navigatorGUI.mriPanel.key

    # TODO: continue writing test here

    mriDataFilename = os.path.split(mriDataSourcePath)[1]
    mriDataTestPath = os.path.join(sessionPath, '..', mriDataFilename)
    if not os.path.exists(mriDataTestPath):
        shutil.copy(mriDataSourcePath, mriDataTestPath)

    navigatorGUI.mriPanel._filepathWdgt.filepath = mriDataTestPath

    if True:
        screenshotPath = os.path.join(sessionPath, 'MRI1.png')
        captureScreenshot(navigatorGUI, screenshotPath)
        pyperclip.copy(str(screenshotPath))
        await asyncio.sleep(10.)
        screenshotPath = os.path.join(sessionPath, 'MRI2.png')
        captureScreenshot(navigatorGUI, screenshotPath)
        pyperclip.copy(str(screenshotPath))

        assertImagesEqualish(screenshotPath,
                             os.path.join(screenshotsDataSourcePath, 'SetMRI.png'))


def child():
    logger.info('A new child process')
    import time
    time.sleep(5.)
    logger.info('Ending child process')


def test_multiprocessing():
    logger.info('Parent process')
    import multiprocessing as mp
    proc = mp.Process(target=child)
    proc.start()
    logger.info('Parent started child')
    proc.join()
    logger.info('Ending parent process')
