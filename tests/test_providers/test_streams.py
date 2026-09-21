"""Tests pour app/providers/intervals/streams.py::streams_to_dict (2026-09-21)."""
from app.providers.intervals.streams import streams_to_dict


def test_converts_list_of_type_data_objects_to_dict():
    raw = [
        {"type": "time", "data": [0, 1, 2]},
        {"type": "watts", "data": [100, 110, 120]},
    ]
    assert streams_to_dict(raw) == {"time": [0, 1, 2], "watts": [100, 110, 120]}


def test_empty_list_returns_empty_dict():
    assert streams_to_dict([]) == {}


def test_entries_missing_type_or_data_are_skipped():
    raw = [
        {"type": "watts", "data": [1, 2]},
        {"type": "incomplete"},
        {"data": [9, 9]},
    ]
    assert streams_to_dict(raw) == {"watts": [1, 2]}
