import socket
import threading
import unittest
from unittest.mock import patch

import fcm_proxy


class RelayTests(unittest.TestCase):
    def test_exact_destination_only(self):
        self.assertTrue(fcm_proxy.destination_allowed("CONNECT", "fcm.googleapis.com:443"))
        for authority in ("example.com:443", "fcm.googleapis.com:80", "127.0.0.1:443",
                          "fcm.googleapis.com.evil:443", "user@fcm.googleapis.com:443"):
            self.assertFalse(fcm_proxy.destination_allowed("CONNECT", authority))
        self.assertFalse(fcm_proxy.destination_allowed("GET", "fcm.googleapis.com:443"))

    def test_private_dns_rejected(self):
        with patch.object(socket, "getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ]), self.assertRaisesRegex(OSError, "non_public"):
            fcm_proxy.connect_upstream()

    def test_forbidden_request_does_not_resolve_or_connect(self):
        with fcm_proxy.Server(("127.0.0.1", 0), fcm_proxy.Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(fcm_proxy, "connect_upstream") as connect:
                    with socket.create_connection(server.server_address) as client:
                        client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
                        self.assertIn(b"403 Forbidden", client.recv(4096))
                    connect.assert_not_called()
            finally:
                server.shutdown()

    def test_tunnel_keeps_bytes_unchanged(self):
        upstream, remote = socket.socketpair()
        with fcm_proxy.Server(("127.0.0.1", 0), fcm_proxy.Handler) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with patch.object(fcm_proxy, "connect_upstream", return_value=upstream):
                    with socket.create_connection(server.server_address) as client:
                        client.settimeout(3)
                        remote.settimeout(3)
                        client.sendall(b"CONNECT fcm.googleapis.com:443 HTTP/1.1\r\n\r\n")
                        self.assertIn(b"200 Connection Established", client.recv(4096))
                        client.sendall(b"synthetic-tls-bytes")
                        self.assertEqual(remote.recv(4096), b"synthetic-tls-bytes")
                        remote.sendall(b"synthetic-response")
                        self.assertEqual(client.recv(4096), b"synthetic-response")
            finally:
                remote.close()
                server.shutdown()


if __name__ == "__main__":
    unittest.main()
