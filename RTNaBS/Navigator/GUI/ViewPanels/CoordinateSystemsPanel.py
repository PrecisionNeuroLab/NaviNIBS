from __future__ import annotations

import asyncio

import appdirs
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
from RTNaBS.Navigator.Model.Session import Session, CoordinateSystems, CoordinateSystem
from RTNaBS.util import makeStrUnique
from RTNaBS.util.pyvista import setActorUserTransform
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import transformToString, stringToTransform, concatenateTransforms, invertTransform
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.util.GUI.QLineEdit import QLineEditWithValidationFeedback
from RTNaBS.util.GUI.QTableWidgetDragRows import QTableWidgetDragRows
from RTNaBS.util.GUI.QValidators import OptionalTransformValidator
from RTNaBS.util.pyvista.plotting import BackgroundPlotter

logger = logging.getLogger(__name__)


@attrs.define
class CoordinateSystemsPanel(MainViewPanel):
    _key: str = 'Set transforms'
    _icon: QtGui.QIcon = attrs.field(init=False, factory=lambda: qta.icon('mdi6.head-sync-outline'))

    # TODO