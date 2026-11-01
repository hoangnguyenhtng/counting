"""PyInstaller runtime hook: load PyTorch before Qt's bundled DLLs."""

import os


# Keep the packaged application offline and avoid optional CPU checks that can
# fail on older factory computers.
os.environ.setdefault("YOLO_AUTOINSTALL", "false")
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import sys

# Only the legacy detector needs Torch. NumPy first avoids initializing a
# second OpenMP runtime on Conda installations, while Torch must precede Qt.
if "--legacy" in sys.argv:
    import numpy  # noqa: E402,F401
    import torch  # noqa: E402,F401
