"""Month-by-month browsing copy with the capture time written into EXIF.

Originals stay untouched in 原始文件/. The copies here import into Photos,
Google Photos or a phone gallery in true capture order.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import jpeg
from .ledger import Ledger
from .names import offset_text, resolve_tz

ARCHIVE = "按月份"


def build_archive(dest: Path, log: Callable[[str], None] = print) -> dict:
    ledger = Ledger(dest / ".album-export" / "ledger.sqlite3")
    made = skipped = failed = 0
    try:
        for row in ledger.files():
            if row["captured_utc"] is None:
                skipped += 1
                continue
            tz = resolve_tz(row["tz"])
            when = datetime.fromtimestamp(row["captured_utc"], tz)
            folder = dest / ARCHIVE / when.strftime("%Y") / when.strftime("%Y-%m")
            stem = when.strftime("%Y-%m-%d_%H%M%S") + ("_夜间" if row["night"] else "")
            target = folder / (stem + ".jpg")
            if target.exists():
                skipped += 1
                continue
            source = dest / row["local_path"]
            data = source.read_bytes()
            stamped = jpeg.with_exif_date(data, when.strftime("%Y:%m:%d %H:%M:%S"), offset_text(when),
                                          description="time from file name " + row["name"])
            folder.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".part")
            tmp.write_bytes(stamped if stamped is not None else data)
            os.replace(str(tmp), str(target))
            ts = when.timestamp()
            os.utime(str(target), (ts, ts))
            made += 1
    finally:
        ledger.close()
    log("按月份副本：新建 %d 张，跳过 %d 张，失败 %d 张。" % (made, skipped, failed))
    return {"made": made, "skipped": skipped, "failed": failed}
