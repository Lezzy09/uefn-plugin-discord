"""Rich Presence: settings, activity text, and the IPC frame round trip."""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import pytest

from . import rich_presence as rp


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rp, "default_app_data_dir", lambda: tmp_path)
    yield tmp_path


def test_activity_defaults():
    a = rp.build_activity({}, duckies=3, version="1.2.416")
    assert a["details"] == "Building with Ducky"
    assert a["state"] == "🦆 3 duckies working"
    assert a["assets"] == {"large_image": rp.LARGE_IMAGE, "large_text": "UEFN Ducky 1.2.416"}
    assert a["buttons"] == rp.BUTTONS
    assert a["timestamps"]["start"] == rp._started_at
    assert rp.build_activity({}, duckies=0)["state"] == "💤 Duckies resting"
    assert rp.build_activity({}, duckies=1)["state"] == "🦆 1 ducky working"
    assert rp.build_activity({}, duckies=0, uefn_open=True)["details"] == "Editing in UEFN"


def test_activity_switches_off():
    a = rp.build_activity({"show_uefn": False, "show_duckies": False}, duckies=2, uefn_open=True)
    assert a["details"] == "Building with Ducky"
    assert "state" not in a


def test_activity_has_no_private_info(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rp, "_running_duckies", lambda: 1)
    monkeypatch.setattr(rp, "_uefn_open", lambda: True)
    text = json.dumps(rp.build_activity({}, **rp._current_facts({})))
    assert "RvB" not in text and "project" not in text.lower()


def test_on_by_default(isolated):
    assert rp.public_settings()["enabled"] is True
    assert rp.save_settings({"enabled": False})["enabled"] is False


def test_save_keeps_only_switches(isolated):
    st = rp.save_settings({"enabled": True, "show_duckies": 0, "app_id": "123", "show_project": True})
    assert st["enabled"] and st["show_duckies"] is False and st["show_uefn"] is True
    saved = rp._read_cfg()
    assert "app_id" not in saved and "show_project" not in saved


def _frame(op: int, payload: dict) -> bytes:
    body = json.dumps(payload).encode()
    return struct.pack("<II", op, len(body)) + body


class _FakePipe(io.RawIOBase):
    def __init__(self, replies: bytes) -> None:
        self._in = io.BytesIO(replies)
        self.sent = b""

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        return self._in.read(n)

    def write(self, b) -> int:
        self.sent += bytes(b)
        return len(b)


def _sent_frames(raw: bytes) -> list[tuple[int, dict]]:
    out = []
    while raw:
        op, n = struct.unpack("<II", raw[:8])
        out.append((op, json.loads(raw[8 : 8 + n])))
        raw = raw[8 + n :]
    return out


def test_ipc_handshake_and_set_activity(monkeypatch: pytest.MonkeyPatch):
    ready = {"cmd": "DISPATCH", "evt": "READY", "data": {"user": {"username": "zheibr"}}}
    pipe = _FakePipe(_frame(1, ready) + _frame(1, {"cmd": "SET_ACTIVITY", "data": {}}))
    monkeypatch.setattr(rp, "_open_pipe", lambda n: pipe)
    ipc = rp._Ipc()
    assert ipc.connect("42")["data"]["user"]["username"] == "zheibr"
    ipc.set_activity({"details": "x"})
    frames = _sent_frames(pipe.sent)
    assert frames[0] == (0, {"v": 1, "client_id": "42"})
    assert frames[1][0] == 1 and frames[1][1]["cmd"] == "SET_ACTIVITY"
    assert frames[1][1]["args"]["activity"] == {"details": "x"}


def test_ipc_rejected_app_id(monkeypatch: pytest.MonkeyPatch):
    pipe = _FakePipe(_frame(2, {"code": 4000, "message": "Invalid Client ID"}))
    monkeypatch.setattr(rp, "_open_pipe", lambda n: pipe)
    with pytest.raises(ConnectionError, match="Invalid Client ID"):
        rp._Ipc().connect("1")
