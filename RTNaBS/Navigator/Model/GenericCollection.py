from __future__ import annotations
import attrs
from abc import ABC
from collections.abc import Sequence, Mapping, Iterable
import logging
import typing as tp

from RTNaBS.util.attrs import attrsAsDict
from RTNaBS.util.numpy import array_equalish
from RTNaBS.util.Signaler import Signal

logger = logging.getLogger(__name__)


K = tp.TypeVar('K', int, str)  # collection item key type


@attrs.define(slots=False)
class GenericCollectionDictItem(ABC, tp.Generic[K]):

    _key: K

    sigKeyAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((K, K)))  # includes old key, new key
    sigKeyChanged: Signal = attrs.field(init=False, factory=lambda: Signal((K, K)))  # includes old key, new key

    sigItemAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((K, tp.Optional[tp.List[str]])))
    """
    This signal includes the key of the item, and optionally a list of keys of attributes about to change;
    if second arg is None, all attributes should be assumed to be about to change.

    Not emitted when key changed (use sigKeyAboutToChange instead!)
    """
    sigItemChanged: Signal = attrs.field(init=False, factory=lambda: Signal((K, tp.Optional[list[str]])))
    """
    This signal includes the key of the item, and optionally a list of keys of changed attributes;  
    if second arg is None, all attributes should be assumed to have changed.

    Not emitted when key changed (use sigKeyChanged instead!)
    """

    def __attrs_post_init__(self):
        pass

    @property
    def key(self):
        return self._key

    @key.setter
    def key(self, newKey: K):
        if self._key == newKey:
            return
        prevKey = self._key
        self.sigKeyAboutToChange.emit(prevKey, newKey)
        self._key = newKey
        self.sigKeyChanged.emit(prevKey, newKey)

    def asDict(self) -> dict[str, tp.Any]:
        return attrsAsDict(self)

    @classmethod
    def fromDict(cls, d: dict[str, tp.Any]):
        return cls(**d)


CI = tp.TypeVar('CI', bound=GenericCollectionDictItem)  # collection item type


@attrs.define(slots=False)
class GenericCollection(ABC, tp.Generic[K, CI]): # (minor note: it would be helpful to specify CI[K] here but python syntax doesn't yet allow for this)
    """
    Base class to implement collection behavior and signaling for various session model components
    """

    _items: Mapping[K, CI] = attrs.field(factory=dict)

    sigItemsAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((list[K],)))
    """
    This signal includes list of keys of collection items about to change, and optionally a list of 
    keys of attributes about to change;  if second arg is None, all attributes should be assumed to
    be about to change.
    """

    sigItemsChanged: Signal = attrs.field(init=False, factory=lambda: Signal((list[K], tp.Optional[list[str]])))
    """
    This signal includes list of keys of changed collection items, and optionally a list of keys of
    changed attributes; if second arg is None, all attributes should be assumed to have changed.
    """

    sigItemKeyAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((K, K)))
    sigItemKeyChanged: Signal = attrs.field(init=False, factory=lambda: Signal((K, K)))
    """
    Emitted in addition to sigItemsAboutToChange and sigItemsChanged specifically when an item key changes.

    This is because when a key does change, everything else about a item may have changed, so these other signals don't include a list of attributes indicating the source of the change.
    """

    def __attrs_post_init__(self):
        for key, item in self._items.items():
            assert item.key == key
            item.sigItemAboutToChange.connect(self._onItemAboutToChange)
            item.sigItemChanged.connect(self._onItemChanged)
            item.sigKeyAboutToChange.connect(self._onItemKeyAboutToChange)
            item.sigKeyChanged.connect(self._onItemKeyChanged)

    def addItem(self, item: CI):
        assert item.key not in self._items
        return self.setItem(item=item)

    def deleteItem(self, key: str):
        assert key in self._items
        self.sigItemsAboutToChange.emit([key], None)

        self._items[key].sigItemAboutToChange.disconnect(self._onItemAboutToChange)
        self._items[key].sigKeyAboutToChange.disconnect(self._onItemKeyAboutToChange)
        self._items[key].sigKeyChanged.disconnect(self._onItemKeyChanged)
        self._items[key].sigItemChanged.disconnect(self._onItemChanged)

        del self._items[key]

        self.sigItemsChanged.emit([key], None)

    def setItem(self, item: CI):
        self.sigItemsAboutToChange.emit([item.key], None)
        if item.key in self._items:
            self._items[item.key].sigItemAboutToChange.disconnect(self._onItemAboutToChange)
            self._items[item.key].sigKeyAboutToChange.disconnect(self._onItemKeyAboutToChange)
            self._items[item.key].sigKeyChanged.disconnect(self._onItemKeyChanged)
            self._items[item.key].sigItemChanged.disconnect(self._onItemChanged)
        self._items[item.key] = item

        item.sigItemAboutToChange.connect(self._onItemAboutToChange)
        item.sigKeyAboutToChange.connect(self._onItemKeyAboutToChange)
        item.sigKeyChanged.connect(self._onItemKeyChanged)
        item.sigItemChanged.connect(self._onItemChanged)

        self.sigItemsChanged.emit([item.key], None)

    def setItems(self, items: list[CI]):
        # assume all keys are changing, though we could do comparisons to find subset changed
        # note that this can also be used for reordering items
        oldKeys = list(self.keys())
        newKeys = [item.key for item in items]
        combinedKeys = list(set(oldKeys) | set(newKeys))
        self.sigItemsAboutToChange.emit(combinedKeys, None)
        for key in oldKeys:
            self._items[key].sigItemAboutToChange.disconnect(self._onItemAboutToChange)
            self._items[key].sigKeyAboutToChange.disconnect(self._onItemKeyAboutToChange)
            self._items[key].sigKeyChanged.disconnect(self._onItemKeyChanged)
            self._items[key].sigItemChanged.disconnect(self._onItemChanged)
        self._items = {item.key: item for item in items}
        for key, item in self._items.items():
            self._items[key].sigItemAboutToChange.connect(self._onItemAboutToChange)
            self._items[key].sigKeyAboutToChange.connect(self._onItemKeyAboutToChange)
            self._items[key].sigKeyChanged.connect(self._onItemKeyChanged)
            self._items[key].sigItemChanged.connect(self._onItemChanged)
        self.sigItemsChanged.emit(combinedKeys, None)

    def setAttribForItems(self, keys: Sequence[K], attribsAndValues: dict[str, Sequence[tp.Any]]) -> None:
        """
        Change an attribute for multiple items at once, without signaling separately for each item.
        E.g. for changing visibility of multiple targets simultaneously.

        :param keys: Identifying keys of items to change
        :param attribsAndValues: mapping of attribute -> list of new values, where this list is of the same length as specified keys.
        :return: None
        """
        assert 'key' not in attribsAndValues, 'Should change key separately, not via setAttribForItems'
        for attrib, values in attribsAndValues.items():
            assert len(values) == len(keys)

        changingKeys = list()
        for iKey, key in enumerate(keys):
            for attrib, values in attribsAndValues.items():
                assert len(values) == len(keys)
                if not array_equalish(getattr(self._items[key], attrib), values[iKey]):
                    changingKeys.append(key)
                    break

        if len(changingKeys) == 0:
            return

        self.sigItemsAboutToChange.emit(changingKeys, list(attribsAndValues.keys()))
        with self.sigItemsAboutToChange.blocked(), self.sigItemsChanged.blocked():
            for iKey, key in enumerate(changingKeys):
                for attrib, values in attribsAndValues.items():
                    setattr(self._items[key], attrib, values[iKey])
        self.sigItemsChanged.emit(changingKeys, list(attribsAndValues.keys()))

    def _onItemAboutToChange(self, key: str, attribKeys: tp.Optional[list[str]] = None):
        self.sigItemsAboutToChange.emit([key], attribKeys)

    def _onItemKeyAboutToChange(self, fromKey: str, toKey: str):
        assert toKey not in self._items
        self.sigItemKeyAboutToChange.emit(fromKey, toKey)
        self.sigItemsAboutToChange.emit([fromKey, toKey], None)

    def _onItemKeyChanged(self, fromKey: str, toKey: str):
        assert toKey not in self._items
        self._items = {(toKey if key == fromKey else key): val for key, val in self._items.items()}
        self.sigItemsChanged.emit([fromKey, toKey], None)
        self.sigItemKeyChanged.emit(fromKey, toKey)

    def _onItemChanged(self, key: str, attribKeys: tp.Optional[list[str]] = None):
        self.sigItemsChanged.emit([key], attribKeys)

    def __getitem__(self, key):
        return self._items[key]

    def __setitem__(self, key, item: CI):
        assert key == item.key
        self.setItem(item=item)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def keys(self):
        return self._items.keys()

    def items(self):
        return self._items.items()

    def get(self, *args, **kwargs):
        return self._items.get(*args, **kwargs)

    def values(self):
        return self._items.values()

    def merge(self: C, otherItems: C):
        self.sigItemsAboutToChange.emit(list(otherItems.keys()), None)

        with self.sigItemsAboutToChange.blocked(), self.sigItemsChanged.blocked():
            for item in otherItems.values():
                self.setItem(item)

        self.sigItemsChanged.emit(list(otherItems.keys()), None)

    def asList(self) -> list[dict[str, tp.Any]]:
        return [item.asDict() for item in self._items.values()]

    @classmethod
    def fromList(cls: tp.Type[C], itemList: list[dict[K, tp.Any]]) -> C:
        raise NotImplementedError('Should be implemented by subclass')



C = tp.TypeVar('C', bound=GenericCollection)  # collection type
