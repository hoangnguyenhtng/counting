import socket
import threading
from PyQt5.QtCore import pyqtSignal
from data.logger import Logger
from hardware.base.base_server import AbstractServer
from app.signals import bus

class Server(AbstractServer):
    """TCP Socket Server that listens for trigger messages from external systems."""
    
    def __init__(self):
        super().__init__()
        self.server_logger = Logger('Server')
        self.client_sockets = []

    def start_server(self, ip='127.0.0.1', port=8000):
        try:
            self.ip = ip
            self.port = port
            self.server_logger.info('Starting the server')
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.ip, self.port))
            self.server_socket.listen(1)
            self.is_connected = True
            threading.Thread(target=self.accept_client, daemon=True).start()
        except Exception as e:
            self.is_connected = False
            self.server_logger.error(f"Error starting server: {e}")

    def accept_client(self):
        while self.is_connected:
            try:
                client_socket, client_address = self.server_socket.accept()
                if not self.is_connected:
                    client_socket.close()
                    break
                else:
                    self.client_sockets.append(client_socket)
                    threading.Thread(target=self.loop_recv_client, args=(client_socket,), daemon=True).start()
                    self.server_logger.info(f'{client_address} connected')
            except Exception as e:
                if self.is_connected:
                    self.server_logger.error(f"Error accepting client: {e}")
                break

    def stop_server(self):
        try:
            self.is_connected = False
            for client in self.client_sockets:
                try:
                    client.close()
                except:
                    pass
            self.client_sockets.clear()
            if self.server_socket:
                self.server_socket.close()
                self.server_logger.info('Disconnected')
        except Exception as e:
            self.server_logger.error(f"Error stopping server: {e}")

    def loop_recv_client(self, conn: socket.socket):
        """Keep reading PLC requests until the peer disconnects or the server stops."""
        try:
            while self.is_connected:
                msg = conn.recv(1024).decode().strip()
                if not msg:
                    break
                bus.log_message.emit(f"TCP socket received raw data: '{msg}'", "INF")
                self.sendData.emit(msg)
        except Exception as e:
            if self.is_connected:
                self.server_logger.error(f"Error receiving data: {e}")

        if conn in self.client_sockets:
            self.client_sockets.remove(conn)
        try:
            conn.close()
        except Exception:
            pass

    def send_data_to_client(self, data: str, conn: socket.socket):
        try:
            if conn:
                conn.sendall(data.encode())
                self.server_logger.info(f"Sent to client: {data}")
                return True
        except Exception as e:
            self.server_logger.error(f"Error sending data to client: {e}")
            if conn in self.client_sockets:
                self.client_sockets.remove(conn)
            try:
                conn.close()
            except Exception:
                pass
        return False

    def send_data_to_all_clients(self, data: str):
        """Send a response while keeping healthy PLC connections open."""
        sent_count = 0
        for conn in self.client_sockets[:]:
            try:
                conn.sendall(data.encode())
                sent_count += 1
                self.server_logger.info(f"Sent to all: {data}")
            except Exception as e:
                self.server_logger.error(f"Error sending to all: {e}")
                if conn in self.client_sockets:
                    self.client_sockets.remove(conn)
                try:
                    conn.close()
                except Exception:
                    pass
        return sent_count > 0
