"""ADB discovery and per-device command helpers.

Every command is bound to one serial, so a second phone plugged in later can
never receive taps meant for the first one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence


class AdbError(RuntimeError):
    """An adb invocation failed or timed out."""


def quote(arg: str) -> str:
    """Single-quote one argument for the device's /system/bin/sh."""
    return "'" + arg.replace("'", "'\\''") + "'"


def find_adb(explicit: Optional[str] = None) -> str:
    """Locate an adb binary: explicit path, $ADB, bundled copy, PATH, SDK dirs."""
    exe = "adb.exe" if os.name == "nt" else "adb"
    candidates: List[Optional[str]] = [explicit, os.environ.get("ADB")]
    bundled = Path(__file__).resolve().parent.parent / "platform-tools" / exe
    candidates.append(str(bundled))
    candidates.append(shutil.which("adb"))
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk = os.environ.get(env)
        if sdk:
            candidates.append(str(Path(sdk) / "platform-tools" / exe))
    home = Path.home()
    if sys.platform == "darwin":
        candidates.append(str(home / "Library/Android/sdk/platform-tools/adb"))
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(str(Path(local) / "Android/Sdk/platform-tools/adb.exe"))
    else:
        candidates.append(str(home / "Android/Sdk/platform-tools/adb"))
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return c
    raise AdbError("找不到 adb。请安装 Android platform-tools，或用 --adb 指定路径。")


@dataclass
class DeviceEntry:
    serial: str
    state: str
    props: Dict[str, str] = field(default_factory=dict)

    @property
    def model(self) -> str:
        return self.props.get("model", "")


def parse_devices(output: str) -> List[DeviceEntry]:
    """Parse `adb devices -l`.

    Columns are separated by any whitespace (tabs or runs of spaces), which is
    what broke the first prototype's connection monitor.
    """
    entries: List[DeviceEntry] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state, rest = parts[0], parts[1], parts[2:]
        if state == "no" and rest[:1] == ["permissions"]:
            state = "no permissions"
        props: Dict[str, str] = {}
        for token in rest:
            if ":" in token:
                key, value = token.split(":", 1)
                props[key] = value
        entries.append(DeviceEntry(serial, state, props))
    return entries


STATE_HINTS = {
    "unauthorized": "手机上弹出了“允许 USB 调试吗？”，请勾选“始终允许”后点“允许”。",
    "offline": "连接处于离线状态：请拔插数据线，或在手机上关闭再打开 USB 调试。",
    "no permissions": "电脑没有权限访问这台手机（Linux 需配置 udev 规则）。",
    "recovery": "手机处于恢复模式，请正常开机。",
}


class Adb:
    """Server-level commands (not bound to a device)."""

    def __init__(self, path: Optional[str] = None):
        self.path = find_adb(path)

    def devices(self) -> List[DeviceEntry]:
        cp = subprocess.run([self.path, "devices", "-l"], capture_output=True, timeout=20)
        return parse_devices(cp.stdout.decode("utf-8", "replace"))

    def device(self, serial: str) -> "Device":
        return Device(self.path, serial)


class Device:
    """Commands addressed to exactly one device serial."""

    def __init__(self, adb_path: str, serial: str):
        self.adb_path = adb_path
        self.serial = serial

    def run(self, args: Sequence[str], timeout: float = 60, check: bool = True) -> subprocess.CompletedProcess:
        cmd = [self.adb_path, "-s", self.serial, *args]
        try:
            cp = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise AdbError("adb 命令超时：" + " ".join(args)[:200]) from exc
        if check and cp.returncode != 0:
            err = (cp.stderr or b"").decode("utf-8", "replace").strip()
            out = (cp.stdout or b"").decode("utf-8", "replace").strip()
            raise AdbError("adb 命令失败（%s）：%s：%s" % (cp.returncode, " ".join(args)[:200], (err or out)[:400]))
        return cp

    def shell(self, command: str, timeout: float = 60, check: bool = True) -> str:
        cp = self.run(["shell", command], timeout=timeout, check=check)
        return cp.stdout.decode("utf-8", "replace")

    def exec_out(self, args: Sequence[str], timeout: float = 60) -> bytes:
        return self.run(["exec-out", *args], timeout=timeout).stdout

    def state(self) -> str:
        cp = self.run(["get-state"], timeout=10, check=False)
        text = (cp.stdout or b"").decode().strip()
        if cp.returncode == 0 and text:
            return text
        err = (cp.stderr or b"").decode("utf-8", "replace")
        for key in ("unauthorized", "offline", "no permissions"):
            if key in err:
                return key
        return "missing"

    def getprop(self, name: str) -> str:
        return self.shell("getprop " + quote(name), timeout=10).strip()

    def pull(self, remote_paths: Sequence[str], local_dir: str, timeout: float = 600) -> None:
        """Pull several files into one local directory, preserving mtimes (-a)."""
        if not remote_paths:
            return
        self.run(["pull", "-a", *remote_paths, local_dir], timeout=timeout)
