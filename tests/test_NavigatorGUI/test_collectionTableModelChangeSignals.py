"""
Unit tests for the change-signal bracketing in CollectionTableModel (NaviNIBS/Navigator/GUI/CollectionModels/__init__.py).

These guard against the "Previous change still pending" wedge: a change cycle that never completes
(due to re-entrancy or an exception mid-change) must never leave the model permanently unable to
process further changes, and Qt begin/end brackets must stay balanced.

Uses a minimal in-memory collection + model rather than a full Session/GUI so the change machinery
can be exercised directly.
"""
import attrs
import logging
import pytest

from qtpy import QtCore

from NaviNIBS.Navigator.Model.GenericCollection import (
    GenericCollection,
    GenericCollectionDictItem,
    collectionDictItemAttrSetter,
)
from NaviNIBS.Navigator.GUI.CollectionModels import CollectionTableModel

logger = logging.getLogger(__name__)


@attrs.define(slots=False, eq=False)
class _DummyItem(GenericCollectionDictItem[str]):
    _label: str = ''

    @property
    def label(self) -> str:
        return self._label

    @label.setter
    @collectionDictItemAttrSetter()
    def label(self, value: str):
        pass


@attrs.define(slots=False)
class _DummyCollection(GenericCollection[str, _DummyItem]):
    @classmethod
    def fromList(cls, itemList):
        raise NotImplementedError


@attrs.define(slots=False, kw_only=True)
class _DummyModel(CollectionTableModel[str, _DummyCollection, _DummyItem]):
    _collection: _DummyCollection = attrs.field()

    def __attrs_post_init__(self):
        self._attrColumns = ['key', 'label']
        self._columns = ['key', 'label']
        self._editableColumns = ['label']
        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange, priority=-2)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged, priority=2)
        super().__attrs_post_init__()


class _SignalSpy:
    """Counts the QAbstractItemModel begin/end bracket signals so balance can be asserted."""
    def __init__(self, model: _DummyModel):
        self.layoutAboutToBeChanged = 0
        self.layoutChanged = 0
        self.rowsAboutToBeInserted = 0
        self.rowsInserted = 0
        self.dataChanged = []  # list of (topRow, bottomRow)

        model.layoutAboutToBeChanged.connect(self._onLayoutAboutToBeChanged)
        model.layoutChanged.connect(self._onLayoutChanged)
        model.rowsAboutToBeInserted.connect(self._onRowsAboutToBeInserted)
        model.rowsInserted.connect(self._onRowsInserted)
        model.dataChanged.connect(self._onDataChanged)

    def _onLayoutAboutToBeChanged(self, *args):
        self.layoutAboutToBeChanged += 1

    def _onLayoutChanged(self, *args):
        self.layoutChanged += 1

    def _onRowsAboutToBeInserted(self, *args):
        self.rowsAboutToBeInserted += 1

    def _onRowsInserted(self, *args):
        self.rowsInserted += 1

    def _onDataChanged(self, topLeft, bottomRight, *args):
        self.dataChanged.append((topLeft.row(), bottomRight.row()))


def _makeCollectionAndModel(keys=('A', 'B', 'C')):
    coll = _DummyCollection(items={k: _DummyItem(key=k, label=f'{k}-init') for k in keys})
    model = _DummyModel(session=None, collection=coll)
    return coll, model


def _assertNotWedged(model: _DummyModel):
    assert model._pendingChangeType is None
    assert model._changeDepth == 0


def _assertStillUsable(coll, model, spy):
    """A subsequent normal edit must still emit a dataChanged."""
    before = len(spy.dataChanged)
    coll['C'].label = 'after-test-edit'
    assert len(spy.dataChanged) == before + 1
    _assertNotWedged(model)


def test_reentrant_modifyExisting(qtbot):
    """A slot that mutates a different item mid-change coalesces into one dataChanged; not wedged."""
    coll, model = _makeCollectionAndModel()
    spy = _SignalSpy(model)

    state = {'done': False}

    def reentrantSlot(keys, attrKeys):
        if not state['done'] and 'A' in keys:
            state['done'] = True
            coll['B'].label = 'changed-by-reentry'

    # priority above the model's changed handler (2) so it runs while the A-change bracket is open
    coll.sigItemsChanged.connect(reentrantSlot, priority=5)

    coll['A'].label = 'new-A'

    assert state['done']
    _assertNotWedged(model)
    # one coalesced dataChanged covering both A (row 0) and B (row 1)
    assert len(spy.dataChanged) == 1
    top, bottom = spy.dataChanged[0]
    assert top == 0 and bottom == 1
    assert coll['B'].label == 'changed-by-reentry'

    _assertStillUsable(coll, model, spy)


def test_slot_raises_during_aboutToChange(qtbot):
    """If a slot raises during sigItemsAboutToChange, sigItemChanged still fires and resets state."""
    coll, model = _makeCollectionAndModel()
    spy = _SignalSpy(model)

    def raisingSlot(keys, attrKeys):
        raise RuntimeError('boom in about-to-change slot')

    # priority below the model's about handler (-2) so the model opens its bracket first
    coll.sigItemsAboutToChange.connect(raisingSlot, priority=-5)

    with pytest.raises(RuntimeError):
        coll['A'].label = 'new-A'

    # the try/finally in the attr-setter still emitted sigItemChanged -> model closed its bracket
    _assertNotWedged(model)

    coll.sigItemsAboutToChange.disconnect(raisingSlot)
    _assertStillUsable(coll, model, spy)


def test_nested_change_during_insertRows(qtbot):
    """A nested modify during an insert keeps begin/endInsertRows balanced and triggers a deferred refresh."""
    coll, model = _makeCollectionAndModel()
    spy = _SignalSpy(model)

    state = {'done': False}

    def reentrantDuringInsert(keys, attrKeys):
        if not state['done'] and 'D' in keys:
            state['done'] = True
            coll['A'].label = 'modified-during-insert'

    # after the model's about handler (-2), i.e. after beginInsertRows
    coll.sigItemsAboutToChange.connect(reentrantDuringInsert, priority=-5)

    coll.setItem(_DummyItem(key='D', label='D-init'))

    assert state['done']
    _assertNotWedged(model)
    # insert bracket stayed balanced
    assert spy.rowsAboutToBeInserted == 1
    assert spy.rowsInserted == 1
    # the nested modify couldn't fold into the insert bracket -> a deferred full refresh happened
    assert spy.layoutAboutToBeChanged == 1
    assert spy.layoutChanged == 1
    assert 'D' in coll
    assert coll['A'].label == 'modified-during-insert'

    _assertStillUsable(coll, model, spy)


def test_modifyExisting_upgraded_to_full_by_nested_insert(qtbot):
    """A nested insert during a modifyExisting upgrades the pending change to a single full layout change."""
    coll, model = _makeCollectionAndModel()
    spy = _SignalSpy(model)

    state = {'done': False}

    def reentrantSlot(keys, attrKeys):
        if not state['done'] and 'A' in keys:
            state['done'] = True
            coll.setItem(_DummyItem(key='E', label='E-init'))

    # run while the A modifyExisting bracket is open (before model's changed handler at 2)
    coll.sigItemsChanged.connect(reentrantSlot, priority=5)

    coll['A'].label = 'new-A'

    assert state['done']
    _assertNotWedged(model)
    # upgraded to full: exactly one layout change pair, and no insert bracket was opened
    assert spy.layoutAboutToBeChanged == 1
    assert spy.layoutChanged == 1
    assert spy.rowsAboutToBeInserted == 0
    assert spy.rowsInserted == 0
    assert 'E' in coll

    _assertStillUsable(coll, model, spy)


def test_exception_inside_modifyingColumns(qtbot):
    """An exception inside modifyingColumns still closes the layout bracket and resets pending state."""
    coll, model = _makeCollectionAndModel()
    spy = _SignalSpy(model)

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        with model.modifyingColumns():
            raise _Boom()

    assert model._pendingChangeType is None
    assert spy.layoutAboutToBeChanged == 1
    assert spy.layoutChanged == 1

    _assertStillUsable(coll, model, spy)


def test_failsafe_recovers_from_leaked_change(qtbot, caplog):
    """A change started but never completed (external caller failure) is reclaimed on the next event-loop turn."""
    coll, model = _makeCollectionAndModel()
    spy = _SignalSpy(model)

    # Simulate a non-standard caller emitting about-to-change directly and then failing before
    #  emitting the matching changed signal. keys=None -> inferred 'full' (opens layoutAboutToBeChanged).
    with caplog.at_level(logging.WARNING):
        coll.sigItemsAboutToChange.emit(None, None)
        # model bracket is now open and would stay open forever without the failsafe
        assert model._pendingChangeType == 'full'
        assert model._changeDepth == 1
        assert spy.layoutAboutToBeChanged == 1
        assert spy.layoutChanged == 0

        # let the event loop turn so the singleShot(0) failsafe fires
        qtbot.wait(50)

    _assertNotWedged(model)
    # failsafe emitted the balancing layoutChanged
    assert spy.layoutChanged == 1
    assert any('force-closing' in rec.getMessage() for rec in caplog.records)

    _assertStillUsable(coll, model, spy)
