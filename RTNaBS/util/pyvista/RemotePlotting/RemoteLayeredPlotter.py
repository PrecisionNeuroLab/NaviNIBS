import typing as tp
from typing import ClassVar

import attrs
from RTNaBS.util.pyvista.plotting import PrimaryLayeredPlotter, SecondaryLayeredPlotter
from RTNaBS.util.pyvista.RemotePlotting import RemotePlotter as RP


class RemoteSecondaryLayeredPlotter(SecondaryLayeredPlotter, RP.RemotePlotterMixin):
    def __init__(self, *args, **kwargs):
        SecondaryLayeredPlotter.__init__(self, *args, **kwargs)
        RP.RemotePlotterMixin.__init__(self)


class RemotePrimaryLayeredPlotter(PrimaryLayeredPlotter, RP.RemotePlotterMixin):

    _createSecondaryPlotter: tp.Callable[..., RemoteSecondaryLayeredPlotter] = RemoteSecondaryLayeredPlotter
    """
    This allows creator to specify a different function to instantiate secondary plotters.
    First two arguments will be key and layer, and the rest will kwargs for the plotter.
    """

    def __init__(self, *args, **kwargs):

        self._createSecondaryPlotter = kwargs.pop('createSecondaryPlotter')  # required kwarg

        PrimaryLayeredPlotter.__init__(self, *args, **kwargs)
        RP.RemotePlotterMixin.__init__(self)

    def addLayeredPlotter(self, key: str, layer: tp.Optional[int] = None) -> int:
        """
        Note: this returns int layer rather than the plotter itself, unlike superclass
        """
        assert key not in self._secondaryPlotters
        self._secondaryPlotters[key] = self._createSecondaryPlotter(
            key, layer, mainPlotter=self, rendererLayer=layer)
        return self._secondaryPlotters[key].rendererLayer


@attrs.define
class RemoteSecondaryLayeredPlotManager(RP.RemotePlotManagerBase):
    _Plotter: ClassVar = RemoteSecondaryLayeredPlotter
    _plotter: SecondaryLayeredPlotter

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class RemotePrimaryLayeredPlotManager(RP.RemotePlotManager):

    _Plotter: ClassVar = RemotePrimaryLayeredPlotter
    _plotter: PrimaryLayeredPlotter | None = attrs.field(init=False, default=None)
    _secondaryPlotManagers: dict[str, RemoteSecondaryLayeredPlotManager] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        self._plotterKwargs['createSecondaryPlotter'] = self._createSecondaryPlot

        super().__attrs_post_init__()

    def _createSecondaryPlot(self, key: str, layer: int | None, **kwargs) -> SecondaryLayeredPlotter:
        self._secondaryPlotManagers[key] = RemoteSecondaryLayeredPlotManager(
            plotter=RemoteSecondaryLayeredPlotter(**kwargs),
            parentLayout=self._parentLayout,
        )
        return self._secondaryPlotManagers[key].plotter

    async def _handleMsg(self, msg):
        # handle first argument that represents primary/secondary plotter key
        plotterKey = msg[0]

        if plotterKey == '__primary':
            return await super()._handleMsg(msg[1:])
        else:
            return await self._secondaryPlotManagers[plotterKey]._handleMsg(msg[1:])


@attrs.define
class RemoteLayeredPlotterApp(RP.RemotePlotterApp):
    _plotManager: RemotePrimaryLayeredPlotManager = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _initPlotManager(self):
        assert self._plotManager is None
        self._plotManager = RemotePrimaryLayeredPlotManager(
            reqPort=self._reqPort,
            repPort=self._repPort,
            parentLayout=self._rootWdgt.layout(),
            plotterKwargs=self._plotterKwargs,
        )