import typing as tp
import attr
import contextlib

#  Note: once variadic generics are supported (see https://www.python.org/dev/peps/pep-0646/)
#   we'll be able to type hint this more usefully

ET = tp.TypeVarTuple('ET')
# Connection = tp.Callable[..., None]
Connection = tp.Callable[[*ET], None]


@attr.s(auto_attribs=True, eq=False)
class Signal(tp.Generic[*ET]):
    _types: tuple[*ET] = attr.ib(default=tuple())
    """
    Legacy, from before support for variadic generics was added.
    If documenting type with typing like Signal[T1, T2, T3], then don't specify a value for this attribute.
    """

    # _connections: dict[int, set[Connection]] = attr.ib(init=False, factory=dict, repr=False)
    _connections: dict[int, set[tp.Callable[[*ET], None]]] = attr.ib(init=False, factory=dict, repr=False)
    """
    Connections grouped by priority
    """

    _blockedSemaphoreCounter: int = attr.ib(init=False, default=0, repr=False)

    def __attrs_post_init__(self):
        pass

    def connect(self, fn: Connection, priority: int = 0):
        """
        Connections with higher priority are called first.
        Connections with same priority are called in undetermined order.
        """
        if priority not in self._connections:
            self._connections[priority] = set()

        self._connections[priority].add(fn)

    def disconnect(self, fn: Connection) -> int:
        removedAtPriority = None
        for priority, connectionSet in self._connections.items():
            try:
                connectionSet.remove(fn)
            except KeyError:
                pass
            else:
                removedAtPriority = priority
                break
        if removedAtPriority is None:
            raise ValueError(f'Function {fn} not connected to signal')
        return removedAtPriority

    @property
    def isBlocked(self):
        return self._blockedSemaphoreCounter > 0

    # def emit(self, *args, **kwargs) -> None:
    def emit(self, *args: *ET, **kwargs) -> None:
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
    def connected(self, fn: Connection):
        self.connect(fn)
        try:
            yield None
        finally:
            self.disconnect(fn)

    @contextlib.contextmanager
    def disconnected(self, fn: Connection):
        assert any(fn in connectionSet for connectionSet in self._connections.values())
        priority = self.disconnect(fn)
        try:
            yield None
        finally:
            self.connect(fn, priority=priority)
