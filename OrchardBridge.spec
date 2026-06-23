# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs, copy_metadata

block_cipher = None
root = Path.cwd()

# Keep the build small and avoid collecting unrelated packages from large Conda
# environments. The clean-venv builder is still strongly recommended.
excludes = [
    'numpy', 'pandas', 'scipy', 'matplotlib', 'sklearn', 'torch', 'tensorflow',
    'keras', 'cv2', 'jupyter', 'notebook', 'pytest', 'sphinx',
    'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'numba', 'llvmlite', 'sympy',
]

hiddenimports = [
    # pymobiledevice3 CLI dispatcher and core modules
    'pymobiledevice3.__main__',
    'pymobiledevice3.cli',
    'pymobiledevice3.cli.usbmux',
    'pymobiledevice3.lockdown',
    'pymobiledevice3.usbmux',
    'pymobiledevice3.services.afc',
    'pymobiledevice3.services.mobilebackup2',
    # Hidden imports recommended by pymobiledevice3's upstream PyInstaller notes
    'ipsw_parser',
    'zeroconf',
    'pyimg4',
    'apple_compress',
    'zeroconf._utils.ipaddress',
    'zeroconf._handlers.answers',
    'readchar',
    # pymobiledevice3 imports these at module import time for AFC shell/utils;
    # they must not be excluded in frozen builds, otherwise Lockdown/AFC import
    # fails before any phone details can be read.
    'IPython',
    'traitlets',
    'traitlets.config',
    'prompt_toolkit',
    'pygments',
    'tqdm',
    'requests',
    'xonsh',
    'xonsh.built_ins',
    'xonsh.cli_utils',
    'xonsh.main',
    'xonsh.tools',
    'pygnuutils',
    'pygnuutils.cli.ls',
    'pygnuutils.ls',
    'pyusb',
    'usb',
]

# Collect pymobiledevice3 and key dynamic-import dependency trees.  The EXE must
# be able to import Lockdown + AFC directly; otherwise usbmux can see the phone
# but photo scanning fails in frozen mode.
for pkg in [
    'pymobiledevice3',
    'pymobiledevice3.cli',
    'ipsw_parser',
    'zeroconf',
    'pyimg4',
    'apple_compress',
    'readchar',
    'IPython',
    'traitlets',
    'prompt_toolkit',
    'pygments',
    'tqdm',
    'requests',
    'xonsh',
    'pygnuutils',
    'usb',
    'pillow_heif',
    'PIL',
    'pystray',
    'tkinterdnd2',
    'send2trash',
    'construct',
    'construct_typed',
    'srptools',
    'qh3',
    'bpylist2',
    'parameter_decorators',
    'coloredlogs',
    'humanfriendly',
    'hexdump',
    'pycrashreport',
    'remotezip',
    'pytun_pmd3',
]:
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        hiddenimports.append(pkg)

# PyAV is optional at runtime: if it is not bundled, video thumbnails fall back
# to a placeholder. Keep it bundled when installed.
hiddenimports.append('av')

datas = [
    ('locales', 'locales'),
    ('assets', 'assets'),
    ('doc/source/images', 'doc/source/images'),
    ('README.md', '.'),
    ('LICENSE', '.'),
]

binaries = []
# Include package metadata for libraries that call importlib.metadata at runtime.
for meta_pkg in ['pymobiledevice3', 'pyimg4', 'readchar', 'apple_compress', 'ipsw_parser', 'IPython', 'traitlets', 'pygments', 'tqdm', 'xonsh', 'pygnuutils']:
    try:
        datas += copy_metadata(meta_pkg)
    except Exception:
        pass

for pkg in ['pymobiledevice3', 'IPython', 'traitlets', 'prompt_toolkit', 'pygments', 'tqdm', 'requests', 'xonsh', 'pygnuutils', 'av', 'pillow_heif', 'tkinterdnd2', 'pytun_pmd3', 'pyimg4', 'apple_compress']:
    try:
        datas += collect_data_files(pkg, include_py_files=False)
    except Exception:
        pass
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

try:
    import tkinterdnd2
    tkdnd_dir = Path(tkinterdnd2.__file__).resolve().parent / 'tkdnd'
    if tkdnd_dir.exists():
        datas.append((str(tkdnd_dir), 'tkinterdnd2/tkdnd'))
except Exception:
    pass


a = Analysis(
    ['main.py'],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='OrchardBridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/orchardbridge_icon.ico',
)
