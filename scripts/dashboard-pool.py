#!/usr/bin/env python3
"""Proxima pool dashboard (proxi.suprnova.cc) + this box's share feed.

    /data   JSON snapshot
    /       the page

Pool figures come from the pool's public API; the share feed is this box's
ProxiChorizo log. Read-only.
"""
import json
import os
import re
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

POOL = "https://proxi.suprnova.cc/api/pools/proxi"
MINERS = POOL + "/miners"
BLOCKS = POOL + "/blocks"
PXLOG = "/root/proxima/px.log"
ADDR = "1e88ad2dc180069e716cdf18162ce759a8f8b1ebbf1667503fac0a37bcc000e0"
PORT = 8080

STATE = {}
LOCK = threading.Lock()


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "proxima-dashboard"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def tail(path, n=200_000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def pool_loop():
    while True:
        try:
            p = get(POOL)["pool"]
            ps = p["poolStats"]
            ns = p["networkStats"]
            pool_h = float(ps["poolHashrate"])
            net_h = float(ns["networkHashrate"])
            share = pool_h / net_h if net_h else 0
            block_time = float(p.get("blockTime") or 10.24)
            reward = float(p.get("avgBlockReward") or p.get("blockReward") or 95)
            fee = float(p.get("poolFeePercent") or 0)
            lb = p.get("lastBlockFound") or {}
            try:
                import math
                net_k = round(math.log2(float(ns.get("networkDifficulty") or 1)), 1)
            except Exception:
                net_k = None
            with LOCK:
                STATE["pool"] = {
                    "effort": float(p.get("poolEffort") or 0) * 100,
                    "avg100": float((p.get("roundStats") or {}).get("effortAvg100") or 0) * 100,
                    "netK": net_k,
                    "roundMiners": (p.get("roundStats") or {}).get("roundMiners"),
                    "roundShares": (p.get("roundStats") or {}).get("roundShares"),
                    "miners": ps.get("connectedMiners"),
                    "workers": ps.get("connectedWorkers"),
                    "poolHashrate": pool_h,
                    "netHashrate": net_h,
                    "share": share,
                    "blockTime": block_time,
                    "expected": block_time / share if share else None,
                    "reward": reward,
                    "net": reward * (1 - fee / 100),
                    "lastHeight": lb.get("height"),
                    "lastFound": lb.get("foundAt"),
                    "height": ns.get("blockHeight"),
                    "fee": fee,
                }
        except Exception as e:
            with LOCK:
                STATE["pool_err"] = str(e)
        time.sleep(5)


def miners_loop():
    while True:
        try:
            ms = get(MINERS)
            ours = next((m for m in ms if str(m.get("miner", "")).lower().startswith(ADDR[:8])), None)
            with LOCK:
                STATE["ours"] = ours
                STATE["minerCount"] = len(ms)
        except Exception as e:
            with LOCK:
                STATE["miners_err"] = str(e)
        time.sleep(20)


def blocks_loop():
    while True:
        try:
            bs = get(BLOCKS).get("blocks", [])[:12]
            with LOCK:
                STATE["blocks"] = bs
        except Exception:
            pass
        time.sleep(30)


def share_loop():
    while True:
        txt = tail(PXLOG)
        rows = []
        cur_k = None
        for line in txt.splitlines():
            mk = re.search(r"k=(\d+)", line)
            if "hash job" in line and mk:
                cur_k = int(mk.group(1))
                continue
            m = re.search(r"(\d\d:\d\d:\d\d)[^\n]*?GPU #(\d+): share accepted \((\d+)/(\d+)\)", line)
            if m:
                rows.append({"t": m.group(1), "gpu": int(m.group(2)),
                             "ok": int(m.group(3)), "all": int(m.group(4)), "k": cur_k})
        with LOCK:
            STATE["shares"] = rows[-14:][::-1]
            STATE["share_total"] = rows[-1]["all"] if rows else 0
            STATE["share_k"] = rows[-1]["k"] if rows else None
        time.sleep(2)


def balance_loop():
    import subprocess
    while True:
        total = None
        try:
            out = subprocess.run(["proxi", "node", "balance"], cwd="/root/proxima/mine",
                                 capture_output=True, text=True, timeout=45).stdout
            m = re.search(r"TOTAL controlled on (\d+) outputs?: ([\d_]+)", out)
            if m:
                total = int(m.group(2).replace("_", "")) / 1e6
        except Exception:
            pass
        with LOCK:
            STATE["balance"] = total
        time.sleep(60)


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Proxima pool dashboard</title>
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
h1{font-size:18px;margin:0;font-weight:650}
.sub{color:var(--dim);font-size:11.5px;font-family:ui-monospace,Consolas,monospace}
.live{margin-left:auto;color:var(--dim);font-size:12px;display:flex;align-items:center;gap:8px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--good);animation:p 2s infinite}
@keyframes p{0%{box-shadow:0 0 0 0 rgba(72,211,138,.5)}70%{box-shadow:0 0 0 7px rgba(72,211,138,0)}100%{box-shadow:0 0 0 0 rgba(72,211,138,0)}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 17px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;margin-bottom:14px}
.k{color:var(--dim);font-size:11px;letter-spacing:.09em;text-transform:uppercase}
.v{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums;margin-top:6px}
.v.big{font-size:34px;letter-spacing:-.5px}
.v small{font-size:13px;color:var(--dim);font-weight:450}
.good{color:var(--good)}.acc{color:var(--acc)}.warn{color:var(--warn)}.gold{color:var(--gold)}.bad{color:var(--bad)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 17px;margin-bottom:14px}
.ph{display:flex;align-items:baseline;gap:10px;margin-bottom:12px}
.ph .k{font-size:11.5px}
.ph .n{margin-left:auto;color:var(--faint);font-size:11.5px;font-variant-numeric:tabular-nums}
.meter{position:relative;height:12px;background:#1d2330;border-radius:6px;margin:10px 0 6px}
.meter i{position:absolute;left:0;top:0;bottom:0;border-radius:6px;
background:linear-gradient(90deg,#48d38a,#a8c95a 45%,#e0b354 70%,#e06c75);transition:width .6s}
.meter b{position:absolute;left:50%;top:-3px;bottom:-3px;width:2px;background:var(--dim);opacity:.8}
.scale{display:flex;justify-content:space-between;color:var(--faint);font-size:11px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th{color:var(--faint);font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;text-align:left;
font-weight:600;padding:0 8px 7px;border-bottom:1px solid var(--line)}
td{padding:7px 8px;border-bottom:1px solid rgba(34,40,54,.55);font-size:13px}
tr:last-child td{border-bottom:none}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
.foot{display:flex;gap:18px;flex-wrap:wrap;color:var(--faint);font-size:12px;margin-top:6px}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.empty{color:var(--faint);font-size:12.5px;padding:8px 0}
</style></head><body><div class="wrap">
<header>
  <div class="logo">P</div>
  <div><h1>Proxima pool</h1><div class="sub">our miner __ADDR__</div></div>
  <div class="live"><span class="dot"></span><span id="upd">connecting</span></div>
</header>

<div class="panel">
  <div class="ph"><span class="k">current round progress</span><span class="n" id="efftxt">–</span></div>
  <div class="meter"><b id="mark"></b><i id="fill" style="width:0%"></i></div>
  <div class="scale"><span id="scalemin">0 %</span><span id="scalemid">100 % = the expected work for one block</span><span id="scalemax">200 %</span></div>
</div>

<div class="grid" id="kpis"></div>

<div class="panel">
  <div class="ph"><span class="k">our miners on the pool</span><span class="n" id="ourn">–</span></div>
  <table><thead><tr><th>address</th><th style="text-align:right">hashrate</th>
  <th style="text-align:right">shares/s</th><th style="text-align:right">workers</th>
  <th style="text-align:right">blocks</th><th style="text-align:right">last share</th></tr></thead>
  <tbody id="our"></tbody></table>
</div>

<div class="panel">
  <div class="ph"><span class="k">share submissions — this box</span><span class="n" id="shn">–</span></div>
  <table><thead><tr><th>time</th><th>ago</th><th>gpu</th>
  <th style="text-align:right">share k</th><th style="text-align:right">accepted</th>
  <th style="text-align:right">total</th></tr></thead>
  <tbody id="sh"></tbody></table>
</div>

<div class="panel">
  <div class="ph"><span class="k">recent pool blocks</span><span class="n" id="bln">–</span></div>
  <table><thead><tr><th>height</th><th>when</th><th style="text-align:right">reward</th>
  <th>miner</th></tr></thead><tbody id="bl"></tbody></table>
</div>

<div class="foot">
  <span>refreshed <b id="ts">–</b></span>
  <a href="/data">/data</a>
  <a href="https://proxi.suprnova.cc" target="_blank">pool site</a>
  <a href="https://proxi.suprnova.cc/api/pools/proxi" target="_blank">pool api</a>
</div>
</div>
<script>
const $ = id => document.getElementById(id);
const pro = n => n == null ? '–' : n.toLocaleString(undefined, {maximumFractionDigits: 2});
const gh = n => n == null ? '–' : (n / 1e9).toFixed(2);
const th = n => n == null ? '–' : (n / 1e12).toFixed(2);
const dur = s => s == null ? '–' : s < 90 ? Math.round(s) + ' s'
  : s < 5400 ? (s / 60).toFixed(1) + ' min' : (s / 3600).toFixed(1) + ' h';
const ago = ts => ts == null ? '–' : dur(Date.now() / 1000 - ts) + ' ago';
function kpi(k, v, cls, sub) {
  return '<div class="card"><div class="k">' + k + '</div><div class="v ' + (cls||'') + '">'
    + v + (sub ? ' <small>' + sub + '</small>' : '') + '</div></div>';
}
async function tick() {
  let d;
  try { d = await (await fetch('/data', {cache:'no-store'})).json(); } catch(e) { return }
  const p = d.pool || {}, o = d.ours;
  const eff = p.effort || 0;
  const maxScale = eff <= 200 ? 200 : Math.ceil(eff / 100) * 100;
  $('fill').style.width = Math.min(eff, maxScale) / maxScale * 100 + '%';
  $('mark').style.left = (100 / maxScale * 100) + '%';
  $('efftxt').textContent = eff.toFixed(2) + ' %'
    + (eff > 100 ? '  ·  ' + (eff / 100).toFixed(2) + '× expected' : '');
  $('scalemax').textContent = maxScale + ' %';
  $('scalemid').textContent = maxScale === 200
    ? '100 % = the expected work for one block'
    : '100 % = expected (scale 0–' + maxScale + ' %)';
  $('kpis').innerHTML = [
    kpi('expected time / block', dur(p.expected), 'acc'),
    kpi('avg effort, last 100', p.avg100 == null ? '–' : p.avg100.toFixed(1), 'warn', '%'),
    kpi('miners this round', pro(p.roundMiners)),
    kpi('shares this round', pro(p.roundShares)),
    kpi('last pool block', p.lastFound ? ago(p.lastFound) : '–'),
    kpi('wallet balance', d.balance == null ? '–' : d.balance.toFixed(2), 'gold', 'PROX'),
    kpi('next block pays', p.net == null ? '–' : p.net.toFixed(2), 'gold', 'PROX'),
    kpi('pool hashrate', th(p.poolHashrate), '', 'TH/s'),
    kpi('pool share of network', p.share == null ? '–' : (p.share * 100).toFixed(2), '', '%'),
  ].join('');
  $('ourn').textContent = d.minerCount ? d.minerCount + ' miners pool-wide' : '';
  $('our').innerHTML = o ? ('<tr><td class="mono">' + o.miner + '</td>'
      + '<td style="text-align:right" class="good">' + gh(o.hashrate) + ' GH/s</td>'
      + '<td style="text-align:right">' + (o.sharesPerSecond || 0).toFixed(2) + '</td>'
      + '<td style="text-align:right">' + (o.workerCount || 0) + '</td>'
      + '<td style="text-align:right">' + (o.blocksFound || 0) + '</td>'
      + '<td style="text-align:right;color:var(--faint)">' + (o.lastShare ? ago(Date.parse(o.lastShare)/1000) : '–') + '</td></tr>')
    : '<tr><td colspan="6" class="empty">our miner not visible on the pool yet</td></tr>';
  const sh = d.shares || [];
  $('shn').textContent = (d.share_total ? d.share_total + ' accepted on this box' : '')
    + (d.share_k != null ? '  ·  share k=' + d.share_k : '')
    + (p.netK != null ? '  ·  network K≈' + p.netK : '');
  const now = new Date();
  const agoClock = t => {
    const [h, m, s] = t.split(':').map(Number);
    const d0 = new Date(now); d0.setHours(h, m, s, 0);
    let secs = (now - d0) / 1000; if (secs < 0) secs += 86400;
    return secs < 60 ? Math.round(secs) + ' s ago' : Math.round(secs / 60) + ' min ago';
  };
  $('sh').innerHTML = sh.map(s => '<tr><td class="mono" style="color:var(--faint)">' + s.t + '</td>'
      + '<td style="color:var(--dim)">' + agoClock(s.t) + '</td>'
      + '<td>GPU #' + s.gpu + '</td>'
      + '<td style="text-align:right;color:var(--warn)">' + (s.k == null ? '–' : s.k) + '</td>'
      + '<td style="text-align:right" class="good">' + s.ok + '</td>'
      + '<td style="text-align:right;color:var(--dim)">' + s.all + '</td></tr>').join('')
    || '<tr><td colspan="6" class="empty">no shares in the log</td></tr>';
  const bl = d.blocks || [];
  $('bln').textContent = bl.length ? 'last ' + bl.length : '';
  $('bl').innerHTML = bl.map(b => '<tr><td class="mono">#' + b.height + '</td>'
      + '<td style="color:var(--dim)">' + ago(Date.parse(b.created)/1000) + '</td>'
      + '<td style="text-align:right">' + Number(b.reward).toFixed(2) + '</td>'
      + '<td class="mono" style="color:var(--dim)">' + (b.miner || '') + '</td></tr>').join('')
    || '<tr><td colspan="4" class="empty">no blocks</td></tr>';
  $('ts').textContent = new Date().toLocaleTimeString();
  $('upd').textContent = 'live';
}
tick(); setInterval(tick, 3000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/data"):
            with LOCK:
                body = json.dumps(STATE).encode()
            ctype = "application/json"
        elif self.path == "/" or self.path.startswith("/?"):
            body = PAGE.replace("__ADDR__", ADDR).encode()
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
    for fn in (pool_loop, miners_loop, blocks_loop, share_loop, balance_loop):
        threading.Thread(target=fn, daemon=True).start()
    time.sleep(1)
    print("pool dashboard on http://0.0.0.0:%d for %s" % (PORT, ADDR), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
