"""Tests for the La Poste API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.laposte.api import (
    LaPosteApiClient,
    LaPosteApiError,
)

CODE = "EW175528686FR"


def _session_returning(status: int, body: object = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_parcel_on_success():
    session = _session_returning(
        200, [{"returnCode": 200, "inputIdShip": CODE, "shipment": {"idShip": CODE}}]
    )
    client = LaPosteApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["idShip"] == CODE
    assert parcel["inputIdShip"] == CODE
    # the tracking code ends up in the URL
    assert CODE in session.get.call_args[0][0]
    assert session.get.call_args.kwargs["headers"]["User-Agent"] == "PostmanRuntime/7.49.1"


async def test_get_parcel_returns_none_when_not_found(caplog):
    """An unknown or not-yet-scanned code is a normal state, not an error."""
    client = LaPosteApiClient(
        _session_returning(200, [])
    )
    for _ in range(2):
        assert await client.async_get_parcel("EXAMPLE000000") is None
    assert caplog.text.count("empty JSON array") == 1
    assert "issues/new" in caplog.text


async def test_get_parcel_raises_on_hollow_success():
    """A successful envelope without a shipment is malformed."""
    client = LaPosteApiClient(
        _session_returning(200, [{"returnCode": 200, "shipment": None}])
    )
    with pytest.raises(LaPosteApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_error_status():
    client = LaPosteApiClient(_session_returning(500, {}))
    with pytest.raises(LaPosteApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_unparseable_body():
    client = LaPosteApiClient(_session_returning(200, "not json"))
    with pytest.raises(LaPosteApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_array_body(caplog):
    client = LaPosteApiClient(_session_returning(200, {"returnCode": 200}))
    for _ in range(2):
        with pytest.raises(LaPosteApiError):
            await client.async_get_parcel(CODE)
    assert caplog.text.count("not a JSON array") == 1


async def test_get_parcel_returns_none_on_non_success_return_code(caplog):
    client = LaPosteApiClient(
        _session_returning(200, [{"returnCode": 404, "returnMessage": "unknown"}])
    )
    for _ in range(2):
        assert await client.async_get_parcel(CODE) is None
    assert caplog.text.count("non-200 returnCode") == 1
    assert "returnMessage=unknown" in caplog.text


async def test_get_parcel_raises_on_multi_item_array(caplog):
    client = LaPosteApiClient(_session_returning(200, [{}, {}]))
    for _ in range(2):
        with pytest.raises(LaPosteApiError):
            await client.async_get_parcel(CODE)
    assert caplog.text.count("not a one-element JSON array") == 1


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = LaPosteApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
