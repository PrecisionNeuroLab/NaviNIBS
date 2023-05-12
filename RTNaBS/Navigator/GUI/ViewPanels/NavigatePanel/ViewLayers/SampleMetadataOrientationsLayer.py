from __future__ import annotations

import attrs
import logging
from math import nan, isnan
import typing as tp
from typing import ClassVar
import pyvista as pv

from RTNaBS.Navigator.GUI.ViewPanels.NavigatePanel.ViewLayers.OrientationsLayers import SampleOrientationsLayer, VisualizedOrientation


logger = logging.getLogger(__name__)


@attrs.define(kw_only=True)
class SampleMetadataOrientationsLayer(SampleOrientationsLayer):

    _type: ClassVar[str] = 'SampleMetadataOrientations'

    _metadataKey: str
    _colorbarLabel: str | None = None
    _metadataScaleFactor: float = 1.0

    _colorDepthIndicator: str | None = None
    _colorHandleIndicator: str | None = None
    _colorDepthIndicatorSelected: str | None = None
    _colorHandleIndicatorSelected: str | None = None

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _createVisualizedOrientationForSample(self, key: str) -> VisualizedOrientation:

        isSelected = self.orientations[key].isSelected

        metadataVal = nan
        if self._metadataKey in self._coordinator.session.samples[key].metadata:
            metadataVal = self._coordinator.session.samples[key].metadata[self._metadataKey]

        logger.debug(f'metadataVal: {metadataVal}')

        if isnan(metadataVal):
            color = 'gray'
        else:
            if self._colorbarLabel is None:
                colorbarLabel = self._metadataKey
            else:
                colorbarLabel = self._colorbarLabel
            color = (metadataVal * self._metadataScaleFactor, colorbarLabel)

        lineWidth = self._lineWidth
        if isSelected:
            lineWidth *= 1.5  # increase line width to highlight selected samples

        return VisualizedOrientation(
            orientation=self.orientations[key],
            plotter=self._plotter,
            colorHandleIndicator=color,
            colorDepthIndicator=color,
            opacity=self._opacity,
            lineWidth=lineWidth,
            style=self._style,
            actorKeyPrefix=self._getActorKey(key)
        )






