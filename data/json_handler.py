import json
import shutil
import os
from hardware.base.base_json import HandleJSON

class HandleJsonPBA(HandleJSON):
    """Utility class to read, write, update, and manage JSON model configurations."""
    
    def add(self, name_model, config: dict, local_path='Settings/ModelSettings'):
        os.makedirs(f'{local_path}/{name_model}', exist_ok=True)
        default_path = f'{local_path}/Default'
        if os.path.exists(default_path):
            shutil.copytree(default_path, f'{local_path}/{name_model}', dirs_exist_ok=True)
        self.save(name_model, config, local_path, overwrite=True)

    def delete(self, name_model, local_path='Settings/ModelSettings'):
        path = os.path.join(local_path, name_model)
        if os.path.exists(path):
            try:
                shutil.rmtree(path)
                print(f"Deleted folder: {path}")
            except Exception as e:
                print(f"Error deleting {path}: {e}")
        else:
            print(f"Not found: {path}")

    def save(self, name_model, config: dict, local_path='Settings/ModelSettings', overwrite=False):
        path = f'{local_path}/{name_model}/config.json'
        try:
            with open(path, 'r', encoding='utf-8') as f:
                current_config = json.load(f)
        except FileNotFoundError:
            current_config = {}

        if overwrite:
            new_config = config
        else:
            new_config = current_config.copy()
            for key, value in config.items():
                if key not in new_config or new_config[key] != value:
                    new_config[key] = value

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=4, ensure_ascii=False)

    def load(self, file_path='Settings/ModelSettings/Default'):
        config_path = os.path.join(file_path, 'config.json')
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as file:
                    return json.load(file)
            except json.JSONDecodeError:
                return None
        return None

    def update_config_key(self, name_model, key, value, local_path='Settings/ModelSettings'):
        path = f'{local_path}/{name_model}/config.json'
        try:
            with open(path, 'r', encoding='utf-8') as file:
                data = json.load(file)
        except FileNotFoundError:
            data = {}
        data[key] = value
        with open(path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
