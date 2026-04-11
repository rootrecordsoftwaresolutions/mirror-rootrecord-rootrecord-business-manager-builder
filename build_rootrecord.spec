# PyInstaller spec — cross-platform (Windows folder + installer; Linux/macOS folder bundle).
#
# Windows:
#   pip install pyinstaller
#   pyinstaller --noconfirm build_rootrecord.spec
#   → dist/RootRecord/RootRecord.exe
#
# Linux (see build/build_linux.sh):
#   pyinstaller --noconfirm build_rootrecord.spec
#   → dist/RootRecord/RootRecord

import importlib.util
import os
import sys

from PyInstaller.utils.hooks import collect_all

if sys.platform == "win32":
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
# Optional assets: repo-local first, then workspace-parent (local dev layout).
_favicon_repo = os.path.join(spec_dir, "favicon.ico")
_favicon_ws = os.path.abspath(os.path.join(spec_dir, "..", "..", "favicon.ico"))
icon_path = _favicon_repo if os.path.isfile(_favicon_repo) else _favicon_ws
_about_repo = os.path.join(spec_dir, "about_page_graphic.jpg")
_about_ws = os.path.abspath(os.path.join(spec_dir, "..", "..", "about page grahic.jpg"))
about_image_path = _about_repo if os.path.isfile(_about_repo) else _about_ws
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


_rootrecord_version_info = None
if sys.platform == "win32":
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

# Executable icon: Windows uses .ico; Linux often ships without or uses same if present.
_icon_for_exe = None
if sys.platform == "win32" and os.path.isfile(icon_path):
    _icon_for_exe = icon_path
elif sys.platform != "win32":
    _png_repo = os.path.join(spec_dir, "icon.png")
    _png_ws = os.path.abspath(os.path.join(spec_dir, "..", "..", "icon.png"))
    if os.path.isfile(_png_repo):
        _icon_for_exe = _png_repo
    elif os.path.isfile(_png_ws):
        _icon_for_exe = _png_ws
    elif os.path.isfile(icon_path):
        _icon_for_exe = icon_path

_exe_kwargs = {
    "exclude_binaries": True,
    "name": "RootRecord",
    "debug": False,
    "bootloader_ignore_signals": False,
    "strip": False,
    "upx": True,
    "console": False,
    "disable_windowed_traceback": False,
    "argv_emulation": False,
    "target_arch": None,
    "codesign_identity": None,
    "entitlements_file": None,
}
if sys.platform == "win32" and _rootrecord_version_info is not None:
    _exe_kwargs["version"] = _rootrecord_version_info
if _icon_for_exe:
    _exe_kwargs["icon"] = _icon_for_exe

exe = EXE(
    pyz,
    a.scripts,
    [],
    **_exe_kwargs,
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
