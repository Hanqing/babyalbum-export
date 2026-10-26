"""Developer tool: create a real "not yet downloaded" queue on a test phone.

The Haima app treats a photo as downloaded when a file with its name exists
in DCIM/Camera/海马爸比. `hold` moves the newest N photos to a holding folder
on the phone (recording their SHA-256 first), so the app offers them again.
`restore` checks every re-downloaded file against the recorded hash, then
removes the held copy; anything missing is moved back. Net change: none.

    python3 tools/test_queue.py hold --newest 40 --manifest /tmp/hold.json
    python3 tools/test_queue.py restore --manifest /tmp/hold.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from album_export.adb import Adb, quote  # noqa: E402
from album_export.haima import EXPORT_DIR  # noqa: E402
from album_export.names import parse_name  # noqa: E402

HOLD_ROOT = "/sdcard/Download/album_export_test_hold"


def sha_map(dev, folder, names):
    out = {}
    for i in range(0, len(names), 120):
        part = names[i:i + 120]
        text = dev.shell("cd %s && sha256sum -- %s" % (quote(folder), " ".join(quote(n) for n in part)), timeout=180)
        for line in text.splitlines():
            digest, sep, name = line.partition("  ")
            if sep:
                out[name.strip()] = digest
    return out


def device(args):
    adb = Adb(args.adb)
    ready = [e for e in adb.devices() if e.state == "device"]
    serial = args.serial or (ready[0].serial if len(ready) == 1 else None)
    if not serial:
        sys.exit("need exactly one connected device or --serial")
    return adb.device(serial)


def cmd_hold(args):
    dev = device(args)
    names = [n.strip() for n in dev.shell("ls %s" % quote(EXPORT_DIR)).splitlines() if n.strip()]
    photos = sorted((n for n in names if parse_name(n) and parse_name(n).dup_index is None),
                    key=lambda n: parse_name(n).epoch)
    chosen = photos[-args.newest:]
    hold = "%s/%s" % (HOLD_ROOT, time.strftime("%Y%m%d-%H%M%S"))
    hashes = sha_map(dev, EXPORT_DIR, chosen)
    if len(hashes) != len(chosen):
        sys.exit("could not hash every chosen file; nothing moved")
    manifest = {"serial": dev.serial, "hold": hold, "files": hashes}
    Path(args.manifest).write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    dev.shell("mkdir -p %s" % quote(hold))
    for i in range(0, len(chosen), 100):
        part = chosen[i:i + 100]
        dev.shell("cd %s && mv -- %s %s" % (quote(EXPORT_DIR), " ".join(quote(n) for n in part), quote(hold + "/")),
                  timeout=120)
    left = dev.shell("ls %s | wc -l" % quote(EXPORT_DIR)).strip()
    print("held %d photos in %s (export dir now %s files); manifest %s" % (len(chosen), hold, left, args.manifest))


def cmd_restore(args):
    manifest = json.loads(Path(args.manifest).read_text())
    dev = device(args)
    hold, files = manifest["hold"], manifest["files"]
    present = set(n.strip() for n in dev.shell("ls %s" % quote(EXPORT_DIR)).splitlines() if n.strip())
    redownloaded = sha_map(dev, EXPORT_DIR, [n for n in files if n in present])
    same, moved_back, conflicts = 0, 0, []
    for name, digest in files.items():
        if name in redownloaded and redownloaded[name] == digest:
            dev.shell("touch -r %s %s && rm -f %s" % (quote(hold + "/" + name), quote(EXPORT_DIR + "/" + name),
                                                      quote(hold + "/" + name)))
            same += 1
        elif name not in present:
            dev.shell("mv -- %s %s" % (quote(hold + "/" + name), quote(EXPORT_DIR + "/")))
            moved_back += 1
        else:
            conflicts.append(name)
    # Remove " (n)" copies the test created, only when the bytes match a held photo.
    dups = [n for n in present if parse_name(n) and parse_name(n).dup_index is not None
            and parse_name(n).canonical in files]
    removed = 0
    for name, digest in sha_map(dev, EXPORT_DIR, dups).items():
        if digest == files[parse_name(name).canonical]:
            dev.shell("content delete --uri content://media/external/images/media --where %s"
                      % quote("_display_name='%s' AND relative_path='DCIM/Camera/海马爸比/'" % name))
            dev.shell("rm -f %s" % quote(EXPORT_DIR + "/" + name))
            removed += 1
    if not conflicts:
        dev.shell("rmdir %s 2>/dev/null; rmdir %s 2>/dev/null" % (quote(hold), quote(HOLD_ROOT)), check=False)
    total = dev.shell("ls %s | wc -l" % quote(EXPORT_DIR)).strip()
    print("verified identical %d, moved back %d, duplicates removed %d, conflicts %d; export dir now %s files"
          % (same, moved_back, removed, len(conflicts), total))
    if conflicts:
        print("conflicts kept in", hold, conflicts[:20])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--adb")
    p.add_argument("--serial")
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hold")
    h.add_argument("--newest", type=int, required=True)
    h.add_argument("--manifest", required=True)
    h.set_defaults(func=cmd_hold)
    r = sub.add_parser("restore")
    r.add_argument("--manifest", required=True)
    r.set_defaults(func=cmd_restore)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
