import json
import numpy as np
from pytransform3d.transformations import check_transform
from qtpy import QtWidgets, QtCore, QtGui
import re
import typing as tp

from RTNaBS.util.numpy import array_equalish
from RTNaBS.util.Transforms import transformToString, stringToTransform


invalidRegex = re.compile(r'(\[\[\[)|([a-zA-Z]+)|(\d+ +[\d.]+)|(]]])|(,,)|(\.\.)|(] +\[)')


class TransformValidator(QtGui.QValidator):
    def validate(self, inputStr: str, pos: int) -> tp.Tuple[QtGui.QValidator.State, str, int]:
        try:
            stringToTransform(inputStr)
        except ValueError as e:
            if invalidRegex.search(inputStr):
                return self.State.Invalid, inputStr, pos
            else:
                # TODO: add detection other clearly invalid cases
                return self.State.Intermediate, inputStr, pos
        else:
            return self.State.Acceptable, inputStr, pos


class OptionalTransformValidator(TransformValidator):
    def validate(self, inputStr: str, pos: int) -> tp.Tuple[QtGui.QValidator.State, str, int]:
        if len(inputStr.strip()) == 0:
            return self.State.Acceptable, inputStr, pos
        else:
            return super().validate(inputStr=inputStr, pos=pos)


