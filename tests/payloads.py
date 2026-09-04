"""Synthetic, privacy-safe fixtures for the shared test machinery.

Product-specific regression payloads live in ``test_parcels.py``; these mirror
a live Chronopost shipment — the product whose vocabulary covers the whole
lifecycle the shared tests need — without containing real codes or PII.
"""
from __future__ import annotations

ACTIVE_CODE = "EXAMPLE999999"
DELIVERED_CODE = "EXAMPLE123456"


def event(code: str, date: str, label: str) -> dict:
    """One entry of the carrier's own event timeline."""
    return {
        "code": code,
        "date": date,
        "label": label,
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A representative tracking response for a delivered parcel."""
    return {
        "inputIdShip": code,
        "idShip": code,
        "product": "chronopost",
        "isFinal": True,
        "deliveryDate": "2026-04-29T13:12:42Z",
        "currentState": {"code": "DI1", "shortLabel": "Delivered to the recipient"},
        "contextData": {"merchantName": "Example Shop"},
        "event": [
            event("DI1", "2026-04-29T13:12:42Z", "Delivered to the recipient"),
            event("MD1", "2026-04-29T08:46:00Z", "Out for delivery"),
            event("ET1", "2026-04-28T15:52:17Z", "At the sorting facility"),
            event("PC1", "2026-04-27T23:03:58Z", "Shipment announced"),
        ],
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An out-for-delivery parcel."""
    sample = delivered_sample(code)
    sample.update(
        {
            "isFinal": False,
            "deliveryDate": None,
            "currentState": {"code": "MD1", "shortLabel": "Out for delivery"},
            "event": sample["event"][1:],
        }
    )
    return sample


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel waiting at a pickup point."""
    sample = active_sample(code)
    sample.update(
        {
            "currentState": {"code": "AG1", "shortLabel": "Ready for collection"},
            "event": [
                event("AG1", "2026-04-29T09:30:00Z", "Ready for collection"),
                *sample["event"],
            ],
        }
    )
    return sample
