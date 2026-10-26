"""Human-readable completion report: transfer integrity, coverage, limits."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from .ledger import Ledger

if TYPE_CHECKING:  # pragma: no cover
    from .session import RunSummary

STATUS_TEXT = {
    "done": "完成",
    "partial": "部分完成（达到批次上限）",
    "paused": "已暂停，需要处理后继续",
    "stopped": "已手动停止",
    "safety-stop": "安全检查已停止",
    "error": "出错停止",
    "dry-run": "试运行（只勾选、不下载）",
    "running": "进行中",
}


def _mb(n: int) -> str:
    return "%.1f MB" % (n / 1e6)


def build_report(ledger: Ledger, run_id: int, summary: "RunSummary") -> str:
    files = ledger.files()
    facts = summary.facts
    lines = ["# 照片导出报告", ""]
    lines.append("- 状态：%s%s" % (STATUS_TEXT.get(summary.status, summary.status),
                                  "（%s）" % summary.note if summary.note else ""))
    lines.append("- 手机：%s %s，Android %s" % (facts.get("brand", ""), facts.get("model", ""), facts.get("android", "")))
    lines.append("- 海马 App 版本：%s%s" % (facts.get("app_version", "?"),
                                         "" if facts.get("app_verified") else "（未在已验证列表中）"))
    lines.append("- 时区：%s（照片时间按此换算）" % facts.get("tz", "?"))
    minutes, seconds = divmod(int(summary.seconds), 60)
    lines.append("- 本次用时：%d 分 %d 秒" % (minutes, seconds))
    lines.append("")

    lines += ["## 搬运完整性", ""]
    s = summary.sync
    lines.append("- 本次新保存 %d 张（%s），跳过重复 %d 张，失败 %d 张。" % (s.saved, _mb(s.bytes_saved), s.duplicates, s.failed))
    lines.append("- 每张都核对过：与手机端相同的大小和 SHA-256、完整的 JPEG 结构；全部通过才放进「原始文件」。")
    problems = ledger.problems(run_id)
    if problems:
        lines.append("- 需要关注的文件：")
        for p in problems[:50]:
            lines.append("    - %s：%s" % (p["name"], p["detail"]))
    lines.append("")

    lines += ["## 来源覆盖", ""]
    if files:
        dated = [f for f in files if f["captured_local"]]
        days = sorted({f["captured_local"][:10] for f in dated})
        total_bytes = sum(f["size"] for f in files)
        lines.append("- 归档共 %d 张（%s）。" % (len(files), _mb(total_bytes)))
        if dated:
            lines.append("- 拍摄时间：%s 至 %s，分布在 %d 天。" % (dated[0]["captured_local"], dated[-1]["captured_local"], len(days)))
        months = Counter(f["captured_local"][:7] for f in dated)
        if months:
            lines += ["", "| 月份 | 照片数 |", "| --- | --- |"]
            for month in sorted(months):
                lines.append("| %s | %d |" % (month, months[month]))
    else:
        lines.append("- 归档中还没有照片。")
    lines.append("")
    if summary.status == "done":
        lines.append("- 官方 App 的“仅显示未下载的文件”列表已清空，App 当前能提供的照片都已保存。")
    elif summary.status in ("paused", "stopped", "partial", "error", "safety-stop"):
        lines.append("- 待下载列表还没清空。再次运行会从断点继续，已保存的照片不会重复保存。")
    lines.append("")

    lines += ["## 质量与限制", ""]
    sizes = Counter("%d×%d" % (f["width"], f["height"]) for f in files if f["width"])
    if sizes:
        lines.append("- 图片尺寸：" + "，".join("%s 共 %d 张" % (k, v) for k, v in sizes.most_common()))
    nights = sum(1 for f in files if f["night"])
    if files:
        lines.append("- 夜间（红外）照片 %d 张。" % nights)
    lines.append("- 照片来自官方 App 的“下载”功能，保存的是 App 提供的文件；是否等同于摄像头原图未经验证。")
    lines.append("- 照片本身不带拍摄时间（无 EXIF），时间取自文件名中的时间戳，并按上面的时区换算。")
    lines.append("- 只能保存 App 里仍然存在的照片，无法找回 App 中已经没有的历史。")
    lines.append("")

    batches = ledger.run_batches(run_id)
    if batches:
        lines += ["## 本次批次", ""]
        sel = sum(b["selected"] or 0 for b in batches)
        dl = sum(b["downloaded"] or 0 for b in batches)
        select_s = sum(b["select_seconds"] or 0 for b in batches)
        download_s = sum(b["download_seconds"] or 0 for b in batches)
        lines.append("- %d 批，勾选 %d 张，下载 %d 张；勾选用时 %.0f 秒，下载用时 %.0f 秒。"
                     % (len(batches), sel, dl, select_s, download_s))
        lines.append("")
    return "\n".join(lines)


def write_report(dest: Path, ledger: Ledger, run_id: int, summary: "RunSummary") -> Path:
    path = dest / "导出报告.md"
    path.write_text(build_report(ledger, run_id, summary), encoding="utf-8")
    return path
