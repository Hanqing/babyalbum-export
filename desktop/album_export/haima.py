"""Adapter for the official Haima app (com.simshine.cn.aiothd).

Verified against app version 3.4.2 on Android 14. The adapter only performs
whitelisted actions on recognised pages; anything unexpected pauses the run.

Path to the batch-download page:
  home (进入设备) -> device page (相册卡片) -> 时光相册 (右上角) -> 全部照片 -> 精彩瞬间
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from .adb import Device, quote
from .ui import Node, Screen, UiDriver

PKG = "com.simshine.cn.aiothd"
VERIFIED_VERSIONS = ("3.4.2",)
EXPORT_DIR = "/sdcard/DCIM/Camera/海马爸比"

# Text that must never be tapped, whatever page we think we are on.
FORBIDDEN_WORDS = ("删除", "清空", "移除", "解绑", "注销", "退出登录", "格式化")

# The only resource ids navigation is allowed to tap.
NAV_TARGETS = (
    "enter_device_detail", "rl_picture", "iv_title_right", "allImage",
    "showSwitch", "tv_media_library_edit", "iv_media_library_close", "homeTabLayout",
)


class Page(Enum):
    HOME = "首页"
    HOME_OTHER_TAB = "首页的其他标签"
    DEVICE = "设备页"
    ALBUM = "时光相册"
    ALBUM_MENU = "时光相册菜单"
    MOMENTS = "精彩瞬间"
    MOMENTS_EDIT = "精彩瞬间（编辑中）"
    OTHER_APP = "其他 App"
    UNKNOWN = "未知页面"


class NeedsUser(Exception):
    """Stop and ask the person to do something on the phone."""


class UnsafeAction(Exception):
    """A tap was refused by the safety checks."""


@dataclass
class Group:
    date: str
    header: Node
    checkbox: Optional[Node]

    @property
    def checked(self) -> bool:
        return bool(self.checkbox and self.checkbox.checked)


@dataclass
class Selection:
    count: int
    dates: List[str]
    screen: Screen
    rounds: int
    reached_end: bool


def app_package(screen: Screen) -> Optional[str]:
    """Package that owns most nodes in this snapshot."""
    counts: Dict[str, int] = {}
    for n in screen.nodes:
        if n.package:
            counts[n.package] = counts.get(n.package, 0) + 1
    return max(counts, key=counts.get) if counts else None


def detect_page(screen: Screen) -> Page:
    if app_package(screen) != PKG:
        return Page.OTHER_APP
    if screen.first("tv_media_library_selected") and screen.first("download_text"):
        return Page.MOMENTS_EDIT
    if screen.first("showSwitch") and screen.first("tv_media_library_edit"):
        return Page.MOMENTS
    if screen.first("allImage"):
        return Page.ALBUM_MENU
    title = screen.first("tv_title_middle")
    if title and title.text == "时光相册" and screen.first("iv_title_right"):
        return Page.ALBUM
    if screen.first("rl_picture") and screen.first("main_rb_device"):
        return Page.DEVICE
    if screen.first("enter_device_detail"):
        return Page.HOME
    if screen.first("homeTabLayout") and screen.first("myTabLayout"):
        return Page.HOME_OTHER_TAB
    return Page.UNKNOWN


def groups(screen: Screen) -> List[Group]:
    """Date headers in the grid, paired with their select-all checkbox."""
    boxes = screen.by_id("days_checkbox")
    out: List[Group] = []
    for header in screen.by_id("timeTV"):
        top, bottom = header.bounds[1], header.bounds[3]
        box = next((b for b in boxes if top <= b.center[1] <= bottom), None)
        out.append(Group(date=header.text.strip(), header=header, checkbox=box))
    return out


def selected_count(screen: Screen) -> Optional[int]:
    node = screen.first("tv_media_library_selected")
    if not node:
        return None
    m = re.search(r"(\d+)", node.text)
    return int(m.group(1)) if m else None


def edit_band(screen: Screen) -> Tuple[int, int]:
    """Vertical band where list items can be tapped without touching bars."""
    title = screen.first("constrain_layout_title") or screen.first("tv_media_library_selected")
    bar = screen.first("bottom_edit_area") or screen.first("download_text")
    top = title.bounds[3] if title else 246
    bottom = bar.bounds[1] if bar else 2214
    return top, bottom


def is_empty(screen: Screen) -> bool:
    return detect_page(screen) == Page.MOMENTS and not groups(screen) and screen.has_text("暂无内容")


def filter_on(screen: Screen) -> Optional[bool]:
    switch = screen.first("showSwitch")
    return None if switch is None else switch.checked


class HaimaApp:
    def __init__(self, device: Device, ui: UiDriver, log: Callable[[str], None] = print,
                 pause: float = 1.0):
        self.device = device
        self.ui = ui
        self.log = log
        self.pause = pause  # base settle time after navigation taps
        self.verbose = False

    def debug(self, msg: str) -> None:
        if self.verbose:
            self.log("  · " + msg)

    # ----- facts -------------------------------------------------------
    def installed_version(self) -> Optional[str]:
        out = self.device.shell("dumpsys package %s | grep versionName" % PKG, timeout=20, check=False)
        m = re.search(r"versionName=(\S+)", out)
        return m.group(1) if m else None

    def launch(self) -> None:
        self.device.shell("monkey -p %s -c android.intent.category.LAUNCHER 1" % PKG, timeout=20, check=False)

    def snapshot(self) -> Tuple[Page, Screen]:
        screen = self.ui.dump()
        return detect_page(screen), screen

    # ----- guarded input -----------------------------------------------
    def _check_node(self, screen: Screen, node: Node) -> None:
        if node.package and node.package != PKG:
            raise UnsafeAction("目标控件不属于海马 App：%s" % node.package)
        label = node.text + node.desc
        if any(word in label for word in FORBIDDEN_WORDS):
            raise UnsafeAction("拒绝点击危险控件：%s" % label)
        for danger in screen.by_id("delete_text"):
            if danger.contains(*node.center):
                raise UnsafeAction("目标落在“删除”按钮范围内")

    def tap_nav(self, screen: Screen, short_id: str, settle: Optional[float] = None) -> bool:
        """Tap a whitelisted control. Returns False when it is momentarily absent
        (the page is reloading); the caller re-reads the screen and decides again."""
        if short_id not in NAV_TARGETS:
            raise UnsafeAction("不在导航白名单内：%s" % short_id)
        node = screen.first(short_id)
        if node is None:
            time.sleep(0.8)
            return False
        self._check_node(screen, node)
        self.ui.tap(*node.center)
        time.sleep(self.pause if settle is None else settle)
        return True

    def tap_group(self, screen: Screen, group: Group, band: Tuple[int, int]) -> None:
        box = group.checkbox
        if box is None or not box.rid.endswith(":id/days_checkbox"):
            raise UnsafeAction("日期勾选框缺失：%s" % group.date)
        top, bottom = band
        if box.bounds[1] < top + 4 or box.bounds[3] > bottom - 4:
            raise UnsafeAction("勾选框被遮挡，跳过：%s" % group.date)
        self._check_node(screen, box)
        self.ui.tap(*box.center)

    def tap_download(self, screen: Screen) -> None:
        download = screen.first("download_text")
        delete = screen.first("delete_text")
        if download is None or download.text.strip() != "下载":
            raise UnsafeAction("没有找到“下载”按钮")
        if delete is not None and delete.text.strip() != "删除":
            raise UnsafeAction("底部按钮布局与已验证版本不同")
        x = download.bounds[2] - download.width // 4
        y = download.center[1]
        if not download.contains(x, y):
            raise UnsafeAction("下载点击位置无效")
        if delete is not None:
            if delete.contains(x, y) or abs(x - delete.center[0]) < 200:
                raise UnsafeAction("下载点击位置离“删除”太近")
        self.ui.tap(x, y)

    def drag_up(self, band: Tuple[int, int]) -> None:
        """Scroll the grid one page without a fling, so no header is skipped."""
        top, bottom = band
        x = 300
        y1, y2 = bottom - 120, top + 160
        steps = 6
        moves = "; ".join(
            "input motionevent MOVE %d %d" % (x, y1 + (y2 - y1) * i // steps) for i in range(1, steps + 1))
        cmd = ("input motionevent DOWN %d %d; %s; sleep 0.25; input motionevent MOVE %d %d; "
               "input motionevent UP %d %d") % (x, y1, moves, x, y2, x, y2)
        self.device.shell(cmd, timeout=30)
        time.sleep(0.35)

    # ----- navigation --------------------------------------------------
    def navigate_to_moments(self, max_steps: int = 14) -> Screen:
        unknown_backs = 0
        unknown_waits = 0
        launched = False
        for _ in range(max_steps):
            page, screen = self.snapshot()
            self.log("当前页面：%s" % page.value)
            if page == Page.MOMENTS:
                return screen
            if page == Page.MOMENTS_EDIT:
                self.tap_nav(screen, "iv_media_library_close")
            elif page == Page.ALBUM_MENU:
                self.tap_nav(screen, "allImage", settle=2.5)
            elif page == Page.ALBUM:
                self.tap_nav(screen, "iv_title_right")
            elif page == Page.DEVICE:
                self.tap_nav(screen, "rl_picture", settle=2.5)
            elif page == Page.HOME:
                self.tap_nav(screen, "enter_device_detail", settle=3.0)
            elif page == Page.HOME_OTHER_TAB:
                self.tap_nav(screen, "homeTabLayout")
            elif page == Page.OTHER_APP:
                if launched:
                    raise NeedsUser("海马 App 没有停留在前台。请打开海马 App 后重试。")
                self.launch()
                launched = True
                time.sleep(4)
            else:
                if unknown_waits < 2:
                    unknown_waits += 1  # loading dialogs and toasts usually clear by themselves
                    time.sleep(1.5)
                    continue
                if unknown_backs >= 3:
                    raise NeedsUser("海马 App 停在一个不认识的页面。请手动回到首页后重试。")
                self.ui.key("KEYCODE_BACK")
                unknown_backs += 1
                unknown_waits = 0
                time.sleep(1.5)
        raise NeedsUser("没能自动进入「精彩瞬间」页面，请手动打开：时光相册 → 右上角 → 全部照片。")

    def ensure_filter_on(self, screen: Screen) -> Screen:
        for _ in range(3):
            state = filter_on(screen)
            if state is True:
                return screen
            if state is None:
                raise NeedsUser("没有找到“仅显示未下载的文件”开关。")
            self.tap_nav(screen, "showSwitch", settle=2.0)
            screen = self.ui.dump()
        raise NeedsUser("无法打开“仅显示未下载的文件”。")

    def enter_edit(self, screen: Screen) -> Screen:
        for _ in range(6):
            if detect_page(screen) == Page.MOMENTS_EDIT:
                return screen
            self.tap_nav(screen, "tv_media_library_edit", settle=1.2)
            screen = self.ui.dump()
        raise NeedsUser("无法进入编辑模式。")

    def exit_edit(self, screen: Screen) -> Screen:
        for _ in range(3):
            if detect_page(screen) != Page.MOMENTS_EDIT:
                return screen
            self.tap_nav(screen, "iv_media_library_close", settle=1.2)
            screen = self.ui.dump()
        raise NeedsUser("无法退出编辑模式。")

    # ----- selection -----------------------------------------------------
    @staticmethod
    def at_top(screen: Screen) -> bool:
        """The newest group header sits whole at the top edge of the list."""
        gs = groups(screen)
        if not gs:
            return True
        top, _ = edit_band(screen) if detect_page(screen) == Page.MOMENTS_EDIT else (
            (screen.first("recyclerView").bounds[1] if screen.first("recyclerView") else 366), 0)
        header = gs[0].header
        return abs(header.bounds[1] - top) <= 2 and header.height >= 110

    def scroll_to_top(self, screen: Screen, max_rounds: int = 12) -> Screen:
        """The app keeps the old scroll position; fling back to the newest groups."""
        for _ in range(max_rounds):
            if self.at_top(screen):
                return screen
            top, bottom = edit_band(screen)
            y1, y2 = top + 150, bottom - 150
            fling = "input swipe 300 %d 300 %d 60" % (y1, y2)
            self.device.shell("; ".join([fling, "sleep 0.15"] * 4), timeout=30)
            time.sleep(0.8)
            screen = self.ui.dump()
        raise NeedsUser("无法回到列表顶部。")

    @staticmethod
    def _tappable(screen: Screen, attempts: Dict[str, int]) -> List[Group]:
        top, bottom = edit_band(screen)
        return [g for g in groups(screen) if g.checkbox is not None and not g.checked
                and g.checkbox.bounds[1] >= top + 4 and g.checkbox.bounds[3] <= bottom - 4
                and attempts.get(g.date, 0) < 2]

    def select_batch(self, screen: Screen, target: int, max_rounds: int = 80) -> Selection:
        """Tick whole date groups, newest first, scrolling down, until `target` photos are selected.

        One screen read per round: tap the visible boxes, scroll one page, read.
        A missed tap is harmless: the group stays in the not-downloaded list and
        is picked up by the next batch.
        """
        attempts: Dict[str, int] = {}
        state: Dict[str, bool] = {}  # date -> believed selected (insertion order = list order)
        screen = self.scroll_to_top(screen)

        def note(s: Screen) -> None:
            for g in groups(s):
                if g.checkbox is not None:
                    state[g.date] = g.checked

        note(screen)
        reached_end = False
        rounds = 0
        for rounds in range(1, max_rounds + 1):
            if detect_page(screen) != Page.MOMENTS_EDIT:
                raise NeedsUser("选择照片时离开了编辑模式。")
            if (selected_count(screen) or 0) >= target:
                break
            band = edit_band(screen)
            pending = self._tappable(screen, attempts)
            self.debug("第%d轮 已选%s 可见%s 点%s" % (
                rounds, selected_count(screen),
                [(g.date[5:], g.header.bounds[1], "√" if g.checked else "") for g in groups(screen)],
                [g.date[5:] for g in pending]))
            for g in pending:
                self.tap_group(screen, g, band)
                attempts[g.date] = attempts.get(g.date, 0) + 1
                state[g.date] = True
                time.sleep(0.12)
            before = groups(screen)
            marker = (before[-1].date, before[-1].header.bounds[1]) if before else None
            if pending:
                time.sleep(0.2)
            self.drag_up(band)
            screen = self.ui.dump()
            note(screen)
            after = groups(screen)
            moved = not after or marker is None or (after[-1].date, after[-1].header.bounds[1]) != marker
            if not moved and not self._tappable(screen, attempts):
                reached_end = True
                break
        note(screen)
        chosen = [d for d, on in state.items() if on]
        return Selection(count=selected_count(screen) or 0, dates=chosen, screen=screen,
                         rounds=rounds, reached_end=reached_end)
