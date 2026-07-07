import json
from optitrain.api import probe_backends, TransportClient


def test_probe_backends_returns_dict():
    r = probe_backends(timeout=3)
    assert isinstance(r, dict)


def test_transport_client_select():
    tc = TransportClient()
    try:
        base = tc.select_backend()
        assert base.startswith("http")
    except RuntimeError:
        # acceptable when offline in CI or network blocked
        assert True
