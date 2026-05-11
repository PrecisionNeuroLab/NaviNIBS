from __future__ import annotations

import logging
import typing as tp
from typing import ClassVar

import attrs

from NaviNIBS.Navigator.GUI.ViewPanels.NavigatePanel.ViewLayers import PlotViewLayer
from NaviNIBS.Navigator.GUI.ViewPanels.VisualizedROI import VisualizedROI, VisualizedROIsMesh, refreshROIAutoColors
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter

logger = logging.getLogger(__name__)


@attrs.define
class _SurfaceROIsViewAdapter:
    """
    Thin adapter providing the Surf3DView-shaped interface that VisualizedROIsMesh expects,
    but backed by a raw plotter instead of a Surf3DView widget.

    setSurfaceVisibility is a no-op here; polygon offset on the ROI mesh actor (set by
    SurfaceROIsLayer after mesh creation) ensures the ROI mesh renders in front of the
    underlying head mesh without needing to hide it.
    """
    _plotter: DefaultBackgroundPlotter
    _bgColors: dict[str, str]
    _bgOpacities: dict[str, float] 

    @property
    def plotter(self) -> DefaultBackgroundPlotter:
        return self._plotter

    def getSurfColorAndOpacity(self, surfKey: str) -> tuple[str, float]:
        return \
            self._bgColors.get(surfKey, '#d9a5b2'), \
                self._bgOpacities.get(surfKey, 0.0)

    def setSurfaceVisibility(self, surfKey: str, visible: bool) -> None:
        pass


@attrs.define(kw_only=True)
class SurfaceROIsLayer(PlotViewLayer):
    _type: ClassVar[str] = 'SurfaceROIs'

    _surfColors: dict[str | None, str] = attrs.field(factory=lambda: {
        'gmSurf': '#d9a5b2',
        'csfSurf': "#9e9a97",
        'skinSurf': '#c9c5c2',})
    """Background (non-ROI) vertex color; can match the HeadMeshSurfaceLayer color."""

    _surfOpacities: dict[str, float] = attrs.field(factory=lambda: {
        'gmSurf': 1.0,
        'csfSurf': 0.,
        'skinSurf': 0.,
    })

    _surfOpacity: float = 1.0
    """Background vertex opacity. 1.0 ensures non-ROI areas fully cover the head mesh."""

    _roiOpacity: float = 0.85
    """Alpha scaling for ROI-colored vertices, passed through to VisualizedROI."""

    _adapter: _SurfaceROIsViewAdapter = attrs.field(init=False)
    _visualizedROIs: dict[str, VisualizedROI] = attrs.field(init=False, factory=dict)
    _visualizedROIsMeshes: dict[str, VisualizedROIsMesh] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        self._adapter = _SurfaceROIsViewAdapter(
            plotter=self._plotter,
            bgColors=self._surfColors,
            bgOpacities=self._surfOpacities,
        )
        super().__attrs_post_init__()  # queues 'all' redraw

        self._coordinator.session.ROIs.sigItemsChanged.connect(self._onROIsChanged)

    def _onROIsChanged(self, changedKeys: list[str], changedAttrs: list[str] | None = None):
        if changedAttrs is None or any(k in changedAttrs for k in ('color', 'isVisible')):
            self._queueRedraw('ROIAutoColors')

        if changedAttrs is None or 'isVisible' in changedAttrs:
            self._queueRedraw(which='ROIs', changedROIKeys=changedKeys)

    def _redraw(self, which: str | list[str] | None = None, changedROIKeys: list[str] | None = None):  # type: ignore[override]
        
        super()._redraw(which=which)

        if not isinstance(which, str):
            # super() handled None→'all' conversion and list iteration
            return

        if which == 'all':
            self._redraw(which=['ROIAutoColors', 'ROIs'])
            return
        
        if which == 'ROIAutoColors':
            refreshROIAutoColors(self._coordinator.session)

        elif which == 'ROIs':
            logger.info(f'Redrawing SurfaceROIsLayer for ROIs changed: {changedROIKeys}')

            session = self._coordinator.session

            if changedROIKeys is None:
                keysToProcess = list(set(list(self._visualizedROIs.keys()) + list(session.ROIs.keys())))
            else:
                keysToProcess = changedROIKeys

            prevMeshKeys = set(self._visualizedROIsMeshes.keys())

            for key in keysToProcess:
                if key in self._visualizedROIs and key not in session.ROIs:
                    self._visualizedROIs.pop(key).clear()
                elif key not in self._visualizedROIs and key in session.ROIs:
                    if session.ROIs[key].isVisible:
                        self._visualizedROIs[key] = VisualizedROI(
                            roi=session.ROIs[key],
                            session=session,
                            linked3DView=self._adapter,
                            opacity=self._roiOpacity,
                            meshRegistry=self._visualizedROIsMeshes,
                        )

            # Apply polygon offset to any newly created shared mesh actors so they
            # render in front of the underlying head mesh and avoid z-fighting.
            newMeshKeys = set(self._visualizedROIsMeshes.keys()) - prevMeshKeys
            for meshKey in newMeshKeys:
                meshViz = self._visualizedROIsMeshes[meshKey]
                if meshViz._meshActor is not None:
                    mapper = meshViz._meshActor.GetMapper()
                    mapper.SetResolveCoincidentTopologyToPolygonOffset()
                    mapper.SetRelativeCoincidentTopologyPolygonOffsetParameters(0, -1)

        else:
            raise NotImplementedError(f'Unhandled redraw which={which!r}')

    def disable(self):
        if not self._isEnabled:
            return
        for vROI in list(self._visualizedROIs.values()):
            vROI.clear()
        self._visualizedROIs.clear()
        self._visualizedROIsMeshes.clear()
        super().disable()

    def enable(self):
        if self._isEnabled:
            return
        super().enable()
        self._queueRedraw('all')
