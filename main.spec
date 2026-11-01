# -*- mode: python ; coding: utf-8 -*-
import os
import shutil
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
os.environ["YOLO_AUTOINSTALL"] = "false"

block_cipher = None

datas = []
datas += collect_data_files('ultralytics')
datas += collect_data_files('torch')

hiddenimports = [
    'torch',
    'torchvision',
    'ultralytics',
    'ultralytics.nn',
    'ultralytics.models',
    'ultralytics.utils',
    'PyQt5',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    'cv2',
    'serial',
    'serial.tools.list_ports',
    'sqlalchemy',
    'sqlalchemy.sql.default_comparator',
    'pydantic',
    'pydantic_core',
    'yaml',
    'openpyxl',
    'onnxruntime',
    'psutil',
    'GPUtil',
    'colorlog',
    'pytz',
    'unittest',
    'tkinter',
    'tkinter.messagebox',
]
hiddenimports += collect_submodules('ultralytics')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_rth_torch_first.py'],
    excludes=[
        'test',
        'tests',
        'polars',
        '_polars_runtime_32',
        'IPython',
        'tensorboard',
        # The application uses PyQt5. Excluding other Qt bindings prevents
        # PyInstaller from aborting on build machines that also have them.
        'PySide2',
        'PySide6',
        'PyQt6',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VisionProtectVinylAttach',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='res/App/smart_factory_g.ico' if Path('res/App/smart_factory_g.ico').exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='VisionProtectVinylAttach',
)

# These are runtime/user-editable resources. The application intentionally
# resolves them beside VisionProtectVinylAttach.exe, so keep them outside
# PyInstaller's _internal
# directory.
bundle_root = Path(DISTPATH) / 'VisionProtectVinylAttach'
for source_dir in (
    Path('res/App'),
    Path('res/Style'),
    Path('res/ModelAI'),
    Path('Settings'),
    Path('tray_counter/config'),
    Path('tray_counter/models'),
    Path('tray_counter/reference'),
):
    if source_dir.exists():
        shutil.copytree(
            source_dir,
            bundle_root / source_dir,
            dirs_exist_ok=True,
        )
