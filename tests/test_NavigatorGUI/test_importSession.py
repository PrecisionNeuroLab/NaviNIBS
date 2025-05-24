import asyncio
import json
import logging

import attrs
import numpy as np
import os
import pyperclip
import pytest
import pytransform3d.transformations as ptt
import pytransform3d.rotations as ptr
from qtpy import QtWidgets
import random
import shutil

from NaviNIBS.Navigator.Model.Session import Session, DigitizedLocation, Addon
from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
from NaviNIBS.Navigator.GUI.EditWindows.ImportSessionWindow import ImportSessionWindow
from NaviNIBS.util.Transforms import applyTransform, invertTransform, composeTransform, concatenateTransforms
from NaviNIBS.util.numpy import array_equalish
from NaviNIBS.util.GUI.QAppWithAsyncioLoop import RunnableAsApp
from tests.test_NavigatorGUI import utils
from tests.test_NavigatorGUI.utils import (
    existingResourcesDataPath,
    navigatorGUIWithoutSession,
    workingDir,
    screenshotsDataSourcePath)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.order(after='test_basicNavigation.py::test_basicNavigation')
async def test_importSessionInStandalone(workingDir: str):
    sessionPath = utils.copySessionFolder(workingDir, 'InfoOnly', 'ImportToInStandalone')
    otherSessionPath = utils.copySessionFolder(workingDir, 'BasicNavigation', 'ImportFromInStandalone')

    otherSession = Session.loadFromFolder(folderpath=otherSessionPath)

    otherSession.subjectID = 'OtherSubject'
    otherSession.sessionID = 'OtherSession'
    otherSession.digitizedLocations.addItem(DigitizedLocation(
        key='TestLoc',
        plannedCoord=np.asarray([0, 1, 2]),
        sampledCoord=np.asarray([3, 4, 5]),
        type='EEG'
    ))
    otherSession.saveToUnpackedDir()

    session = Session.loadFromFolder(folderpath=sessionPath)

    importer = StandaloneImportWindow.createAndRunAsTask(
        session=session,
        otherSession=otherSession)

    importFinishedEvt = asyncio.Event()
    importer._importWindow.sigFinished.connect(lambda *args: importFinishedEvt.set())

    importer._importWindow._presetsComboBox.setCurrentText('Same subject, different session')

    await asyncio.sleep(1.)

    # await utils.waitForever()

    importer._importWindow._finalizeButtonBox.button(QtWidgets.QDialogButtonBox.Ok).click()

    await importFinishedEvt.wait()

    session.saveToUnpackedDir()

    assert utils.assertSavedSessionIsValid(sessionPath)

    assert session.subjectID == otherSession.subjectID

    assert session.sessionID != otherSession.sessionID

    assert session.MRI.filepath == otherSession.MRI.filepath

    assert session.headModel.filepath == otherSession.headModel.filepath

    fiducials = session.subjectRegistration.fiducials
    otherFiducials = otherSession.subjectRegistration.fiducials
    assert len(fiducials) == len(otherFiducials)
    assert all(array_equalish(fiducials[key].plannedCoord, otherFiducials[key].plannedCoord) for key in fiducials.keys())
    assert all(fid.sampledCoord is None for fid in fiducials.values())
    assert len(session.subjectRegistration.sampledHeadPoints) == 0
    assert session.subjectRegistration.trackerToMRITransf is None

    assert len(session.targets) == len(otherSession.targets)

    assert len(session.tools) == len(otherSession.tools)

    assert len(session.samples) == 0

    assert len(session.digitizedLocations) == len(otherSession.digitizedLocations)
    assert session.digitizedLocations['TestLoc'].plannedCoord is not None
    assert session.digitizedLocations['TestLoc'].sampledCoord is None

    assert len(session.triggerSources) == len(otherSession.triggerSources)
    # TODO: define a trigger source in otherSession before import and then test it copied correctly here

    # TODO: test that dock widget layouts copied correctly

    # TODO: test that addons copied correctly




@attrs.define
class StandaloneImportWindow(RunnableAsApp):
    _session: Session
    _otherSession: Session

    _theme: str = 'light'
    _importWindow: ImportSessionWindow = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._importWindow = ImportSessionWindow(
            parent=self._win.centralWidget(),
            session=self._session,
            otherSession=self._otherSession)

        self._win.show()




