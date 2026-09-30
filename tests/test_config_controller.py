import unittest
from unittest.mock import Mock, patch

from app.controllers.config_controller import ConfigController
from app.models.config import AppConfig


class ConfigControllerTests(unittest.TestCase):
    def test_model_name_validation_rejects_unsafe_windows_names(self):
        invalid_names = ["", "..", "A/B", "A\\B", "COM1", "name."]
        for name in invalid_names:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    ConfigController.validate_model_name(name)

    def test_add_model_saves_current_config_under_new_name(self):
        json_handler = Mock()
        controller = ConfigController(json_handler)
        config = AppConfig(name_model="Default")

        with patch.object(controller, "list_models", return_value=["Default"]):
            created = controller.add_model("Product_A", config)

        self.assertEqual(created.name_model, "Product_A")
        self.assertEqual(config.name_model, "Default")
        json_handler.add.assert_called_once()
        self.assertEqual(json_handler.add.call_args.args[0], "Product_A")
        self.assertEqual(json_handler.add.call_args.args[1]["name_model"], "Product_A")

    def test_add_model_rejects_case_insensitive_duplicate(self):
        controller = ConfigController(Mock())
        with patch.object(controller, "list_models", return_value=["Default"]):
            with self.assertRaises(FileExistsError):
                controller.add_model("default", AppConfig())


if __name__ == "__main__":
    unittest.main()
