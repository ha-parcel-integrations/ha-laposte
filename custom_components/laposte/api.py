"""La Poste public tracking API client.

The coordinator contract is:

* ``async_get_parcel`` returns the raw per-parcel dict on success,
* returns ``None`` when the carrier says the tracking code is unknown or not
  yet scanned (a normal, expected state — never an error),
* raises :class:`LaPosteApiError` for anything else, with ``status_code`` set
  on a non-2xx response and ``retry_after`` set when the carrier's own
  ``Retry-After`` header on a 429 could be parsed as seconds — the
  coordinator's backoff reads both,
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import TRACKING_API_URL, USER_AGENT

_LOGGER = logging.getLogger(__name__)

NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-laposte/issues/new"
    "?template=unrecognised_status.yml"
)

# Response shapes already warned about, so each unconfirmed shape is logged
# only once per HA session instead of on every poll.
_warned: set[str] = set()


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key in _warned:
        return
    _warned.add(key)
    _LOGGER.warning(message, *args)


class LaPosteApiError(Exception):
    """Raised when a La Poste API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"La Poste API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class LaPosteApiClient:
    """Client for the public La Poste tracking endpoint.

    No authentication: this consumer endpoint answers a one-element JSON array
    whose first item contains ``returnCode`` and ``shipment``.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the parcel dict for a known parcel, or ``None`` when the
        endpoint reports the code as unknown — which is also what a
        not-yet-scanned parcel gets. Any other failure envelope or non-2xx
        status raises :class:`LaPosteApiError`; network errors propagate
        as ``aiohttp.ClientError``.
        """
        url = TRACKING_API_URL.format(tracking_code=tracking_code)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with self._session.get(url, headers=headers) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_header) if retry_after_header else None
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
                raise LaPosteApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status != 200:
                raise LaPosteApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            try:
                # content_type=None: consumer endpoints routinely serve JSON as
                # text/plain, and aiohttp would otherwise refuse to parse it.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise LaPosteApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, list):
            _warn_once(
                "shape:not-a-list",
                "La Poste response body was not a JSON array — open an "
                "issue and paste this line: %s\n  type=%s",
                NEW_ISSUE_URL,
                type(payload).__name__,
            )
            raise LaPosteApiError("unexpected body (not a JSON array)")
        if not payload:
            _warn_once(
                "shape:empty-list",
                "La Poste response body was an empty JSON array — open an "
                "issue and paste this line: %s",
                NEW_ISSUE_URL,
            )
            return None
        if len(payload) != 1 or not isinstance(payload[0], dict):
            _warn_once(
                "shape:not-one-element",
                "La Poste response body was not a one-element JSON array — "
                "open an issue and paste this line: %s\n  length=%s",
                NEW_ISSUE_URL,
                len(payload),
            )
            raise LaPosteApiError("unexpected response array")

        envelope = payload[0]
        return_code = envelope.get("returnCode")
        if return_code != 200:
            _warn_once(
                f"return-code:{return_code}",
                "La Poste response carried a non-200 returnCode — open an "
                "issue and paste this line: %s\n  returnCode=%s "
                "returnMessage=%s",
                NEW_ISSUE_URL,
                return_code,
                envelope.get("returnMessage"),
            )
            return None
        shipment = envelope.get("shipment")
        if not isinstance(shipment, dict):
            raise LaPosteApiError("success returnCode without shipment")
        # Preserve the exact user-registered barcode, including the Chronopost
        # case where shipment.idShip gains a trailing check character.
        shipment = dict(shipment)
        shipment["inputIdShip"] = envelope.get("inputIdShip") or tracking_code
        return shipment
