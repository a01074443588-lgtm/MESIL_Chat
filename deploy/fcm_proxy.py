"""Opaque CONNECT relay restricted to Google's FCM endpoint. No payload logging."""
import ipaddress
import select
import socket
import socketserver
import threading
import time

DESTINATION = "fcm.googleapis.com:443"
SLOTS = threading.BoundedSemaphore(32)


def destination_allowed(method, authority):
    return method == "CONNECT" and authority == DESTINATION


def connect_upstream():
    addresses = socket.getaddrinfo("fcm.googleapis.com", 443, type=socket.SOCK_STREAM)
    for family, kind, protocol, _, address in addresses:
        if not ipaddress.ip_address(address[0]).is_global:
            raise OSError("non_public_destination")
    for family, kind, protocol, _, address in addresses:
        upstream = socket.socket(family, kind, protocol)
        upstream.settimeout(12)
        try:
            upstream.connect(address)
            return upstream
        except OSError:
            upstream.close()
    raise OSError("destination_unreachable")


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if not SLOTS.acquire(blocking=False):
            return
        try:
            self.serve()
        except (OSError, ValueError):
            pass
        finally:
            SLOTS.release()

    def serve(self):
        client = self.request
        client.settimeout(10)
        header = bytearray()
        # Do not consume any TLS bytes after CONNECT headers.
        while not header.endswith(b"\r\n\r\n"):
            part = client.recv(1)
            if not part or len(header) >= 8192:
                return
            header.extend(part)
        request = bytes(header).split(b"\r\n", 1)[0].decode("ascii").split()
        if len(request) != 3 or not destination_allowed(request[0], request[1]):
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        try:
            upstream = connect_upstream()
        except OSError:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        with upstream:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            deadline = time.monotonic() + 120
            transferred = 0
            while time.monotonic() < deadline and transferred < 8 * 1024 * 1024:
                readable, _, _ = select.select([client, upstream], [], [], 15)
                if not readable:
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    transferred += len(data)
                    (upstream if source is client else client).sendall(data)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Never log request bytes, endpoint tokens, or client addresses.
        pass


if __name__ == "__main__":
    with Server(("0.0.0.0", 3128), Handler) as server:
        server.serve_forever()
