"""UI hierarchy capture and input injection through uiautomator."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from .adb import AdbError, Device

Bounds = Tuple[int, int, int, int]
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_FOCUS = re.compile(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/([\w.$]+)")


@dataclass(frozen=True)
class Node:
    rid: str
    text: str
    desc: str
    cls: str
    package: str
    bounds: Bounds
    checkable: bool
    checked: bool
    clickable: bool
    enabled: bool
    scrollable: bool

    @property
    def short_id(self) -> str:
        return self.rid.split(":id/", 1)[1] if ":id/" in self.rid else self.rid

    @property
    def center(self) -> Tuple[int, int]:
        l, t, r, b = self.bounds
        return (l + r) // 2, (t + b) // 2

    @property
    def width(self) -> int:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> int:
        return self.bounds[3] - self.bounds[1]

    def contains(self, x: int, y: int) -> bool:
        l, t, r, b = self.bounds
        return l <= x < r and t <= y < b


def _flag(attrs: dict, key: str) -> bool:
    return attrs.get(key) == "true"


class Screen:
    """One parsed uiautomator snapshot."""

    def __init__(self, nodes: List[Node], raw: str = ""):
        self.nodes = nodes
        self.raw = raw

    def by_id(self, short_id: str) -> List[Node]:
        suffix = ":id/" + short_id
        return [n for n in self.nodes if n.rid.endswith(suffix)]

    def first(self, short_id: str) -> Optional[Node]:
        found = self.by_id(short_id)
        return found[0] if found else None

    def by_text(self, text: str) -> List[Node]:
        return [n for n in self.nodes if n.text == text]

    def has_text(self, text: str) -> bool:
        return any(n.text == text for n in self.nodes)

    def texts(self) -> Iterable[str]:
        return (n.text for n in self.nodes if n.text)


def parse_hierarchy(text: str) -> Screen:
    end = text.rfind("</hierarchy>")
    if end < 0:
        raise AdbError("界面结构读取失败：" + text.strip()[:200])
    start = text.find("<?xml")
    if start < 0:
        start = text.find("<hierarchy")
    root = ET.fromstring(text[start:end + len("</hierarchy>")])
    nodes: List[Node] = []
    for el in root.iter("node"):
        a = el.attrib
        m = _BOUNDS.match(a.get("bounds", ""))
        if not m:
            continue
        nodes.append(Node(
            rid=a.get("resource-id", ""),
            text=a.get("text", ""),
            desc=a.get("content-desc", ""),
            cls=a.get("class", ""),
            package=a.get("package", ""),
            bounds=tuple(int(v) for v in m.groups()),  # type: ignore[arg-type]
            checkable=_flag(a, "checkable"),
            checked=_flag(a, "checked"),
            clickable=_flag(a, "clickable"),
            enabled=_flag(a, "enabled"),
            scrollable=_flag(a, "scrollable"),
        ))
    return Screen(nodes, text)


def parse_focus(dumpsys_window: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (package, activity) of the focused window, if any."""
    found = _FOCUS.findall(dumpsys_window)
    if not found:
        return None, None
    return found[-1]


@dataclass
class PowerState:
    awake: bool
    keyguard: bool


def parse_power(dumpsys_power: str, dumpsys_window: str) -> PowerState:
    awake = "mWakefulness=Awake" in dumpsys_power
    keyguard = bool(re.search(r"isKeyguardShowing=true|mDreamingLockscreen=true|mShowingLockscreen=true", dumpsys_window))
    return PowerState(awake=awake, keyguard=keyguard)


class UiDriver:
    """Reads the screen and injects input on one device."""

    def __init__(self, device: Device):
        self.device = device
        self.dumps = 0
        self.dump_seconds = 0.0

    def dump(self, retries: int = 4) -> Screen:
        last_error: Optional[Exception] = None
        for attempt in range(retries):
            started = time.monotonic()
            try:
                raw = self.device.exec_out(["uiautomator", "dump", "/dev/tty"], timeout=40)
                text = raw.decode("utf-8", "replace")
                screen = parse_hierarchy(text)
                self.dumps += 1
                self.dump_seconds += time.monotonic() - started
                return screen
            except (AdbError, ET.ParseError) as exc:  # e.g. "could not get idle state"
                last_error = exc
                time.sleep(0.6 * (attempt + 1))
        raise AdbError("连续多次读取界面失败：%s" % last_error)

    def focus(self) -> Tuple[Optional[str], Optional[str]]:
        out = self.device.shell("dumpsys window | grep mCurrentFocus", timeout=15, check=False)
        return parse_focus(out)

    def power(self) -> PowerState:
        power = self.device.shell("dumpsys power | grep mWakefulness=", timeout=15, check=False)
        window = self.device.shell(
            "dumpsys window | grep -E 'isKeyguardShowing|mDreamingLockscreen|mShowingLockscreen'",
            timeout=15, check=False)
        return parse_power(power, window)

    def tap(self, x: int, y: int) -> None:
        self.device.shell("input tap %d %d" % (x, y), timeout=15)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int) -> None:
        self.device.shell("input swipe %d %d %d %d %d" % (x1, y1, x2, y2, ms), timeout=20)

    def key(self, keycode: str) -> None:
        self.device.shell("input keyevent " + keycode, timeout=15)

    def screenshot(self, path: str) -> None:
        data = self.device.exec_out(["screencap", "-p"], timeout=30)
        with open(path, "wb") as fh:
            fh.write(data)
