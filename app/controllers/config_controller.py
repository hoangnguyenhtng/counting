import os
from typing import List
from app.models.config import AppConfig


class ConfigController:

    _WINDOWS_RESERVED_NAMES = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }

    def __init__(self, json_handler):
        self.json_handler = json_handler

    @staticmethod
    def set_com_port_combo(combo, port: str):
        port = (port or "").strip()
        if not port:
            return
        if combo.findText(port) == -1:
            combo.addItem(port)
        combo.setCurrentText(port)

    def load_config(self, model_name: str) -> AppConfig:
        try:
            config_data = self.json_handler.load(f'Settings/ModelSettings/{model_name}')
            if config_data is not None:
                return AppConfig.model_validate(config_data)
        except Exception as e:
            raise ValueError(f"Invalid configuration for '{model_name}': {e}") from e
        raise FileNotFoundError(f"Configuration file not found for '{model_name}'.")

    def save_config(self, model_name: str, config: AppConfig):
        config_dict = config.model_dump()
        self.json_handler.save(model_name, config_dict)

    @classmethod
    def validate_model_name(cls, model_name: str) -> str:
        """Return a safe model-directory name or raise ``ValueError``."""
        name = (model_name or "").strip()
        if not name:
            raise ValueError("Model name cannot be empty.")
        if len(name) > 64:
            raise ValueError("Model name must be 64 characters or fewer.")
        if name in {".", ".."} or name.endswith((".", " ")):
            raise ValueError("Model name cannot end with a dot or space.")
        if any(character in '<>:"/\\|?*' or ord(character) < 32 for character in name):
            raise ValueError("Model name contains characters that are not allowed by Windows.")
        if name.split(".", 1)[0].upper() in cls._WINDOWS_RESERVED_NAMES:
            raise ValueError(f"'{name}' is a reserved Windows name.")
        return name

    def add_model(self, model_name: str, config: AppConfig) -> AppConfig:
        model_name = self.validate_model_name(model_name)
        if any(name.casefold() == model_name.casefold() for name in self.list_models()):
            raise FileExistsError(f"Model configuration '{model_name}' already exists.")
        model_config = config.model_copy(update={"name_model": model_name}, deep=True)
        self.json_handler.add(model_name, model_config.model_dump())
        return model_config

    def delete_model(self, model_name: str):
        self.json_handler.delete(model_name)

    def list_models(self) -> List[str]:
        model_dir = "Settings/ModelSettings"
        if not os.path.exists(model_dir):
            os.makedirs(model_dir, exist_ok=True)
            return ["Default"]
        return sorted(
            [name for name in os.listdir(model_dir)
             if os.path.isdir(os.path.join(model_dir, name))],
            key=str.casefold,
        )

    def list_ai_models(self) -> List[str]:
        model_dir = "res/ModelAI"
        if not os.path.exists(model_dir):
            os.makedirs(model_dir, exist_ok=True)
            return []
        return [name for name in os.listdir(model_dir)
                if name.endswith((".pt", ".pth"))]
