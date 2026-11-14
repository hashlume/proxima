#!/usr/bin/env python3
"""Live status page for this Proxima mining wallet (stdlib only).

    /data   JSON snapshot, polled by the page
    /       the page itself

Reads the miner/seeker logs and the public node monitor, and asks `proxi node
balance` for the wallet every half minute. Nothing is written; no key material
is ever served.
"""
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MINE = "/root/proxima/mine"
MONITOR = "http://65.21.170.230:8001/api/v1/monitor"
PORT = 8080

STATE = {}
LOCK = threading.Lock()
HOLDER = "?"


def read(path, n=300_000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def last(pattern, text, cast=str, group=1):
    found = None
    for found in re.finditer(pattern, text):
        pass
    return cast(found.group(group)) if found else None


def num(s):
    return int(s.replace("_", "")) if s else None


def log_loop():
    while True:
        mine = read(os.path.join(MINE, "mine.log"))
        h = last(r"([\d_]+) H/s", mine, num)
        transit = last(r"mining transit #(\d+)", mine, int)
        k = last(r"mining transit #\d+[^\n]*?difficulty K=(\d+)", mine, int)
        slot = last(r"mining transit #\d+[^\n]*?target slot (\d+)", mine, int)
        attempts = last(r"mining\.\.\. ([\d_]+) attempts", mine, num)
        phase = "searching" if mine.rstrip().rsplit("\n", 1)[-1].strip() else "waiting"
        wins = 0
        last_win = None
        for g in ("gpu0.log", "gpu1.log"):
            txt = read(os.path.join(MINE, g))
            wins += txt.count("ACCEPTED")
            m = last(r"ACCEPTED nonce (\w+)", txt)
            if m:
                last_win = m
        with LOCK:
            STATE.update(hashrate=h, transit=transit, k=k, target_slot=slot,
                         attempts=attempts, phase=phase, wins=wins,
                         last_win_nonce=last_win)
        time.sleep(1)


def monitor_loop():
    while True:
        try:
            with urllib.request.urlopen(MONITOR, timeout=10) as r:
                doc = json.load(r)
            d = doc["live"]
            f = d["fair_launch"]
            h = doc.get("mine_history") or {}
            transits = h.get("transits", [])[:12]
            net = dict(
                slot=d.get("current_slot"),
                mined=f.get("mined_transactions"),
                remaining=(f.get("remaining") or 0) / 1e6,
                difficulty=f.get("difficulty"),
                miners=(f.get("contest") or {}).get("competing_miners"),
                submissions=(f.get("contest") or {}).get("submissions"),
                mean_pace=h.get("mean_pace"),
                mined_last_hour=h.get("mined_last_hour"),
                transits=transits,
                ok=True,
            )
        except Exception as e:  # noqa: BLE001
            net = dict(ok=False, err=str(e))
        with LOCK:
            STATE["net"] = net
        time.sleep(3)


def balance_loop():
    while True:
        total = None
        delegations = 0
        outputs = None
        try:
            out = subprocess.run(["proxi", "node", "balance"], cwd=MINE,
                                 capture_output=True, text=True, timeout=40).stdout
            m = re.search(r"TOTAL controlled on (\d+) outputs?: ([\d_]+)", out)
            if m:
                outputs = int(m.group(1))
                total = int(m.group(2).replace("_", "")) / 1e6
            delegations = len(re.findall(r"^\s+\S*delegation", out, re.M))
            if "NO DELEGATIONS" in out:
                delegations = 0
        except Exception:  # noqa: BLE001
            pass
        with LOCK:
            STATE["wallet"] = dict(total=total, delegations=delegations, outputs=outputs)
        time.sleep(30)


def snapshot():
    with LOCK:
        s = dict(STATE)
    net = s.get("net") or {}
    h = s.get("hashrate")
    k = s.get("k")
    s["eta_seconds"] = (2 ** k / h) if (h and k) else None
    s["net_share"] = None
    if h and net.get("difficulty") and net.get("mean_pace"):
        # network does 2^K attempts per transit; we do h per second
        try:
            per_s = (2 ** net["difficulty"]) / (net["mean_pace"] * 10.24)
            s["net_share"] = h / per_s
        except Exception:  # noqa: BLE001
            pass
    s["holder"] = HOLDER
    s["now"] = int(time.time())
    return s


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Proxima mining — {short}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--bg:#0f1115;--card:#171a21;--ink:#e8eaf0;--dim:#8b93a5;--line:#242936;--good:#5ac37b;--acc:#6aa9e0;--warn:#d9a441}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1020px;margin:0 auto;padding:26px 20px 70px}}
h1{{font-size:20px;margin:0 0 2px;font-weight:600}}
.sub{{color:var(--dim);font-size:12.5px;word-break:break-all}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px;margin:20px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 15px}}
.k{{color:var(--dim);font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}}
.v{{font-size:23px;font-weight:600;font-variant-numeric:tabular-nums}}
.v small{{font-size:12px;font-weight:400;color:var(--dim)}}
.good{{color:var(--good)}} .acc{{color:var(--acc)}} .warn{{color:var(--warn)}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);font-size:13px}}
th{{color:var(--dim);font-weight:500;font-size:11.5px;text-transform:uppercase;letter-spacing:.05em}}
.mine{{color:var(--good);font-weight:600}}
.foot{{color:var(--dim);font-size:12px;margin-top:18px;display:flex;gap:16px;flex-wrap:wrap}}
a{{color:var(--acc);text-decoration:none}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--good);margin-right:6px;vertical-align:1px}}
</style></head><body><div class="wrap">
<h1><span class="dot"></span>Proxima mining</h1>
<div class="sub">{holder}</div>
<div class="grid" id="grid"></div>
<div class="card"><div class="k">Last transits on the mine chain</div><table><thead>
<tr><th>slot</th><th>difficulty</th><th>miner</th></tr></thead><tbody id="tb"></tbody></table></div>
<div class="foot">
 <span>updated <b id="upd">–</b></span>
 <a href="/data">/data</a>
 <a href="http://65.21.170.230:8001/monitor" target="_blank">network monitor</a>
 <a href="http://65.21.170.230:8001/dag_explorer" target="_blank">DAG explorer</a>
</div></div>
<script>
const HOLDER = {holder_json};
const pro = n => n==null ? '–' : n.toLocaleString(undefined,{{maximumFractionDigits:2}});
const dur = s => s==null ? '–' : s<90 ? Math.round(s)+' s' : s<5400 ? (s/60).toFixed(1)+' min' : (s/3600).toFixed(1)+' h';
function card(k,v,cls,sub){{return `<div class="card"><div class="k">${{k}}</div><div class="v ${{cls||''}}">${{v}}${{sub?` <small>${{sub}}</small>`:''}}</div></div>`}}
async function tick(){{
  let d;
  try{{ d = await (await fetch('/data',{{cache:'no-store'}})).json(); }}catch(e){{ return }}
  const w = d.wallet||{{}}, net = d.net||{{}};
  document.getElementById('grid').innerHTML = [
    card('our transits', pro(d.wins), 'good', d.wins ? '' : 'none yet'),
    card('hashrate', d.hashrate ? (d.hashrate/1e9).toFixed(2) : '–', 'acc', 'GH/s'),
    card('difficulty', d.k == null ? '–' : d.k, '', 'bits'),
    card('expected win', dur(d.eta_seconds), 'warn'),
    card('network share', d.net_share==null?'–':(d.net_share*100).toFixed(2), '', '%'),
    card('wallet', w.total==null?'–':pro(w.total), '', 'PROX'),
    card('delegations', w.delegations==null?'–':w.delegations),
    card('mine chain', pro(net.mined), '', 'transits'),
    card('remaining', pro(Math.round(net.remaining||0)), '', 'PROX'),
    card('miners / 120 slots', pro(net.miners)),
  ].join('');
  const tb = document.getElementById('tb');
  tb.innerHTML = (net.transits||[]).map(t => {{
    const ours = t.miner === HOLDER;
    return `<tr><td>s${{t.slot}}</td><td>${{t.difficulty}}</td><td class="${{ours?'mine':''}}">${{ours?'OURS':t.miner.slice(0,12)+'…'}}</td></tr>`;
  }}).join('');
  document.getElementById('upd').textContent = new Date().toLocaleTimeString();
}}
tick(); setInterval(tick, 2000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/data"):
            body = json.dumps(snapshot()).encode()
            ctype = "application/json"
        elif self.path == "/" or self.path.startswith("/?"):
            body = PAGE.format(short=HOLDER[:8], holder=HOLDER,
                               holder_json=json.dumps(HOLDER)).encode()
            ctype = "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    global HOLDER
    try:
        with open(os.path.join(MINE, "proxi.yaml")) as f:
            m = re.search(r"holder_id:\s*([0-9a-f]{64})", f.read())
            if m:
                HOLDER = m.group(1)
    except OSError:
        pass
    for fn in (log_loop, monitor_loop, balance_loop):
        threading.Thread(target=fn, daemon=True).start()
    time.sleep(1)
    print(f"dashboard on http://0.0.0.0:{PORT} for {HOLDER}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
