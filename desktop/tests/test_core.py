"""Unit tests that need no phone. Run: python3 -m unittest discover -s tests"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from album_export import jpeg  # noqa: E402
from album_export.adb import parse_devices, quote  # noqa: E402
from album_export.haima import (HaimaApp, Page, UnsafeAction, detect_page, edit_band, groups,  # noqa: E402
                                is_empty, selected_count, filter_on)
from album_export.ledger import Ledger  # noqa: E402
from album_export.names import capture_time, offset_text, parse_name, resolve_tz  # noqa: E402
from album_export.transfer import Transfer  # noqa: E402
from album_export.ui import parse_focus, parse_hierarchy, parse_power  # noqa: E402

FIX = HERE / "fixtures"
REAL_PHOTO_DIR = Path.home() / "Pictures" / "海马爸比时光相册_本次导出" / "海马爸比"


def screen(name):
    return parse_hierarchy((FIX / name).read_text(encoding="utf-8"))


class AdbParsing(unittest.TestCase):
    def test_tabs_spaces_and_states(self):
        out = ("* daemon not running; starting now at tcp:5037\n* daemon started successfully\n"
               "List of devices attached\n"
               "0123456789ABCDEF        device usb:2-1.2.3 product:PD2133 model:V2133A device:PD2133 transport_id:10\n"
               "emulator-5554\tdevice product:sdk model:sdk_phone\n"
               "ABC123\tunauthorized usb:1-1 transport_id:3\n"
               "XYZ  offline\n"
               "QQQ\tno permissions (user in plugdev group); see [http://developer.android.com/tools/device.html] usb:3-1\n")
        entries = parse_devices(out)
        self.assertEqual([e.serial for e in entries], ["0123456789ABCDEF", "emulator-5554", "ABC123", "XYZ", "QQQ"])
        self.assertEqual(entries[0].state, "device")
        self.assertEqual(entries[0].model, "V2133A")
        self.assertEqual(entries[2].state, "unauthorized")
        self.assertEqual(entries[3].state, "offline")
        self.assertEqual(entries[4].state, "no permissions")

    def test_quote(self):
        self.assertEqual(quote("/sdcard/DCIM/Camera/海马爸比"), "'/sdcard/DCIM/Camera/海马爸比'")
        self.assertEqual(quote("a'b"), "'a'\\''b'")


class UiParsing(unittest.TestCase):
    def test_focus(self):
        text = ("  mCurrentFocus=null\n  mFocusedApp=null\n"
                "  mCurrentFocus=Window{e34b59b u0 com.simshine.cn.aiothd/com.simshine.device.activity.WonderfulMomentActivity type=1}\n")
        self.assertEqual(parse_focus(text), ("com.simshine.cn.aiothd", "com.simshine.device.activity.WonderfulMomentActivity"))
        self.assertEqual(parse_focus("mCurrentFocus=null"), (None, None))

    def test_power(self):
        p = parse_power("  mWakefulness=Awake\n", "    isKeyguardShowing=false\n mDreamingLockscreen=false")
        self.assertTrue(p.awake)
        self.assertFalse(p.keyguard)
        p = parse_power("  mWakefulness=Asleep\n", "    isKeyguardShowing=true\n")
        self.assertFalse(p.awake)
        self.assertTrue(p.keyguard)

    def test_trailing_banner_is_ignored(self):
        raw = (FIX / "moments_empty.xml").read_text(encoding="utf-8").strip() + "UI hierchary dumped to: /dev/tty"
        self.assertTrue(parse_hierarchy(raw).has_text("暂无内容"))


class PageDetection(unittest.TestCase):
    def test_pages(self):
        expected = {
            "home.xml": Page.HOME, "device.xml": Page.DEVICE, "album.xml": Page.ALBUM,
            "album_menu.xml": Page.ALBUM_MENU, "moments_all.xml": Page.MOMENTS,
            "moments_filter_on.xml": Page.MOMENTS, "moments_edit.xml": Page.MOMENTS_EDIT,
            "moments_edit_selected1.xml": Page.MOMENTS_EDIT, "moments_empty.xml": Page.MOMENTS,
        }
        for name, page in expected.items():
            with self.subTest(name=name):
                self.assertEqual(detect_page(screen(name)), page)

    def test_filter_and_empty(self):
        self.assertFalse(filter_on(screen("moments_all.xml")))
        self.assertTrue(filter_on(screen("moments_filter_on.xml")))
        self.assertTrue(is_empty(screen("moments_empty.xml")))
        self.assertFalse(is_empty(screen("moments_filter_on.xml")))

    def test_groups_and_count(self):
        s = screen("moments_edit.xml")
        g = groups(s)
        self.assertEqual(g[0].date, "2026-10-25")
        self.assertIsNotNone(g[0].checkbox)
        self.assertFalse(g[0].checked)
        self.assertEqual(selected_count(s), 0)
        self.assertEqual(edit_band(s), (246, 2214))
        s1 = screen("moments_edit_selected1.xml")
        self.assertTrue(groups(s1)[0].checked)
        self.assertEqual(selected_count(s1), 1)

    def test_all_photos_headers(self):
        dates = [g.date for g in groups(screen("moments_all.xml"))]
        self.assertEqual(dates[:3], ["2026-10-25", "2026-10-24", "2026-10-22"])


class FakeUi:
    def __init__(self):
        self.taps = []

    def tap(self, x, y):
        self.taps.append((x, y))


class Safety(unittest.TestCase):
    def setUp(self):
        self.ui = FakeUi()
        self.app = HaimaApp(device=None, ui=self.ui, log=lambda m: None, pause=0)

    def test_download_tap_is_far_from_delete(self):
        s = screen("moments_edit_selected1.xml")
        self.app.tap_download(s)
        (x, y), = self.ui.taps
        delete = s.first("delete_text")
        download = s.first("download_text")
        self.assertTrue(download.contains(x, y))
        self.assertFalse(delete.contains(x, y))
        self.assertGreater(x - delete.bounds[2], 200)

    def test_refuses_forbidden_and_occluded(self):
        s = screen("moments_edit.xml")
        g = groups(s)[0]
        with self.assertRaises(UnsafeAction):
            self.app.tap_group(s, g, band=(g.checkbox.bounds[1] + 10, 2214))  # header under the title bar
        with self.assertRaises(UnsafeAction):
            self.app.tap_nav(s, "delete_text")  # not on the navigation whitelist
        self.assertEqual(self.ui.taps, [])

    def test_missing_nav_control_is_retryable(self):
        s = screen("moments_edit.xml")  # edit mode: the 编辑 button is not on screen
        self.assertFalse(self.app.tap_nav(s, "tv_media_library_edit", settle=0))
        self.assertEqual(self.ui.taps, [])

    def test_group_tap_hits_checkbox(self):
        s = screen("moments_edit.xml")
        g = groups(s)[0]
        self.app.tap_group(s, g, band=edit_band(s))
        self.assertEqual(self.ui.taps, [g.checkbox.center])


class Names(unittest.TestCase):
    def test_parse(self):
        a = parse_name("1792826431.jpg")
        self.assertEqual((a.epoch, a.night, a.dup_index), (1792826431, False, None))
        b = parse_name("1763676650_NGT.jpg")
        self.assertTrue(b.night)
        c = parse_name("1792912420 (1).jpg")
        self.assertEqual((c.dup_index, c.canonical), (1, "1792912420.jpg"))
        self.assertIsNone(parse_name("IMG_0001.jpg"))

    def test_timezones(self):
        info = parse_name("1792826431.jpg")
        sh = capture_time(info, resolve_tz("Asia/Shanghai"))
        self.assertEqual(sh.strftime("%Y-%m-%d %H:%M:%S"), "2026-10-24 15:20:31")
        self.assertEqual(offset_text(sh), "+08:00")
        fixed = capture_time(info, resolve_tz("+08:00"))
        self.assertEqual(fixed.strftime("%Y-%m-%d %H:%M:%S"), "2026-10-24 15:20:31")
        la = capture_time(info, resolve_tz("America/Los_Angeles"))
        self.assertEqual(la.strftime("%Y-%m-%d"), "2026-10-24")
        self.assertEqual(sh - la, timedelta(0))  # same instant, different wall clock


def sample_jpeg() -> bytes:
    candidates = sorted(REAL_PHOTO_DIR.glob("*.jpg"))[:1] if REAL_PHOTO_DIR.is_dir() else []
    if candidates:
        return candidates[0].read_bytes()
    # Tiny synthetic baseline JPEG skeleton (structure only).
    sof = b"\xff\xc0\x00\x11\x08\x00\x10\x00\x10\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    sos = b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00" + b"\x12\x34"
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof + sos + b"\xff\xd9"


class Jpeg(unittest.TestCase):
    def test_inspect_and_broken(self):
        data = sample_jpeg()
        info = jpeg.inspect(data)
        self.assertTrue(info.ok, info.reason)
        self.assertFalse(info.has_exif)
        self.assertFalse(jpeg.inspect(data[: len(data) // 2]).ok)
        self.assertFalse(jpeg.inspect(b"not a jpeg").ok)

    def test_exif_round_trip(self):
        data = sample_jpeg()
        stamped = jpeg.with_exif_date(data, "2026:10:24 15:20:31", "+08:00", "time from file name 1792826431.jpg")
        self.assertIsNotNone(stamped)
        info = jpeg.inspect(stamped)
        self.assertTrue(info.ok and info.has_exif)
        self.assertEqual(stamped[: 20], data[: 20])  # JFIF header kept first
        self.assertIsNone(jpeg.with_exif_date(stamped, "2026:10:24 15:20:31", "+08:00"))
        dates = jpeg.read_exif_dates(stamped)
        self.assertEqual(dates["DateTimeOriginal"], "2026:10:24 15:20:31")
        self.assertEqual(dates["OffsetTimeOriginal"], "+08:00")
        self.assertEqual(dates["DateTime"], "2026:10:24 15:20:31")
        self.assertEqual(jpeg.read_exif_dates(data), {})


class FakeDevice:
    """Serves a local folder as if it were the phone's export directory."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.serial = "FAKE"

    def shell(self, cmd, timeout=60, check=True):
        if "| wc -l" in cmd:
            return "%d\n" % len(list(self.folder.iterdir()))
        if cmd.startswith("ls "):
            return "".join(p.name + "\n" for p in sorted(self.folder.iterdir()))
        if "stat -c" in cmd:
            return "".join("%d|%d|%s\n" % (p.stat().st_size, int(p.stat().st_mtime), p.name)
                           for p in sorted(self.folder.iterdir()))
        if "sha256sum" in cmd:
            names = re.findall(r"'((?:[^']|'\\'')*)'", cmd.split("sha256sum --", 1)[1])
            out = ""
            for n in names:
                n = n.replace("'\\''", "'")
                out += "%s  %s\n" % (hashlib.sha256((self.folder / n).read_bytes()).hexdigest(), n)
            return out
        raise AssertionError("unexpected command: " + cmd)

    def pull(self, remote_paths, local_dir, timeout=600):
        for r in remote_paths:
            src = self.folder / r.rsplit("/", 1)[1]
            shutil.copy2(str(src), local_dir)


class TransferFlow(unittest.TestCase):
    def test_sync_verify_dedupe_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            phone, dest = tmp / "phone", tmp / "dest"
            phone.mkdir()
            good = sample_jpeg()
            (phone / "1792826431.jpg").write_bytes(good)
            (phone / "1792826431 (1).jpg").write_bytes(good)  # the app's duplicate download
            other = bytearray(good)
            other[-8] ^= 0x01  # different bytes, still a valid structure
            (phone / "1763676650_NGT.jpg").write_bytes(bytes(other))
            (phone / "1700000000.jpg").write_bytes(good[: len(good) // 3])  # truncated download
            ledger = Ledger(dest / ".album-export" / "ledger.sqlite3")
            t = Transfer(FakeDevice(phone), ledger, dest, resolve_tz("Asia/Shanghai"), "Asia/Shanghai",
                         "FAKE", "Fake", "/sdcard/DCIM/Camera/海马爸比", log=lambda m: None)
            stats = t.sync(run_id=1)
            self.assertEqual((stats.saved, stats.duplicates, stats.failed), (2, 1, 1))
            saved = sorted(p.name for p in (dest / "原始文件").iterdir())
            self.assertEqual(saved, ["1763676650_NGT.jpg", "1792826431.jpg"])
            self.assertTrue((dest / ".album-export" / "quarantine" / "1700000000.jpg").exists())
            row = ledger.has_sha(hashlib.sha256(good).hexdigest())
            self.assertEqual(row["captured_local"], "2026-10-24 15:20:31")
            self.assertEqual(row["night"], 0)
            again = t.sync(run_id=2)
            self.assertEqual((again.saved, again.duplicates), (0, 0))
            self.assertEqual(again.candidates, 1)  # only the failed file is retried
            ledger.close()

            from album_export.archive import build_archive
            result = build_archive(dest, log=lambda m: None)
            self.assertEqual(result["made"], 2)
            day = dest / "按月份" / "2026" / "2026-10" / "2026-10-24_152031.jpg"
            night = dest / "按月份" / "2025" / "2025-11" / "2025-11-21_061050_夜间.jpg"
            self.assertTrue(day.exists())
            self.assertTrue(night.exists())
            dates = jpeg.read_exif_dates(day.read_bytes())
            self.assertEqual((dates["DateTimeOriginal"], dates["OffsetTimeOriginal"]), ("2026:10:24 15:20:31", "+08:00"))
            self.assertEqual(int(day.stat().st_mtime), 1792826431)
            self.assertEqual(build_archive(dest, log=lambda m: None)["made"], 0)  # idempotent


class GuiServer(unittest.TestCase):
    def test_token_required(self):
        import json
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        from album_export.gui import App, make_handler

        app = App(adb_path=None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, "secret-token"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % server.server_address[1]
        try:
            page = urllib.request.urlopen(base + "/?t=secret-token").read().decode("utf-8")
            self.assertIn("相册导出助手", page)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + "/api/state")
            self.assertEqual(ctx.exception.code, 403)
            req = urllib.request.Request(base + "/api/stop", data=b"{}", method="POST",
                                         headers={"X-Token": "wrong", "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(req)
            self.assertFalse(app.stop_event.is_set())
            ok = urllib.request.Request(base + "/api/state", headers={"X-Token": "secret-token"})
            state = json.loads(urllib.request.urlopen(ok).read())
            self.assertFalse(state["running"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
