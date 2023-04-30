import typing as tp
import attr
import contextlib

#  Note: once variadic generics are supported (see https://www.python.org/dev/peps/pep-0646/)
#   we'll be able to type hint this more usefully


@attr.s(auto_attribs=True, eq=False)
class Signal:
    _types: tuple[tp.Type, ...] = attr.ib(default=tuple())
    _connections: dict[int, set[tp.Callable[..., None]]] = attr.ib(init=False, factory=dict)
    """
    Connections groupded by priority
    """
    _blockedSemaphoreCounter: int = 0

    def __attrs_post_init__(self):
        pass

    def connect(self, fn: tp.Callable[[], None], priority: int = 0):
        """
        Connections with higher priority are called first.
        Connections with same priority are called in undetermined order.
        """
        if priority not in self._connections:
            self._connections[priority] = set()

        self._connections[priority].add(fn)

    def disconnect(self, fn: tp.Callable[[], None]):
        for connectionSet in self._connections.values():
            connectionSet.remove(fn)

    @property
    def isBlocked(self):
        return self._blockedSemaphoreCounter > 0

    def emit(self, *args, **kwargs) -> None:
        if self._blockedSemaphoreCounter > 0:
            return
        priorities = sorted(self._connections.keys(), reverse=True)
        for priority in priorities:
            connectionSet = self._connections[priority].copy()
            for fn in connectionSet:
                if fn in self._connections[priority]:
                    fn(*args, **kwargs)

    @contextlib.contextmanager
    def blocked(self):
        """
        Can be used like:
                sig = Signal()
                sig.emit()  # will emit
                with sig.blocked():
                    sig.emit()  # will do nothing
                sig.emit()  # will emit
        """
        self._blockedSemaphoreCounter += 1
        try:
            yield None
        finally:
            self._blockedSemaphoreCounter -= 1

    @contextlib.contextmanager
    def connected(self, fn: tp.Callable[[], None]):
        self.connect(fn)
        try:
            yield None
        finally:
            self.disconnect(fn)

    @contextlib.contextmanager
    def disconnected(self, fn: tp.Callable[[], None]):
        assert any(fn in connectionSet for connectionSet in self._connections.values())
        self.disconnect(fn)
        try:
            yield None
        finally:
            self.connect(fn)