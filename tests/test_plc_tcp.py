import socket
import time
import unittest

from PyQt5.QtCore import QCoreApplication

from hardware.server import Server


class PlcTcpCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_multiple_request_response_cycles_share_one_connection(self):
        server = Server()
        received = []
        server.sendData.connect(received.append)
        server.start_server("127.0.0.1", 0)
        port = server.server_socket.getsockname()[1]

        client = socket.create_connection(("127.0.0.1", port), timeout=2)
        client.settimeout(2)
        try:
            client.sendall(b"Check")
            deadline = time.time() + 2
            while not received and time.time() < deadline:
                self.app.processEvents()
                time.sleep(0.01)

            self.assertEqual(received, ["Check"])
            self.assertTrue(server.send_data_to_all_clients("OK"))
            self.assertEqual(client.recv(1024), b"OK")

            client.sendall(b"Check")
            deadline = time.time() + 2
            while len(received) < 2 and time.time() < deadline:
                self.app.processEvents()
                time.sleep(0.01)

            self.assertEqual(received, ["Check", "Check"])
            self.assertTrue(server.send_data_to_all_clients("NG"))
            self.assertEqual(client.recv(1024), b"NG")
            self.assertEqual(len(server.client_sockets), 1)
        finally:
            client.close()
            server.stop_server()


if __name__ == "__main__":
    unittest.main()
