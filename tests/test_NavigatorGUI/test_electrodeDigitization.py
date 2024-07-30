import asyncio
import logging
import numpy as np
import os
import pyperclip
import pytest
from pytestqt.qtbot import QtBot
from qtpy import QtGui, QtWidgets, QtCore
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
from tests.test_NavigatorGUI.test_headModel import headModelDataSourcePath

logger = logging.getLogger(__name__)


@pytest.fixture
def eegCoordinatesFilepath(headModelDataSourcePath) -> str:
    return os.path.join(os.path.dirname(headModelDataSourcePath), 'm2m_sub-test', 'eeg_positions', 'EEG10-10_UI_Jurak_2007.csv')


@pytest.fixture
def eegCoordinates(eegCoordinatesFilepath) -> dict[str, np.ndarray]:
    import pandas as pd
    df = pd.read_csv(eegCoordinatesFilepath, names=('type', 'x', 'y', 'z', 'key'))
    return {row.key: np.array([row.x, row.y, row.z])
            for row in df.itertuples()
            if row.type not in 'Fiducial'}


@pytest.mark.asyncio
@pytest.mark.order(after='test_headRegistration.py::test_acquireHeadPoints')
async def test_electrodeDigitization(navigatorGUIWithoutSession: NavigatorGUI,
                                     workingDir: str,
                                     screenshotsDataSourcePath: str,
                                     eegCoordinates: dict[str, np.ndarray]):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'HeadPointAcquisition', 'ElectrodeDigitization')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.digitizeLocationsPanel.key)

    # give time for initialization
    await navigatorGUI.digitizeLocationsPanel.finishedAsyncInitializationEvent.wait()
    await asyncio.sleep(1.)

    assert navigatorGUI.activeViewKey == navigatorGUI.digitizeLocationsPanel.key

    await asyncio.sleep(1.)

    tw = navigatorGUI.digitizeLocationsPanel._tblWdgt
    placeholderIndex = tw._model.index(0, 0)
    tw._tableView.setCurrentIndex(placeholderIndex)
    tw._tableView.edit(placeholderIndex)
    wdgt = tw._tableView.indexWidget(placeholderIndex)

    QtBot.keyClicks(wdgt, 'test loc')
    QtBot.keyClick(wdgt, QtCore.Qt.Key.Key_Enter)

    await asyncio.sleep(1.)

    index = tw._model.index(tw.rowCount - 2, tw._model._columns.index('type'))
    tw._tableView.setCurrentIndex(index)
    tw._tableView.edit(index)
    wdgt = tw._tableView.indexWidget(index)
    QtBot.keyClicks(wdgt, 'testType')
    QtBot.keyClick(wdgt, QtCore.Qt.Key.Key_Tab)

    logger.debug(f'Getting subject tracker pose')
    trackerKey = navigatorGUI.session.tools.subjectTracker.key
    trackerPose_worldSpace = navigatorGUI.subjectRegistrationPanel._positionsClient.getLatestTransf(trackerKey)

    logger.debug('Getting tracker to MRI transf')
    trackerToMRITransf = navigatorGUI.session.subjectRegistration.trackerToMRITransf

    pointerKey = navigatorGUI.session.tools.pointer.key
    for coordKey, coord_MRISpace in eegCoordinates.items():
        logger.debug(f'Digitizing {coordKey} at {coord_MRISpace}')

        placeholderIndex = tw._model.index(tw.rowCount - 1, 0)
        tw._tableView.setCurrentIndex(placeholderIndex)

        await asyncio.sleep(0.1)

        coord_trackerSpace = applyTransform(invertTransform(trackerToMRITransf), coord_MRISpace)
        pointerPose_trackerSpace = composeTransform(np.eye(3), coord_trackerSpace)
        pointerPose_worldSpace = concatenateTransforms((pointerPose_trackerSpace,
                                                        trackerPose_worldSpace))
        await utils.setSimulatedToolPose(navigatorGUI=navigatorGUI,
                                         key=pointerKey,
                                         transf=pointerPose_worldSpace)

        await asyncio.sleep(0.5)

        navigatorGUI.digitizeLocationsPanel._sampleLocationBtn.click()

        await asyncio.sleep(0.5)

        index = tw._model.index(tw.rowCount - 2, tw._model._columns.index('key'))
        tw._tableView.setCurrentIndex(index)
        tw._tableView.edit(index)
        wdgt = tw._tableView.indexWidget(index)
        QtBot.keyClicks(wdgt, coordKey)
        QtBot.keyClick(wdgt, QtCore.Qt.Key.Key_Tab)

        index = tw._model.index(tw.rowCount - 2, tw._model._columns.index('type'))
        tw._tableView.setCurrentIndex(index)
        tw._tableView.edit(index)
        wdgt = tw._tableView.indexWidget(index)
        QtBot.keyClicks(wdgt, 'electrode')
        QtBot.keyClick(wdgt, QtCore.Qt.Key.Key_Enter)

        if coordKey == 'C4':
            # take screenshot partway through digitization

            tw.resizeColumnsToContents()

            await asyncio.sleep(1.)

            screenshotPath = os.path.join(sessionPath, 'ElectrodeDigitization_Digitizing.png')
            utils.captureScreenshot(navigatorGUI, screenshotPath)
            pyperclip.copy(str(screenshotPath))

            # utils.compareImages(screenshotPath,
            #                     os.path.join(screenshotsDataSourcePath, 'ElectrodeDigitization_Digitizing.png'),
            #                     doAssertEqual=utils.doAssertScreenshotsEqual)


    # select a previous electrode to show highlighting
    index = tw._model.index(list(navigatorGUI.session.digitizedLocations.keys()).index('C3'),
                            tw._model._columns.index('key'))
    tw._tableView.setCurrentIndex(index)

    mode = QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows
    i_start = list(navigatorGUI.session.digitizedLocations.keys()).index('Cz')
    tw._tableView.selectionModel().clearSelection()
    for i in range(i_start, i_start+5):
        index = tw._model.index(i, tw._model._columns.index('key'))
        tw._tableView.selectionModel().select(index, mode)

    await asyncio.sleep(0.5)

    screenshotPath = os.path.join(sessionPath, 'ElectrodeDigitization_Digitized.png')
    utils.captureScreenshot(navigatorGUI, screenshotPath)
    pyperclip.copy(str(screenshotPath))

    # utils.compareImages(screenshotPath,
    #                     os.path.join(screenshotsDataSourcePath, 'ElectrodeDigitization_Digitized.png'),
    #                     doAssertEqual=utils.doAssertScreenshotsEqual)

    await asyncio.sleep(1.)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    assert array_equalish(ses.digitizedLocations['C4'].sampledCoord, eegCoordinates['C4'])
    assert ses.digitizedLocations['C4'].type == 'electrode'



