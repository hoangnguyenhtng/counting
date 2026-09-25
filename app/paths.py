import os
import sys

def get_base_path() -> str:
    """Return the portable data directory beside the source or executable."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

def get_abs_path(relative_path: str) -> str:
    """Returns absolute path relative to the base directory of execution."""
    return os.path.abspath(os.path.join(get_base_path(), relative_path))

MODEL_AI_DIR = "res/ModelAI"
MODEL_SETTINGS_DIR = "Settings/ModelSettings"
STYLE_PATH = "res/Style/style.qss"
DEFAULT_MODEL = "Default"

def get_model_ai_path(*args) -> str:
    return os.path.join(get_abs_path(MODEL_AI_DIR), *args)

def get_model_settings_path(*args) -> str:
    return os.path.join(get_abs_path(MODEL_SETTINGS_DIR), *args)

def get_style_path() -> str:
    return get_abs_path(STYLE_PATH)
