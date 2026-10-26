"""netutil is the one set of TCP helpers for the CLI, launcher and file browser."""

from __future__ import annotations

import socket
import threading

import pytest

from vibeview import netutil


def _listening_socket() -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    return sock, sock.getsockname()[1]


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("0.0.0.0", "127.0.0.1"),
        ("::", "127.0.0.1"),
        ("", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("10.0.0.5", "10.0.0.5"),
    ],
)
def test_probe_host_maps_the_wildcard_to_loopback(host: str, expected: str) -> None:
    assert netutil.probe_host(host) == expected
    assert netutil.viewer_url(host, 8080) == f"http://{expected}:8080"


def test_port_open_reflects_a_listener() -> None:
    sock, port = _listening_socket()
    try:
        assert netutil.port_open("127.0.0.1", port) is True
        assert netutil.port_open("0.0.0.0", port) is True
    finally:
        sock.close()
    assert netutil.port_open("127.0.0.1", port) is False


def test_wait_for_port_returns_when_bound_and_false_on_timeout() -> None:
    sock, port = _listening_socket()
    try:
        assert netutil.wait_for_port("127.0.0.1", port, timeout=1.0) is True
    finally:
        sock.close()
    assert netutil.wait_for_port("127.0.0.1", port, timeout=0.3, interval=0.05) is False


def test_announce_when_bound_reports_the_url_once_listening(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(netutil.webbrowser, "open", lambda url: opened.append(url))
    announced: list[str] = []
    warned: list[str] = []
    sock, port = _listening_socket()
    try:
        thread = netutil.announce_when_bound(
            "0.0.0.0", port, open_browser=True, announce=announced.append, warn=warned.append
        )
        thread.join(timeout=5.0)
    finally:
        sock.close()
    assert thread.daemon and not thread.is_alive()
    assert announced == [f"http://127.0.0.1:{port}"]
    assert opened == [f"http://127.0.0.1:{port}"]
    assert warned == []


def test_announce_when_bound_warns_instead_of_announcing_on_timeout(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(netutil.webbrowser, "open", lambda url: opened.append(url))
    announced: list[str] = []
    warned: list[str] = []
    sock, port = _listening_socket()
    sock.close()
    thread = netutil.announce_when_bound(
        "127.0.0.1",
        port,
        open_browser=True,
        announce=announced.append,
        warn=warned.append,
        timeout=0.3,
    )
    thread.join(timeout=5.0)
    assert announced == [] and opened == []
    assert len(warned) == 1 and "did not bind within 0s" in warned[0]


def test_find_free_port_skips_a_busy_port() -> None:
    sock, port = _listening_socket()
    try:
        found = netutil.find_free_port(port)
        assert found > port
        assert netutil.port_open("127.0.0.1", found) is False
    finally:
        sock.close()
    with pytest.raises(OSError, match="No free port"):
        netutil.find_free_port(port, span=0)


def test_no_module_keeps_a_private_copy() -> None:
    from vibeview import cli, launcher, serve

    for module, names in (
        (cli, ("_wait_until_bound_and_announce", "_port_in_use")),
        (launcher, ("_wait_until_bound_and_open",)),
        (serve, ("_port_responding", "_find_free_port")),
    ):
        for name in names:
            assert not hasattr(module, name), (module.__name__, name)
    assert isinstance(threading.current_thread(), threading.Thread)
