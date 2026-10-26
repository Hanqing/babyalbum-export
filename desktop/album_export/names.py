"""File-name conventions of the Haima album and capture-time handling.

Names look like ``1792826431.jpg`` or ``1763676650_NGT.jpg``: the number is the
capture time in Unix seconds, ``_NGT`` marks a night (infrared) shot, and the
app appends `` (1)`` when the same photo is downloaded twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional

_NAME = re.compile(r"^(?P<ts>\d{10})(?P<ngt>_NGT)?(?: \((?P<dup>\d+)\))?\.(?P<ext>jpe?g)$", re.IGNORECASE)
_OFFSET = re.compile(r"^([+-])(\d{2}):?(\d{2})$")

DEFAULT_TZ = "Asia/Shanghai"


@dataclass(frozen=True)
class NameInfo:
    epoch: int
    night: bool
    dup_index: Optional[int]
    ext: str

    @property
    def canonical(self) -> str:
        """Name without the app's duplicate suffix."""
        return "%d%s.%s" % (self.epoch, "_NGT" if self.night else "", self.ext.lower())


def parse_name(name: str) -> Optional[NameInfo]:
    m = _NAME.match(name)
    if not m:
        return None
    dup = m.group("dup")
    return NameInfo(epoch=int(m.group("ts")), night=bool(m.group("ngt")),
                    dup_index=int(dup) if dup else None, ext=m.group("ext"))


def resolve_tz(name: Optional[str]) -> tzinfo:
    """IANA zone name (e.g. Asia/Shanghai) or a fixed offset like +08:00."""
    name = (name or DEFAULT_TZ).strip()
    m = _OFFSET.match(name)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
        return ZoneInfo(name)
    except Exception:
        if name in ("Asia/Shanghai", "Asia/Chongqing", "Asia/Harbin", "PRC", "Asia/Hong_Kong",
                    "Asia/Taipei", "Asia/Macau", "Asia/Singapore"):
            return timezone(timedelta(hours=8), name)
        raise ValueError("无法识别的时区：%s" % name)


def capture_time(info: NameInfo, tz: tzinfo) -> datetime:
    return datetime.fromtimestamp(info.epoch, tz)


def offset_text(dt: datetime) -> str:
    """'+08:00' style UTC offset of an aware datetime."""
    off = dt.utcoffset() or timedelta(0)
    minutes = int(off.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return "%s%02d:%02d" % (sign, minutes // 60, minutes % 60)
