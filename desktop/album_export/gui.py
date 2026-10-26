"""Local web UI: a page in the browser, served on 127.0.0.1 only.

Every API call must carry the per-launch token, so other web pages cannot
drive the phone through this server.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .adb import STATE_HINTS, Adb, AdbError
from .haima import NeedsUser, detect_page
from .session import ExportOptions, ExportSession

DEFAULT_DEST = str(Path.home() / "Pictures" / "海马爸比相册归档")


class App:
    def __init__(self, adb_path: Optional[str] = None):
        self.adb_path = adb_path
        self.lock = threading.Lock()
        self.logs: deque = deque(maxlen=400)
        self.progress: dict = {}
        self.summary: Optional[dict] = None
        self.running = False
        self.stop_event = threading.Event()
        self.dest = DEFAULT_DEST

    def log(self, msg: str) -> None:
        with self.lock:
            self.logs.append(time.strftime("%H:%M:%S ") + msg)

    def devices(self) -> dict:
        try:
            adb = Adb(self.adb_path)
            entries = adb.devices()
        except AdbError as exc:
            return {"ok": False, "message": str(exc), "devices": []}
        ready = [e for e in entries if e.state == "device"]
        message = ""
        if not entries:
            message = "没有检测到手机。请用数据线连接，并在开发者选项里打开“USB 调试”。"
        elif not ready:
            message = STATE_HINTS.get(entries[0].state, "手机状态：%s" % entries[0].state)
        elif len(ready) > 1:
            message = "连接了多台手机，请只保留一台。"
        return {"ok": len(ready) == 1, "message": message,
                "devices": [{"serial": e.serial, "state": e.state, "model": e.model} for e in entries]}

    def _serial(self) -> str:
        info = self.devices()
        if not info["ok"]:
            raise NeedsUser(info["message"] or "请连接一台手机。")
        return [d for d in info["devices"] if d["state"] == "device"][0]["serial"]

    def doctor(self) -> dict:
        try:
            adb = Adb(self.adb_path)
            session = ExportSession(adb, self._serial(), ExportOptions(dest=Path(self.dest)), log=self.log)
            facts = session.preflight()
            facts["page"] = detect_page(session.ui.dump()).value
            self.log("体检通过：%s %s，海马 App %s，当前页面「%s」" % (
                facts.get("brand"), facts.get("model"), facts.get("app_version"), facts["page"]))
            return {"ok": True, "facts": facts}
        except (NeedsUser, AdbError) as exc:
            self.log("需要处理：%s" % exc)
            return {"ok": False, "message": str(exc)}

    def start(self, dest: str, batch: int) -> dict:
        with self.lock:
            if self.running:
                return {"ok": False, "message": "已经在导出中。"}
            self.running = True
            self.summary = None
            self.progress = {}
            self.stop_event.clear()
            self.dest = dest or DEFAULT_DEST

        def work() -> None:
            try:
                adb = Adb(self.adb_path)
                options = ExportOptions(dest=Path(self.dest).expanduser(), batch_target=batch)
                session = ExportSession(adb, self._serial(), options, log=self.log,
                                        progress=self._on_progress, stop=self.stop_event)
                s = session.run()
                self.summary = {"status": s.status, "note": s.note, "saved": s.sync.saved,
                                "duplicates": s.sync.duplicates, "failed": s.sync.failed,
                                "batches": s.batches, "downloaded": s.downloaded, "seconds": round(s.seconds)}
                self.log("结束：%s。新保存 %d 张，失败 %d 张。%s" % (s.status, s.sync.saved, s.sync.failed, s.note))
            except (NeedsUser, AdbError) as exc:
                self.summary = {"status": "paused", "note": str(exc)}
                self.log("需要处理：%s" % exc)
            except Exception as exc:  # keep the UI alive
                self.summary = {"status": "error", "note": repr(exc)}
                self.log("出错：%r" % exc)
            finally:
                with self.lock:
                    self.running = False

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def _on_progress(self, info: dict) -> None:
        with self.lock:
            self.progress.update(info)

    def stop(self) -> dict:
        self.stop_event.set()
        self.log("正在停止……当前这一批结束后停下。")
        return {"ok": True}

    def state(self) -> dict:
        with self.lock:
            return {"running": self.running, "logs": list(self.logs)[-200:], "progress": dict(self.progress),
                    "summary": self.summary, "dest": self.dest}

    def report(self) -> dict:
        path = Path(self.dest).expanduser() / "导出报告.md"
        return {"ok": path.exists(), "text": path.read_text(encoding="utf-8") if path.exists() else ""}

    def open_folder(self) -> dict:
        path = Path(self.dest).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return {"ok": True}

    def archive(self) -> dict:
        from .archive import build_archive
        result = build_archive(Path(self.dest).expanduser(), log=self.log)
        return {"ok": True, **result}


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>相册导出助手</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c1c1e;--quiet:#6e6e73;--line:#e3e4e8;--accent:#2f6fde;--ok:#1e824c;--warn:#c45a00}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1f1f22;--ink:#f2f2f4;--quiet:#a1a1a8;--line:#333338;--accent:#6c9cff;--ok:#4cc38a;--warn:#ffa24d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}p.sub{color:var(--quiet);margin:0 0 20px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:14px}
h2{font-size:16px;margin:0 0 10px}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input[type=text],input[type=number]{flex:1;min-width:180px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:transparent;color:var(--ink);font:inherit}
input[type=number]{flex:0 0 90px;min-width:0}
button{padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:transparent;color:var(--ink);font:inherit;cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button:disabled{opacity:.45;cursor:default}
.status{margin-top:8px;color:var(--quiet)}.ok{color:var(--ok)}.warn{color:var(--warn)}
.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin:10px 0}.bar>div{height:100%;width:0;background:var(--accent);transition:width .4s}
pre{white-space:pre-wrap;background:var(--bg);border-radius:8px;padding:10px;max-height:300px;overflow:auto;font:12.5px/1.5 ui-monospace,Menlo,Consolas,monospace;margin:8px 0 0}
ol{margin:6px 0 0;padding-left:20px;color:var(--quiet)}
</style></head><body><main>
<h1>相册导出助手</h1><p class="sub">适用于海马爸比 · 自动在官方 App 里勾选并下载“未下载”的照片，再逐张校验后保存到这台电脑。照片不会上传。</p>
<section><h2>1 · 连接手机</h2>
<div class="row"><button id="check">检查连接</button><span id="dev" class="status">尚未检查</span></div>
<ol><li>手机：设置 → 关于手机 → 连续点“版本号”7 次，打开开发者选项。</li><li>开发者选项里打开“USB 调试”，用数据线连接电脑，手机上点“允许”。</li><li>手机解锁，打开海马爸比 App 并登录。</li></ol></section>
<section><h2>2 · 保存位置</h2><div class="row"><input id="dest" type="text"><button id="open">打开文件夹</button></div>
<div class="row" style="margin-top:8px"><span class="status">每批勾选约</span><input id="batch" type="number" value="150" min="20" max="500"><span class="status">张</span></div></section>
<section><h2>3 · 导出</h2><div class="row"><button id="start" class="primary">开始导出</button><button id="stop" disabled>停止</button><button id="archive">生成按月份副本</button><button id="report">查看报告</button></div>
<div class="bar"><div id="fill"></div></div><div id="prog" class="status">导出时请保持手机亮屏、不要操作手机。</div><pre id="log"></pre></section>
<section id="repsec" hidden><h2>导出报告</h2><pre id="rep"></pre></section>
</main><script>
const T=new URLSearchParams(location.search).get('t');const $=id=>document.getElementById(id);
async function api(p,body){const r=await fetch('/api/'+p,{method:body?'POST':'GET',headers:{'X-Token':T,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return r.json()}
async function check(){$('dev').textContent='检查中……';const d=await api('devices');if(!d.ok){$('dev').innerHTML='<span class="warn">'+(d.message||'未连接')+'</span>';return false}
const dr=await api('doctor',{});$('dev').innerHTML=dr.ok?'<span class="ok">已连接 '+dr.facts.brand+' '+dr.facts.model+' · 海马 App '+dr.facts.app_version+(dr.facts.app_verified?'（已验证）':'（未验证版本）')+'</span>':'<span class="warn">'+dr.message+'</span>';return dr.ok}
$('check').onclick=check;
$('start').onclick=async()=>{const r=await api('start',{dest:$('dest').value,batch:+$('batch').value||150});if(!r.ok)alert(r.message)};
$('stop').onclick=()=>api('stop',{});$('open').onclick=()=>api('open',{dest:$('dest').value});
$('archive').onclick=async()=>{const r=await api('archive',{dest:$('dest').value});alert('按月份副本：新建 '+r.made+' 张，跳过 '+r.skipped+' 张')};
$('report').onclick=async()=>{const r=await api('report');$('repsec').hidden=false;$('rep').textContent=r.ok?r.text:'还没有报告。'};
let first=true;async function tick(){const s=await api('state');if(first){$('dest').value=s.dest;first=false}
$('start').disabled=s.running;$('stop').disabled=!s.running;$('log').textContent=s.logs.join('\n');$('log').scrollTop=1e9;
const p=s.progress||{};if(s.running){$('prog').textContent='进行中 · 第 '+(p.batches||0)+' 批 · 已下载 '+(p.downloaded||0)+' 张 · 已保存到电脑 '+(p.saved||0)+' 张'+(p.last_date?' · 进度到 '+p.last_date:'');$('fill').style.width=Math.min(100,(p.batches||0)*7)+'%'}
else if(s.summary){const m=s.summary;$('prog').innerHTML=(m.status==='done'?'<span class="ok">完成</span>':'<span class="warn">'+m.status+'</span>')+' · 新保存 '+(m.saved||0)+' 张 · 失败 '+(m.failed||0)+' 张'+(m.note?' · '+m.note:'');$('fill').style.width=m.status==='done'?'100%':$('fill').style.width}}
setInterval(tick,1000);tick();
</script></body></html>
"""


def make_handler(app: App, token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _json(self, obj, code=200):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Token", ""), token)

        def do_GET(self):
            if self.path.startswith("/api/"):
                if not self._authorized():
                    return self._json({"ok": False, "message": "forbidden"}, 403)
                name = self.path[5:].split("?")[0]
                if name == "state":
                    return self._json(app.state())
                if name == "devices":
                    return self._json(app.devices())
                if name == "report":
                    return self._json(app.report())
                return self._json({"ok": False}, 404)
            data = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if not self._authorized():
                return self._json({"ok": False, "message": "forbidden"}, 403)
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            name = self.path[5:].split("?")[0]
            if body.get("dest"):
                app.dest = body["dest"]
            if name == "doctor":
                return self._json(app.doctor())
            if name == "start":
                return self._json(app.start(body.get("dest", DEFAULT_DEST), int(body.get("batch", 150))))
            if name == "stop":
                return self._json(app.stop())
            if name == "open":
                return self._json(app.open_folder())
            if name == "archive":
                return self._json(app.archive())
            return self._json({"ok": False}, 404)

    return Handler


def serve(adb_path: Optional[str] = None, port: int = 0, open_browser: bool = True) -> None:
    token = secrets.token_urlsafe(16)
    app = App(adb_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, token))
    url = "http://127.0.0.1:%d/?t=%s" % (server.server_address[1], token)
    print("相册导出助手已启动：%s\n（关闭这个窗口即退出）" % url, flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
