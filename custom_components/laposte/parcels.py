"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

La Poste uses distinct Colissimo and Chronopost vocabularies, selected from
``shipment.product``. The rest is suite-wide machinery.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-laposte/issues/new"
    "?template=unrecognised_status.yml"
)

_COLISSIMO_MAP: dict[str, ParcelStatus] = {
    "EXPANN": ParcelStatus.REGISTERED,
    "EDRDEP": ParcelStatus.IN_TRANSIT, "EDRINT": ParcelStatus.IN_TRANSIT,
    "ACHNAT": ParcelStatus.IN_TRANSIT, "ACHORI": ParcelStatus.IN_TRANSIT,
    "ACHERI": ParcelStatus.IN_TRANSIT, "ACHDOU": ParcelStatus.IN_TRANSIT,
    "DISINTAS": ParcelStatus.IN_TRANSIT, "DISARR": ParcelStatus.IN_TRANSIT,
    "DISTOU": ParcelStatus.IN_TRANSIT, "DISIRST": ParcelStatus.IN_TRANSIT,
    "AARIDOU": ParcelStatus.IN_TRANSIT, "AARENDDOU": ParcelStatus.IN_TRANSIT,
    "AARIREF": ParcelStatus.IN_TRANSIT,
    "ACHIECA": ParcelStatus.PROBLEM, "AARAREF": ParcelStatus.PROBLEM,
    "AARABECH": ParcelStatus.PROBLEM, "DISIECHEC": ParcelStatus.PROBLEM,
    "DESLIVD": ParcelStatus.DELIVERED, "DESOBS": ParcelStatus.DELIVERED,
}
_CHRONOPOST_MAP: dict[str, ParcelStatus] = {
    "PC1": ParcelStatus.IN_TRANSIT, "ET1": ParcelStatus.IN_TRANSIT,
    "EP1": ParcelStatus.IN_TRANSIT, "MD2": ParcelStatus.IN_TRANSIT,
    "DR1": ParcelStatus.IN_TRANSIT, "MD1": ParcelStatus.OUT_FOR_DELIVERY,
    "AG1": ParcelStatus.AT_PICKUP_POINT, "RE1": ParcelStatus.RETURNING,
    "DI1": ParcelStatus.DELIVERED,
}
# Keys already warned about, so each unconfirmed shape is logged only once
# per HA session instead of on every poll.
_warned: set[str] = set()


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key in _warned:
        return
    _warned.add(key)
    _LOGGER.warning(message, *args)


def _warn_unmapped_status(code: str, product: str = "unknown") -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    _warn_once(
        f"status:{product}:{code}",
        "Unrecognised La Poste status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s product=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
        product,
    )


def _warn_is_final_not_delivered(status_code: str | None, product: str) -> None:
    """Warn once when ``isFinal: true`` doesn't map to ``delivered``.

    The terminal non-delivery outcome (destroyed, returned-and-closed) nobody
    has seen yet.
    """
    _warn_once(
        "is-final-not-delivered",
        "La Poste marked a shipment isFinal without a delivered status — "
        "open an issue and paste this line: %s\n  status=%s product=%s",
        NEW_ISSUE_URL,
        status_code,
        product,
    )


def _warn_unexpected_estim_date(product: str, is_final: bool) -> None:
    """Warn once for an ``estimDate`` outside the only confirmed case.

    The only confirmed case is a delivered Colissimo parcel — a real ETA on
    Chronopost, or on a still-moving Colissimo parcel, would unlock
    ``planned_from``/``planned_to``.
    """
    _warn_once(
        "unexpected-estim-date",
        "La Poste returned an estimDate outside the confirmed case — open "
        "an issue and paste this line: %s\n  product=%s isFinal=%s",
        NEW_ISSUE_URL,
        product,
        is_final,
    )


def _warn_empty_colissimo_status(raw_code: Any) -> None:
    """Warn once when a Colissimo currentState.code is empty/missing."""
    _warn_once(
        "empty-colissimo-status",
        "La Poste Colissimo currentState.code was empty or missing — open "
        "an issue and paste this line: %s\n  code=%r",
        NEW_ISSUE_URL,
        raw_code,
    )


def _warn_is_parcel_back() -> None:
    """Warn once for a populated ``contextData.isParcelBack``.

    The unconfirmed Colissimo ``returning`` signal.
    """
    _warn_once(
        "is-parcel-back",
        "La Poste reported contextData.isParcelBack — open an issue and "
        "paste this line: %s",
        NEW_ISSUE_URL,
    )


def _status_map(product: str | None) -> dict[str, ParcelStatus]:
    if product == "colissimo":
        return _COLISSIMO_MAP
    if product == "chronopost":
        return _CHRONOPOST_MAP
    # An unknown product has no vocabulary of its own: every code falls through
    # to the one-shot unmapped warning rather than a guessed meaning.
    return {}


def map_parcel_status(code: str | None, product: str | None = None) -> ParcelStatus:
    """Map a carrier status code to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.
    """
    if not code:
        return ParcelStatus.UNKNOWN
    mapped = _status_map(product).get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code, product or "unknown")
    return ParcelStatus.UNKNOWN


def map_event_status(code: str | None, product: str | None = None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, reusing the parcel-status one-shot set.
    """
    if not code:
        return None
    mapped = _status_map(product).get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code, product or "unknown")
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def build_history(
    events: list | None, *, product: str | None = None, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is the carrier's own text, or
    its event code when the API has no human-readable text. Sorted oldest →
    newest and capped to the most recent ``max_events``.

    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("date") or event.get("timestamp"))
        if not timestamp:
            continue
        event_code = (
            event.get("group") if product == "colissimo" else event.get("code")
        ) or event.get("statusCode")
        entry = {
            "timestamp": timestamp,
            "status": map_event_status(event_code, product),
            "raw_status": event.get("label") or event.get("description") or event_code,
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def carrier_from_product(product: str | None) -> str:
    """Name the actual carrier brand rather than the shared backend."""
    return {"colissimo": "Colissimo", "chronopost": "Chronopost"}.get(
        (product or "").lower(), "La Poste"
    )


def _newest_event(events: list[dict]) -> dict:
    """Events arrive newest-first; retain wire order when dates tie."""
    return max(
        events,
        key=lambda event: parse_iso(event.get("date"))
        or datetime.min.replace(tzinfo=timezone.utc),
        default={},
    )


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    ``raw`` is the ``shipment`` object with ``inputIdShip`` injected by the API
    client, so Chronopost's canonical id cannot replace the registered barcode.
    """
    product = str(raw.get("product") or "").lower()
    events = [event for event in raw.get("event") or [] if isinstance(event, dict)]
    newest = _newest_event(events)
    current_state = raw.get("currentState") or {}
    if product == "colissimo":
        raw_code = current_state.get("code")
        if not raw_code:
            _warn_empty_colissimo_status(raw_code)
        status_code = raw_code or newest.get("group")
    else:
        # RE1 remains current while its physical return legs add ET1 events;
        # only a subsequent terminal delivery overrides that inference.
        codes = [event.get("code") for event in events]
        status_code = (
            "RE1" if "RE1" in codes and "DI1" not in codes else newest.get("code")
        )
    status = map_parcel_status(status_code, product)
    delivered = status is ParcelStatus.DELIVERED
    context = raw.get("contextData") or {}
    tracking_code = raw.get("inputIdShip") or raw.get("idShip")

    is_final = bool(raw.get("isFinal"))
    if is_final and not delivered:
        _warn_is_final_not_delivered(status_code, product)
    estim_date = raw.get("estimDate")
    if estim_date and (product == "chronopost" or not is_final):
        _warn_unexpected_estim_date(product, is_final)
    if context.get("isParcelBack"):
        _warn_is_parcel_back()
    # Confirmed live: contextData.merchantName carries the sending merchant's
    # name (e.g. an online retailer) when the shipper set one.
    merchant_name = context.get("merchantName")

    return {
        "carrier": carrier_from_product(product),
        "barcode": tracking_code,
        "sender": merchant_name or None,
        "receiver": None,
        "status": status,
        "raw_status": current_state.get("shortLabel") or newest.get("label") or status_code,
        "delivered": delivered,
        "delivered_at": to_iso_timestamp(raw.get("deliveryDate")) if delivered else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": None,
        "url": tracking_url(tracking_code),
        "weight": None,
        "dimensions": None,
        "history": build_history(events, product=product) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
