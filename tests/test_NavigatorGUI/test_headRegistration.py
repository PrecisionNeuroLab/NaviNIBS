import asyncio
import logging
import numpy as np
import os
import pyperclip
import pytest
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

logger = logging.getLogger(__name__)


@pytest.fixture
def trackerToMRITransf():
    return np.asarray([
        [0.9678884219692884, 0.12098540650006323, 0.22035093381198817, 34.45752550020878],
        [-0.2511741588202068, 0.42996526829785314, 0.8672032114784379, 124.75547193939892],
        [0.010175684682725016, -0.8947024083300377, 0.4465468127415853, 47.33965520789809],
        [0.0, 0.0, 0.0, 1.0]
    ])


@pytest.fixture
def headPoints_trackerSpace():
    return np.asarray([
    [64.0190871872029, 35.45084207764229, -120.32860818725167],
    [62.71195490629869, 19.402026059320164, -96.90008195442515],
    [53.355707298042454, 14.135816926274018, -72.52813341903139],
    [31.517612027777027, 6.569108195420384, -47.320215165377604],
    [-2.1541286328227116, 8.86413373898742, -30.25787377311086],
    [51.41614038437579, -31.782544515105542, -70.32416418683191],
    [76.29000866760393, -13.063730550087058, -127.19245051948975],
    [77.79799283157087, -21.419066220949155, -178.76789853415954],
    [50.74345237639509, -26.482265136814767, -214.9609912118952],
    [69.11486279231013, -76.06053403136364, -144.92148445674525],
    [33.41363262851835, -87.17773802564173, -97.25031667972628],
    [13.629997814204565, -52.89662794049651, -48.79982850412496],
    [53.51480366242691, -70.88060238866768, -193.59468162810674],
    [21.851122661037046, -75.19129109490459, -212.97263213852392],
    [11.140614080704964, -111.9147693130316, -147.01343462904117],
    [11.25153502651954, -104.8460172769617, -176.6568438616626],
    [-23.59878923659098, -45.51709741932188, -224.1913838569758],
    [-56.6760605270695, -58.21119431118896, -196.3510338319589],
    [-48.88747950877281, -86.3096625997889, -168.94087173821958],
    [-43.437925765691915, -85.17144599416314, -115.07332573856611],
    [-77.62171147394733, -35.503627393463674, -132.89745778995712],
    [-82.1014520369893, 8.483862491455, -100.82959853067437],
    [-67.24793948872458, 3.604176538802335, -58.63497131707818],
    [-34.31197539669814, 5.018802843820623, -34.704941493661366],
    [-56.797536443315046, -27.512989876288053, -56.265942500960236],
    [-27.59011091127132, -40.40949328883741, -40.41224127823294],
    [-27.893236233532953, -65.07823257974566, -60.954764697468974],
    [-8.843036933936098, -95.44932283019462, -99.29752203028094],
    [-58.66228152110755, -55.134982389436296, -83.6700910871463],
    [-27.70122948245926, 57.554853037498944, -51.728502323420784],
    [-30.93679753035809, 72.15824845155983, -49.142756743883055],
    [-32.9113920815359, 83.72631739165799, -50.129642881821596]
  ])


@pytest.mark.asyncio
@pytest.mark.order(after='test_tools.py::test_calibrateCoil')
async def test_initialFiducialRegistration(navigatorGUIWithoutSession: NavigatorGUI,
                                           workingDir: str,
                                           screenshotsDataSourcePath: str,
                                           trackerToMRITransf: np.ndarray):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'CalibrateCoil', 'InitialFiducialRegistration')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.subjectRegistrationPanel.key)

    # give time for initialization
    await navigatorGUI.subjectRegistrationPanel.finishedAsyncInitializationEvent.wait()
    await asyncio.sleep(1.)

    assert navigatorGUI.activeViewKey == navigatorGUI.subjectRegistrationPanel.key

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='HeadRegistration_Empty',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    reg = navigatorGUI.session.subjectRegistration
    fids = reg.fiducials

    plannedFidCoords_MRISpace = {key: fid.plannedCoord for key, fid in fids.items() if 'Nose' not in key}
    assert len(plannedFidCoords_MRISpace) == 3

    MRIToTrackerTransf = invertTransform(trackerToMRITransf)

    toSampleFidCoords_trackerSpace = {key: applyTransform(MRIToTrackerTransf, coord)
                                      for key, coord in plannedFidCoords_MRISpace.items()}

    # TODO: set tracker and pointer positions, sample each fiducial
    logger.debug(f'Getting subject tracker pose')
    trackerKey = navigatorGUI.session.tools.subjectTracker.key
    trackerPose_worldSpace = navigatorGUI.subjectRegistrationPanel._positionsClient.getLatestTransf(trackerKey)

    pointerKey = navigatorGUI.session.tools.pointer.key
    for fidKey, coord in toSampleFidCoords_trackerSpace.items():
        logger.debug(f'Registering fiducial {fidKey}')
        pointerPose_trackerSpace = composeTransform(np.eye(3), coord)
        pointerPose_worldSpace = concatenateTransforms((pointerPose_trackerSpace,
                                                       trackerPose_worldSpace))
        await utils.setSimulatedToolPose(navigatorGUI=navigatorGUI,
                                   key=pointerKey,
                                   transf=pointerPose_worldSpace)

        await asyncio.sleep(1.)

        # equivalent to clicking on corresponding entry in table
        navigatorGUI.subjectRegistrationPanel._fidTblWdgt.currentCollectionItemKey = fidKey

        await asyncio.sleep(1.)

        # equivalent to clicking on sample button
        navigatorGUI.subjectRegistrationPanel._sampleFiducialBtn.click()

    await asyncio.sleep(1.)

    # equivalent to clicking on align button
    navigatorGUI.subjectRegistrationPanel._alignToFiducialsBtn.click()

    await asyncio.sleep(1.)

    # dodge pointer position slightly to update distance readouts
    newPose = pointerPose_worldSpace.copy()
    newPose[2, 3] += 2
    await utils.setSimulatedToolPose(navigatorGUI=navigatorGUI,
                                     key=pointerKey,
                                     transf=newPose)

    await asyncio.sleep(1.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='HeadRegistration_InitialFiducials',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    # new transform should be identical(ish) to planned transform
    assert array_equalish(ses.subjectRegistration.trackerToMRITransf, trackerToMRITransf)


@pytest.mark.asyncio
@pytest.mark.order(after='test_initialFiducialRegistration')
async def test_acquireHeadPoints(navigatorGUIWithoutSession: NavigatorGUI,
                                   workingDir: str,
                                   screenshotsDataSourcePath: str,
                                   trackerToMRITransf: np.ndarray,
                                   headPoints_trackerSpace: np.ndarray):
    """
    Make this a separate test from later head point refinement test so that we have a deterministic output
    (i.e. without non-deterministic head point alignment) for use in later tests, e.g. basic navigation, while
    still displaying some acquired head points.
    """
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'InitialFiducialRegistration', 'HeadPointAcquisition')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.subjectRegistrationPanel.key)

    # give time for initialization
    await navigatorGUI.subjectRegistrationPanel.finishedAsyncInitializationEvent.wait()
    await asyncio.sleep(1.)

    assert navigatorGUI.activeViewKey == navigatorGUI.subjectRegistrationPanel.key

    offset_MRISpace = np.asarray([0., 0., 5.])  # add an offset to simulate fiducials having been slightly off, will be refined by head points
    transf_MRI1ToMRI2 = composeTransform(np.eye(3), offset_MRISpace)

    logger.debug(f'Getting subject tracker pose')
    trackerKey = navigatorGUI.session.tools.subjectTracker.key
    trackerPose_worldSpace = navigatorGUI.subjectRegistrationPanel._positionsClient.getLatestTransf(trackerKey)

    pointerKey = navigatorGUI.session.tools.pointer.key
    for iCoord, coord in enumerate(headPoints_trackerSpace):
        logger.debug(f'Sampling head point {iCoord}')
        adjCoord = applyTransform((trackerToMRITransf, transf_MRI1ToMRI2, invertTransform(trackerToMRITransf)), coord)
        pointerPose_trackerSpace = composeTransform(np.eye(3), adjCoord)
        pointerPose_worldSpace = concatenateTransforms((pointerPose_trackerSpace,
                                                        trackerPose_worldSpace))
        await utils.setSimulatedToolPose(navigatorGUI=navigatorGUI,
                                         key=pointerKey,
                                         transf=pointerPose_worldSpace)

        await asyncio.sleep(1.)

        # equivalent to clicking on sample head point button
        navigatorGUI.subjectRegistrationPanel._sampleHeadPtsBtn.click()

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='HeadRegistration_HeadPointAcquisition',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)


@pytest.mark.asyncio
@pytest.mark.order(after='test_initialFiducialRegistration')
async def test_headPointRefinement(navigatorGUIWithoutSession: NavigatorGUI,
                                   workingDir: str,
                                   screenshotsDataSourcePath: str,
                                   trackerToMRITransf: np.ndarray,
                                   headPoints_trackerSpace: np.ndarray):
    navigatorGUI = navigatorGUIWithoutSession

    sessionPath = utils.copySessionFolder(workingDir, 'InitialFiducialRegistration', 'HeadPointRefinement')

    # open session
    navigatorGUI.manageSessionPanel.loadSession(sesFilepath=sessionPath)

    await asyncio.sleep(1.)

    # equivalent to clicking on tab
    navigatorGUI._activateView(navigatorGUI.subjectRegistrationPanel.key)

    # give time for initialization
    await navigatorGUI.subjectRegistrationPanel.finishedAsyncInitializationEvent.wait()
    await asyncio.sleep(1.)

    assert navigatorGUI.activeViewKey == navigatorGUI.subjectRegistrationPanel.key

    offset_MRISpace = np.asarray([0., 0., 5.])  # add an offset to simulate fiducials having been slightly off, will be refined by head points
    transf_MRI1ToMRI2 = composeTransform(np.eye(3), offset_MRISpace)

    logger.debug(f'Getting subject tracker pose')
    trackerKey = navigatorGUI.session.tools.subjectTracker.key
    trackerPose_worldSpace = navigatorGUI.subjectRegistrationPanel._positionsClient.getLatestTransf(trackerKey)

    pointerKey = navigatorGUI.session.tools.pointer.key
    for iCoord, coord in enumerate(headPoints_trackerSpace):
        logger.debug(f'Sampling head point {iCoord}')
        adjCoord = applyTransform((trackerToMRITransf, transf_MRI1ToMRI2, invertTransform(trackerToMRITransf)), coord)
        pointerPose_trackerSpace = composeTransform(np.eye(3), adjCoord)
        pointerPose_worldSpace = concatenateTransforms((pointerPose_trackerSpace,
                                                        trackerPose_worldSpace))
        await utils.setSimulatedToolPose(navigatorGUI=navigatorGUI,
                                         key=pointerKey,
                                         transf=pointerPose_worldSpace)

        await asyncio.sleep(1.)

        # equivalent to clicking on sample head point button
        navigatorGUI.subjectRegistrationPanel._sampleHeadPtsBtn.click()

    await asyncio.sleep(1.)

    logger.debug('Setting refinement weights')
    navigatorGUI.subjectRegistrationPanel._refineWeightsField.setText('[0, 0.1]')
    navigatorGUI.subjectRegistrationPanel._refineWeightsField.editingFinished.emit()  # not emitted by programmatic changes
    await asyncio.sleep(1.)

    logger.debug('Refining with head points')
    navigatorGUI.subjectRegistrationPanel._refineWithHeadpointsBtn.click()

    await asyncio.sleep(1.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='HeadRegistration_HeadPointRefinement',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # equivalent to clicking save button
    navigatorGUI.manageSessionPanel._onSaveSessionBtnClicked(checked=False)

    ses = utils.assertSavedSessionIsValid(sessionPath)

    # new transform after refinement should not be identical(ish) to planned transform
    refinedTrackerToMRITransf = ses.subjectRegistration.trackerToMRITransf
    assert not array_equalish(refinedTrackerToMRITransf, trackerToMRITransf)

    preMoveHeadPoints = navigatorGUI.session.subjectRegistration.sampledHeadPoints.asList()

    # re-aligning to fiducials should bring it back to planned transform
    # equivalent to clicking on align button
    navigatorGUI.subjectRegistrationPanel._alignToFiducialsBtn.click()

    await asyncio.sleep(1.)

    assert array_equalish(navigatorGUI.session.subjectRegistration.trackerToMRITransf, trackerToMRITransf)

    # unrefining should not have changed head points themselves
    postMoveHeadPoints = navigatorGUI.session.subjectRegistration.sampledHeadPoints.asList()
    assert array_equalish(np.asarray(preMoveHeadPoints), np.asarray(postMoveHeadPoints))

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='HeadRegistration_Unrefined',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # re-refining should produce approximately same refined transform
    navigatorGUI.subjectRegistrationPanel._refineWithHeadpointsBtn.click()

    await asyncio.sleep(1.0)

    assert not array_equalish(navigatorGUI.session.subjectRegistration.trackerToMRITransf, trackerToMRITransf)
    assert array_equalish(navigatorGUI.session.subjectRegistration.trackerToMRITransf, refinedTrackerToMRITransf)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='HeadRegistration_Rerefined',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

    # resamping fiducial(s) after head point refinement and then re-aligning should trigger transformation of head points
    pointerKey = navigatorGUI.session.tools.pointer.key
    fidKey = 'RPA'
    origSampledFidCoord_trackerSpace = navigatorGUI.session.subjectRegistration.fiducials[fidKey].sampledCoord
    newSampledFidCoord_trackerSpace = origSampledFidCoord_trackerSpace + np.asarray([0., 0., 20.])
    pointerPose_trackerSpace = composeTransform(np.eye(3), newSampledFidCoord_trackerSpace)
    pointerPose_worldSpace = concatenateTransforms((pointerPose_trackerSpace,
                                                        trackerPose_worldSpace))
    await utils.setSimulatedToolPose(navigatorGUI=navigatorGUI,
                                     key=pointerKey,
                                     transf=pointerPose_worldSpace)

    await asyncio.sleep(1.)

    # equivalent to clicking on corresponding entry in table
    navigatorGUI.subjectRegistrationPanel._fidTblWdgt.currentCollectionItemKey = fidKey

    await asyncio.sleep(1.)

    # equivalent to clicking on sample button
    navigatorGUI.subjectRegistrationPanel._sampleFiducialBtn.click()

    await asyncio.sleep(1.)

    preMoveHeadPoints = navigatorGUI.session.subjectRegistration.sampledHeadPoints.asList()

    # equivalent to clicking on align button
    navigatorGUI.subjectRegistrationPanel._alignToFiducialsBtn.click()

    postMoveHeadPoints = navigatorGUI.session.subjectRegistration.sampledHeadPoints.asList()

    assert not array_equalish(np.asarray(preMoveHeadPoints), np.asarray(postMoveHeadPoints))

    await asyncio.sleep(1.)

    await utils.captureAndCompareScreenshot(navigatorGUI=navigatorGUI,
                                            sessionPath=sessionPath,
                                            screenshotName='HeadRegistration_RefinedThenRegistered',
                                            screenshotsDataSourcePath=screenshotsDataSourcePath)

