"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime

import pytest

from custom_components.laposte.const import ParcelStatus
from custom_components.laposte.parcels import normalize_parcel, parse_iso

from .payloads import active_sample, delivered_sample, event, pickup_sample

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def _returning() -> dict:
    sample = active_sample()
    sample["currentState"] = {"code": "RE1", "shortLabel": "Returning"}
    sample["event"] = [
        event("RE1", "2026-04-29T09:30:00Z", "Returning"),
        *sample["event"],
    ]
    return sample


def _collected() -> dict:
    sample = pickup_sample()
    sample.update(
        {
            "isFinal": True,
            "deliveryDate": "2026-04-29T16:00:00Z",
            "currentState": {"code": "DI1", "shortLabel": "Collected"},
        }
    )
    sample["event"] = [
        event("DI1", "2026-04-29T16:00:00Z", "Collected"),
        *sample["event"],
    ]
    return sample


def _colissimo(**extra) -> dict:
    sample = {
        "inputIdShip": "EXAMPLE000002",
        "product": "colissimo",
        "isFinal": False,
        "currentState": {"code": "DISTOU", "shortLabel": "In transit"},
        "event": [{"date": "2026-08-06T10:00:00+02:00", "group": "ACHNAT"}],
        "estimDate": "2026-08-07T01:00:00+02:00",
    }
    sample.update(extra)
    return sample


def _moment_only() -> dict:
    return _colissimo(estimHourMin="2026-08-07T13:00:00+02:00")


def _window() -> dict:
    return _colissimo(
        estimHourMin="2026-08-07T08:00:00+02:00",
        estimHourMax="2026-08-07T12:00:00+02:00",
    )


def _max_only() -> dict:
    return _colissimo(estimHourMax="2026-08-07T12:00:00+02:00")


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "returning": _returning,
    "window": _window,
    "moment_only": _moment_only,
    "max_only": _max_only,
    "bare": lambda: {"inputIdShip": "EXAMPLE000001"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True


def test_a_pickup_route_is_found_without_the_history_option():
    parcel = normalize_parcel(_collected(), include_history=False)
    assert parcel["history"] is None
    assert parcel["pickup"] is True


def test_a_home_delivery_is_not_pickup():
    assert normalize_parcel(delivered_sample())["pickup"] is False


def test_a_return_is_active_and_empties_the_window():
    parcel = normalize_parcel(_returning())
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["planned_from"] is None


def test_a_moment_ends_at_the_end_of_that_day():
    parcel = normalize_parcel(_moment_only())
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == start.date()
    assert end.utcoffset() == start.utcoffset()


def test_a_lone_slot_end_is_no_window():
    parcel = normalize_parcel(_max_only())
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_a_window_keeps_both_ends():
    parcel = normalize_parcel(_window())
    assert parcel["planned_from"] == "2026-08-07T08:00:00+02:00"
    assert parcel["planned_to"] == "2026-08-07T12:00:00+02:00"
