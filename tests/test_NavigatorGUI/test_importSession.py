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
import random
import shutil

from NaviNIBS.Navigator.Model.Session import Session
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
@pytest.mark.order(after='test_manageSession.py::test_createSessionViaGUI')
@pytest.mark.order(after='test_NavigatorGUI.py::test_basicNavigation')
async def test_standaloneImportSession(workingDir: str):
    sessionPath = utils.copySessionFolder(workingDir, 'InfoOnly', 'StandaloneImportTo')
    otherSessionPath = utils.copySessionFolder(workingDir, 'BasicNavigation', 'StandaloneImportFrom')

    otherSession = Session.loadFromFolder(folderpath=otherSessionPath)

    otherSession.subjectID = 'OtherSubject'
    otherSession.sessionID = 'OtherSession'
    otherSession.saveToUnpackedDir()

    session = Session.loadFromFolder(folderpath=sessionPath)

    importer = StandaloneImportWindow.createAndRunAsTask(
        session=session,
        otherSession=otherSession)


    await utils.waitForever()  #  TODO: debug, delete




@attrs.define
class StandaloneImportWindow(RunnableAsApp):
    _session: Session
    _otherSession: Session

    _theme: str = 'light'
    _augmentWindow: ImportSessionWindow = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._augmentWindow = ImportSessionWindow(
            parent=self._win.centralWidget(),
            session=self._session,
            otherSession=self._otherSession)

        self._win.show()




