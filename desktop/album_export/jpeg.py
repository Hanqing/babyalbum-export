"""JPEG structure checks and a minimal EXIF date writer (standard library only)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


@dataclass
class JpegInfo:
    ok: bool
    width: int = 0
    height: int = 0
    has_exif: bool = False
    reason: str = ""


def inspect(data: bytes) -> JpegInfo:
    """Walk the marker segments; confirm SOI, a frame header, scan data and EOI."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return JpegInfo(False, reason="缺少 JPEG 文件头")
    tail = data.rstrip(b"\x00")
    if not tail.endswith(b"\xff\xd9"):
        return JpegInfo(False, reason="缺少 JPEG 结束标记，文件可能不完整")
    i, width, height, has_exif, saw_sos = 2, 0, 0, False, False
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return JpegInfo(False, reason="段结构损坏（偏移 %d）" % i)
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0x01,) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xD9:
            break
        (length,) = struct.unpack(">H", data[i + 2:i + 4])
        if length < 2 or i + 2 + length > len(data):
            return JpegInfo(False, reason="段长度越界（偏移 %d）" % i)
        body = data[i + 4:i + 2 + length]
        if marker in _SOF and len(body) >= 5:
            height, width = struct.unpack(">HH", body[1:5])
        elif marker == 0xE1 and body.startswith(b"Exif\x00\x00"):
            has_exif = True
        elif marker == 0xDA:
            saw_sos = True
            break
        i += 2 + length
    if not saw_sos:
        return JpegInfo(False, reason="没有图像数据段")
    if not width or not height:
        return JpegInfo(False, reason="没有图像尺寸信息")
    return JpegInfo(True, width=width, height=height, has_exif=has_exif)


def _ifd(entries, data_offset: int):
    """Serialise one little-endian IFD. entries: (tag, type, count, payload_bytes)."""
    entries = sorted(entries, key=lambda e: e[0])
    head = struct.pack("<H", len(entries))
    table, blob = b"", b""
    for tag, typ, count, payload in entries:
        if len(payload) <= 4:
            table += struct.pack("<HHI", tag, typ, count) + payload.ljust(4, b"\x00")
        else:
            table += struct.pack("<HHII", tag, typ, count, data_offset + len(blob))
            blob += payload
            if len(blob) % 2:
                blob += b"\x00"
    return head + table + struct.pack("<I", 0), blob


def build_exif(local_time: str, offset: str, description: str = "") -> bytes:
    """APP1 segment with DateTime/DateTimeOriginal/Digitized and their offsets.

    local_time: 'YYYY:MM:DD HH:MM:SS' in the photo's own time zone.
    offset:     '+08:00'.
    """
    ascii_ = lambda s: s.encode("ascii") + b"\x00"
    dt, off = ascii_(local_time), ascii_(offset)
    ifd0_entries = [(0x0132, 2, len(dt), dt)]
    if description:
        desc = description.encode("ascii", "replace") + b"\x00"
        ifd0_entries.append((0x010E, 2, len(desc), desc))
    # Layout: TIFF header (8) | IFD0 | IFD0 data | Exif IFD | Exif data
    ifd0_size = 2 + 12 * (len(ifd0_entries) + 1) + 4
    ifd0_probe, ifd0_blob_probe = _ifd(ifd0_entries + [(0x8769, 4, 1, b"\x00" * 4)], 8 + ifd0_size)
    exif_ifd_offset = 8 + len(ifd0_probe) + len(ifd0_blob_probe)
    ifd0, ifd0_blob = _ifd(ifd0_entries + [(0x8769, 4, 1, struct.pack("<I", exif_ifd_offset))], 8 + ifd0_size)
    exif_entries = [
        (0x9000, 7, 4, b"0232"),
        (0x9003, 2, len(dt), dt),
        (0x9004, 2, len(dt), dt),
        (0x9010, 2, len(off), off),
        (0x9011, 2, len(off), off),
        (0x9012, 2, len(off), off),
    ]
    exif_size = 2 + 12 * len(exif_entries) + 4
    exif_ifd, exif_blob = _ifd(exif_entries, exif_ifd_offset + exif_size)
    tiff = b"II*\x00" + struct.pack("<I", 8) + ifd0 + ifd0_blob + exif_ifd + exif_blob
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def read_exif_dates(data: bytes) -> dict:
    """Return DateTimeOriginal / OffsetTimeOriginal (and DateTime) if present."""
    found: dict = {}
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xDA:
            break
        (length,) = struct.unpack(">H", data[i + 2:i + 4])
        body = data[i + 4:i + 2 + length]
        if marker == 0xE1 and body.startswith(b"Exif\x00\x00"):
            t = body[6:]
            e = "<" if t[:2] == b"II" else ">"

            def entries(off):
                (n,) = struct.unpack(e + "H", t[off:off + 2])
                for k in range(n):
                    tag, typ, cnt = struct.unpack(e + "HHI", t[off + 2 + 12 * k:off + 10 + 12 * k])
                    raw = t[off + 10 + 12 * k:off + 14 + 12 * k]
                    yield tag, typ, cnt, raw

            def ascii_value(typ, cnt, raw):
                if cnt <= 4:
                    return raw[:cnt].rstrip(b"\x00").decode("ascii", "replace")
                (ptr,) = struct.unpack(e + "I", raw)
                return t[ptr:ptr + cnt].rstrip(b"\x00").decode("ascii", "replace")

            (ifd0,) = struct.unpack(e + "I", t[4:8])
            exif_ptr = None
            for tag, typ, cnt, raw in entries(ifd0):
                if tag == 0x0132 and typ == 2:
                    found["DateTime"] = ascii_value(typ, cnt, raw)
                elif tag == 0x8769:
                    (exif_ptr,) = struct.unpack(e + "I", raw)
            if exif_ptr:
                names = {0x9003: "DateTimeOriginal", 0x9011: "OffsetTimeOriginal"}
                for tag, typ, cnt, raw in entries(exif_ptr):
                    if tag in names and typ == 2:
                        found[names[tag]] = ascii_value(typ, cnt, raw)
            break
        i += 2 + length
    return found


def with_exif_date(data: bytes, local_time: str, offset: str, description: str = "") -> Optional[bytes]:
    """Return a copy of the JPEG with an EXIF date segment, or None if it already has EXIF."""
    info = inspect(data)
    if not info.ok or info.has_exif:
        return None
    insert_at = 2
    if data[2:4] == b"\xff\xe0":  # keep JFIF APP0 first
        (length,) = struct.unpack(">H", data[4:6])
        insert_at = 4 + length
    return data[:insert_at] + build_exif(local_time, offset, description) + data[insert_at:]
