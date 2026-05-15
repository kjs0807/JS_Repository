"""Back-compat alias for the BbkcDemoBroker → BbkcBroker rename (Stage A).

The legacy class name has been renamed because the broker itself is
mode-agnostic (demo *or* live). Existing imports must keep working via the
``BbkcDemoBroker = BbkcBroker`` alias defined at the bottom of
``src/execution/bbkc_demo_broker.py``.
"""
from __future__ import annotations


def test_new_name_is_importable():
    from src.execution.bbkc_demo_broker import BbkcBroker
    assert BbkcBroker.__name__ == "BbkcBroker"


def test_legacy_alias_is_same_class_object():
    from src.execution.bbkc_demo_broker import BbkcBroker, BbkcDemoBroker
    assert BbkcDemoBroker is BbkcBroker


def test_module_all_exposes_both_names():
    import src.execution.bbkc_demo_broker as mod
    assert "BbkcBroker" in mod.__all__
    assert "BbkcDemoBroker" in mod.__all__


def test_legacy_import_path_still_resolves():
    """Confirm a legacy `from src.execution.bbkc_demo_broker import BbkcDemoBroker`
    still works for old consumers (e.g. ``tests/test_execution/test_live_broker_helpers.py``)."""
    from src.execution.bbkc_demo_broker import BbkcDemoBroker
    # subclass relationship: still a LiveBroker.
    from src.execution.live_broker import LiveBroker
    assert issubclass(BbkcDemoBroker, LiveBroker)
