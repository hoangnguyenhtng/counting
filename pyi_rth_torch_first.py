"""PyInstaller runtime hook: load PyTorch before Qt's bundled DLLs."""

import os


# Keep the packaged application offline and avoid optional CPU checks that can
# fail on older factory computers.
os.environ.setdefault("YOLO_AUTOINSTALL", "false")
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

# PyQt5 can otherwise load an incompatible C++ runtime before c10.dll.
import torch  # noqa: E402,F401
