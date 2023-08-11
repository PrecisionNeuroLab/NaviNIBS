import asyncio
import attrs
import json
import logging
import numpy as np
import pytransform3d.rotations as ptr
from skspatial.objects import Line, Plane, Vector
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from RTNaBS.Navigator.Model.Targets import Target
from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Navigator.GUI.CollectionModels.TargetsTableModel import TargetsTableModel, FullTargetsTableModel
from RTNaBS.Navigator.Model.Session import Session, Tool
from RTNaBS.Navigator.Model.Calculations import getClosestPointToPointOnMesh, calculateCoilToMRITransfFromTargetEntryAngle
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import applyTransform, invertTransform, composeTransform, concatenateTransforms, applyDirectionTransform, calculateRotationMatrixFromVectorToVector
from RTNaBS.util.GUI.QDial import AngleDial
from RTNaBS.util.GUI.QScrollContainer import QScrollContainer
from RTNaBS.util.GUI.QMouseWheelAdjustmentGuard import preventAnnoyingScrollBehaviour

logger = logging.getLogger(__name__)


@attrs.define
class CoordinateWidget:

    _session: tp.Optional[Session] = attrs.field(default=None, repr=False)
    _target: Target | None = None
    _whichCoord: str = 'target'  # target or entry

    _onNewCoordRequested: tp.Callable[[], np.ndarray] | None = attrs.field(default=None)
    """
    Use this to specify a callback for user pressing button to set new coordinates (e.g. from a cursor in a view outside this widget).
    
    Callable should return ndarray vector of coordinates in world space.
    """
    # TODO: maybe make this async instead to allow for a request to prompt user for feedback, etc.
    _setCoordButtonLabel: str = 'Set coord'

    _wdgt: QtWidgets.QWidget = attrs.field(factory=QtWidgets.QWidget)
    _layout: QtWidgets.QFormLayout = attrs.field(init=False, factory=QtWidgets.QFormLayout)
    _coordInSysWdgts: dict[str, QtWidgets.QLabel] = attrs.field(init=False, factory=dict)
    _setCoordButton: QtWidgets.QPushButton | None = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        self._wdgt.setLayout(self._layout)

        if self._onNewCoordRequested is not None:
            self._setCoordButton = QtWidgets.QPushButton(self._setCoordButtonLabel)
            self._setCoordButton.clicked.connect(self._onSetCoordButtonClicked)
            self._layout.addRow(self._setCoordButton)

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSes: tp.Optional[Session]):
        self._session = newSes
        self._redraw()

    @property
    def target(self):
        return self._target

    @target.setter
    def target(self, newTarget: Target | None):
        if self._target is newTarget:
            return
        if self._target is not None:
            self._target.sigItemChanged.disconnect(self._onTargetItemChanged)

        self._target = newTarget

        if self._target is not None:
            self._target.sigItemChanged.connect(self._onTargetItemChanged)

        self._redraw()

    @property
    def coordAttrib(self):
        return self._whichCoord + 'Coord'

    @property
    def wdgt(self):
        return self._wdgt

    def _onTargetItemChanged(self, targetKey: str, attribsChanged: list[str] | None = None):
        if attribsChanged is None or self.coordAttrib in attribsChanged:
            self._redraw()

    def _onSetCoordButtonClicked(self, _: bool):
        assert self._onNewCoordRequested is not None
        assert self.target is not None
        newCoord = self._onNewCoordRequested()
        logger.info(f'Changing {self.target.key} {self.coordAttrib} to {newCoord}')
        setattr(self.target, self.coordAttrib, newCoord)

    def _redraw(self):
        if self._session is None:
            for key, wdgt in self._coordInSysWdgts.items():
                self._layout.removeWidget(wdgt)
            self._coordInSysWdgts.clear()
            if self._setCoordButton is not None:
                self._setCoordButton.setEnabled(False)
            return

        if self._target is None:
            for key, wdgt in self._coordInSysWdgts.items():
                wdgt.setText('')
            if self._setCoordButton is not None:
                self._setCoordButton.setEnabled(False)
            return

        if self._setCoordButton is not None:
            self._setCoordButton.setEnabled(True)

        assert 'World' not in self._session.coordinateSystems
        coordSysKeys = ['World'] + list(self._session.coordinateSystems.keys())
        for key in coordSysKeys:
            if key not in self._coordInSysWdgts:
                wdgt = QtWidgets.QLabel()
                if key != 'World':
                    wdgt.setToolTip(self._session.coordinateSystems[key].description)
                self._coordInSysWdgts[key] = wdgt
                self._layout.addRow(key, wdgt)

            # TODO: make mainCoord widget an editable lineedit, keep others readonly unless user
            #  specifically chooses to switch main coordinate system defining the coordinate (if allowed)

            worldCoord = getattr(self._target, self.coordAttrib)

            if key == 'World':
                coord = worldCoord
            else:
                coord = self._session.coordinateSystems[key].transformFromWorldToThis(worldCoord)

            # assume one decimal point is sufficient for any coordinate system
            # (works for integer or mm units, not m units...)
            if False:
                coordTxt = json.dumps(np.around(coord, decimals=1).tolist())
                # note: this sometimes has floating point precision errors such that many more decimal places are shown
            else:
                coordTxt = '[' + ', '.join([f'{c:.2f}' for c in coord]) + ']'

            self._coordInSysWdgts[key].setText(coordTxt)

        removeKeys = set(self._coordInSysWdgts.keys()) - set(coordSysKeys)
        for key in removeKeys:
            wdgt = self._coordInSysWdgts.pop(key)
            self._layout.removeWidget(wdgt)


@attrs.define(kw_only=True)
class EntryAnglesWidgets:
    _layout: QtWidgets.QFormLayout
    _session: tp.Optional[Session] = attrs.field(default=None, repr=False)
    _target: Target | None = None

    _pivotWdgt: QtWidgets.QComboBox = attrs.field(init=False)
    _angleRefWdgt: QtWidgets.QComboBox = attrs.field(init=False)
    _angleXWdgt: AngleDial = attrs.field(init=False)
    _angleYWdgt: AngleDial = attrs.field(init=False)

    def __attrs_post_init__(self):
        # TODO: maybe put all entry angle widgets into groupbox
        self._pivotWdgt = QtWidgets.QComboBox()
        self._pivotWdgt.addItems(['Coil', 'Entry', 'Target'])
        self._pivotWdgt.setCurrentIndex(2)
        self._pivotWdgt.currentIndexChanged.connect(self._onEntryAnglePivotChanged)
        self._layout.addRow('Entry angle pivot', self._pivotWdgt)

        self._angleRefWdgt = QtWidgets.QComboBox()
        self._angleRefWdgt.addItems(['Closest scalp to target'])
        self._angleRefWdgt.setCurrentIndex(0)
        self._angleRefWdgt.currentIndexChanged.connect(self._onEntryAngleRefChanged)
        self._layout.addRow('Entry angle relative to', self._angleRefWdgt)

        self._angleXWdgt = AngleDial(
            doInvert=False,
            offsetAngle=180,
            centerAngle=0
        )
        self._angleXWdgt.sigValueChanged.connect(self._onEntryAngleXChangedFromGUI)
        self._layout.addRow('Entry angle X', self._angleXWdgt.wdgt)

        self._angleYWdgt = AngleDial(
            doInvert=False,
            offsetAngle=180,
            centerAngle=0
        )
        self._angleYWdgt.sigValueChanged.connect(self._onEntryAngleYChangedFromGUI)
        self._layout.addRow('Entry angle Y', self._angleYWdgt.wdgt)

    @property
    def target(self):
        return self._target

    @target.setter
    def target(self, target: Target | None):
        if self._target is target:
            return

        if self._target is not None:
            self._target.sigItemChanged.disconnect(self._onTargetItemChanged)

        self._target = target

        if self._target is not None:
            self._target.sigItemChanged.connect(self._onTargetItemChanged)

        self._refreshEntryAngleWidgets()

    @property
    def pivotWdgt(self):
        return self._pivotWdgt

    @property
    def angleRefWdgt(self):
        return self._angleRefWdgt

    @property
    def angleXWdgt(self):
        return self._angleXWdgt

    @property
    def angleYWdgt(self):
        return self._angleYWdgt

    def _onEntryAnglePivotChanged(self, index: int):
        self._refreshEntryAngleWidgets()

    def _onEntryAngleRefChanged(self, index: int):
        self._refreshEntryAngleWidgets()

    def _onEntryAngleXChangedFromGUI(self, newAngle: float):
        logger.info(f'Entry angle X changed to {newAngle} degrees')
        self._applyEntryAngleChangesToModel()

    def _onEntryAngleYChangedFromGUI(self, newAngle: float):
        logger.info(f'Entry angle Y changed to {newAngle} degrees')
        self._applyEntryAngleChangesToModel()

    def _getAngleRefVec(self) -> Vector | None:
        angleRef = self._angleRefWdgt.currentText()
        match angleRef:
            case 'Closest scalp to target':
                closestPt_skin = getClosestPointToPointOnMesh(
                    session=self._session,
                    whichMesh='skinSurf',
                    point_MRISpace=self._target.targetCoord)
                if closestPt_skin is None:
                    return None
                idealNormal = Vector(closestPt_skin - self._target.targetCoord)
                return idealNormal

            case _:
                raise NotImplementedError

    def _clearAndDisableEntryAngleWidgets(self):
        self._angleXWdgt.value = 0
        self._angleYWdgt.value = 0
        self._angleXWdgt.enabled = False
        self._angleYWdgt.enabled = False

    def _refreshEntryAngleWidgets(self):
        if self._target is None:
            self._clearAndDisableEntryAngleWidgets()
            return

        angleRefVec_MRI = self._getAngleRefVec()
        if angleRefVec_MRI is None:
            self._clearAndDisableEntryAngleWidgets()
            return
        logger.debug(f'angleRefVec_MRI: {angleRefVec_MRI}')

        coilToMRITransf = self._target.coilToMRITransf
        if coilToMRITransf is None:
            self._clearAndDisableEntryAngleWidgets()
            return

        angleRefVec_coil = applyDirectionTransform(invertTransform(coilToMRITransf), angleRefVec_MRI)
        logger.debug(f'angleRefVec_coil: {angleRefVec_coil}')
        coilDepthVec_coil = Vector([0, 0, 1])
        logger.debug(f'coilDepthVec_coil: {coilDepthVec_coil}')

        rot = calculateRotationMatrixFromVectorToVector(angleRefVec_coil, coilDepthVec_coil)  # TODO: check sign
        logger.debug(f'rot: {rot}')

        angleZ, angleX, angleY = ptr.extrinsic_euler_zxy_from_active_matrix(rot)
        logger.debug(f'Entry angle X: {np.rad2deg(angleX)}  Y: {np.rad2deg(angleY)}  Z: {np.rad2deg(angleZ)}')

        with self._angleXWdgt.sigValueChanged.disconnected(self._onEntryAngleXChangedFromGUI), \
             self._angleYWdgt.sigValueChanged.disconnected(self._onEntryAngleYChangedFromGUI):
            self._angleXWdgt.value = np.rad2deg(angleX)
            self._angleYWdgt.value = np.rad2deg(angleY)

    def _applyEntryAngleChangesToModel(self):

        if self._target is None:
            logger.warning('Can\'t apply entry angle changes: no target selected')
            return

        refVec = self._getAngleRefVec()
        if refVec is None:
            logger.warning("Can't apply entry angle changes: no reference vector")
            return

        refVec = refVec.unit()

        entryToTargetDist = np.linalg.norm(self._target.entryCoord - self._target.targetCoord)

        pivot = self._pivotWdgt.currentText()
        match pivot:
            case 'Coil':
                coilToPivotZOffset = 0
                pivotCoord_MRISpace = self._target.entryCoordPlusDepthOffset
            case 'Entry':
                coilToPivotZOffset = self._target.depthOffset
                pivotCoord_MRISpace = self._target.entryCoord
            case 'Target':
                coilToPivotZOffset = self._target.depthOffset + entryToTargetDist
                pivotCoord_MRISpace = self._target.targetCoord

            case _:
                raise NotImplementedError

        prepivotCoilCoord_MRISpace = pivotCoord_MRISpace + refVec * coilToPivotZOffset
        prepivotEntryCoord_MRISpace = prepivotCoilCoord_MRISpace - refVec * self._target.depthOffset
        prepivotTargetCoord_MRISpace = prepivotCoilCoord_MRISpace - refVec * (self._target.depthOffset + entryToTargetDist)

        prepivotTargetCoilToMRITransf = calculateCoilToMRITransfFromTargetEntryAngle(
            session=self._session,
            targetCoord=prepivotTargetCoord_MRISpace,
            entryCoord=prepivotEntryCoord_MRISpace,
            angle=self._target.angle,
            depthOffset=self._target.depthOffset,
            prevCoilToMRITransf=self._target.coilToMRITransf,
        )

        coilToPivotSpaceTransf = np.eye(4)
        coilToPivotSpaceTransf[2, 3] = coilToPivotZOffset

        angleX = np.deg2rad(self._angleXWdgt.value)
        angleY = np.deg2rad(self._angleYWdgt.value)

        pivotTransf = np.eye(4)
        pivotTransf[:3, :3] = ptr.active_matrix_from_extrinsic_euler_zxy(np.asarray([0, angleX, angleY]))  # TODO: double check signs

        pivotedCoilToMRITransf = concatenateTransforms([coilToPivotSpaceTransf, pivotTransf, invertTransform(coilToPivotSpaceTransf), prepivotTargetCoilToMRITransf])

        self._target.entryCoord = applyTransform(pivotedCoilToMRITransf, np.asarray([0, 0, -self._target.depthOffset]))
        self._target.targetCoord = applyTransform(pivotedCoilToMRITransf, np.asarray([0, 0, -self._target.depthOffset - entryToTargetDist]))

    def _onTargetItemChanged(self, targetKey: str, attribsChanged: list[str] | None = None):
        if attribsChanged is None or any(x in attribsChanged for x in
                                         ('targetCoord',
                                          'entryCoord',
                                          'depthOffset',
                                          'coilToMRITransf')):
            self._refreshEntryAngleWidgets()


@attrs.define(kw_only=True)
class EditTargetWidget:
    _session: Session = attrs.field(repr=False)

    _wdgt: QtWidgets.QWidget = attrs.field(factory=lambda: QtWidgets.QGroupBox('Edit target'))
    _scroll: QScrollContainer = attrs.field(init=False)

    _depthOffsetWdgt: QtWidgets.QDoubleSpinBox = attrs.field(init=False)

    _getNewTargetCoord: tp.Callable[[], tp.Tuple[float, float, float]] | None = attrs.field(default=None)
    """
    Use this to specify a callback for user pressing button to set new target coordinates (e.g. from a cursor in a view outside this widget).
    """
    _setTargetCoordButtonLabel: str = 'Set target coord'

    _getNewEntryCoord: tp.Callable[[], tp.Tuple[float, float, float]] | None = attrs.field(default=None)
    """
    Use this to specify a callback for user pressing button to set new entry  coordinates (e.g. from a cursor in a view outside this widget).
    """
    _setEntryCoordButtonLabel: str = 'Set entry coord'

    _targetComboBox: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)
    _targetsModel: FullTargetsTableModel = attrs.field(init=False)

    _targetCoordWdgt: CoordinateWidget = attrs.field(init=False)
    _entryCoordWdgt: CoordinateWidget = attrs.field(init=False)

    _handleAngleWdgt: AngleDial = attrs.field(init=False)

    _entryAngleWdgts: EntryAnglesWidgets = attrs.field(init=False)

    _target: Target | None = attrs.field(init=False, default=None)

    _doTrackModelSelectedTarget: bool = True

    _disableWidgetsWhenNoTarget: list[QtWidgets.QWidget] = attrs.field(init=False)

    def __attrs_post_init__(self):
        outerLayout = QtWidgets.QVBoxLayout()
        self._wdgt.setLayout(outerLayout)
        outerLayout.setContentsMargins(0, 0, 0, 0)

        layout = QtWidgets.QFormLayout()
        self._scroll = QScrollContainer(innerContainerLayout=layout)
        self._scroll.scrollArea.setFrameShape(QtWidgets.QFrame.NoFrame)
        outerLayout.addWidget(self._scroll.scrollArea)

        self._scroll.scrollArea.setSizePolicy(QtWidgets.QSizePolicy.Minimum,
                                              QtWidgets.QSizePolicy.Preferred)

        self._targetsModel = FullTargetsTableModel(session=self._session)
        self._targetComboBox.setModel(self._targetsModel)
        self._targetComboBox.currentIndexChanged.connect(self._onTargetComboBoxCurrentIndexChanged)
        self._targetsModel.sigSelectionChanged.connect(self._onModelSelectionChanged)
        layout.addRow('Editing target:', self._targetComboBox)

        self._depthOffsetWdgt = QtWidgets.QDoubleSpinBox()
        preventAnnoyingScrollBehaviour(self._depthOffsetWdgt)
        self._depthOffsetWdgt.valueChanged.connect(self._onDepthOffsetChangedFromGUI)
        self._depthOffsetWdgt.setRange(-10, 1000)
        self._depthOffsetWdgt.setSingleStep(0.1)
        self._depthOffsetWdgt.setDecimals(1)
        self._depthOffsetWdgt.setSuffix(' mm')
        layout.addRow('Depth offset:', self._depthOffsetWdgt)

        self._entryCoordWdgt = CoordinateWidget(self._session,
                                                whichCoord='entry',
                                                wdgt=QtWidgets.QGroupBox('Entry coordinate'),
                                                onNewCoordRequested=self._getNewEntryCoord,
                                                setCoordButtonLabel=self._setEntryCoordButtonLabel)
        layout.addRow(self._entryCoordWdgt.wdgt)

        self._targetCoordWdgt = CoordinateWidget(self._session,
                                                 whichCoord='target',
                                                 wdgt=QtWidgets.QGroupBox('Target coordinate'),
                                                 onNewCoordRequested=self._getNewTargetCoord,
                                                 setCoordButtonLabel=self._setTargetCoordButtonLabel)
        layout.addRow(self._targetCoordWdgt.wdgt)

        self._handleAngleWdgt = AngleDial(
            doInvert=True,
            offsetAngle=0,
            centerAngle=0
        )
        self._handleAngleWdgt.sigValueChanged.connect(self._onHandleAngleChangedFromGUI)
        layout.addRow('Handle angle', self._handleAngleWdgt.wdgt)

        self._entryAngleWdgts = EntryAnglesWidgets(
            layout=layout,
            session=self._session,
            target=self._target
        )
        # children widgets added to layout internally in constructor

        self._disableWidgetsWhenNoTarget = [
            self._depthOffsetWdgt,
            self._targetCoordWdgt.wdgt,
            self._entryCoordWdgt.wdgt,
            self._handleAngleWdgt.wdgt,
            self._entryAngleWdgts.pivotWdgt,
            self._entryAngleWdgts.angleRefWdgt,
            self._entryAngleWdgts.angleXWdgt.wdgt,
            self._entryAngleWdgts.angleYWdgt.wdgt
        ]

        self._onTargetComboBoxCurrentIndexChanged(self._targetComboBox.currentIndex())  # enable/disable widgets

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def target(self):
        return self._target

    @target.setter
    def target(self, target: Target | None):
        if self._target is target:
            return

        if self._target is not None:
            self._target.sigItemChanged.disconnect(self._onTargetItemChanged)

        self._target = target

        if self._target is not None:
            self._target.sigItemChanged.connect(self._onTargetItemChanged)

        # update children widgets

        for obj in (self._targetCoordWdgt, self._entryCoordWdgt, self._entryAngleWdgts):
            obj.target = self._target

        if target is None:
            self._targetComboBox.setCurrentIndex(-1)
            self._handleAngleWdgt.value = 0
            self._depthOffsetWdgt.setValue(0)
        else:
            self._targetComboBox.setCurrentIndex(self._targetsModel.getIndexFromCollectionItemKey(target.key))
            self._handleAngleWdgt.value = target.angle
            self._depthOffsetWdgt.setValue(target.depthOffset)

    def setEnabled(self, enabled: bool):
        self._wdgt.setEnabled(enabled)

    def _onModelSelectionChanged(self, changedKeys: list[str]):
        if not self._doTrackModelSelectedTarget:
            return

        # make sure current combobox item is in selection

        menuCurrentTargetIndex = self._targetComboBox.currentIndex()
        if menuCurrentTargetIndex != -1:
            try:
                menuCurrentTargetKey = self._targetsModel.getCollectionItemKeyFromIndex(menuCurrentTargetIndex)
            except IndexError:
                # model probably just changed to reduce number of items
                return  # TODO: determine better behavior to handle this instead of just ignoring

            if menuCurrentTargetKey not in changedKeys:
                return

            if self._targetsModel.getCollectionItemIsSelected(menuCurrentTargetKey):
                return

        firstSelectedTargetKey = None
        for target in self._session.targets.values():
            if target.isSelected:
                firstSelectedTargetKey = target.key
                break

        if firstSelectedTargetKey is None:
            self._targetComboBox.setCurrentIndex(-1)
        else:
            index = self._targetsModel.getIndexFromCollectionItemKey(firstSelectedTargetKey)
            if False:
                self._targetComboBox.setCurrentIndex(index)
            else:
                # run after short delay to prevent issue with nested model changes
                QtCore.QTimer.singleShot(0, lambda index=index: self._targetComboBox.setCurrentIndex(index))

    def _onTargetComboBoxCurrentIndexChanged(self, index: int):

        if index == -1:
            self.target = None
            for wdgt in self._disableWidgetsWhenNoTarget:
                wdgt.setEnabled(False)
            return

        else:
            for wdgt in self._disableWidgetsWhenNoTarget:
                wdgt.setEnabled(True)

        targetKey = self._targetsModel.getCollectionItemKeyFromIndex(index)
        target = self._session.targets[targetKey]

        self.target = target

        if not target.isSelected:
            if False:
                # newly selected target in combobox is not selected in model,
                # so assume user doesn't want edit widget to track model selection
                self._doTrackModelSelectedTarget = False
            else:
                pass  # temporarily disabled for now due to issue with over-aggressive disabling
                # of tracking model's selected target (perhaps due to signal emit order?)


    def _onTargetItemChanged(self, targetKey: str, attribsChanged: list[str] | None = None):
        if attribsChanged is None or 'angle' in attribsChanged:
            self._handleAngleWdgt.value = self.target.angle

        if attribsChanged is None or 'depthOffset' in attribsChanged:
            self._depthOffsetWdgt.setValue(self.target.depthOffset)

    def _onHandleAngleChangedFromGUI(self, newAngle: float):
        logger.info(f'Handle angle changed to {newAngle} degrees')
        if self.target is not None:
            if self.target.angle != newAngle:
                logger.debug(f'Changing handle angle for {self.target.key} from {self.target.angle} to {newAngle}')
                self.target.angle = newAngle  # TODO: maybe check if angle (accounting for rounding) actually changed

    def _onDepthOffsetChangedFromGUI(self, newDepth: float):
        logger.info(f'Depth offset changed to {newDepth} mm')
        if self.target is not None:
            if round(self.target.depthOffset, 1) != round(newDepth, 1):
                logger.debug(f'Changing depth offset for {self.target.key} from {self.target.depthOffset} to {newDepth}')
                self.target.depthOffset = newDepth

