# PyInstaller spec — run from scripts/time_tracker:
#   pip install pyinstaller
#   pyinstaller --noconfirm build_rootrecord.spec
#
# Output: dist/RootRecord/RootRecord.exe (windowed, one folder — good for Inno Setup).

import importlib.util
import os

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

block_cipher = None

spec_dir = os.path.dirname(os.path.abspath(SPEC))
icon_path = os.path.abspath(os.path.join(spec_dir, "..", "..", "favicon.ico"))
about_image_path = os.path.abspath(os.path.join(spec_dir, "..", "..", "about page grahic.jpg"))
icon_datas = [(icon_path, ".")] if os.path.isfile(icon_path) else []
about_datas = [(about_image_path, ".")] if os.path.isfile(about_image_path) else []


def _app_version_tuple_and_string() -> tuple[tuple[int, int, int, int], str]:
    path = os.path.join(spec_dir, "app_version.py")
    s = importlib.util.spec_from_file_location("app_version_for_spec", path)
    mod = importlib.util.module_from_spec(s)
    assert s.loader is not None
    s.loader.exec_module(mod)
    ver = str(getattr(mod, "APP_VERSION", "0.0.0"))
    parts = [int(x) for x in ver.split(".") if x.isdigit()]
    while len(parts) < 4:
        parts.append(0)
    return (parts[0], parts[1], parts[2], parts[3]), ver


_filevers, _prodver_str = _app_version_tuple_and_string()
_rootrecord_version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_filevers,
        prodvers=_filevers,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "RootRecord"),
                        StringStruct(
                            "FileDescription",
                            "RootRecord Business Manager (Beta)",
                        ),
                        StringStruct(
                            "FileVersion",
                            f"{_filevers[0]}.{_filevers[1]}.{_filevers[2]}.{_filevers[3]}",
                        ),
                        StringStruct("InternalName", "RootRecord"),
                        StringStruct("OriginalFilename", "RootRecord.exe"),
                        StringStruct(
                            "ProductName",
                            "RootRecord Business Manager (Beta)",
                        ),
                        StringStruct("ProductVersion", _prodver_str),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

ctk_datas, ctk_bins, ctk_hidden = collect_all("customtkinter")
mpl_datas, mpl_bins, mpl_hidden = collect_all("matplotlib")

a = Analysis(
    [os.path.join(spec_dir, "desktop_app.py")],
    pathex=[spec_dir],
    binaries=ctk_bins + mpl_bins,
    datas=ctk_datas + mpl_datas + icon_datas + about_datas,
    hiddenimports=list(
        {
            *ctk_hidden,
            *mpl_hidden,
            "matplotlib.backends.backend_tkagg",
            "db",
            "paths",
            "storage",
            "tracking_core",
            "export_sheet",
            "sqlite3",
            "ui_main",
            "migrations",
            "data_api",
            "suite_data",
            "help_text",
            "env_loader",
            "app_version",
            "firebase_auth_client",
            "firebase_embedded",
            "firebase_cloud_backup",
            "httpx",
            "httpx._transports",
            "dotenv",
            "dotenv.main",
            "keyring",
            "cryptography",
            "cryptography.fernet",
            "google_auth_oauthlib",
            "google_auth_oauthlib.flow",
            "google.oauth2.credentials",
            "google.auth.transport.requests",
        }
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="RootRecord",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_rootrecord_version_info,
    icon=icon_path if os.path.isfile(icon_path) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RootRecord",
)
