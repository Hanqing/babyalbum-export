"""Install and drive the app on a test phone over adb.

    python3 tools/device_test.py install
    python3 tools/device_test.py enable  --state /tmp/a11y.txt   # remembers the original setting
    python3 tools/device_test.py run --batch 150                 # start and follow the log to the end
    python3 tools/device_test.py disable --state /tmp/a11y.txt   # restores the original setting

While the app runs, nothing here calls uiautomator: a UiAutomation session
suppresses other accessibility services and would stall the export.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = "app.babyalbum.export"
COMPONENT = PKG + "/.ExportService"
FULL_COMPONENT = PKG + "/" + PKG + ".ExportService"


def adb_path() -> str:
    for c in (os.environ.get("ADB"), str(Path.home() / "Library/Android/sdk/platform-tools/adb"), "adb"):
        if c and (c == "adb" or Path(c).exists()):
            return c
    return "adb"


ADB = adb_path()


def adb(*args, check=True, timeout=120) -> str:
    cp = subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout)
    if check and cp.returncode != 0:
        sys.exit("adb %s failed: %s" % (" ".join(args), cp.stderr.strip() or cp.stdout.strip()))
    return cp.stdout


def cmd_install(args):
    apk = ROOT / "build" / "babyalbum-export.apk"
    print(adb("install", "-r", "-g", str(apk), timeout=300).strip())
    adb("shell", "pm", "grant", PKG, "android.permission.READ_MEDIA_IMAGES", check=False)


def cmd_enable(args):
    current = adb("shell", "settings", "get", "secure", "enabled_accessibility_services").strip()
    state = Path(args.state)
    if not state.exists():
        state.write_text(current)
    services = [s for s in current.split(":") if s and s != "null"]
    if not any(s in (COMPONENT, FULL_COMPONENT) for s in services):
        services.append(COMPONENT)
    adb("shell", "settings", "put", "secure", "enabled_accessibility_services", ":".join(services))
    adb("shell", "settings", "put", "secure", "accessibility_enabled", "1")
    time.sleep(2)
    print("enabled:", adb("shell", "settings", "get", "secure", "enabled_accessibility_services").strip())


def cmd_disable(args):
    state = Path(args.state)
    original = state.read_text().strip() if state.exists() else "null"
    if original in ("", "null"):
        adb("shell", "settings", "delete", "secure", "enabled_accessibility_services", check=False)
    else:
        adb("shell", "settings", "put", "secure", "enabled_accessibility_services", original)
    print("restored:", adb("shell", "settings", "get", "secure", "enabled_accessibility_services").strip())


def cmd_run(args):
    adb("logcat", "-c", check=False)
    adb("shell", "am", "start", "-n", PKG + "/.MainActivity", "--ez", "autostart", "true",
        "--ei", "batch", str(args.batch))
    started = time.time()
    proc = subprocess.Popen([ADB, "logcat", "-v", "time", "-s", "AlbumExport:I", "AndroidRuntime:E"],
                            stdout=subprocess.PIPE, text=True)
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if not line or line.startswith("-----"):
                continue
            print("%5.0fs %s" % (time.time() - started, line[19:] if len(line) > 19 else line), flush=True)
            if "finished:" in line or "FATAL" in line:
                break
            if time.time() - started > args.timeout:
                print("timeout")
                break
    finally:
        proc.terminate()
    print("total %.0f s" % (time.time() - started))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install").set_defaults(func=cmd_install)
    for name, fn in (("enable", cmd_enable), ("disable", cmd_disable)):
        s = sub.add_parser(name)
        s.add_argument("--state", required=True)
        s.set_defaults(func=fn)
    r = sub.add_parser("run")
    r.add_argument("--batch", type=int, default=150)
    r.add_argument("--timeout", type=int, default=3600)
    r.set_defaults(func=cmd_run)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
