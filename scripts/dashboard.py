#!/usr/bin/env python3
"""Live status page for a Proxima mining wallet (stdlib only).

    /data   JSON snapshot, polled by the page every 2 s
    /       the page

Reads the miner's logs, its /seeker/stats endpoint (per-seeker hashrate), the public
network monitor and `proxi node balance`. Read-only; no key material is served.
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
STATS = "http://127.0.0.1:8100/seeker/stats"
MONITOR = "http://65.21.170.230:8001/api/v1/monitor"
PORT = 8080

STATE = {"seekers": [], "history": [], "net": {}, "wallet": {}}
LOCK = threading.Lock()
HOLDER = "?"
TOKEN = ""
PREV = {}   # name -> deque of (wall clock, cumulative attempts) over the last ~30 s


def read(path, n=400_000):
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
        # the miner's log grows through the run; keep enough of it to see every
        # transit we confirmed (read() seeks, so a big window is cheap)
        mine = read(os.path.join(MINE, "mine.log"), n=8_000_000)
        with LOCK:
            STATE["hashrate"] = last(r"([\d_]+) H/s", mine, num)
            STATE["transit"] = last(r"mining transit #(\d+)", mine, int)
            STATE["k"] = last(r"mining transit #\d+[^\n]*?difficulty K=(\d+)", mine, int)
            STATE["target_slot"] = last(r"mining transit #\d+[^\n]*?target slot (\d+)", mine, int)
            STATE["attempts"] = last(r"mining\.\.\. ([\d_]+) attempts", mine, num)
            # who found each submitted transit: "SOLVED transit #202617; submitting 228031-... from O-2440051"
            found = {}
            for m in re.finditer(r"(?:SOLVED|IMPROVED) transit #(\d+); submitting \S+(?: from (\S+))?", mine):
                if m.group(2):
                    found[int(m.group(1))] = m.group(2)
            # our own confirmed transits, newest first: "#201579 226877-80-020982ba386c.."
            ours = []
            for m in re.finditer(r"confirmed transit #(\d+) (\d{6}-\d+-[0-9a-f]+)[^\n]*?\((\d+) of ours\)", mine):
                no = int(m.group(1))
                ours.append({"no": no, "txid": m.group(2), "slot": int(m.group(2).split("-")[0]),
                             "by": found.get(no, "")})
            STATE["our_transits"] = ours[-6:][::-1]
            STATE["confirmed"] = len(ours)
            STATE["minted"] = len(ours) * 95_000_000     # 95 PROX per transit
            # "our transits" counts ledger confirmations: the seeker-specific ACCEPTED
            # lines stopped existing when the box moved to a single-process miner
            STATE["wins"] = len(ours)
            STATE["solved"] = len(re.findall(r"SOLVED transit #", mine))
        time.sleep(1)


def stats_loop():
    while True:
        try:
            req = urllib.request.Request(STATS)
            if TOKEN:
                req.add_header("Authorization", "Bearer " + TOKEN)
            with urllib.request.urlopen(req, timeout=5) as r:
                d = json.load(r)
            now = time.time()
            rows = []
            with LOCK:
                for s in d.get("seekers") or []:
                    name = s.get("name") or "?"
                    att = s.get("attempts") or 0
                    age_ms = s.get("age_ms") or 0
                    if age_ms > 30_000:        # a seeker that stopped reporting
                        continue
                    # rate over a sliding ~30 s window of the seeker's own cumulative
                    # attempts: immune to the miner's EWMA spikes and to idle gaps
                    hist = PREV.setdefault(name, [])
                    hist.append((now, att))
                    while len(hist) > 2 and now - hist[0][0] > 30:
                        hist.pop(0)
                    rate = 0.0
                    if len(hist) >= 2:
                        dt = hist[-1][0] - hist[0][0]
                        datt = hist[-1][1] - hist[0][1]
                        if dt > 1 and datt >= 0:
                            rate = datt / dt
                    if age_ms > 6000:          # silent: stopped hashing
                        rate = 0.0
                    rows.append({"name": name, "hashrate": rate, "attempts": att, "age_ms": age_ms})
                total = sum(x["hashrate"] for x in rows)
                rows.sort(key=lambda x: x["hashrate"], reverse=True)
                STATE["seekers"] = rows
                STATE["seekers_total"] = total
                STATE["job"] = {"id": d.get("job_id"), "slot": d.get("slot"),
                                "k": d.get("k"), "ttl_ms": d.get("ttl_ms")}
                h = STATE.setdefault("history", [])
                h.append(round(total / 1e9, 3))
                del h[:-180]
                STATE["stats_ok"] = True
        except Exception as e:  # older miner build, or restarting
            with LOCK:
                STATE["stats_ok"] = False
                STATE["stats_err"] = str(e)
        time.sleep(2)


def monitor_loop():
    while True:
        try:
            with urllib.request.urlopen(MONITOR, timeout=10) as r:
                doc = json.load(r)
            live, f = doc["live"], doc["live"]["fair_launch"]
            hist = doc.get("mine_history") or {}
            with LOCK:
                STATE["net"] = {
                    "ok": True,
                    "slot": live.get("current_slot"),
                    "mined": f.get("mined_transactions"),
                    "remaining": (f.get("remaining") or 0) / 1e6,
                    "difficulty": f.get("difficulty"),
                    "miners": (f.get("contest") or {}).get("competing_miners"),
                    "mean_pace": hist.get("mean_pace"),
                    "mined_last_hour": hist.get("mined_last_hour"),
                    "transits": hist.get("transits", [])[:10],
                }
        except Exception as e:
            with LOCK:
                STATE["net"] = {"ok": False, "err": str(e)}
        time.sleep(3)


def balance_loop():
    while True:
        total = delegations = outputs = None
        try:
            out = subprocess.run(["proxi", "node", "balance"], cwd=MINE,
                                 capture_output=True, text=True, timeout=40).stdout
            m = re.search(r"TOTAL controlled on (\d+) outputs?: ([\d_]+)", out)
            if m:
                outputs = int(m.group(1))
                total = int(m.group(2).replace("_", "")) / 1e6
            dm = re.search(r"DELEGATIONS \((\d+)\)", out)
            if dm:
                delegations = int(dm.group(1))
            elif "NO DELEGATIONS" in out:
                delegations = 0
        except Exception:
            pass
        with LOCK:
            STATE["wallet"] = {"total": total, "delegations": delegations, "outputs": outputs}
        time.sleep(30)


def snapshot():
    with LOCK:
        s = json.loads(json.dumps(STATE))
    h = s.get("seekers_total") or s.get("hashrate")
    k = (s.get("job") or {}).get("k") or s.get("k")
    s["eta_seconds"] = (2 ** k / h) if (h and k) else None
    s["holder"] = HOLDER
    s["now"] = int(time.time())
    s["net_share"] = None
    net = s.get("net") or {}
    if h and net.get("difficulty") and net.get("mean_pace"):
        try:
            per_s = (2 ** net["difficulty"]) / (net["mean_pace"] * 10.24)
            s["net_share"] = h / per_s
        except Exception:
            pass
    if net.get("mean_pace") and s.get("net_share"):
        # one transit per mean_pace slots, 95 PROX each, in proportion to our share
        s["prox_per_day"] = (86400.0 / (net["mean_pace"] * 10.24)) * s["net_share"] * 95.0
    return s


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Proxima mining — __SHORT__</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0b0d12;--panel:#12151d;--panel2:#161a24;--ink:#e9ecf3;--dim:#8b93a7;--faint:#5b6376;
--line:#222836;--good:#48d38a;--acc:#6aa9e0;--warn:#e0b354;--bad:#e06c75;--gold:#e8c14a}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 ui-sans-serif,system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:22px 20px 60px}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:18px}
.logo{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,#2b6cb0,#48d38a);
display:flex;align-items:center;justify-content:center;font-weight:700;font-size:15px;color:#0b0d12}
h1{font-size:18px;margin:0;font-weight:650;letter-spacing:.2px}
.sub{color:var(--dim);font-size:11.5px;font-family:ui-monospace,Consolas,monospace}
.live{margin-left:auto;display:flex;align-items:center;gap:8px;color:var(--dim);font-size:12px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--good);box-shadow:0 0 0 0 rgba(72,211,138,.6);
animation:p 2s infinite}
@keyframes p{0%{box-shadow:0 0 0 0 rgba(72,211,138,.5)}70%{box-shadow:0 0 0 7px rgba(72,211,138,0)}100%{box-shadow:0 0 0 0 rgba(72,211,138,0)}}
.hero{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:14px;margin-bottom:14px}
@media(max-width:820px){.hero{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 17px}
.card.hero-card{padding:18px 20px}
.k{color:var(--dim);font-size:11px;letter-spacing:.09em;text-transform:uppercase}
.v{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums;margin-top:6px}
.v.big{font-size:34px;letter-spacing:-.5px}
.v small{font-size:13px;color:var(--dim);font-weight:450}
.good{color:var(--good)}.acc{color:var(--acc)}.warn{color:var(--warn)}.gold{color:var(--gold)}.bad{color:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;margin-bottom:14px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 17px;margin-bottom:14px}
.ph{display:flex;align-items:baseline;gap:10px;margin-bottom:12px}
.ph .k{font-size:11.5px}
.ph .n{margin-left:auto;color:var(--faint);font-size:11.5px;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th{color:var(--faint);font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;text-align:left;
font-weight:600;padding:0 8px 7px;border-bottom:1px solid var(--line)}
td{padding:8px;border-bottom:1px solid rgba(34,40,54,.55);font-size:13px}
tr:last-child td{border-bottom:none}
.name{font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
.dim{color:var(--faint);font-weight:400}
.bar{height:6px;border-radius:3px;background:#1d2330;overflow:hidden;min-width:70px}
.bar i{display:block;height:100%;border-radius:3px;background:linear-gradient(90deg,#2b6cb0,#48d38a)}
.host td{font-weight:600;padding-top:11px;padding-bottom:6px;color:var(--ink);background:var(--panel2)}
.st{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:7px;vertical-align:1px}
.on{background:var(--good)}.slow{background:var(--warn)}.off{background:var(--bad)}
.mine{color:var(--good);font-weight:600}
.foot{display:flex;gap:18px;flex-wrap:wrap;color:var(--faint);font-size:12px;margin-top:6px}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
#spark{display:block;width:100%;height:46px;margin-top:10px}
.empty{color:var(--faint);font-size:12.5px;padding:8px 0}
</style></head><body><div class="wrap">
<header>
  <div class="logo">P</div>
  <div><h1>Proxima mining</h1><div class="sub">__HOLDER__</div></div>
  <div class="live"><span class="dot"></span><span id="upd">connecting</span></div>
</header>

<div class="hero">
  <div class="card hero-card">
    <div class="k">total hashrate</div>
    <div class="v big acc" id="hr">–</div>
    <svg id="spark" viewBox="0 0 300 46" preserveAspectRatio="none"></svg>
  </div>
  <div class="card hero-card">
    <div class="k">our transits</div>
    <div class="v big good" id="wins">–</div>
    <div class="k" style="margin-top:8px">expected next win</div>
    <div class="v" style="font-size:17px" id="eta">–</div>
  </div>
  <div class="card hero-card">
    <div class="k">wallet</div>
    <div class="v big gold" id="bal">–</div>
    <div class="k" style="margin-top:8px">delegations</div>
    <div class="v" style="font-size:17px" id="dlg">–</div>
  </div>
</div>

<div class="grid" id="kpis"></div>

<div class="panel">
  <div class="ph"><span class="k">seekers</span><span class="n" id="sksum">–</span></div>
  <table><thead><tr><th>device</th><th style="width:40%">share</th>
  <th style="text-align:right">hashrate</th><th style="text-align:right">of total</th>
  </tr></thead><tbody id="sks"></tbody></table>
</div>

<div class="panel">
  <div class="ph"><span class="k">earnings</span><span class="n" id="earnsub">–</span></div>
  <div class="grid" style="margin:0 0 12px">
    <div class="card"><div class="k">est. PROX / day</div><div class="v gold" id="ppd">–</div></div>
    <div class="card"><div class="k">minted this run</div><div class="v" id="minted">–</div></div>
    <div class="card"><div class="k">our share of network</div><div class="v" id="share">–</div></div>
  </div>
  <table><thead><tr><th>slot</th><th>transit</th><th>when</th><th>found by</th><th>tx</th></tr></thead>
  <tbody id="tb"></tbody></table>
</div>

<div class="foot">
  <span>refreshed <b id="ts">–</b></span>
  <a href="/data">/data</a>
  <a href="http://65.21.170.230:8001/monitor" target="_blank">network monitor</a>
  <a href="http://65.21.170.230:8001/dag_explorer" target="_blank">DAG explorer</a>
</div>
</div>
<script>
const HOLDER = __HOLDER_JSON__;
const $ = id => document.getElementById(id);
const pro = n => n == null ? '–' : n.toLocaleString(undefined, {maximumFractionDigits: 2});
const dur = s => s == null ? '–' : s < 90 ? Math.round(s) + ' s'
  : s < 5400 ? (s / 60).toFixed(1) + ' min' : (s / 3600).toFixed(1) + ' h';
const gh = n => n == null ? '–' : (n / 1e9).toFixed(2);
const age = ms => ms == null ? '–' : ms < 1500 ? 'now'
  : ms < 60000 ? (ms/1000).toFixed(0) + 's ago' : (ms/60000).toFixed(1) + 'm ago';
const agoS = s => s == null ? '–' : s < 90 ? Math.round(s) + 's ago'
  : s < 5400 ? Math.round(s/60) + 'm ago'
  : s < 172800 ? (s/3600).toFixed(1) + 'h ago' : (s/86400).toFixed(1) + 'd ago';

function kpi(k, v, cls, sub) {
  return '<div class="card"><div class="k">' + k + '</div><div class="v ' + (cls||'') + '">'
    + v + (sub ? ' <small>' + sub + '</small>' : '') + '</div></div>';
}

function drawSpark(h) {
  const svg = $('spark'), W = 300, H = 46;
  if (!h || h.length < 2) return;
  const max = Math.max.apply(null, h.concat([0.001])), n = h.length;
  const pts = h.map((v,i) => ((i/(n-1)*W).toFixed(1)) + ',' +
                     (H-2-(v/max)*(H-6)).toFixed(1)).join(' ');
  svg.innerHTML = '<polyline points="' + pts + '" fill="none" stroke="#48d38a" stroke-width="1.6" '
    + 'stroke-linejoin="round" stroke-linecap="round" opacity=".9"/>';
}

function renderSeekers(d) {
  const list = d.seekers || [];
  const byHost = new Map();
  for (const s of list) {
    const parts = (s.name || '?').split('@');
    const host = parts.length > 1 ? parts[1] : parts[0];
    const g = byHost.get(host) || {host: host, rate: 0, n: 0, age: 1e9};
    g.rate += s.hashrate || 0;
    g.n += 1;
    g.age = Math.min(g.age, s.age_ms || 0);
    byHost.set(host, g);
  }
  const rows = [...byHost.values()].sort((a,b) => b.rate - a.rate);
  const total = rows.reduce((s,x) => s + x.rate, 0);
  $('sksum').textContent = rows.length
    ? rows.length + ' device' + (rows.length > 1 ? 's' : '') + ' · ' + gh(total) + ' GH/s'
    : (d.stats_ok === false ? 'per-seeker stats unavailable on this miner build' : 'none connected');
  if (!rows.length) {
    $('sks').innerHTML = '<tr><td colspan="4" class="empty">no seekers reporting</td></tr>';
    return;
  }
  const max = Math.max.apply(null, rows.map(x => x.rate).concat([1]));
  let html = '';
  for (const r of rows) {
    const pct = r.rate / max * 100;
    const st = r.age < 5000 ? 'on' : (r.age < 20000 ? 'slow' : 'off');
    html += '<tr>'
      + '<td class="name"><span class="st ' + st + '"></span>' + r.host + '</td>'
      + '<td><div class="bar"><i style="width:' + pct.toFixed(1) + '%"></i></div></td>'
      + '<td style="text-align:right">' + gh(r.rate) + ' GH/s</td>'
      + '<td style="text-align:right;color:var(--dim)">'
      + (total ? (r.rate/total*100).toFixed(1) : '–') + '%</td></tr>';
  }
  $('sks').innerHTML = html;
}

async function tick() {
  let d;
  try { d = await (await fetch('/data', {cache:'no-store'})).json(); } catch(e) { return }
  const net = d.net || {}, w = d.wallet || {}, job = d.job || {};
  const hr = d.seekers_total || d.hashrate || 0;
  $('hr').innerHTML = gh(hr) + ' <small>GH/s</small>';
  $('wins').textContent = d.wins == null ? '–' : d.wins;
  $('eta').textContent = dur(d.eta_seconds);
  $('bal').innerHTML = w.total == null ? '–' : pro(w.total) + ' <small>PROX</small>';
  $('dlg').textContent = w.delegations == null ? '–' : w.delegations;
  $('kpis').innerHTML = [
    kpi('difficulty', job.k != null ? job.k : (d.k != null ? d.k : '–'), 'warn', 'bits'),
    kpi('network share', d.net_share == null ? '–' : (d.net_share*100).toFixed(2), '', '%'),
    kpi('job slot', job.slot != null ? job.slot : (d.target_slot != null ? d.target_slot : '–'), 'acc'),
    kpi('network transits', pro(net.mined), '', 'mined'),
    kpi('remaining', pro(Math.round(net.remaining||0)), '', 'PROX'),
    kpi('miners / 120 slots', pro(net.miners)),
    kpi('network pace', net.mean_pace == null ? '–' : Number(net.mean_pace).toFixed(2), '', 'slots'),
  ].join('');
  renderSeekers(d);
  drawSpark(d.history);
  $('ppd').innerHTML = d.prox_per_day == null ? '–'
    : Math.round(d.prox_per_day).toLocaleString() + ' <small>PROX</small>';
  $('minted').innerHTML = d.minted == null ? '–'
    : (d.minted / 1e6).toFixed(2) + ' <small>PROX</small>';
  $('share').textContent = d.net_share == null ? '–' : (d.net_share * 100).toFixed(2) + '%';
  const ours = d.our_transits || [];
  $('earnsub').textContent = ours.length
    ? ours.length + ' confirmed transit' + (ours.length > 1 ? 's' : '') + ' of ours'
    : 'no transit won yet';
  $('tb').innerHTML = ours.map(t => {
      const ageS = (net.slot && t.slot) ? (net.slot - t.slot) * 10.24 : null;
      return '<tr><td>s' + t.slot + '</td><td>' + t.no + '</td>'
        + '<td style="color:var(--dim)">' + (ageS == null ? '–' : agoS(ageS)) + '</td>'
        + '<td class="name" style="color:var(--acc)">' + (t.by || '–') + '</td>'
        + '<td class="name" style="color:var(--good)">' + t.txid.slice(0, 22) + '…</td></tr>';
    }).join('') || '<tr><td colspan="5" class="empty">nothing mined yet</td></tr>';
  $('ts').textContent = new Date().toLocaleTimeString();
  $('upd').textContent = 'live';
}
tick(); setInterval(tick, 2000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/data"):
            body = json.dumps(snapshot()).encode()
            ctype = "application/json"
        elif self.path == "/" or self.path.startswith("/?"):
            body = (PAGE.replace("__SHORT__", HOLDER[:8])
                        .replace("__HOLDER_JSON__", json.dumps(HOLDER))
                        .replace("__HOLDER__", HOLDER)).encode()
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
    global HOLDER, TOKEN
    try:
        with open(os.path.join(MINE, "proxi.yaml")) as f:
            y = f.read()
        m = re.search(r"holder_id:\s*([0-9a-f]{64})", y)
        if m:
            HOLDER = m.group(1)
        m = re.search(r"^\s*token:\s*(\S+)", y, re.M)
        if m:
            TOKEN = m.group(1)
    except OSError:
        pass
    for fn in (log_loop, stats_loop, monitor_loop, balance_loop):
        threading.Thread(target=fn, daemon=True).start()
    time.sleep(1)
    print("dashboard on http://0.0.0.0:%d for %s (stats token: %s)"
          % (PORT, HOLDER, "yes" if TOKEN else "no"), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
