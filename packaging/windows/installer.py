"""Small Windows installer for the onedir Level Up Studio Aleph app."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

PRODUCT = "Level Up Studio Aleph"
APP_FILENAME = "LevelUpStudioAleph.exe"
APP_ICON_FILENAME = "LevelUpStudioAleph.ico"
APP_DIRECTORY = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / PRODUCT
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\LevelUpStudioAleph"
BACKGROUND = "#050806"
SURFACE = "#0e1510"
TEXT = "#edf5ef"
MUTED = "#9aab9f"
GREEN = "#28d17c"
STAGING_DIRECTORY = APP_DIRECTORY.with_name(f"{PRODUCT}.installing")
BACKUP_DIRECTORY = APP_DIRECTORY.with_name(f"{PRODUCT}.previous")


def resource_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / relative


def powershell_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def create_shortcuts(executable: Path) -> None:
    desktop = "[Environment]::GetFolderPath('Desktop')"
    menu = "Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs'"
    icon = executable.with_name(APP_ICON_FILENAME)
    if not icon.is_file():
        raise FileNotFoundError(f"The app icon was not found: {icon.name}")
    icon_location = powershell_quote(f"{icon},0")
    script = f"""
$shell = New-Object -ComObject WScript.Shell
$exe = {powershell_quote(executable)}
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {{ throw 'Installed app executable was not found.' }}
$ico = {powershell_quote(icon)}
if (-not (Test-Path -LiteralPath $ico -PathType Leaf)) {{ throw 'Installed app icon was not found.' }}
$menuPath = {menu}
New-Item -ItemType Directory -Force -Path $menuPath | Out-Null
$paths = @(
  (Join-Path ({desktop}) '{PRODUCT}.lnk'),
  (Join-Path $menuPath '{PRODUCT}.lnk')
)
foreach ($path in $paths) {{
  Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
  $shortcut = $shell.CreateShortcut($path)
  $shortcut.TargetPath = $exe
  $shortcut.Arguments = '--run'
  $shortcut.WorkingDirectory = {powershell_quote(executable.parent)}
  $shortcut.Description = '{PRODUCT}'
  $shortcut.IconLocation = {icon_location}
  $shortcut.Save()
}}
if (-not ('LevelUpAlephShellIcons' -as [type])) {{
  Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class LevelUpAlephShellIcons {{
  [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
  public static extern void SHChangeNotify(int eventId, uint flags, string path, IntPtr item2);
}}
'@
}}
foreach ($path in $paths) {{ [LevelUpAlephShellIcons]::SHChangeNotify(0x2000, 0x0005, $path, [IntPtr]::Zero) }}
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def register_uninstaller(executable: Path) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        values = {
            "DisplayName": PRODUCT,
            "DisplayVersion": "0.2.0",
            "Publisher": "Level Up Studio",
            "DisplayIcon": str(executable),
            "InstallLocation": str(executable.parent),
            "UninstallString": f'"{executable}" --uninstall',
            "NoModify": 1,
            "NoRepair": 1,
        }
        for name, value in values.items():
            kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, kind, value)


def make_window(title: str, size: str) -> tk.Tk:
    root = tk.Tk()
    root.title(title)
    root.geometry(size)
    root.resizable(False, False)
    root.configure(bg=BACKGROUND)
    icon = resource_path("resources/level-up-studio-aleph.ico")
    if icon.is_file():
        root.iconbitmap(icon)
    return root


def add_brand(root: tk.Tk, subtitle: str) -> None:
    from PIL import Image, ImageTk

    with Image.open(resource_path("resources/level-up-studio-aleph-cropped.png")) as source:
        source.thumbnail((200, 154), Image.Resampling.LANCZOS)
        root.brand_image = ImageTk.PhotoImage(source.copy())
    tk.Label(root, image=root.brand_image, bg=BACKGROUND).pack(pady=(16, 8))
    tk.Label(root, text=subtitle, bg=BACKGROUND, fg=MUTED,
             font=("Segoe UI", 10), wraplength=380, justify="center").pack(pady=(0, 18))


def make_button(parent: tk.Widget, text: str, command, *, primary: bool = False) -> tk.Button:
    return tk.Button(
        parent, text=text, command=command,
        bg=GREEN if primary else SURFACE,
        fg=BACKGROUND if primary else TEXT,
        activebackground=TEXT if primary else GREEN,
        activeforeground=BACKGROUND,
        relief="flat", borderwidth=0, padx=18, pady=10,
        font=("Segoe UI", 10, "bold" if primary else "normal"), cursor="hand2",
    )


def close_running_app(root: tk.Tk) -> bool:
    powershell = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]
    check = subprocess.run(
        powershell + [
            "if (Get-Process -Name 'LevelUpStudioAleph' -ErrorAction SilentlyContinue) { exit 1 }"
        ],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if check.returncode == 0:
        return True
    if check.returncode != 1:
        raise RuntimeError("Windows could not check whether Aleph is open.")

    if not messagebox.askyesno(
        PRODUCT,
        "Aleph is open. Save or stop any active capture first.\n\n"
        "Close Aleph now so its files can be updated?",
        parent=root,
    ):
        return False

    close_script = r"""
$processes = @(Get-Process -Name 'LevelUpStudioAleph' -ErrorAction SilentlyContinue)
foreach ($process in $processes) { [void]$process.CloseMainWindow() }
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
  if (-not (Get-Process -Name 'LevelUpStudioAleph' -ErrorAction SilentlyContinue)) { exit 0 }
  Start-Sleep -Milliseconds 250
}
exit 1
"""
    result = subprocess.run(
        powershell + [close_script], capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode == 0:
        return True

    messagebox.showwarning(
        PRODUCT,
        "Aleph is still open. Close its window and click Install again.\n\n"
        "The current installation has not been changed.",
        parent=root,
    )
    return False


def install_bundle(bundle: Path, root: tk.Tk, status: tk.Label) -> Path | None:
    APP_DIRECTORY.parent.mkdir(parents=True, exist_ok=True)

    # Recover a previous installation if Windows stopped an earlier update
    # between moving the old folder aside and installing the new one.
    if BACKUP_DIRECTORY.exists():
        if APP_DIRECTORY.exists():
            shutil.rmtree(BACKUP_DIRECTORY)
        else:
            os.replace(BACKUP_DIRECTORY, APP_DIRECTORY)
    if STAGING_DIRECTORY.exists():
        shutil.rmtree(STAGING_DIRECTORY)

    status.config(text="Preparing the update…", fg=TEXT)
    root.update_idletasks()
    shutil.copytree(bundle, STAGING_DIRECTORY)
    staged_executable = STAGING_DIRECTORY / APP_FILENAME
    if not staged_executable.is_file():
        shutil.rmtree(STAGING_DIRECTORY, ignore_errors=True)
        raise FileNotFoundError("The app files are missing from this installer.")
    bundled_icon = bundle / "_internal" / "resources" / "level-up-studio-aleph.ico"
    if not bundled_icon.is_file():
        shutil.rmtree(STAGING_DIRECTORY, ignore_errors=True)
        raise FileNotFoundError("The app icon is missing from this installer.")
    shutil.copy2(bundled_icon, STAGING_DIRECTORY / APP_ICON_FILENAME)

    if not close_running_app(root):
        shutil.rmtree(STAGING_DIRECTORY, ignore_errors=True)
        return None

    had_installation = APP_DIRECTORY.exists()
    if had_installation:
        os.replace(APP_DIRECTORY, BACKUP_DIRECTORY)
    try:
        os.replace(STAGING_DIRECTORY, APP_DIRECTORY)
        executable = APP_DIRECTORY / APP_FILENAME
        create_shortcuts(executable)
        register_uninstaller(executable)
    except Exception:
        shutil.rmtree(APP_DIRECTORY, ignore_errors=True)
        if had_installation and BACKUP_DIRECTORY.exists():
            os.replace(BACKUP_DIRECTORY, APP_DIRECTORY)
        raise

    if BACKUP_DIRECTORY.exists():
        shutil.rmtree(BACKUP_DIRECTORY, ignore_errors=True)
    return APP_DIRECTORY / APP_FILENAME


def friendly_install_error(error: Exception) -> str:
    if isinstance(error, OSError) and getattr(error, "winerror", None) == 32:
        return "A file is still in use. Close Aleph and try installing again."
    if isinstance(error, shutil.Error) and error.args and isinstance(error.args[0], list):
        failures = error.args[0]
        if failures:
            filename = Path(failures[0][1]).name
            return f"Windows could not copy {filename}. Close Aleph and try again."
    return str(error)


def install_window() -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Build Setup.exe before opening the installer.")

    root = make_window(f"Install {PRODUCT}", "460x390")
    add_brand(root, "Install Level Up Studio Aleph for your Windows account. Python is included.")

    tk.Label(root, text=str(APP_DIRECTORY), bg=BACKGROUND, fg=MUTED,
             font=("Segoe UI", 9), wraplength=400).pack(pady=(0, 14))
    status = tk.Label(root, text="Your saved captures will remain in Documents.", bg=BACKGROUND,
                      fg=MUTED, font=("Segoe UI", 9), wraplength=400)
    status.pack(pady=(0, 12))
    actions = tk.Frame(root, bg=BACKGROUND)
    actions.pack(pady=(0, 20))

    def open_app() -> None:
        subprocess.Popen([str(APP_DIRECTORY / APP_FILENAME), "--run"])
        root.destroy()

    def install() -> None:
        try:
            bundle = resource_path("app")
            executable = bundle / APP_FILENAME
            if not executable.is_file():
                raise FileNotFoundError("The app files are missing from this installer.")
            executable = install_bundle(bundle, root, status)
            if executable is None:
                status.config(text="Installation canceled. The current app files are unchanged.", fg=MUTED)
                return
        except Exception as error:
            shutil.rmtree(STAGING_DIRECTORY, ignore_errors=True)
            messagebox.showerror(
                PRODUCT, f"Aleph could not be installed.\n\n{friendly_install_error(error)}", parent=root,
            )
            return

        status.config(text="Aleph is installed. A shortcut is ready on your desktop and Start menu.", fg=TEXT)
        install_button.pack_forget()
        make_button(actions, "Open Level Up Studio Aleph", open_app, primary=True).pack(side="left", padx=5)
        make_button(actions, "Close", root.destroy).pack(side="left", padx=5)

    install_button = make_button(actions, "Install", install, primary=True)
    install_button.pack(side="left", padx=5)
    make_button(actions, "Cancel", root.destroy).pack(side="left", padx=5)
    root.mainloop()


def uninstall_window() -> None:
    if not messagebox.askyesno(
        PRODUCT,
        "Remove Level Up Studio Aleph from this computer?\n\nYour saved captures in Documents will stay in place.",
    ):
        return

    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass

    script = f"""
Start-Sleep -Seconds 2
Get-Process -Name 'LevelUpStudioAleph' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 700
Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) '{PRODUCT}.lnk') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $env:APPDATA 'Microsoft/Windows/Start Menu/Programs/{PRODUCT}.lnk') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath {powershell_quote(APP_DIRECTORY)} -Recurse -Force -ErrorAction SilentlyContinue
"""
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def main() -> int:
    if "--uninstall" in sys.argv[1:]:
        uninstall_window()
    else:
        install_window()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
