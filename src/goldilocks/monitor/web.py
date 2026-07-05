"""Web dashboard (roadmap phase 3): the `goldilocks status` truth, viewable off-machine.

FastAPI app reading ONLY the state store. JSON API + one self-contained HTML page
(no build step, no static assets) that polls the API and draws equity sparklines.
Decimals are serialized as strings — precision survives the wire; the display edge
may round.

Run with `goldilocks web` (binds 127.0.0.1 by default; pass --host 0.0.0.0 to expose
on the LAN — there is no auth, so keep it inside networks you trust).
"""

from __future__ import annotations

from pathlib import Path


def create_app(db_path: Path):
    from fastapi import FastAPI, HTTPException

    from goldilocks.store import StateStore

    app = FastAPI(title="goldilocks", docs_url=None, redoc_url=None)

    def store() -> StateStore:
        # One connection per request: SQLite connections aren't thread-safe and
        # FastAPI sync endpoints run in a threadpool. WAL mode makes this cheap.
        return StateStore(db_path)

    @app.get("/api/status")
    def api_status():
        return [
            {
                "strategy": r.strategy_name,
                "mode": r.mode,
                "allocation": str(r.allocation),
                "exposure": str(r.exposure),
                "equity": str(r.equity) if r.equity is not None else None,
                "realized_pnl": str(r.realized_pnl),
                "wins": r.wins,
                "losses": r.losses,
                "state": "stopped" if r.stopped else "running",
            }
            for r in store().status_rows()
        ]

    @app.get("/api/equity/{strategy}")
    def api_equity(strategy: str, limit: int = 500):
        s = store()
        if strategy not in {r.strategy_name for r in s.status_rows()}:
            raise HTTPException(404, f"unknown strategy {strategy!r}")
        return [
            {"t": ts.isoformat(), "equity": str(eq)}
            for ts, eq in s.equity_curve_for(strategy, limit=limit)
        ]

    @app.get("/api/fills")
    def api_fills(limit: int = 50):
        return store().recent_fills(limit=limit)

    from fastapi.responses import HTMLResponse

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _PAGE

    return app


_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>goldilocks</title>
<style>
  body { font: 14px/1.5 -apple-system, system-ui, sans-serif; margin: 2rem auto;
         max-width: 900px; color: #1a1a1a; padding: 0 1rem; }
  h1 { font-size: 18px; } h2 { font-size: 15px; margin-top: 2rem; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: right; padding: 6px 10px; border-bottom: 1px solid #ddd;
           font-variant-numeric: tabular-nums; }
  th:first-child, td:first-child { text-align: left; }
  th { font-weight: 600; border-bottom: 2px solid #999; }
  .pos { color: #0a7a4a; } .neg { color: #b03030; }
  .stopped { color: #999; } svg { vertical-align: middle; }
  #updated { color: #999; font-size: 12px; }
</style></head><body>
<h1>goldilocks <span id="updated"></span></h1>
<table id="status"><thead><tr>
  <th>strategy</th><th>mode</th><th>alloc</th><th>exposure</th><th>equity</th>
  <th>equity (recent)</th><th>realized</th><th>W/L</th><th>state</th>
</tr></thead><tbody></tbody></table>
<h2>recent fills</h2>
<table id="fills"><thead><tr>
  <th>time (UTC)</th><th>strategy</th><th>instrument</th><th>side</th>
  <th>qty</th><th>price</th>
</tr></thead><tbody></tbody></table>
<script>
function spark(points) {
  if (!points.length) return "";
  const vals = points.map(p => parseFloat(p.equity));
  const lo = Math.min(...vals), hi = Math.max(...vals), w = 160, h = 28;
  const span = (hi - lo) || 1;
  const xy = vals.map((v, i) =>
    `${(i / Math.max(vals.length - 1, 1) * w).toFixed(1)},` +
    `${(h - 3 - (v - lo) / span * (h - 6)).toFixed(1)}`).join(" ");
  const color = vals[vals.length - 1] >= vals[0] ? "#0a7a4a" : "#b03030";
  return `<svg width="${w}" height="${h}"><polyline points="${xy}" fill="none"
          stroke="${color}" stroke-width="1.5"/></svg>`;
}
function cls(v) { return parseFloat(v) >= 0 ? "pos" : "neg"; }
async function refresh() {
  const rows = await (await fetch("/api/status")).json();
  const body = document.querySelector("#status tbody");
  body.innerHTML = "";
  for (const r of rows) {
    const eq = await (await fetch(`/api/equity/${r.strategy}?limit=200`)).json();
    body.insertAdjacentHTML("beforeend", `<tr class="${r.state}">
      <td>${r.strategy}</td><td>${r.mode}</td>
      <td>${parseFloat(r.allocation).toFixed(2)}</td>
      <td>${parseFloat(r.exposure).toFixed(2)}</td>
      <td>${r.equity ? parseFloat(r.equity).toFixed(2) : "-"}</td>
      <td>${spark(eq)}</td>
      <td class="${cls(r.realized_pnl)}">${parseFloat(r.realized_pnl).toFixed(2)}</td>
      <td>${r.wins}/${r.losses}</td><td>${r.state}</td></tr>`);
  }
  const fills = await (await fetch("/api/fills?limit=25")).json();
  document.querySelector("#fills tbody").innerHTML = fills.map(f => `<tr>
    <td>${f.filled_at.slice(0, 16).replace("T", " ")}</td><td>${f.strategy}</td>
    <td>${f.instrument}</td><td>${f.side}</td><td>${f.quantity}</td>
    <td>${f.price}</td></tr>`).join("");
  document.querySelector("#updated").textContent =
    "updated " + new Date().toLocaleTimeString();
}
refresh(); setInterval(refresh, 5000);
</script></body></html>
"""
