import asyncio
import logging
import glob
import numpy as np
import os
import pyperclip
import pytest
from pytest_lazy_fixtures import lf
import shutil
from time import time

from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from NaviNIBS.Navigator.Model.Session import Session
from tests.test_NavigatorGUI import utils
from tests.test_NavigatorGUI.utils import (
    existingResourcesDataPath,
    getSessionPath,
    tracer,
    navigatorGUIWithoutSession,
    workingDir,
    screenshotsDataSourcePath)

logger = logging.getLogger(__name__)


@pytest.fixture
def headrecoHeadModelDataSourcePath(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testSourceData',
                        'sub-test_T1Seq-SagFSPGRBRAVO_SimNIBS', 'sub-test.msh')

@pytest.fixture
def charmHeadModelDataSourcePath(existingResourcesDataPath):
    return os.path.join(existingResourcesDataPath, 'testSourceData',
                        'sub-test_T1Seq-SagFSPGRBRAVO_SimNIBSCharm', 'm2m_sub-test', 'sub-test.msh')

@pytest.mark.asyncio
@pytest.mark.skip(reason='For troubleshooting')
async def test_openHeadModelSession(workingDir):
    await utils.openSessionForInteraction(workingDir, 'SetHeadModel')

@pytest.mark.asyncio
@pytest.mark.skip(reason='For troubleshooting')
async def test_openCharmHeadModelSession(workingDir):
    await utils.openSessionForInteraction(workingDir, 'SetCharmHeadModel')


@pytest.mark.asyncio
@pytest.mark.order(after='test_MRI.py::test_setMRIInfo')
@pytest.mark.parametrize('modelLabel,headModelDataSourcePath', (
        ('Charm', lf('charmHeadModelDataSourcePath')),
        ('', lf('headrecoHeadModelDataSourcePath'))))
async def test_setHeadModel(navigatorGUIWithoutSession: NavigatorGUI,
                          workingDir: str,
                            modelLabel: str,
                          headModelDataSourcePath: tuple[str, str],
                          screenshotsDataSourcePath: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'SetMRI', f'Set{modelLabel}HeadModel')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on head model tab
    navigatorGUI._activateView(navigatorGUI.headModelPanel.key)

    # give time for initialization
    await navigatorGUI.headModelPanel.finishedAsyncInit.wait()

    assert navigatorGUI.activeViewKey == navigatorGUI.headModelPanel.key

    headModelSourceDir, headModelMeshName = os.path.split(headModelDataSourcePath)
    headModelDirName = os.path.split(headModelSourceDir)[1]
    headModelTestDir = os.path.join(sessionPath, '..', headModelDirName)
    if len(glob.glob(os.path.join(headModelTestDir, '*.msh'))) < 1:
        shutil.copytree(headModelSourceDir, headModelTestDir, dirs_exist_ok=True)

    headModelTestSourcePath = os.path.join(headModelTestDir, headModelMeshName)

    navigatorGUI.headModelPanel._filepathWdgt.filepath = headModelTestSourcePath

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    assert os.path.normpath(ses.headModel.filepath) == os.path.normpath(headModelTestSourcePath)

    for view in navigatorGUI.headModelPanel._views.values():
        await view.redrawQueueIsEmpty.wait()
    await asyncio.sleep(1.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName=f'Set{modelLabel}HeadModel',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)


@pytest.mark.asyncio
@pytest.mark.order(after='test_MRI.py::test_setMRIInfo')
@pytest.mark.parametrize('modelLabel,headModelDataSourcePath', (
        ('', lf('headrecoHeadModelDataSourcePath')),))
async def test_setHeadModelWithSeparateMeshes(navigatorGUIWithoutSession: NavigatorGUI,
                          workingDir: str,
                          modelLabel: str,
                          headModelDataSourcePath: str,
                          screenshotsDataSourcePath: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'SetMRI', f'Set{modelLabel}HeadModelWithSeparateMeshes')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on head model tab
    navigatorGUI._activateView(navigatorGUI.headModelPanel.key)

    # give time for initialization
    await navigatorGUI.headModelPanel.finishedAsyncInit.wait()

    assert navigatorGUI.activeViewKey == navigatorGUI.headModelPanel.key

    headModelSourceDir, headModelMeshName = os.path.split(headModelDataSourcePath)
    headModelDirName = os.path.split(headModelSourceDir)[1]
    headModelTestDir = os.path.join(sessionPath, '..', headModelDirName)
    if len(glob.glob(os.path.join(headModelTestDir, '*.msh'))) < 1:
        shutil.copytree(headModelSourceDir, headModelTestDir, dirs_exist_ok=True)

    subStr = os.path.splitext(headModelMeshName)[0]  # e.g. 'sub-1234'

    skinMeshPath = os.path.join(headModelTestDir, 'm2m_' + subStr, 'skin.stl')
    navigatorGUI.headModelPanel._skinFilepathWdgt.filepath = skinMeshPath
    navigatorGUI.headModelPanel._gmFilepathWdgt.filepath = os.path.join(headModelTestDir, 'm2m_' + subStr, 'gm.stl')

    #await utils.waitForever()

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    assert os.path.normpath(ses.headModel.skinSurfPath) == os.path.normpath(skinMeshPath)

    for view in navigatorGUI.headModelPanel._views.values():
        await view.redrawQueueIsEmpty.wait()
    await asyncio.sleep(1.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName=f'Set{modelLabel}HeadModelWithSeparateMeshes',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # test rotating meshes
    navigatorGUI.headModelPanel._meshToMRITransformWdgt.transform = np.asarray([[1, 0, 0, 10], [0, 0, -1, 20], [0, 1, 0, 30], [0, 0, 0,1]])
    await asyncio.sleep(1.)
    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName=f'Set{modelLabel}HeadModelWithSeparateMeshesTransformed',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)



@pytest.mark.asyncio
@pytest.mark.order(after='test_setHeadModel')
@pytest.mark.parametrize('modelLabel,headModelDataSourcePath', (
        ('', lf('headrecoHeadModelDataSourcePath')),
        ('Charm', lf('charmHeadModelDataSourcePath')),
))
async def test_mniTransforms(workingDir: str,
                             modelLabel: str,
                             headModelDataSourcePath: tuple[str, str]):

    sessionKey = f'Set{modelLabel}HeadModel'

    sessionPath = getSessionPath(workingDir=workingDir, key=sessionKey)

    with tracer(workingDir, sessionKey, doOpen=False):

        session = Session.loadFromFolder(folderpath=sessionPath)

        inputCoords = np.array([[0, 0, 0],
                                [100, 0, 0],
                                [0, 100, 0],
                                [0, 0, 100],
                                [-37.3, -18.6, 65.7],  # M1
                               ], dtype=np.float64)

        # coordSys = session.coordinateSystems['MNI_SimNIBS12DoF']
        coordSys = session.coordinateSystems['MNI_SimNIBSNonlinear']

        coord = inputCoords[0:1, :]
        tStart = time()
        outputCoord = coordSys.transformFromThisToWorld(coord)
        tEnd = time()
        elapsedTime = tEnd - tStart
        logger.info(f'Transform from MNI to world took {elapsedTime*1e3:.3f} ms')

        # when using identical coordinates, this second transform should be *much* faster due to caching
        coord = inputCoords[0:1, :]
        tStart = time()
        outputCoord = coordSys.transformFromThisToWorld(coord)
        tEnd = time()
        elapsedTime = tEnd - tStart
        assert elapsedTime < 1e-3
        logger.info(f'Repeated transform from MNI to world took {elapsedTime * 1e3:.3f} ms')

        # when using optimized nitransforms with prefiltering, transform new points after first should also be *much* faster
        coord = inputCoords[1:2, :]
        tStart = time()
        outputCoord = coordSys.transformFromThisToWorld(coord)
        tEnd = time()
        elapsedTime = tEnd - tStart
        assert elapsedTime < 1e-3
        logger.info(f'New transform from MNI to world took {elapsedTime * 1e3:.3f} ms')

        tStart = time()
        outputCoords = coordSys.transformFromThisToWorld(inputCoords)
        tEnd = time()
        logger.info(f'Transforming {inputCoords.shape[0]} points from MNI to world took {(tEnd - tStart) * 1e3:.3f} ms')





