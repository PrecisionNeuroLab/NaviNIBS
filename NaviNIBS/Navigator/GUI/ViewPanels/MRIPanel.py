from __future__ import annotations

import asyncio

import attrs
from datetime import datetime
import logging
import nibabel as nib
from nibabel.affines import apply_affine
import numpy as np
import os
import pathlib
import pyvista as pv
import pyvistaqt as pvqt
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from . import MainViewPanel
from NaviNIBS.Navigator.GUI.Widgets.MRIViews import MRISliceView, MRI3DView
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.GUI.Icons import getIcon
from NaviNIBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from NaviNIBS.util.GUI.QMouseWheelAdjustmentGuard import preventAnnoyingScrollBehaviour
from NaviNIBS.util.Signaler import Signal


logger = logging.getLogger(__name__)


@attrs.define()
class MRIPanel(MainViewPanel):
    _key: str = 'Set MRI'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: getIcon('mdi6.image'))
    _filepathWdgt: QFileSelectWidget = attrs.field(init=False)
    _views: tp.Dict[str, tp.Union[MRISliceView, MRI3DView]] = attrs.field(init=False, factory=dict)

    _climSpinboxWidgets: dict[str, QtWidgets.QDoubleSpinBox] = attrs.field(init=False, factory=dict)
    _climCheckboxWidgets: dict[str, QtWidgets.QCheckBox] = attrs.field(init=False, factory=dict)

    finishedAsyncInit: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def canBeEnabled(self) -> tuple[bool, str | None]:
        if self.session is None:
            return False, 'No session set'
        return True, None

    def _finishInitialization(self):
        # don't initialize computationally-demanding views until panel is activated (viewed)

        super()._finishInitialization()

        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        wdgt = QFileSelectWidget(browseMode='getOpenFilename',
                                 extFilters='Nifti (*.nii; *.nii.gz)')
        # TODO: set supported file formats to (.nii | .nii.gz) only
        self._wdgt.layout().addWidget(wdgt)
        self._filepathWdgt = wdgt

        containerWdgt = QtWidgets.QWidget()
        containerWdgt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        containerLayout = QtWidgets.QGridLayout()
        containerWdgt.setLayout(containerLayout)
        self._wdgt.layout().addWidget(containerWdgt)

        for iDim, dim in enumerate(('2D', '3D')):
            dimOuterContainer = QtWidgets.QWidget()
            dimOuterContainer.setLayout(QtWidgets.QVBoxLayout())
            containerWdgt.layout().addWidget(dimOuterContainer, iDim, 2)
            dimContainer = QtWidgets.QGroupBox(f'{dim} colorbar limits')
            dimContainer.setLayout(QtWidgets.QGridLayout())
            dimContainer.layout().setContentsMargins(0, 0, 0, 0)
            dimOuterContainer.layout().addWidget(dimContainer)
            dimOuterContainer.layout().addStretch()

            for iRow, minOrMax in enumerate(('Max', 'Min')):
                wdgt = QtWidgets.QLabel(f'{minOrMax}:')
                dimContainer.layout().addWidget(wdgt, iRow, 0)

                wdgt = QtWidgets.QDoubleSpinBox()
                preventAnnoyingScrollBehaviour(wdgt)
                wdgt.editingFinished.connect(
                    lambda *args, dim=dim, minOrMax=minOrMax:
                    self._onClimSpinboxChanged(dim, minOrMax))
                wdgt.setDecimals(1)
                wdgt.setMinimum(0)
                wdgt.setMaximum(float('inf'))
                wdgt.setMinimumWidth(100)
                dimContainer.layout().addWidget(wdgt, iRow, 1)
                self._climSpinboxWidgets[f'clim{dim}{minOrMax}'] = wdgt

                wdgt = QtWidgets.QLabel('Auto:')
                dimContainer.layout().addWidget(wdgt, iRow, 3)

                wdgt = QtWidgets.QCheckBox()
                wdgt.stateChanged.connect(
                    lambda *args, dim=dim, minOrMax=minOrMax:
                    self._onClimCheckboxChanged(dim, minOrMax))
                dimContainer.layout().addWidget(wdgt, iRow, 4)
                self._climCheckboxWidgets[f'clim{dim}{minOrMax}'] = wdgt

            self._updateClimWidgetsFromModel(dim)

        for iRow, iCol, dim in ((0, 1, 'x'), (0, 0, 'y'), (1, 0, 'z'), (1, 1, '3D')):
            if dim in ('x', 'y', 'z'):
                self._views[dim] = MRISliceView(normal=dim, doShowScalarBar=True)
            elif dim == '3D':
                self._views[dim] = MRI3DView(label=dim, doShowScalarBar=True)
            else:
                raise NotImplementedError()

            self._views[dim].sigSliceOriginChanged.connect(lambda key=dim: self._onSliceOriginChanged(sourceKey=key))

            containerLayout.addWidget(self._views[dim].wdgt, iRow, iCol)

        if self.session is not None:
            self._onPanelInitializedAndSessionSet()

    def _onSliceOriginChanged(self, sourceKey: str):
        for key, view in self._views.items():
            if key == sourceKey:
                continue
            view.sliceOrigin = self._views[sourceKey].sliceOrigin

    def _onSessionSet(self):
        super()._onSessionSet()

        if self._hasInitialized:
            self._onPanelInitializedAndSessionSet()

    def _onPanelInitializedAndSessionSet(self):
        self._updateFilepath()
        self._updateRelativeToPath()
        self._filepathWdgt.sigFilepathChanged.connect(self._onBrowsedNewFilepath)
        self.session.sigInfoChanged.connect(self._onSessionInfoChanged)
        self.session.MRI.sigFilepathChanged.connect(self._updateFilepath)
        self.session.MRI.sigClimChanged.connect(self._updateClimWidgetsFromModel)

        for key, view in self._views.items():
            view.session = self.session

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._finishInitialization_async))

    async def _finishInitialization_async(self):
        for view in self._views.values():
            await view.finishedAsyncInit.wait()

        self.finishedAsyncInit.set()

    def _updateFilepath(self):
        self._filepathWdgt.filepath = self.session.MRI.filepath

    def _onSessionInfoChanged(self, whatChanged: tp.Optional[list[str]] = None):
        if whatChanged is None or 'filepath' in whatChanged:
            self._updateRelativeToPath()

    def _updateRelativeToPath(self):
        self._filepathWdgt.showRelativeTo = os.path.dirname(self.session.filepath)
        self._filepathWdgt.showRelativePrefix = '<session>'

    def _onBrowsedNewFilepath(self, newFilepath: str):
        self.session.MRI.filepath = newFilepath

    def _onClimSpinboxChanged(self, dim: str, minOrMax: str):
        logger.debug(f'Clim spinbox changed for {dim}{minOrMax}')
        valKey = f'clim{dim}{minOrMax}'
        isChecked = self._climCheckboxWidgets[valKey].isChecked()
        if not isChecked:
            setattr(self.session.MRI, valKey, self._climSpinboxWidgets[valKey].value())

    def _onClimCheckboxChanged(self, dim: str, minOrMax: str):
        logger.debug(f'Clim checkbox changed for {dim}{minOrMax}')
        valKey = f'clim{dim}{minOrMax}'
        autovalKey = f'autoClim{dim}{minOrMax}'
        isChecked = self._climCheckboxWidgets[valKey].isChecked()
        self._climSpinboxWidgets[valKey].setEnabled(not isChecked)

        if isChecked:
            val = getattr(self.session.MRI, autovalKey)
            setattr(self.session.MRI, valKey, None)  # clear any previous manually set value
        else:
            val = getattr(self.session.MRI, valKey)
            if val is None:
                val = getattr(self.session.MRI, autovalKey)

        if val is None:
            # can't set value, assume we are initializing and will update again later
            return

        self._climSpinboxWidgets[valKey].setValue(val)

    def _updateClimWidgetsFromModel(self, dim: str):
        logger.debug(f'Updating clim widgets from model for {dim}')
        for minOrMax in ('Min', 'Max'):
            valKey = f'clim{dim}{minOrMax}'
            autovalKey = f'autoClim{dim}{minOrMax}'

            if self.session is None or self.session.MRI.filepath is None:
                self._climSpinboxWidgets[valKey].setEnabled(False)
                self._climCheckboxWidgets[valKey].setEnabled(False)
            else:
                self._climCheckboxWidgets[valKey].setEnabled(True)

                isChecked = getattr(self.session.MRI, valKey) is None
                self._climCheckboxWidgets[valKey].setChecked(isChecked)
                self._climSpinboxWidgets[valKey].setEnabled(not isChecked)

                if isChecked:
                    val = getattr(self.session.MRI, autovalKey)
                else:
                    val = getattr(self.session.MRI, valKey)
                    if val is None:
                        val = getattr(self.session.MRI, autovalKey)

                self._climSpinboxWidgets[valKey].setValue(val)

