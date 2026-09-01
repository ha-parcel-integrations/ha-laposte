"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.laposte.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.laposte.parcels import (
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import active_sample, delivered_sample, event, pickup_sample

# ---------------------------------------------------------------------------
# map_parcel_status / map_event_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("REGISTERED", ParcelStatus.REGISTERED),
        ("IN_TRANSIT", ParcelStatus.IN_TRANSIT),
        ("OUT_FOR_DELIVERY", ParcelStatus.OUT_FOR_DELIVERY),
        ("AT_PICKUP_POINT", ParcelStatus.AT_PICKUP_POINT),
        ("DELIVERED", ParcelStatus.DELIVERED),
        ("RETURN_TO_SENDER", ParcelStatus.RETURNING),
        ("EXCEPTION", ParcelStatus.PROBLEM),
    ],
)
def test_map_parcel_status_known(code, expected):
    assert map_parcel_status(code) == expected


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    assert map_parcel_status("TELEPORTED") == ParcelStatus.UNKNOWN


def test_map_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("SOMETHING_NEW") is None
    assert map_event_status("DELIVERED") == ParcelStatus.DELIVERED


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status("ABDUCTED") == ParcelStatus.UNKNOWN
    assert map_parcel_status("ABDUCTED") == ParcelStatus.UNKNOWN
    assert caplog.text.count("ABDUCTED") == 1
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    history = build_history(delivered_sample()["events"])
    assert len(history) == 4
    assert history[0]["raw_status"] == "Shipment announced"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_caps_to_max_events():
    events = [
        event("IN_TRANSIT", f"2026-04-{day:02d}T10:00:00Z", "moved")
        for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"statusCode": "IN_TRANSIT"}]) == []  # no timestamp
    assert build_history(["not-a-dict"]) == []


def test_build_history_keeps_unparseable_timestamp_last():
    history = build_history(
        [
            event("REGISTERED", "2026-04-24T10:00:00Z", "fine"),
            event("IN_TRANSIT", "not-a-date", "odd"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["fine", "odd"]


def test_build_history_falls_back_to_status_code_without_text():
    history = build_history([event("IN_TRANSIT", "2026-04-24T10:00:00Z", "")])
    assert history[0]["raw_status"] == "IN_TRANSIT"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

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


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample.

    Copy this test into a real carrier's own test_parcels.py verbatim — it
    stays correct for whatever subset of CAPABILITIES that carrier declares.
    """
    delivered = normalize_parcel(delivered_sample())
    active = normalize_parcel(active_sample())
    pickup = normalize_parcel(pickup_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "dimensions" in CAPABILITIES:
        assert delivered["dimensions"] is not None
    if "delivery_window" in CAPABILITIES:
        assert active["planned_from"] is not None or active["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert pickup["pickup_point"] is not None
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "La Poste"
    assert parcel["barcode"] == "EXAMPLE123456"
    assert parcel["sender"] == "Example Shop"
    assert parcel["receiver"] == "Jane Doe"
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Delivered to the recipient"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-29T13:12:42Z"
    # A delivered parcel drops its ETA — the window is meaningless once it has
    # arrived.
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["url"] == "https://www.laposte.fr/outils/suivre-vos-envois?code=EXAMPLE123456"
    assert parcel["weight"] == 1.25
    assert parcel["dimensions"]["text"] == "30 x 20 x 10 cm"
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 4
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_active_parcel_has_window():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["planned_from"] == "2026-04-29T13:00:00Z"
    assert parcel["planned_to"] == "2026-04-29T15:00:00Z"


def test_normalize_collapses_point_estimate_to_no_window_end():
    raw = active_sample()
    raw["estimatedDelivery"]["to"] = raw["estimatedDelivery"]["from"]
    parcel = normalize_parcel(raw)
    assert parcel["planned_from"] == "2026-04-29T13:00:00Z"
    assert parcel["planned_to"] is None


def test_normalize_pickup_parcel():
    parcel = normalize_parcel(pickup_sample())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Point Central Station"


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"trackingNumber": "EXAMPLE000001"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None


def test_normalize_blank_fields_become_none():
    raw = active_sample()
    raw["sender"] = ""
    raw["recipient"] = ""
    parcel = normalize_parcel(raw)
    assert parcel["sender"] is None
    assert parcel["receiver"] is None


def test_normalize_keeps_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw)["raw"] is raw


def test_normalize_falls_back_to_status_code_without_text():
    raw = active_sample()
    raw["statusText"] = None
    assert normalize_parcel(raw)["raw_status"] == "OUT_FOR_DELIVERY"


def test_colissimo_uses_current_state_not_newest_event():
    raw = {
        "inputIdShip": "EW000000000FR", "product": "colissimo",
        "currentState": {"code": "DISIECHEC", "shortLabel": "Échec"},
        "event": [
            {"date": "2026-08-06T10:00:00+02:00", "group": "ACHNAT", "code": "ET1", "label": "Transit"},
            {"date": "2026-04-20T10:00:00+02:00", "group": "DISIECHEC", "code": "MD3", "label": "Échec"},
        ],
    }
    parcel = normalize_parcel(raw, include_history=True)
    assert parcel["carrier"] == "Colissimo"
    assert parcel["status"] == ParcelStatus.PROBLEM
    assert parcel["history"][-1]["status"] == ParcelStatus.IN_TRANSIT


def test_colissimo_empty_current_state_code_warns_once(caplog):
    raw = {
        "inputIdShip": "EW000000000FR", "product": "colissimo",
        "currentState": {"code": ""},
        "event": [
            {"date": "2026-08-06T10:00:00+02:00", "group": "ACHNAT", "code": "ET1", "label": "Transit"},
        ],
    }
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.IN_TRANSIT  # falls back to newest group
    normalize_parcel(raw)
    assert caplog.text.count("currentState.code was empty or missing") == 1


def test_is_final_not_delivered_warns_once(caplog):
    raw = {
        "inputIdShip": "EW000000000FR", "product": "colissimo", "isFinal": True,
        "currentState": {"code": "ACHNAT"},
        "event": [{"date": "2026-08-06T10:00:00+02:00", "group": "ACHNAT", "code": "ET1"}],
    }
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert caplog.text.count("isFinal without a delivered status") == 1


def test_is_final_delivered_does_not_warn(caplog):
    raw = {
        "inputIdShip": "EW000000000FR", "product": "colissimo", "isFinal": True,
        "currentState": {"code": "DESLIVD"},
        "event": [{"date": "2026-08-06T10:00:00+02:00", "group": "DESLIVD", "code": "DI1"}],
    }
    normalize_parcel(raw)
    assert "isFinal without a delivered status" not in caplog.text


def test_unexpected_estim_date_warns_once_across_shapes(caplog):
    """Both Chronopost-any and Colissimo-not-final trigger the same one-shot
    warning; it fires only for the first shape seen in a session."""
    chronopost_raw = {
        "inputIdShip": "13199341485349", "product": "chronopost", "estimDate": "2026-08-10T10:00:00Z",
        "currentState": {}, "event": [{"date": "2026-08-06T10:00:00+02:00", "code": "PC1"}],
    }
    colissimo_raw = {
        "inputIdShip": "EW000000000FR", "product": "colissimo", "estimDate": "2026-08-10T10:00:00Z",
        "isFinal": False, "currentState": {"code": "ACHNAT"},
        "event": [{"date": "2026-08-06T10:00:00+02:00", "group": "ACHNAT", "code": "ET1"}],
    }
    normalize_parcel(chronopost_raw)
    normalize_parcel(chronopost_raw)
    normalize_parcel(colissimo_raw)
    assert caplog.text.count("estimDate outside the confirmed case") == 1


def test_estim_date_on_delivered_colissimo_does_not_warn(caplog):
    raw = {
        "inputIdShip": "EW000000000FR", "product": "colissimo", "estimDate": "2026-08-10T10:00:00Z",
        "isFinal": True, "currentState": {"code": "DESLIVD"},
        "event": [{"date": "2026-08-06T10:00:00+02:00", "group": "DESLIVD", "code": "DI1"}],
    }
    normalize_parcel(raw)
    assert "estimDate outside the confirmed case" not in caplog.text


def test_is_parcel_back_warns_once(caplog):
    raw = {
        "inputIdShip": "EW000000000FR", "product": "colissimo",
        "currentState": {"code": "ACHNAT"}, "contextData": {"isParcelBack": True},
        "event": [{"date": "2026-08-06T10:00:00+02:00", "group": "ACHNAT", "code": "ET1"}],
    }
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert caplog.text.count("isParcelBack") == 1


def test_merchant_name_populates_sender_without_warning(caplog):
    raw = {
        "inputIdShip": "EW000000000FR", "product": "colissimo",
        "currentState": {"code": "ACHNAT"}, "contextData": {"merchantName": "A Real Shop"},
        "event": [{"date": "2026-08-06T10:00:00+02:00", "group": "ACHNAT", "code": "ET1"}],
    }
    parcel = normalize_parcel(raw)
    assert parcel["sender"] == "A Real Shop"
    assert "merchantName" not in caplog.text  # confirmed live, no longer a one-shot warning


def test_chronopost_return_is_sticky_and_barcode_keeps_input_code():
    raw = {
        "inputIdShip": "13199341485349", "trackingNumber": "13199341485349",
        "idShip": "13199341485349X",
        "product": "chronopost", "currentState": {"shortLabel": "Retour"},
        "event": [
            {"date": "2026-08-06T10:00:00+02:00", "code": "ET1", "label": "Transit"},
            {"date": "2026-08-05T10:00:00+02:00", "code": "RE1", "label": "Retour expéditeur"},
        ],
    }
    parcel = normalize_parcel(raw)
    assert parcel["carrier"] == "Chronopost"
    assert parcel["barcode"] == "13199341485349"
    assert parcel["status"] == ParcelStatus.RETURNING


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
