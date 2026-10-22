"""Optional read-only smoke test for a real Remocon-Net gateway.

Run only when the four REMOCON_* environment variables are set. This test
never calls a write method.
"""

import os

import pytest

from custom_components.elco_remocon.api import RemoconClient


REQUIRED_ENV = (
    "REMOCON_EMAIL",
    "REMOCON_PASSWORD",
    "REMOCON_GATEWAY",
)


@pytest.mark.skipif(
    not all(os.environ.get(name) for name in REQUIRED_ENV),
    reason="Set REMOCON_EMAIL, REMOCON_PASSWORD, and REMOCON_GATEWAY for live testing",
)
def test_live_get_data_is_read_only():
    """Authenticate and fetch current data without changing the system."""
    client = RemoconClient(
        email=os.environ["REMOCON_EMAIL"],
        password=os.environ["REMOCON_PASSWORD"],
        gateway_id=os.environ["REMOCON_GATEWAY"],
        zone=os.environ.get("REMOCON_ZONE", "1"),
    )

    data = client.get_data()

    assert data is not None
    assert client.is_flat_api in (True, False)
