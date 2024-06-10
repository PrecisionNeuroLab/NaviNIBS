from __future__ import annotations

import asyncio

import attrs
from datetime import datetime
import json
import logging
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
from NaviNIBS.Navigator.Model.Session import Session, CoordinateSystems, CoordinateSystem
from NaviNIBS.util import makeStrUnique
from NaviNIBS.util.pyvista import setActorUserTransform
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import transformToString, stringToTransform, concatenateTransforms, invertTransform
from NaviNIBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from NaviNIBS.util.GUI.QLineEdit import QLineEditWithValidationFeedback
from NaviNIBS.util.GUI.QTableWidgetDragRows import QTableWidgetDragRows
from NaviNIBS.util.GUI.QValidators import OptionalTransformValidator
from NaviNIBS.util.pyvista.plotting import BackgroundPlotter

logger = logging.getLogger(__name__)


@attrs.define
class CoordinateSystemsPanel(MainViewPanel):
    _key: str = 'Set transforms'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.head-sync-outline'))

    # TODO