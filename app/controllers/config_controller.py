import os
from typing import List
from app.models.config import AppConfig


class ConfigController:
    """Manages application configurations."""

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

    def add_model(self, model_name: str, config: AppConfig):
        config_dict = config.model_dump()
        self.json_handler.add(model_name, config_dict)

    def delete_model(self, model_name: str):
        self.json_handler.delete(model_name)

    def list_models(self) -> List[str]:
        model_dir = "Settings/ModelSettings"
        if not os.path.exists(model_dir):
            os.makedirs(model_dir, exist_ok=True)
            return ["Default"]
        return [name for name in os.listdir(model_dir)
                if os.path.isdir(os.path.join(model_dir, name))]

    def list_ai_models(self) -> List[str]:
        model_dir = "res/ModelAI"
        if not os.path.exists(model_dir):
            os.makedirs(model_dir, exist_ok=True)
            return []
        return [name for name in os.listdir(model_dir)
                if name.endswith((".pt", ".pth"))]
