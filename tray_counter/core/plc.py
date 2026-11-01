"""PLC adapters. Signal addresses and the real transport belong to deployment config."""
from abc import ABC, abstractmethod
from collections import deque
from threading import RLock


class PLC(ABC):
    @abstractmethod
    def read_trigger(self) -> bool:
        pass

    @abstractmethod
    def write_signal(self, name, value):
        pass

    def write_result(self, result, done, busy, err_code=0):
        # Never expose DONE with an old RESULT. This ordering also works when
        # adapters implement individual register writes rather than atomic writes.
        if not done:
            self.write_signal("DONE", 0)
        self.write_signal("RESULT", int(result))
        self.write_signal("ERR_CODE", int(err_code))
        self.write_signal("BUSY", int(busy))
        if done:
            self.write_signal("DONE", 1)

    def heartbeat(self):
        self._heartbeat = 1 - getattr(self, "_heartbeat", 0)
        self.write_signal("HEARTBEAT", self._heartbeat)

    def close(self):
        pass


class SimPLC(PLC):
    def __init__(self):
        self._lock = RLock()
        self.signals = dict(TRIGGER=0, BUSY=0, DONE=0, RESULT=0, HEARTBEAT=0, ERR_CODE=0)
        self.history = deque(maxlen=2000)

    def set_trigger(self, value):
        with self._lock:
            self.signals["TRIGGER"] = int(bool(value))

    def read_trigger(self):
        with self._lock:
            return bool(self.signals["TRIGGER"])

    def write_signal(self, name, value):
        with self._lock:
            if name == "TRIGGER" or name not in self.signals:
                raise ValueError(f"Tín hiệu PC không được ghi: {name}")
            self.signals[name] = int(value)
            self.history.append((name, int(value)))

    def snapshot(self):
        with self._lock:
            return dict(self.signals)


class MappedPLC(PLC):
    """Bind a vendor client's bit/word reader and writer after the PLC map is agreed.

    read(address) and write(address, value) must raise on connection errors and
    use bounded I/O timeouts. No PLC mechanics or conveyor commands are issued.
    """
    def __init__(self, read, write, addresses):
        required = {"TRIGGER", "BUSY", "DONE", "RESULT", "HEARTBEAT", "ERR_CODE"}
        if set(addresses) != required or any(v is None for v in addresses.values()):
            raise ValueError("Cần địa chỉ PLC đầy đủ cho cả 6 tín hiệu")
        self.read, self.write, self.addresses = read, write, dict(addresses)

    def read_trigger(self):
        value = self.read(self.addresses["TRIGGER"])
        if value not in (0, 1, False, True):
            raise IOError("Giá trị TRIGGER không hợp lệ")
        return bool(value)

    def write_signal(self, name, value):
        if name == "TRIGGER":
            raise ValueError("PC không được ghi TRIGGER")
        self.write(self.addresses[name], int(value))
