"""The export run: preflight, batch loop over the official app, verified copy."""

from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Sequence

from . import haima
from .adb import STATE_HINTS, Adb, AdbError, Device
from .haima import HaimaApp, NeedsUser, Page, UnsafeAction, detect_page, groups, is_empty
from .ledger import Ledger
from .names import DEFAULT_TZ, resolve_tz
from .transfer import WORKDIR, SyncStats, Transfer
from .ui import UiDriver

Log = Callable[[str], None]


@dataclass
class ExportOptions:
    dest: Path
    batch_target: int = 120
    max_batches: Optional[int] = None
    dry_run: bool = False
    keep_awake: bool = True
    tz_name: Optional[str] = None
    remote_dir: str = haima.EXPORT_DIR
    stall_seconds: float = 45.0
    sync_only: bool = False


@dataclass
class RunSummary:
    run_id: int
    status: str
    note: str = ""
    batches: int = 0
    selected: int = 0
    downloaded: int = 0
    sync: SyncStats = field(default_factory=SyncStats)
    seconds: float = 0.0
    dumps: int = 0
    facts: dict = field(default_factory=dict)


class Stopped(Exception):
    pass


class ExportSession:
    def __init__(self, adb: Adb, serial: str, options: ExportOptions, log: Log = print,
                 progress: Optional[Callable[[dict], None]] = None,
                 stop: Optional[threading.Event] = None):
        self.adb = adb
        self.serial = serial
        self.options = options
        self.log = log
        self.progress = progress or (lambda info: None)
        self.stop = stop or threading.Event()
        self.device: Device = adb.device(serial)
        self.ui = UiDriver(self.device)
        self.app = HaimaApp(self.device, self.ui, log=log)

    # ---- checks ------------------------------------------------------------
    def preflight(self) -> dict:
        state = self.device.state()
        if state != "device":
            raise NeedsUser(STATE_HINTS.get(state, "没有连接到手机（状态：%s）。请检查数据线和 USB 调试。" % state))
        facts = {
            "serial": self.serial,
            "model": self.device.getprop("ro.product.model"),
            "brand": self.device.getprop("ro.product.brand"),
            "android": self.device.getprop("ro.build.version.release"),
            "device_tz": self.device.getprop("persist.sys.timezone"),
        }
        version = self.app.installed_version()
        if not version:
            raise NeedsUser("这台手机上没有找到海马爸比 App。")
        facts["app_version"] = version
        facts["app_verified"] = version in haima.VERIFIED_VERSIONS
        if not facts["app_verified"]:
            self.log("提示：海马 App 版本 %s 尚未验证（已验证：%s），遇到不认识的页面会自动暂停。"
                     % (version, "、".join(haima.VERIFIED_VERSIONS)))
        power = self.ui.power()
        if not power.awake:
            self.ui.key("KEYCODE_WAKEUP")
            time.sleep(1.0)
            power = self.ui.power()
        if power.keyguard or not power.awake:
            raise NeedsUser("请先解锁手机并保持亮屏，然后重新开始。")
        tz_name = self.options.tz_name or facts["device_tz"] or DEFAULT_TZ
        resolve_tz(tz_name)
        facts["tz"] = tz_name
        return facts

    def _check_running(self) -> None:
        if self.stop.is_set():
            raise Stopped("已按要求停止。")
        state = self.device.state()
        if state != "device":
            raise NeedsUser("手机连接中断（%s）。重新连接后再次运行即可从断点继续。" % state)
        power = self.ui.power()
        if power.keyguard or not power.awake:
            raise NeedsUser("手机锁屏了。请解锁后再次运行，已保存的照片不会重复下载。")

    @contextlib.contextmanager
    def _keep_awake(self) -> Iterator[None]:
        if not self.options.keep_awake:
            yield
            return
        original = self.device.shell("settings get global stay_on_while_plugged_in", timeout=10, check=False).strip()
        changed = False
        try:
            if original not in ("3", "7", "15"):
                self.device.shell("settings put global stay_on_while_plugged_in 7", timeout=10, check=False)
                changed = True
            yield
        finally:
            if changed:
                value = original if original.isdigit() else "0"
                self.device.shell("settings put global stay_on_while_plugged_in %s" % value, timeout=10, check=False)

    # ---- waiting -------------------------------------------------------------
    def _wait_download(self, transfer: Transfer, before: Sequence[str], expected: int) -> List[str]:
        before_set = set(before)
        target = len(before_set) + expected
        started = time.monotonic()
        deadline = started + 60 + expected * 4
        last_count, last_change = len(before_set), started
        while True:
            time.sleep(0.5)
            count = transfer.remote_count()
            now = time.monotonic()
            if count != last_count:
                last_count, last_change = count, now
                self.progress({"phase": "downloading", "done": count - len(before_set), "expected": expected})
            if count >= target:
                break
            if now - last_change > self.options.stall_seconds:
                self.log("下载停滞：已等待 %.0f 秒没有新文件。" % (now - last_change))
                break
            if now > deadline:
                self.log("下载超时。")
                break
        # Let the last file finish writing: wait until two listings agree.
        previous = None
        for _ in range(10):
            listing = transfer.remote_listing()
            snapshot = {n: v[0] for n, v in listing.items() if n not in before_set}
            if snapshot == previous:
                break
            previous = snapshot
            time.sleep(0.8)
        return sorted(previous or {})

    def _await_refresh(self, dates: Sequence[str], timeout: float = 15.0):
        """After a download, wait until the app leaves edit mode and drops finished groups."""
        deadline = time.monotonic() + timeout
        forced = False
        transient = 0
        while True:
            screen = self.ui.dump()
            page = detect_page(screen)
            if page == Page.UNKNOWN and transient < 8:
                # A short loading dialog while the app saves; wait it out, never press Back here.
                transient += 1
                time.sleep(1.0)
                continue
            if page == Page.MOMENTS_EDIT:
                if time.monotonic() > deadline:
                    screen = self.app.exit_edit(screen)
                    page = detect_page(screen)
                else:
                    time.sleep(1.0)
                    continue
            if page != Page.MOMENTS:
                return self.app.ensure_filter_on(self.app.navigate_to_moments())
            listed = groups(screen)
            loaded = bool(listed) or screen.has_text("暂无内容")
            stale = {g.date for g in listed}.intersection(dates)
            if loaded and not stale:
                return screen
            if not loaded and time.monotonic() <= deadline:
                time.sleep(0.8)  # the list is reloading after the download
                continue
            if time.monotonic() > deadline:
                if forced:
                    return screen  # partial groups legitimately remain (e.g. a failed photo)
                # Force a reload: switch the filter off and on again.
                self.log("列表还没刷新，重新加载一次。")
                self.app.tap_nav(screen, "showSwitch", settle=1.5)
                screen = self.ui.dump()
                self.app.tap_nav(screen, "showSwitch", settle=2.0)
                forced = True
                deadline = time.monotonic() + 6
                continue
            time.sleep(1.0)

    # ---- run -------------------------------------------------------------------
    def run(self) -> RunSummary:
        started = time.monotonic()
        facts = self.preflight()
        dest = self.options.dest.expanduser().resolve()
        dest.mkdir(parents=True, exist_ok=True)
        ledger = Ledger(dest / WORKDIR / "ledger.sqlite3")
        mode = "sync" if self.options.sync_only else ("dry-run" if self.options.dry_run else "export")
        run_id = ledger.start_run(self.serial, facts["model"], facts["app_version"], mode)
        summary = RunSummary(run_id=run_id, status="running", facts=facts)
        transfer = Transfer(self.device, ledger, dest, resolve_tz(facts["tz"]), facts["tz"], self.serial,
                            facts["model"], self.options.remote_dir, log=self.log)
        try:
            with self._keep_awake():
                if not self.options.dry_run:
                    self.log("先保存手机上已有、电脑上还没有的照片……")
                    summary.sync.add(transfer.sync(run_id))
                if not self.options.sync_only:
                    self._batch_loop(transfer, ledger, run_id, summary)
                    if not self.options.dry_run:
                        summary.sync.add(transfer.sync(run_id))
            if summary.status == "running":
                summary.status = "done"
        except NeedsUser as exc:
            summary.status, summary.note = "paused", str(exc)
        except Stopped as exc:
            summary.status, summary.note = "stopped", str(exc)
        except UnsafeAction as exc:
            summary.status, summary.note = "safety-stop", "安全检查拦截了一次点击：%s" % exc
            self._save_screenshot(dest, run_id)
        except AdbError as exc:
            summary.status, summary.note = "error", str(exc)
        finally:
            self._leave_edit_mode()
            summary.seconds = time.monotonic() - started
            summary.dumps = self.ui.dumps
            stats = {
                "batches": summary.batches, "selected": summary.selected, "downloaded": summary.downloaded,
                "saved": summary.sync.saved, "duplicates": summary.sync.duplicates, "failed": summary.sync.failed,
                "seconds": round(summary.seconds, 1), "ui_dumps": summary.dumps,
            }
            ledger.end_run(run_id, summary.status, summary.note, stats)
            from .report import write_report
            write_report(dest, ledger, run_id, summary)
            ledger.close()
        return summary

    def _batch_loop(self, transfer: Transfer, ledger: Ledger, run_id: int, summary: RunSummary) -> None:
        screen = self.app.navigate_to_moments()
        screen = self.app.ensure_filter_on(screen)
        no_progress = 0
        while True:
            if is_empty(screen):
                self.log("待下载列表已清空：当前 App 中的照片已全部下载。")
                return
            if self.options.max_batches is not None and summary.batches >= self.options.max_batches:
                summary.status = "partial"
                summary.note = "已达到本次设定的批次数上限。"
                return
            self._check_running()
            t0 = time.monotonic()
            screen = self.app.enter_edit(screen)
            selection = self.app.select_batch(screen, self.options.batch_target)
            t1 = time.monotonic()
            self.log("第 %d 批：选中 %d 张（%s 至 %s，%d 组）" % (
                summary.batches + 1, selection.count,
                selection.dates[0] if selection.dates else "-", selection.dates[-1] if selection.dates else "-",
                len(selection.dates)))
            if selection.count == 0:
                self.app.exit_edit(selection.screen)
                raise NeedsUser("没能选中任何照片，请确认停在「精彩瞬间」页面。")
            if self.options.dry_run:
                self.app.exit_edit(selection.screen)
                summary.status, summary.note = "dry-run", "试运行：只勾选、不下载。"
                summary.selected += selection.count
                return
            before = transfer.remote_names()
            self.app.tap_download(selection.screen)
            new_names = self._wait_download(transfer, before, selection.count)
            t2 = time.monotonic()
            summary.batches += 1
            summary.selected += selection.count
            summary.downloaded += len(new_names)
            ledger.record_batch(run_id, summary.batches, selection.dates, selection.count, len(new_names),
                                t1 - t0, t2 - t1)
            self.log("第 %d 批：下载 %d/%d 张，用时 %.0f 秒" % (summary.batches, len(new_names), selection.count, t2 - t0))
            if new_names:
                no_progress = 0
                summary.sync.add(transfer.sync(run_id, only=new_names))
            else:
                no_progress += 1
                if no_progress >= 2:
                    raise NeedsUser("连续两批没有下载到新照片，请检查手机网络后重试。")
            self.progress({"phase": "batch", "batches": summary.batches, "downloaded": summary.downloaded,
                           "saved": summary.sync.saved, "last_date": selection.dates[-1] if selection.dates else ""})
            screen = self._await_refresh(selection.dates)

    def _leave_edit_mode(self) -> None:
        try:
            screen = self.ui.dump()
            if detect_page(screen) == Page.MOMENTS_EDIT:
                self.app.exit_edit(screen)
        except Exception:
            pass

    def _save_screenshot(self, dest: Path, run_id: int) -> None:
        try:
            path = dest / WORKDIR / ("safety-stop-run%d.png" % run_id)
            self.ui.screenshot(str(path))
        except Exception:
            pass
