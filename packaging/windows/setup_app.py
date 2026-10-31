"""Windows desktop launcher for Level Up Studio Aleph."""

from __future__ import annotations

import ctypes
import multiprocessing
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

multiprocessing.freeze_support()

from alephgeo.__main__ import main

PRODUCT = "Level Up Studio Aleph"
APP_DIRECTORY = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / PRODUCT
CAPTURE_DIRECTORY = Path.home() / "Documents" / PRODUCT / "captures"
BACKGROUND = "#050806"
WEBVIEW2_DOWNLOAD = "https://developer.microsoft.com/en-us/microsoft-edge/webview2/"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def run_dashboard(port: int) -> int:
    log_directory = APP_DIRECTORY / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    log = (log_directory / "dashboard.log").open("a", encoding="utf-8")
    sys.stdout = log
    sys.stderr = log
    CAPTURE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    return main(["dashboard", "-o", str(CAPTURE_DIRECTORY), "--port", str(port), "--no-browser"])


def control_window() -> None:
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    process = subprocess.Popen(
        [sys.executable, "--serve", str(port)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("The local Aleph service stopped before the app window opened.")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise TimeoutError("The local Aleph service did not start in time.")

        import webview

        # WebView2 cancels downloads by default. Allow its save dialog so the
        # capture ZIP link works in the packaged desktop window.
        webview.settings["ALLOW_DOWNLOADS"] = True
        window = webview.create_window(
            PRODUCT, url, width=1280, height=820, min_size=(1000, 680),
            background_color=BACKGROUND, text_select=True,
        )
        webview.start(gui="edgechromium")
    except Exception as error:
        stop_process(process)
        result = ctypes.windll.user32.MessageBoxW(
            None,
            f"Aleph could not open its app window.\n\n{error}\n\n"
            "Aleph uses Microsoft's WebView2 runtime to show its map inside the app. "
            "Would you like to open Microsoft's WebView2 download page?",
            PRODUCT,
            0x00000004 | 0x00000030 | 0x00010000,
        )
        if result == 6:
            webbrowser.open(WEBVIEW2_DOWNLOAD)
    finally:
        if process.poll() is None:
            request_server_shutdown(port, process)


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def request_server_shutdown(port: int, process: subprocess.Popen) -> None:
    if process.poll() is None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/shutdown", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=3):
                pass
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        try:
            process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            stop_process(process)


def uninstall() -> None:
    answer = ctypes.windll.user32.MessageBoxW(
        None,
        "Remove Level Up Studio Aleph from this computer?\n\n"
        "Your saved captures in Documents will stay in place.",
        PRODUCT,
        0x00000004 | 0x00000020 | 0x00010000,
    )
    if answer != 6:
        return

    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\LevelUpStudioAleph"
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
    except FileNotFoundError:
        pass

    script = f"""
Start-Sleep -Seconds 2
Get-Process -Name 'LevelUpStudioAleph' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 700
Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) '{PRODUCT}.lnk') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $env:APPDATA 'Microsoft/Windows/Start Menu/Programs/{PRODUCT}.lnk') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath '{str(APP_DIRECTORY).replace("'", "''")}' -Recurse -Force -ErrorAction SilentlyContinue
"""
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def main_entry() -> int:
    arguments = sys.argv[1:]
    if arguments and arguments[0] == "--serve":
        return run_dashboard(int(arguments[1]))
    if arguments and arguments[0] == "--uninstall":
        uninstall()
        return 0
    control_window()
    return 0


if __name__ == "__main__":
    raise SystemExit(main_entry())
