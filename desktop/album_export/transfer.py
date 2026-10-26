"""Copy downloaded photos from the phone and verify each one before keeping it."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from datetime import tzinfo
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import jpeg
from .adb import Device, quote
from .ledger import Ledger
from .names import capture_time, parse_name

ORIGINALS = "原始文件"
WORKDIR = ".album-export"


def chunks(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


@dataclass
class SyncStats:
    listed: int = 0
    candidates: int = 0
    saved: int = 0
    duplicates: int = 0
    failed: int = 0
    bytes_saved: int = 0
    saved_names: List[str] = field(default_factory=list)

    def add(self, other: "SyncStats") -> None:
        self.listed = other.listed
        self.candidates += other.candidates
        self.saved += other.saved
        self.duplicates += other.duplicates
        self.failed += other.failed
        self.bytes_saved += other.bytes_saved
        self.saved_names.extend(other.saved_names)


class Transfer:
    def __init__(self, device: Device, ledger: Ledger, dest: Path, tz: tzinfo, tz_name: str,
                 serial: str, model: str, remote_dir: str, log: Callable[[str], None] = print):
        self.device = device
        self.ledger = ledger
        self.dest = dest
        self.tz = tz
        self.tz_name = tz_name
        self.serial = serial
        self.model = model
        self.remote_dir = remote_dir.rstrip("/")
        self.log = log
        self.originals = dest / ORIGINALS
        self.work = dest / WORKDIR

    # ---- phone side ------------------------------------------------------
    def remote_count(self) -> int:
        out = self.device.shell("ls %s 2>/dev/null | wc -l" % quote(self.remote_dir), timeout=20, check=False)
        try:
            return int(out.strip() or "0")
        except ValueError:
            return 0

    def remote_names(self) -> List[str]:
        out = self.device.shell("ls %s 2>/dev/null" % quote(self.remote_dir), timeout=30, check=False)
        return [line.strip() for line in out.splitlines() if line.strip()]

    def remote_listing(self) -> Dict[str, Tuple[int, int]]:
        if self.remote_count() == 0:
            return {}
        out = self.device.shell("cd %s && stat -c '%%s|%%Y|%%n' -- *" % quote(self.remote_dir),
                                timeout=60, check=False)
        listing: Dict[str, Tuple[int, int]] = {}
        for line in out.splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3 and parts[0].isdigit():
                listing[parts[2]] = (int(parts[0]), int(parts[1]))
        return listing

    def remote_hashes(self, names: Sequence[str]) -> Dict[str, str]:
        found: Dict[str, str] = {}
        for part in chunks(list(names), 120):
            args = " ".join(quote(n) for n in part)
            out = self.device.shell("cd %s && sha256sum -- %s" % (quote(self.remote_dir), args),
                                    timeout=180, check=False)
            for line in out.splitlines():
                digest, sep, name = line.partition("  ")
                if sep and len(digest) == 64:
                    found[name.strip()] = digest
        return found

    # ---- computer side ---------------------------------------------------
    def sync(self, run_id: Optional[int], only: Optional[Iterable[str]] = None) -> SyncStats:
        stats = SyncStats()
        listing = self.remote_listing()
        stats.listed = len(listing)
        wanted = set(only) if only is not None else None
        seen = self.ledger.seen(self.serial)
        todo = sorted(n for n, (size, _mtime) in listing.items()
                      if (wanted is None or n in wanted) and not (n in seen and seen[n][0] == size))
        stats.candidates = len(todo)
        if not todo:
            return stats
        need = sum(listing[n][0] for n in todo)
        self.originals.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(self.dest)).free
        if free < need * 1.2 + 50 * 1024 * 1024:
            raise OSError("目标磁盘空间不足：需要约 %.0f MB，剩余 %.0f MB" % (need / 1e6, free / 1e6))
        stage = self.work / "staging" / ("run%s" % (run_id or 0))
        stage.mkdir(parents=True, exist_ok=True)
        remote_sha = self.remote_hashes(todo)
        for part in chunks(todo, 150):
            self.device.pull([self.remote_dir + "/" + n for n in part], str(stage), timeout=900)
            for name in part:
                size, mtime = listing[name]
                self._commit_one(stage / name, name, size, mtime, remote_sha.get(name), run_id, stats)
            self.ledger.commit()
            self.log("已保存 %d 张，跳过重复 %d 张，失败 %d 张（本次待处理 %d 张）"
                     % (stats.saved, stats.duplicates, stats.failed, len(todo)))
        shutil.rmtree(str(stage), ignore_errors=True)
        return stats

    def _fail(self, run_id, name, kind, detail, stats: SyncStats, staged: Optional[Path] = None) -> None:
        stats.failed += 1
        self.ledger.problem(run_id, name, kind, detail)
        if staged is not None and staged.exists():
            quarantine = self.work / "quarantine"
            quarantine.mkdir(parents=True, exist_ok=True)
            os.replace(str(staged), str(quarantine / name))

    def _commit_one(self, staged: Path, name: str, size: int, mtime: int, remote_sha: Optional[str],
                    run_id: Optional[int], stats: SyncStats) -> None:
        if not staged.exists():
            return self._fail(run_id, name, "missing", "拉取后找不到文件", stats)
        data = staged.read_bytes()
        if len(data) != size:
            return self._fail(run_id, name, "size", "大小不一致：手机 %d，电脑 %d" % (size, len(data)), stats, staged)
        sha = hashlib.sha256(data).hexdigest()
        if remote_sha and sha != remote_sha:
            return self._fail(run_id, name, "hash", "SHA-256 与手机端不一致", stats, staged)
        info = jpeg.inspect(data)
        if not info.ok:
            return self._fail(run_id, name, "jpeg", info.reason, stats, staged)
        if self.ledger.has_sha(sha):
            stats.duplicates += 1
            self.ledger.mark_seen(self.serial, name, size, sha, "duplicate")
            staged.unlink()
            return None
        parsed = parse_name(name)
        target_name = parsed.canonical if parsed else name
        final = self.originals / target_name
        if final.exists():
            final = self.originals / ("%s_%s%s" % (final.stem, sha[:8], final.suffix))
        os.replace(str(staged), str(final))
        os.utime(str(final), (mtime, mtime))
        local = capture_time(parsed, self.tz) if parsed else None
        self.ledger.add_file(
            sha256=sha, name=name, local_path=str(final.relative_to(self.dest)), size=size,
            width=info.width, height=info.height,
            captured_utc=parsed.epoch if parsed else None,
            captured_local=local.strftime("%Y-%m-%d %H:%M:%S") if local else None,
            tz=self.tz_name if parsed else None, time_source="filename" if parsed else None,
            night=int(parsed.night) if parsed else None,
            device_serial=self.serial, device_model=self.model, remote_mtime=mtime, run_id=run_id)
        self.ledger.mark_seen(self.serial, name, size, sha, "saved")
        stats.saved += 1
        stats.bytes_saved += size
        stats.saved_names.append(name)
        return None
