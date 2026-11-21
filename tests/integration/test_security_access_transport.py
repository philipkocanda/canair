"""A synthetic SecurityAccess pipeline over a loopback ELM327 TCP adapter."""

import asyncio
import json

import pytest

from canair.live.read.multi import mode_multi
from canair.profile.model import Profile
from canair.safety.blocklist import SessionPolicy
from canair.transport.elm327.tcp import Elm327TcpTerminal
from canair.uds.protocol import UDS


@pytest.mark.asyncio
async def test_security_pipeline_over_loopback(tmp_path, capsys):
    observed = []
    replies = {"1003": "5003", "2701": "67011234", "2702A1B2": "6702", "2101": "6101ABCD"}

    async def adapter(reader, writer):
        try:
            while request := await reader.readuntil(b"\r"):
                text = request.decode("ascii").strip().upper()
                observed.append(text)
                reply = "OK" if text.startswith("AT") else replies.get(text, "NO DATA")
                writer.write(f"{reply}\r>".encode())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(adapter, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    key_file = tmp_path / "key"
    key_file.write_bytes(bytes.fromhex("A1B2"))
    key_file.chmod(0o600)
    root = tmp_path / "profile"
    root.mkdir()
    (root / "ecus").mkdir()
    (root / "profile.yaml").write_text('car_model: "Test"\ninit: ""\n')
    profile = Profile("test", root)
    data = {"ecus": {"TEST": {"tx_id": 0x720, "pids": {"2101": {"parameters": {}}}}}}
    terminal = Elm327TcpTerminal("127.0.0.1", port, verbose=True)
    terminal.session_policy = SessionPolicy(protocols={0x720: UDS})
    try:
        await terminal.connect()
        await mode_multi(
            terminal,
            ["session TEST --mode 03", "unlock TEST --level 01", "query TEST:2101"],
            data,
            True,
            no_repl=True,
            save=True,
            label="synthetic",
            as_json=True,
            security_key_file=str(key_file),
            security_confirmed=True,
            profile=profile,
        )
    finally:
        await terminal.close()
        server.close()

    output = capsys.readouterr()
    assert "1234" not in output.out + output.err
    assert "A1B2" not in output.out + output.err
    assert json.loads(output.out)["results"][0]["security_access"] == "accepted"
    assert observed.count("2701") == observed.count("2702A1B2") == 1
    assert "2101" in observed
    saved = "".join(path.read_text() for path in profile.captures_dir.rglob("*.json"))
    assert "6101ABCD" in saved and "1234" not in saved and "A1B2" not in saved
