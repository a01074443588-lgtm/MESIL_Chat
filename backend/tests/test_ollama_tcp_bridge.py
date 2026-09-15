from __future__ import annotations

import importlib.util
import socket
import socketserver
import time
from pathlib import Path
from threading import Thread
from types import ModuleType

import pytest


def _load_bridge_module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "deploy" / "ollama_tcp_bridge.py"
    spec = importlib.util.spec_from_file_location("ollama_tcp_bridge_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Ollama bridge test module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SlowUpstreamHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while self.request.recv(65536):
            pass
        time.sleep(0.15)
        self.request.sendall(b"delayed-response")


class _ThreadingTestServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _start(server: socketserver.BaseServer) -> Thread:
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    return thread


def test_bridge_does_not_reuse_connect_timeout_for_slow_ollama_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _load_bridge_module()
    original_create_connection = socket.create_connection

    with (
        _ThreadingTestServer(("127.0.0.1", 0), _SlowUpstreamHandler) as upstream,
        bridge.ThreadingTcpBridge(("127.0.0.1", 0), bridge.OllamaBridgeHandler) as proxy,
    ):
        proxy.bridge_config = bridge.BridgeConfig(
            bind_tcp=proxy.server_address, bind_unix=None,
            target_tcp=upstream.server_address, target_unix=None,
        )
        upstream_thread = _start(upstream)
        proxy_thread = _start(proxy)

        def connect_with_short_timeout(
            address: tuple[str, int],
            timeout: float | None = None,
        ) -> socket.socket:
            del timeout
            return original_create_connection(address, timeout=0.05)

        monkeypatch.setattr(
            bridge.socket, "create_connection", connect_with_short_timeout
        )

        try:
            with original_create_connection(proxy.server_address, timeout=1) as client:
                client.settimeout(1)
                client.sendall(b"request")
                client.shutdown(socket.SHUT_WR)
                assert client.recv(65536) == b"delayed-response"
        finally:
            proxy.shutdown()
            upstream.shutdown()
            proxy_thread.join(timeout=1)
            upstream_thread.join(timeout=1)
