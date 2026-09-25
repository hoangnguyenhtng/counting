from serial import Serial
import time
from data.logger import Logger


def _serial_kwargs(baud: int) -> dict:
    return {
        "baudrate": baud,
        "timeout": 1,
        "write_timeout": 2,
        "rtscts": False,
        "dsrdtr": False,
    }


class _SerialLightBase:
    """Shared serial open/close helpers for light controllers."""

    light_logger: Logger
    comport: Serial

    def is_open(self):
        return self.comport is not None and self.comport.is_open

    def _open_serial(self, com: str, baud: int) -> bool:
        try:
            self.comport = Serial(port=com, **_serial_kwargs(baud))
            # Many USB-to-RS232 adapters need DTR/RTS asserted before the device accepts data.
            self.comport.dtr = True
            self.comport.rts = True
            time.sleep(0.15)
            self.light_logger.info(f"Opened COM port {com} @ {baud}")
            return self.comport.is_open
        except Exception as e:
            self.light_logger.error(f"Failed to open {com} @ {baud}: {e}")
            self.comport = None
            return False

    def _release_serial(self):
        if self.comport is None:
            return
        try:
            if self.comport.is_open:
                self.comport.close()
        except Exception as e:
            self.light_logger.warning(f"Error while releasing COM port: {e}")
        finally:
            self.comport = None

    def send_data(self, data: str) -> bool:
        if not self.is_open():
            return False
        try:
            payload = data.encode("latin-1")
            self.comport.write(payload)
            self.comport.flush()
            return True
        except Exception as e:
            self.light_logger.warning(f"Light command not accepted by device: {e}")
            return False


class DCPController(_SerialLightBase):
    """Controller for DCP series light sources."""

    def __init__(self):
        self.comport: Serial = None
        self.light_logger = Logger("DCPController")

    def open(self, com, baud):
        self.close(silent=True)
        time.sleep(0.1)
        return self._open_serial(com, baud)

    def close(self, silent: bool = False):
        try:
            if self.is_open():
                if not silent:
                    self.off_all_channels()
                self._release_serial()
                self.light_logger.info("Closed COM port")
            return True
        except Exception as e:
            self.light_logger.error(f"Failed to close COM port: {e}")
            self._release_serial()
            return False

    def off_all_channels(self):
        success = True
        for channel in range(4):
            success = self.set_light_value(channel, 0) and success
            time.sleep(0.02)
        return success

    def set_light_value(self, channel=0, val=0):
        channel_str = {0: "SA", 1: "SB", 2: "SC", 3: "SD"}.get(channel, "")
        value_str = str(val).zfill(3)
        data = f"{channel_str}0{value_str}#"
        return self.send_data(data)


class LCPController(_SerialLightBase):
    """Controller for LCP series light sources."""

    def __init__(self):
        self.comport: Serial = None
        self.light_logger = Logger("LCPController")

    def set_light_value(self, channel=0, val=100):
        data = f"\x02{channel}w{val:04d}\x03"
        return self.send_data(data)

    def on_channel(self, channel=0):
        data = f"\x02{channel}o\x03"
        return self.send_data(data)

    def off_channel(self, channel=0):
        data = f"\x02{channel}f\x03"
        return self.send_data(data)

    def off_all_channels(self):
        # Setting brightness to 0 is more widely supported than the off ('f') command.
        success = True
        for channel in range(4):
            success = self.set_light_value(channel, 0) and success
            time.sleep(0.02)
        return success

    def open(self, com, baud):
        self.close(silent=True)
        time.sleep(0.1)
        return self._open_serial(com, baud)

    def close(self, silent: bool = False):
        try:
            if self.is_open():
                if not silent:
                    self.off_all_channels()
                self._release_serial()
                self.light_logger.info("Closed COM port")
            return True
        except Exception as e:
            self.light_logger.error(f"Failed to close COM port: {e}")
            self._release_serial()
            return False
