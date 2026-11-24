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

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
from typing import Any
from zoneinfo import ZoneInfo

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

_ZONE = ZoneInfo("Europe/Paris")

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
    "EDRDEP": ParcelStatus.IN_TRANSIT,
    "EDRINT": ParcelStatus.IN_TRANSIT,
    "ACHNAT": ParcelStatus.IN_TRANSIT,
    "ACHORI": ParcelStatus.IN_TRANSIT,
    "ACHERI": ParcelStatus.IN_TRANSIT,
    "ACHDOU": ParcelStatus.IN_TRANSIT,
    "DISINTAS": ParcelStatus.IN_TRANSIT,
    "DISARR": ParcelStatus.IN_TRANSIT,
    "DISTOU": ParcelStatus.IN_TRANSIT,
    "DISIRST": ParcelStatus.IN_TRANSIT,
    "AARIDOU": ParcelStatus.IN_TRANSIT,
    "AARENDDOU": ParcelStatus.IN_TRANSIT,
    "AARIREF": ParcelStatus.IN_TRANSIT,
    "ACHIECA": ParcelStatus.PROBLEM,
    "AARAREF": ParcelStatus.PROBLEM,
    "AARABECH": ParcelStatus.PROBLEM,
    "DISIECHEC": ParcelStatus.PROBLEM,
    "DESLIVD": ParcelStatus.DELIVERED,
    "DESOBS": ParcelStatus.DELIVERED,
    # Confirmed live: collected from a relay/locker and return ready for the
    # sender respectively; DISINS means ready to be collected.
    "DESLIVHD": ParcelStatus.DELIVERED,
    "DESMDREXP": ParcelStatus.RETURNING,
    "DISINS": ParcelStatus.AT_PICKUP_POINT,
}
_CHRONOPOST_MAP: dict[str, ParcelStatus] = {
    "PC1": ParcelStatus.IN_TRANSIT,
    "ET1": ParcelStatus.IN_TRANSIT,
    "EP1": ParcelStatus.IN_TRANSIT,
    "MD2": ParcelStatus.IN_TRANSIT,
    "DR1": ParcelStatus.IN_TRANSIT,
    "MD1": ParcelStatus.OUT_FOR_DELIVERY,
    "AG1": ParcelStatus.AT_PICKUP_POINT,
    "RE1": ParcelStatus.RETURNING,
    "DI1": ParcelStatus.DELIVERED,
}
# Confirmed live: a tracked international letter ("lettre et PPI import
# suivi") reports the same event codes as Chronopost — MD1 when it is out for
# delivery, then DI1 with isFinal once it is in the letterbox.
_EVENT_CODE_PRODUCTS = frozenset({"chronopost", "lettre et ppi import suivi"})
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
    """Warn once for an ``estimDate`` outside the confirmed Colissimo cases.

    Confirmed on Colissimo: a delivered parcel, and a still-moving one whose
    ``estimHourMin``/``estimHourMax`` become ``planned_from``/``planned_to``.
    A real ETA on Chronopost would unlock the same window there.
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
    if product in _EVENT_CODE_PRODUCTS:
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


def map_event_status(
    code: str | None, product: str | None = None
) -> ParcelStatus | None:
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
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
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
            return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def estimated_slot_bound(hour: Any, estim_date: Any) -> str | None:
    """Return a slot bound on the day ``estimDate`` names.

    Seen live: ``estimHourMin``/``estimHourMax`` carry the right hours but can
    carry the day of the lookup, while ``estimDate`` holds the delivery day
    (at 01:00, so it is never a bound on its own).
    """
    bound = parse_iso(to_iso_timestamp(hour))
    day = parse_iso(to_iso_timestamp(estim_date))
    if bound is None:
        return None
    if day is not None:
        # The lookup day's offset is wrong when a clock change falls between
        # the lookup and the delivery day.
        bound = bound.replace(
            year=day.year, month=day.month, day=day.day, tzinfo=day.tzinfo
        )
    return bound.isoformat()


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
        "text": f"{round(length)} x {round(width)} x {round(height)} cm",
    }


def _event_code(event: dict, product: str | None) -> str | None:
    return (
        event.get("group") if product == "colissimo" else event.get("code")
    ) or event.get("statusCode")


def build_history(
    events: list | None,
    *,
    product: str | None = None,
    max_events: int = HISTORY_MAX_EVENTS,
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
        event_code = _event_code(event, product)
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
        key=lambda event: (
            parse_iso(event.get("date")) or datetime.min.replace(tzinfo=UTC)
        ),
        default={},
    )


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
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
    if estim_date and product == "chronopost":
        _warn_unexpected_estim_date(product, is_final)
    if context.get("isParcelBack"):
        _warn_is_parcel_back()
    # Confirmed live: contextData.merchantName carries the sending merchant's
    # name (e.g. an online retailer) when the shipper set one.
    merchant_name = context.get("merchantName")

    slot_start = estimated_slot_bound(raw.get("estimHourMin"), estim_date)
    slot_end = estimated_slot_bound(raw.get("estimHourMax"), estim_date)
    planned_from, planned_to = planned_for_status(
        status,
        *planned_window(slot_start, slot_end if slot_start else None, zone=_ZONE),
    )
    status_map = _status_map(product)
    # DESLIVHD is Colissimo's collected-from-relay/locker delivery code.
    collected_at_point = status_code == "DESLIVHD" or any(
        _event_code(event, product) == "DESLIVHD" for event in events
    )
    pickup = is_pickup(
        collected_at_point,
        status,
        (status_map.get(_event_code(event, product) or "") for event in events),
    )

    return {
        "carrier": carrier_from_product(product),
        "barcode": tracking_code,
        "sender": merchant_name or None,
        "receiver": None,
        "contents": None,
        "status": status,
        "raw_status": current_state.get("shortLabel")
        or newest.get("label")
        or status_code,
        "delivered": delivered,
        "delivered_at": to_iso_timestamp(raw.get("deliveryDate"))
        if delivered
        else None,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": pickup,
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
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
