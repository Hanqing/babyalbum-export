"""Command line: doctor / export / sync / archive / report."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import __version__, haima
from .adb import STATE_HINTS, Adb, AdbError, quote
from .haima import NeedsUser, detect_page
from .session import ExportOptions, ExportSession

EXIT = {"done": 0, "dry-run": 0, "partial": 0, "paused": 2, "stopped": 2, "safety-stop": 3, "error": 1}


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def pick_serial(adb: Adb, wanted: Optional[str]) -> str:
    entries = adb.devices()
    if wanted:
        for e in entries:
            if e.serial == wanted:
                if e.state != "device":
                    raise NeedsUser(STATE_HINTS.get(e.state, "设备状态：%s" % e.state))
                return e.serial
        raise NeedsUser("没有找到序列号为 %s 的手机。" % wanted)
    ready = [e for e in entries if e.state == "device"]
    if len(ready) == 1:
        return ready[0].serial
    if not ready:
        for e in entries:
            if e.state in STATE_HINTS:
                raise NeedsUser(STATE_HINTS[e.state])
        raise NeedsUser("没有检测到手机。请用数据线连接，并在开发者选项中打开 USB 调试。")
    names = "、".join("%s（%s）" % (e.serial, e.model) for e in ready)
    raise NeedsUser("连接了多台手机：%s。请用 --serial 指定一台。" % names)


def cmd_doctor(args) -> int:
    adb = Adb(args.adb)
    log("adb：%s" % adb.path)
    entries = adb.devices()
    if not entries:
        log("没有检测到手机。")
    for e in entries:
        log("设备 %s：%s %s%s" % (e.serial, e.state, e.model, "" if e.state == "device" else "（" + STATE_HINTS.get(e.state, "") + "）"))
    serial = pick_serial(adb, args.serial)
    session = ExportSession(adb, serial, ExportOptions(dest=Path(args.dest or ".")), log=log)
    facts = session.preflight()
    for key, label in (("brand", "品牌"), ("model", "型号"), ("android", "Android"), ("app_version", "海马 App"),
                       ("tz", "时区")):
        log("%s：%s" % (label, facts.get(key)))
    log("App 版本%s" % ("已验证" if facts["app_verified"] else "未验证，遇到陌生页面会暂停"))
    page = detect_page(session.ui.dump())
    log("当前页面：%s" % page.value)
    count = session.device.shell("ls %s 2>/dev/null | wc -l" % quote(haima.EXPORT_DIR), check=False).strip()
    log("手机导出目录 %s：%s 张" % (haima.EXPORT_DIR, count))
    log("体检通过，可以开始导出。")
    return 0


def _run(args, sync_only: bool) -> int:
    adb = Adb(args.adb)
    serial = pick_serial(adb, args.serial)
    options = ExportOptions(
        dest=Path(args.dest), batch_target=getattr(args, "batch", 120), max_batches=getattr(args, "max_batches", None),
        dry_run=getattr(args, "dry_run", False), keep_awake=not getattr(args, "no_keep_awake", False),
        tz_name=args.tz, sync_only=sync_only)
    session = ExportSession(adb, serial, options, log=log)
    session.app.verbose = getattr(args, "verbose", False)
    summary = session.run()
    s = summary.sync
    log("结束：%s。新保存 %d 张，重复 %d 张，失败 %d 张；%d 批；用时 %.0f 秒；界面读取 %d 次。"
        % (summary.status, s.saved, s.duplicates, s.failed, summary.batches, summary.seconds, summary.dumps))
    if summary.note:
        log("说明：" + summary.note)
    log("报告：%s" % (Path(args.dest).expanduser().resolve() / "导出报告.md"))
    return EXIT.get(summary.status, 1)


def cmd_export(args) -> int:
    return _run(args, sync_only=False)


def cmd_sync(args) -> int:
    return _run(args, sync_only=True)


def cmd_archive(args) -> int:
    from .archive import build_archive
    build_archive(Path(args.dest).expanduser().resolve(), log=log)
    return 0


def cmd_report(args) -> int:
    dest = Path(args.dest).expanduser().resolve()
    path = dest / "导出报告.md"
    if not path.exists():
        log("还没有报告，请先运行 export 或 sync。")
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_gui(args) -> int:
    from .gui import serve
    serve(adb_path=args.adb, port=args.port, open_browser=not args.no_browser)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="album-export", description="海马爸比时光相册批量导出（安卓桌面版）")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--adb", help="adb 路径（默认自动查找）")
    p.add_argument("--serial", help="手机序列号（连接多台手机时需要）")
    p.add_argument("--tz", help="照片时区，默认取手机时区，例如 Asia/Shanghai 或 +08:00")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="检查连接、手机状态和 App 版本")
    d.add_argument("--dest", help="（可选）归档目录")
    d.set_defaults(func=cmd_doctor)

    e = sub.add_parser("export", help="自动批量下载并保存到电脑")
    e.add_argument("--dest", required=True, help="电脑上的归档目录")
    e.add_argument("--batch", type=int, default=120, help="每批勾选的照片数，默认 120")
    e.add_argument("--max-batches", type=int, help="最多处理几批（默认直到全部完成）")
    e.add_argument("--dry-run", action="store_true", help="只勾选一批、不下载，用于检查")
    e.add_argument("--no-keep-awake", action="store_true", help="导出期间不保持手机亮屏")
    e.add_argument("--verbose", action="store_true", help="输出每一轮勾选的细节")
    e.set_defaults(func=cmd_export)

    s = sub.add_parser("sync", help="只把手机上已下载的照片保存到电脑（不操作 App）")
    s.add_argument("--dest", required=True)
    s.set_defaults(func=cmd_sync)

    a = sub.add_parser("archive", help="生成按月份整理、写入拍摄时间的副本")
    a.add_argument("--dest", required=True)
    a.set_defaults(func=cmd_archive)

    g = sub.add_parser("gui", help="在浏览器里打开图形界面")
    g.add_argument("--port", type=int, default=0)
    g.add_argument("--no-browser", action="store_true")
    g.set_defaults(func=cmd_gui)

    r = sub.add_parser("report", help="显示最近一次的导出报告")
    r.add_argument("--dest", required=True)
    r.set_defaults(func=cmd_report)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except NeedsUser as exc:
        log("需要你处理：%s" % exc)
        return 2
    except AdbError as exc:
        log("连接出错：%s" % exc)
        return 1
    except KeyboardInterrupt:
        log("已中断。再次运行会从断点继续。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
