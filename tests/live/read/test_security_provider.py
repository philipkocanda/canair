"""The provider is an untrusted subprocess boundary; test with synthetic material only."""

import asyncio
import json
import sys
import time

import pytest

from canair.live.read.security_provider import ProviderError, check_key_file, compute_key
from canair.uds.protocol import UDS


def provider(tmp_path, body):
    path = tmp_path / "provider"
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)
    return path


def invoke(path):
    return asyncio.run(
        compute_key(
            path, protocol=UDS, ecu="TEST", tx_id=0x720, level=1, seed=b"\x12\x34", max_key_bytes=6
        )
    )


def test_provider_gets_request_and_returns_synthetic_fixed_key(tmp_path):
    path = provider(
        tmp_path,
        "import json,sys\njson.load(sys.stdin)\nprint(json.dumps({'version':1,'key':'A1B2'}))\n",
    )
    assert invoke(path) == bytes.fromhex("A1B2")


def test_provider_does_not_inherit_unrelated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CANAIR_TEST_SECRET", "synthetic")
    path = provider(
        tmp_path,
        "import os\nassert 'CANAIR_TEST_SECRET' not in os.environ\n"
        'print(\'{"version":1,"key":"A1B2"}\')\n',
    )
    assert invoke(path) == bytes.fromhex("A1B2")


@pytest.mark.parametrize(
    "output",
    [
        "not json",
        '{"version":1,"version":1,"key":"A1B2"}',
        json.dumps({"version": 1, "key": "F"}),
        json.dumps({"version": 1, "key": "00", "extra": 2}),
    ],
)
def test_provider_rejects_invalid_output_without_exposing_it(tmp_path, output):
    path = provider(tmp_path, f"print({output!r})\n")
    with pytest.raises(ProviderError) as exc:
        invoke(path)
    assert output not in str(exc.value)


def test_provider_caps_stdout_and_never_echoes_stderr(tmp_path):
    path = provider(tmp_path, "import sys\nsys.stderr.write('sensitive')\nprint('x'*5000)\n")
    with pytest.raises(ProviderError) as exc:
        invoke(path)
    assert "sensitive" not in str(exc.value)


def test_failed_provider_does_not_expose_child_output(tmp_path):
    path = provider(
        tmp_path, "import sys\nprint('secret')\nsys.stderr.write('secret')\nsys.exit(1)\n"
    )
    with pytest.raises(ProviderError) as exc:
        invoke(path)
    assert "secret" not in str(exc.value)


def test_provider_timeout_terminates_without_disclosing_output(tmp_path, monkeypatch):
    path = provider(tmp_path, "import time\ntime.sleep(5)\n")
    monkeypatch.setattr("canair.live.read.security_provider.PROVIDER_TIMEOUT", 0.1)
    start = time.monotonic()
    with pytest.raises(ProviderError, match="timed out"):
        invoke(path)
    assert time.monotonic() - start < 2


def test_provider_key_length_need_not_match_seed_length(tmp_path):
    path = provider(tmp_path, 'print(\'{"version": 1, "key": "A1"}\')\n')
    assert invoke(path) == b"\xa1"


def test_key_file_requires_owner_only_permissions(tmp_path):
    path = tmp_path / "key"
    path.write_bytes(b"\xa1\xb2")
    path.chmod(0o644)
    with pytest.raises(ProviderError):
        check_key_file(str(path))
    path.chmod(0o600)
    assert check_key_file(str(path)) == path
