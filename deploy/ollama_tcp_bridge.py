from __future__ import annotations

import os
import socket
import socketserver
import stat
from pathlib import Path, PurePosixPath
from threading import Thread
from typing import Mapping


def _optional(mapping: Mapping[str, str], name: str) -> str | None:
    value = mapping.get(name, "").strip()
    return value or None


def _unix_path(value: str, label: str) -> str:
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be an absolute path without parent traversal")
    return path.as_posix()


class BridgeConfig:
    def __init__(
        self,
        *,
        bind_tcp: tuple[str, int] | None,
        bind_unix: str | None,
        target_tcp: tuple[str, int] | None,
        target_unix: str | None,
    ) -> None:
        self.bind_tcp = bind_tcp
        self.bind_unix = bind_unix
        self.target_tcp = target_tcp
        self.target_unix = target_unix

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "BridgeConfig":
        bind_unix = _optional(mapping, "OLLAMA_BRIDGE_BIND_UNIX")
        explicit_bind_tcp = any(
            _optional(mapping, name)
            for name in ("OLLAMA_BRIDGE_BIND_HOST", "OLLAMA_BRIDGE_BIND_PORT")
        )
        if bind_unix and explicit_bind_tcp:
            raise ValueError("configure exactly one bind endpoint: TCP or Unix socket")
        if bind_unix:
            bind_unix = _unix_path(bind_unix, "bind Unix socket")
            bind_tcp = None
        else:
            bind_tcp = (
                mapping.get("OLLAMA_BRIDGE_BIND_HOST", "127.0.0.1"),
                int(mapping.get("OLLAMA_BRIDGE_BIND_PORT", "11434")),
            )

        target_unix = _optional(mapping, "OLLAMA_BRIDGE_TARGET_UNIX")
        explicit_target_tcp = any(
            _optional(mapping, name)
            for name in ("OLLAMA_BRIDGE_TARGET_HOST", "OLLAMA_BRIDGE_TARGET_PORT")
        )
        if target_unix and explicit_target_tcp:
            raise ValueError("configure exactly one target endpoint: TCP or Unix socket")
        if target_unix:
            target_unix = _unix_path(target_unix, "target Unix socket")
            target_tcp = None
        else:
            target_tcp = (
                mapping.get("OLLAMA_BRIDGE_TARGET_HOST", "127.0.0.1"),
                int(mapping.get("OLLAMA_BRIDGE_TARGET_PORT", "11435")),
            )
        return cls(
            bind_tcp=bind_tcp,
            bind_unix=bind_unix,
            target_tcp=target_tcp,
            target_unix=target_unix,
        )


class OllamaBridgeHandler(socketserver.BaseRequestHandler):
    @staticmethod
    def _copy(source: socket.socket, target: socket.socket) -> None:
        try:
            while True:
                chunk = source.recv(65536)
                if not chunk:
                    return
                target.sendall(chunk)
        except OSError:
            return
        finally:
            try:
                target.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def _open_upstream(self) -> socket.socket:
        config = self.server.bridge_config  # type: ignore[attr-defined]
        if config.target_unix:
            upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            upstream.settimeout(5)
            upstream.connect(config.target_unix)
            return upstream
        return socket.create_connection(config.target_tcp, timeout=5)

    def handle(self) -> None:
        try:
            upstream = self._open_upstream()
        except OSError:
            return
        upstream.settimeout(None)
        try:
            client_to_upstream = Thread(
                target=self._copy,
                args=(self.request, upstream),
                daemon=True,
            )
            client_to_upstream.start()
            self._copy(upstream, self.request)
            client_to_upstream.join(timeout=1)
        finally:
            for current in (self.request, upstream):
                try:
                    current.close()
                except OSError:
                    pass


class ThreadingTcpBridge(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if hasattr(socketserver, "ThreadingUnixStreamServer"):
    class ThreadingUnixBridge(socketserver.ThreadingUnixStreamServer):  # type: ignore[attr-defined]
        daemon_threads = True
else:
    class ThreadingUnixBridge:  # pragma: no cover - Unix sockets run in Linux containers.
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("Unix-domain bridge servers require a Unix-like runtime")


def _remove_stale_socket(path: Path) -> None:
    if not path.exists():
        return
    if not stat.S_ISSOCK(path.stat().st_mode):
        raise RuntimeError(f"refusing to replace non-socket path: {path}")
    path.unlink()


def serve(config: BridgeConfig) -> None:
    socket_path: Path | None = None
    if config.bind_unix:
        socket_path = Path(config.bind_unix)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        _remove_stale_socket(socket_path)
        server = ThreadingUnixBridge(config.bind_unix, OllamaBridgeHandler)
        os.chmod(socket_path, 0o660)
    else:
        server = ThreadingTcpBridge(config.bind_tcp, OllamaBridgeHandler)
    server.bridge_config = config  # type: ignore[attr-defined]
    try:
        with server:
            server.serve_forever(poll_interval=0.5)
    finally:
        if socket_path is not None and socket_path.exists():
            _remove_stale_socket(socket_path)


if __name__ == "__main__":
    serve(BridgeConfig.from_mapping(os.environ))
