import unittest
from unittest.mock import patch

import numpy as np

from app.controllers.hardware_controller import HardwareController
from app.controllers.auto_controller import AutoController
from app.constants import Step
from app.models.config import AppConfig
from app.signals import bus
from hardware.light_controller import DCPController, LCPController


class _FakeLight:
    def __init__(self):
        self.calls = []

    def set_light_value(self, channel, value):
        self.calls.append((channel, value))
        return True


class _FakeAutoHardware:
    def __init__(self):
        self.is_open_light = True
        self.is_connect_server = False
        self.is_com_send_data = False
        self.light_calls = []
        self.camera_start_count = 0
        self.frame = np.zeros((8, 8, 3), dtype=np.uint8)
        self.camera = self

    def stop_camera_acquisition(self):
        return True

    def start_camera_acquisition(self):
        self.camera_start_count += 1
        return True

    def set_light_values(self, *values):
        self.light_calls.append(values)
        return True

    def get_frame(self):
        return self.frame


class LightControlTests(unittest.TestCase):
    def test_hardware_controller_turns_all_channels_on_and_off(self):
        controller = HardwareController()
        fake_light = _FakeLight()
        controller.light = fake_light
        controller.is_open_light = True

        self.assertTrue(controller.set_light_values(10, 20, 30, 40, 0))
        self.assertTrue(controller.is_light_on)
        self.assertEqual(fake_light.calls, [(0, 10), (1, 20), (2, 30), (3, 40)])

        self.assertTrue(controller.set_light_values(0, 0, 0, 0, 0))
        self.assertFalse(controller.is_light_on)
        self.assertEqual(fake_light.calls[-4:], [(0, 0), (1, 0), (2, 0), (3, 0)])

    def test_controller_command_formats_match_supported_devices(self):
        dcp_commands = []
        dcp = DCPController()
        dcp.send_data = lambda command: dcp_commands.append(command) or True
        self.assertTrue(dcp.set_light_value(2, 7))
        self.assertEqual(dcp_commands, ["SC0007#"])

        lcp_commands = []
        lcp = LCPController()
        lcp.send_data = lambda command: lcp_commands.append(command) or True
        self.assertTrue(lcp.set_light_value(2, 7))
        self.assertEqual(lcp_commands, ["\x022w0007\x03"])

    def test_auto_waits_for_plc_trigger_before_light_and_camera(self):
        hardware = _FakeAutoHardware()
        controller = AutoController(hardware, model_pool=None)
        config = AppConfig()
        config.hardware.lighting.channel_value_0 = 10
        config.hardware.lighting.channel_value_1 = 20
        config.hardware.lighting.channel_value_2 = 30
        config.hardware.lighting.channel_value_3 = 40
        config.hardware.lighting.delay = 0

        controller.start(config)
        controller.run_step()
        self.assertEqual(controller.state, Step.READ_TRIGGER)
        self.assertEqual(hardware.light_calls, [])
        self.assertEqual(hardware.camera_start_count, 0)

        accepted_triggers = []
        on_trigger = lambda: accepted_triggers.append(True)
        bus.inspection_triggered.connect(on_trigger)
        try:
            controller.handle_trigger("L1")
        finally:
            bus.inspection_triggered.disconnect(on_trigger)
        self.assertEqual(accepted_triggers, [True])
        controller.run_step()
        self.assertEqual(controller.state, Step.ON_LIGHTING)
        self.assertEqual(hardware.light_calls, [])
        self.assertEqual(hardware.camera_start_count, 0)

        controller.run_step()
        self.assertEqual(hardware.light_calls, [(10, 20, 30, 40, 0)])
        self.assertEqual(hardware.camera_start_count, 1)
        self.assertEqual(controller.state, Step.CAPTURE)
        controller.is_running = False

    def test_bypass_captures_and_emits_result_for_managed_result_logging(self):
        hardware = _FakeAutoHardware()
        controller = AutoController(hardware, model_pool=None)
        config = AppConfig()
        config.hardware.camera.delay = 0
        config.hardware.lighting.delay = 0

        controller.set_bypass_mode(True)
        controller.start(config)
        controller.handle_trigger("L1")

        with patch("app.controllers.auto_controller.detect_tape") as detect:
            controller.run_step()  # READ_TRIGGER -> ON_LIGHTING
            controller.run_step()  # ON_LIGHTING -> CAPTURE
            controller.run_step()  # CAPTURE -> OFF_LIGHTING
            controller.run_step()  # OFF_LIGHTING -> DETECT
            controller.run_step()  # DETECT -> OUTPUT

        self.assertEqual(hardware.camera_start_count, 1)
        detect.assert_not_called()
        self.assertEqual(controller.active_result.label_counts, "BYPASS_OK")
        self.assertIs(controller.active_result.src, hardware.frame)
        self.assertIsNot(controller.active_result.dst, hardware.frame)
        self.assertGreater(int(controller.active_result.dst[1, 1, 1]), 100)
        self.assertEqual(controller.output_str, "OK")
        self.assertEqual(controller.state, Step.OUTPUT)
        controller.is_running = False


if __name__ == "__main__":
    unittest.main()
