import asyncio
import json
import logging
import numpy as np
import os
import pylsl as lsl
import pyperclip
import pytest
import pytransform3d.transformations as ptt
import pytransform3d.rotations as ptr
import random
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

from tests.test_NavigatorGUI.test_basicNavigation import simulatedPositionsBasicNav1Path


logger = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.order(after='test_basicNavigation.py::test_basicNavigation')
async def test_LSLTriggeredSampling(navigatorGUIWithoutSession: NavigatorGUI,
                                    workingDir: str,
                                    screenshotsDataSourcePath: str,
                                    simulatedPositionsBasicNav1Path: str):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'BasicNavigation', 'LSLTriggeredSampling')

    triggerStreamKey_primary = 'TestMarkerStream1'
    triggerStreamKey_secondary = 'TestMarkerStream2'

    # add LSL trigger info to session config
    sessionConfigPath = os.path.join(sessionPath, 'SessionConfig.json')
    with open(sessionConfigPath, 'r') as f:
        sessionConfig = json.load(f)

    sessionConfig['triggerSources'] = []
    sessionConfig['triggerSources'].append(dict(
        key='LSLTriggerSource_Primary',
        type='LSLTriggerSource',
        streamKey=triggerStreamKey_primary + '@localhost',
        fallbackTriggerSourceKey='LSLTriggerSource_Secondary'
    ))

    sessionConfig['triggerSources'].append(dict(
        key='LSLTriggerSource_Secondary',
        type='LSLTriggerSource',
        streamKey=triggerStreamKey_secondary + '@localhost',
    ))

    with open(sessionConfigPath, 'w') as f:
        json.dump(sessionConfig, f, indent=4)

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(
        10.)  # give time to restore any previous simulated positions  (TODO: handle this differently to speed up test)

    # TODO: go to TriggerSources tab

    # TODO: screenshot before streams are available

    # create fallback stream
    logger.info('Initializing secondary LSL trigger stream')
    info = lsl.StreamInfo(
        name=triggerStreamKey_secondary,
        type='Markers',
        channel_count=1,
        nominal_srate=lsl.IRREGULAR_RATE,
        channel_format='string',  # noqa
        source_id=triggerStreamKey_secondary
    )
    outlet_secondary = lsl.StreamOutlet(info)

    await asyncio.sleep(2.)  # give time for stream to be recognized

    # TODO: screenshot after secondary stream is available

    # TODO: go to navigation tab, fire a few triggers on secondary to make sure it works, then return here

    logger.info('Initializing primary LSL trigger stream')
    info = lsl.StreamInfo(
        name=triggerStreamKey_primary,
        type='Markers',
        channel_count=1,
        nominal_srate=lsl.IRREGULAR_RATE,
        channel_format='string',  # noqa
        source_id=triggerStreamKey_primary
    )
    outlet_primary = lsl.StreamOutlet(info)

    await asyncio.sleep(2.)  # give time for stream to be recognized

    # TODO: screenshot after primary stream is available

    # TODO: return to navigation tab

    # fire a few triggers on primary to make sure it works
    numSamplesBefore = 'todo'
    numToFire = 2
    for i in range(numToFire):
        logger.info(f'Firing trigger {i} on primary stream')
        outlet_primary.push_sample([f'Trigger{i}'])
        await asyncio.sleep(2.)
    numSamplesAfter = 'todo'
    # assert numSamplesAfter - numSamplesBefore = numToFire

    numSamplesBeforeSecondaryFire = 'todo'
    # fire a few triggers on secondary to make sure they don't get picked up while primary is present
    for i in range(numToFire):
        logger.info(f'Firing trigger {i} on secondary stream')
        outlet_secondary.push_sample([f'Trigger{i}'])
        await asyncio.sleep(2.)

    numSamplesAfterSecondaryFire = 'todo'
    assert numSamplesBeforeSecondaryFire == numSamplesAfterSecondaryFire, 'Secondary triggers were picked up while primary stream was present'

    # TODO: screenshot after some triggers

    # take down primary stream
    logger.info('Taking down primary LSL trigger stream')
    del outlet_primary
    await asyncio.sleep(1.)

    # TODO: go to TriggerSources tab, screenshot that primary is gone, then return to navigation tab

    # fire a few triggers on secondary to make sure they now get picked up
    numSamplesBefore = 'todo'
    for i in range(numToFire):
        logger.info(f'Firing trigger {i} on secondary stream')
        outlet_secondary.push_sample([f'Trigger{i}'])
        await asyncio.sleep(2.)
    numSamplesAfter = 'todo'
    # assert numSamplesAfter - numSamplesBefore == numToFire, 'Secondary triggers were not picked up after primary stream was taken down'

    # bring back primary stream
    logger.info('Re-initializing primary LSL trigger stream')
    info = lsl.StreamInfo(
        name=triggerStreamKey_primary,
        type='Markers',
        channel_count=1,
        nominal_srate=lsl.IRREGULAR_RATE,
        channel_format='string',  # noqa
        source_id=triggerStreamKey_primary
    )
    outlet_primary = lsl.StreamOutlet(info)
    await asyncio.sleep(2.)  # give time for stream to be recognized

    # TODO: go to TriggerSources tab, screenshot that primary is back, then return to navigation tab

    # fire a few triggers on primary to make sure it works (again)
    numSamplesBefore = 'todo'
    numToFire = 2
    for i in range(numToFire):
        logger.info(f'Firing trigger {i} on primary stream')
        outlet_primary.push_sample([f'Trigger{i}'])
        await asyncio.sleep(2.)
    numSamplesAfter = 'todo'
    # assert numSamplesAfter - numSamplesBefore = numToFire

    # await utils.waitForever()