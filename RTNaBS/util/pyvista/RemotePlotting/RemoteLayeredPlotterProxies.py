from __future__ import annotations

import multiprocessing as mp

import attrs

from RTNaBS.util.pyvista.RemotePlotting.RemotePlotterProxy import RemotePlotterProxy, RemotePlotterProxyBase
from RTNaBS.util.pyvista.RemotePlotting.RemoteLayeredPlotter import RemoteLayeredPlotterApp


class RemoteSecondaryLayeredPlotterProxy(RemotePlotterProxyBase):
    _mainPlotter: RemotePrimaryLayeredPlotterProxy
    _layerKey: str

    def __init__(self,
                 mainPlotter: RemotePrimaryLayeredPlotterProxy,
                 layerKey: str,
                 **kwargs):
        RemotePlotterProxyBase.__init__(self)
        self._layerKey = layerKey
        self._mainPlotter = mainPlotter

        self._isReady.set()  # no async init needed for secondary plotter

    async def _sendReqAndRecv_async(self, msg):
        return await self._mainPlotter._sendReqAndRecv_async(msg, layerKey=self._layerKey)

    def _sendReqAndRecv(self, msg):
        return self._mainPlotter._sendReqAndRecv(msg, layerKey=self._layerKey)


class RemotePrimaryLayeredPlotterProxy(RemotePlotterProxy):

    _secondaryPlotters: dict[str, RemoteSecondaryLayeredPlotterProxy]

    def __init__(self, **kwargs):
        RemotePlotterProxy.__init__(self, **kwargs)

        self._secondaryPlotters = dict()

    def _startRemoteProc(self, procKwargs, **kwargs):
        assert self.remoteProc is None
        self.remoteProc = mp.Process(target=RemoteLayeredPlotterApp.createAndRun,
                                     daemon=True,
                                     kwargs=procKwargs)
        self.remoteProc.start()

    async def _sendReqAndRecv_async(self, msg, layerKey: str | None = '__primary'):
        # add plotter layer key to msg
        msg = (layerKey, *msg)
        return await super()._sendReqAndRecv_async(msg)

    def _sendReqAndRecv(self, msg, layerKey: str | None = '__primary'):
        # add plotter layer key to msg
        msg = (layerKey, *msg)
        return super()._sendReqAndRecv(msg)

    def addLayeredPlotter(self, key: str, layer: int | None = None) -> RemoteSecondaryLayeredPlotterProxy:

        assert key not in self._secondaryPlotters
        # add as a remote plotter
        self._remotePlotterCall('addLayeredPlotter', key=key, layer=layer)
        # add as a proxy
        self._secondaryPlotters[key] = RemoteSecondaryLayeredPlotterProxy(
            mainPlotter=self,
            layerKey=key,
            rendererLayer=layer)

        return self._secondaryPlotters[key]


