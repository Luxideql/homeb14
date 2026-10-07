#!/usr/bin/env python3
"""Дашборд UNI: статистика по журналу + живые позиции. Порт 8097.

Читает trades_log_uni.csv (закрытые сделки) и позиции с биржи, считает net, winrate,
profit factor, матожидание, payoff, безубыточный WR, просадку, разбивки по источникам /
причинам / монетам, лонг/шорт, цифры для калибровки стопов. Отдаёт / (страница) и /api (JSON).
Смотреть: http://homeb14:8097 (Tailscale) или публично через Funnel.
"""
import csv
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "scripts"))
import bybit_bot          # noqa: E402
import money              # noqa: E402

PORT = int(os.environ.get("UNI_DASH_PORT", "8097"))
TRADES = os.path.join(BASE, "trades_log_uni.csv")
STATE = os.path.join(BASE, "positions_state_uni.json")

bybit_bot.load_env_file(os.path.join(BASE, ".env"))
_CLIENT = None
_CACHE = {"t": 0.0, "data": None}
CACHE_TTL = 5.0


def client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = bybit_bot.Client()
    return _CLIENT


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def read_trades():
    rows = []
    try:
        with open(TRADES, "r", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.append(r)
    except OSError:
        pass
    return rows


def read_state():
    try:
        with open(STATE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"positions": {}, "pending": {}}


def coin(symbol):
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def compute_stats(rows):
    n = len(rows)
    nets = [_f(r.get("net_usdt")) for r in rows]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    net = sum(nets)
    by_reason, by_source, by_coin = {}, {}, {}
    long_net = short_net = 0.0
    long_n = short_n = 0
    today = time.strftime("%Y-%m-%d", time.gmtime())
    today_net = 0.0
    today_n = 0
    for r in rows:
        nt = _f(r.get("net_usdt"))
        for key, d in ((r.get("exit_reason") or "?", by_reason),
                       (r.get("source") or "?", by_source),
                       (r.get("coin") or "?", by_coin)):
            a = d.setdefault(key, {"n": 0, "net": 0.0, "wins": 0})
            a["n"] += 1; a["net"] += nt; a["wins"] += 1 if nt > 0 else 0
        if r.get("side") == "Buy":
            long_n += 1; long_net += nt
        elif r.get("side") == "Sell":
            short_n += 1; short_net += nt
        if (r.get("exit_time_utc") or "").startswith(today):
            today_n += 1; today_net += nt
    loser_maxnets = [_f(r.get("max_net_reached")) for r in rows if _f(r.get("net_usdt")) <= 0]
    maes = [_f(r.get("mae_pct")) for r in rows]
    win_maes = [_f(r.get("mae_pct")) for r in rows if _f(r.get("net_usdt")) > 0]
    loss_maes = [_f(r.get("mae_pct")) for r in rows if _f(r.get("net_usdt")) <= 0]
    cum = []
    acc = 0.0
    for r in rows:
        acc += _f(r.get("net_usdt"))
        cum.append(round(acc, 2))
    # максимальная просадка по кривой накопленного net
    peak = 0.0
    max_dd = 0.0
    for v in [0.0] + cum:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)

    def avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else 0.0
    aw = avg(wins)
    al = avg(losses)
    payoff = round(aw / abs(al), 2) if al < 0 else 0.0
    be_wr = round(abs(al) / (abs(al) + aw) * 100, 1) if (aw + abs(al)) > 0 else 0.0
    return {
        "trades": n, "net": round(net, 2),
        "wins": len(wins), "losses": len(losses),
        "winrate": round(len(wins) / n * 100, 1) if n else 0.0,
        "pf": round(gross_win / gross_loss, 2) if gross_loss > 0 else (round(gross_win, 2) if gross_win else 0.0),
        "avg_win": aw, "avg_loss": al,
        "expectancy": round(net / n, 2) if n else 0.0,
        "payoff": payoff, "be_winrate": be_wr,
        "gross_win": round(gross_win, 2), "gross_loss": round(gross_loss, 2),
        "max_dd": round(max_dd, 2),
        "best": round(max(nets), 2) if nets else 0.0,
        "worst": round(min(nets), 2) if nets else 0.0,
        "avg_hold": avg([_f(r.get("hold_min")) for r in rows]),
        "long_net": round(long_net, 2), "long_n": long_n,
        "short_net": round(short_net, 2), "short_n": short_n,
        "today_net": round(today_net, 2), "today_n": today_n,
        "by_reason": by_reason, "by_source": by_source, "by_coin": by_coin,
        "loser_avg_maxnet": avg(loser_maxnets),
        "avg_mae": avg(maes), "win_avg_mae": avg(win_maes), "loss_avg_mae": avg(loss_maes),
        "cum": cum,
    }


def live_open(state):
    """Живые позиции с биржи + source/scenario из state, текущий net."""
    out = []
    total = 0.0
    long_n = short_n = 0
    try:
        positions = [p for p in client().positions() if abs(_f(p.get("size"))) > 0]
    except Exception:
        positions = []
    for p in positions:
        sym = p["symbol"]
        st = state.get("positions", {}).get(sym, {})
        side = p.get("side")
        entry = _f(p.get("avgPrice"))
        qty = abs(_f(p.get("size")))
        mark = _f(p.get("markPrice"), entry)
        zone = st.get("zone", "normal")
        fee_in = st.get("fee_in", entry * qty * money.taker_rate(zone == "innovation"))
        net = money.net(side, entry, qty, fee_in, mark, money.taker_rate(zone == "innovation"))
        total += net
        if side == "Buy":
            long_n += 1
        else:
            short_n += 1
        chg = ((mark - entry) / entry * 100.0) if entry else 0.0
        if side == "Sell":
            chg = -chg
        out.append({
            "coin": coin(sym), "source": st.get("source", "?"), "scenario": st.get("scenario", "?"),
            "side": "L" if side == "Buy" else "S", "entry": entry, "qty": qty, "mark": mark,
            "chg": round(chg, 2), "notional": round(mark * qty, 0),
            "net": round(net, 2), "step": st.get("step", 0),
            "trailing": bool(st.get("trailing_set")) or bool(p.get("trailingStop") not in (None, "", "0")),
        })
    out.sort(key=lambda x: x["net"])
    return out, round(total, 2), long_n, short_n


def balance():
    try:
        w = client().wallet_balance()
        acc = (w.get("list") or [{}])[0]
        eq = _f(acc.get("totalEquity"))
        av = acc.get("totalAvailableBalance")
        av = _f(av) if av not in (None, "") else eq - _f(acc.get("totalInitialMargin"))
        return {"equity": round(eq, 2), "available": round(av, 2)}
    except Exception:
        return {"equity": 0.0, "available": 0.0}


def build_api():
    now = time.time()
    if _CACHE["data"] and now - _CACHE["t"] < CACHE_TTL:
        return _CACHE["data"]
    rows = read_trades()
    state = read_state()
    opens, open_net, long_n, short_n = live_open(state)
    data = {
        "ts": time.strftime("%d.%m %H:%M:%S UTC", time.gmtime()),
        "balance": balance(),
        "open": opens, "open_net": open_net,
        "open_long": long_n, "open_short": short_n,
        "pending": len(state.get("pending", {})),
        "stats": compute_stats(rows),
        "recent": [{
            "exit": (r.get("exit_time_utc") or "")[5:16].replace("T", " "),
            "coin": r.get("coin"), "source": r.get("source"), "scenario": r.get("scenario"),
            "side": "L" if r.get("side") == "Buy" else "S",
            "net": round(_f(r.get("net_usdt")), 2), "reason": r.get("exit_reason"),
            "maxnet": round(_f(r.get("max_net_reached")), 1), "hold": round(_f(r.get("hold_min")), 0),
        } for r in rows[-30:][::-1]],
    }
    _CACHE["t"] = now
    _CACHE["data"] = data
    return data


PAGE = """<!doctype html>
<html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UNI дашборд</title>
<style>
  :root{ color-scheme:dark;
    --bg:#0b0c0e; --bg2:#101216; --card:#15171c; --card2:#1b1e24; --line:#262a31;
    --ink:#f2f4f8; --ink2:#aeb4c0; --ink3:#727a88;
    --accent:#4c8dff; --good:#2ecc71; --bad:#ff5b63; --warn:#f6b73c;
    --goodbg:rgba(46,204,113,.14); --badbg:rgba(255,91,99,.14); --warnbg:rgba(246,183,60,.14); }
  *{ box-sizing:border-box; }
  html,body{ margin:0; background:var(--bg); color:var(--ink2);
    font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; font-size:14px;
    -webkit-font-smoothing:antialiased; }
  body{ background:
    radial-gradient(1200px 400px at 80% -120px, rgba(76,141,255,.10), transparent 60%),
    var(--bg); min-height:100vh; }
  header{ display:flex; align-items:center; gap:10px; padding:12px 16px;
    border-bottom:1px solid var(--line); position:sticky; top:0; z-index:9;
    background:rgba(11,12,14,.82); backdrop-filter:blur(10px); }
  header .logo{ font-weight:700; color:var(--ink); font-size:15px; letter-spacing:.02em; }
  header .logo span{ color:var(--accent); }
  .dot{ width:9px;height:9px;border-radius:50%;background:var(--ink3); box-shadow:0 0 0 0 rgba(46,204,113,.5);
    transition:background .3s; }
  .live .dot{ background:var(--good); animation:pulse 2s infinite; }
  @keyframes pulse{ 0%{box-shadow:0 0 0 0 rgba(46,204,113,.45)} 70%{box-shadow:0 0 0 7px rgba(46,204,113,0)} 100%{box-shadow:0 0 0 0 rgba(46,204,113,0)} }
  .ts{ color:var(--ink3); font-size:12px; margin-left:auto; font-variant-numeric:tabular-nums; }
  .wrap{ padding:14px; display:flex; flex-direction:column; gap:14px; max-width:760px; margin:0 auto; }

  .hero{ background:linear-gradient(160deg,var(--card2),var(--card)); border:1px solid var(--line);
    border-radius:18px; padding:16px 18px; }
  .hero .top{ display:flex; align-items:baseline; justify-content:space-between; gap:10px; }
  .hero .eqlbl{ font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--ink3); }
  .hero .eq{ font-size:30px; font-weight:700; color:var(--ink); font-variant-numeric:tabular-nums; line-height:1.1; }
  .verdict{ font-size:11px; font-weight:600; padding:4px 9px; border-radius:999px; white-space:nowrap; }
  .hero .split{ display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:14px; }
  .hero .b{ background:var(--bg2); border:1px solid var(--line); border-radius:12px; padding:10px 12px; }
  .hero .b .l{ font-size:11px; color:var(--ink3); text-transform:uppercase; letter-spacing:.05em; }
  .hero .b .v{ font-size:22px; font-weight:700; font-variant-numeric:tabular-nums; margin-top:2px; }
  .hero .b .s{ font-size:11px; color:var(--ink3); margin-top:2px; }

  .kpis{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }
  @media(max-width:600px){ .kpis{ grid-template-columns:repeat(2,1fr); } .hero .eq{font-size:26px;} }
  .kpi{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:11px 13px; }
  .kpi .l{ font-size:10.5px; color:var(--ink3); text-transform:uppercase; letter-spacing:.05em; }
  .kpi .v{ font-size:19px; font-weight:700; color:var(--ink); margin-top:4px; font-variant-numeric:tabular-nums; }
  .kpi .s{ font-size:10.5px; color:var(--ink3); margin-top:2px; }

  .card{ background:var(--card); border:1px solid var(--line); border-radius:16px; padding:14px 16px; }
  .card h3{ margin:0 0 12px; font-size:11.5px; letter-spacing:.06em; text-transform:uppercase;
    color:var(--ink3); font-weight:700; display:flex; align-items:center; gap:8px; }
  .card h3 .r{ margin-left:auto; color:var(--ink3); font-weight:500; text-transform:none; letter-spacing:0; }
  .pos{ color:var(--good); } .neg{ color:var(--bad); } .zero{ color:var(--ink3); }

  table{ width:100%; border-collapse:collapse; font-size:13px; }
  th,td{ text-align:right; padding:7px 6px; border-bottom:1px solid var(--line); white-space:nowrap; }
  th:first-child,td:first-child{ text-align:left; }
  tr:last-child td{ border-bottom:none; }
  th{ color:var(--ink3); font-weight:600; font-size:10.5px; text-transform:uppercase; letter-spacing:.04em; }
  tbody tr:hover{ background:var(--card2); }
  td.coin{ color:var(--ink); font-weight:600; }

  .badge{ display:inline-block; padding:1px 7px; border-radius:6px; font-size:10.5px; font-weight:700; }
  .L{ background:var(--goodbg); color:var(--good); } .S{ background:var(--badbg); color:var(--bad); }
  .chip{ display:inline-block; padding:1px 7px; border-radius:6px; background:var(--card2);
    border:1px solid var(--line); color:var(--ink2); font-size:10.5px; }

  .bar{ display:flex; align-items:center; gap:10px; margin:7px 0; font-size:13px; }
  .bar .n{ width:70px; flex:none; color:var(--ink); font-weight:600; overflow:hidden; text-overflow:ellipsis; }
  .bar .t{ flex:1; height:18px; background:var(--bg2); border-radius:6px; overflow:hidden; position:relative; }
  .bar .f{ height:100%; border-radius:6px; transition:width .5s ease; }
  .bar .val{ width:118px; flex:none; text-align:right; font-variant-numeric:tabular-nums; color:var(--ink3); font-size:12px; }
  .bar .val b{ font-weight:700; }

  svg{ width:100%; height:150px; display:block; }
  .axis{ display:flex; justify-content:space-between; font-size:10.5px; color:var(--ink3);
    font-variant-numeric:tabular-nums; margin-top:4px; }
  .hint{ font-size:11px; color:var(--ink3); margin-top:10px; line-height:1.5; }
  .empty{ color:var(--ink3); padding:10px 0; text-align:center; }
  .grid2{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }
  .mini{ background:var(--bg2); border:1px solid var(--line); border-radius:10px; padding:8px 10px; }
  .mini .l{ font-size:10.5px; color:var(--ink3); } .mini .v{ font-size:15px; font-weight:700; margin-top:2px; font-variant-numeric:tabular-nums; }
  footer{ text-align:center; color:var(--ink3); font-size:11px; padding:4px 0 20px; }
</style></head>
<body>
<header><span class="dot" id="dot"></span><span class="logo">UNI<span>•</span>дашборд</span><span class="ts" id="ts">…</span></header>
<div class="wrap">

  <div class="hero">
    <div class="top">
      <div><div class="eqlbl">Баланс demo</div><div class="eq" id="h_eq">—</div></div>
      <span class="verdict" id="h_verdict">—</span>
    </div>
    <div class="split">
      <div class="b"><div class="l">Реалізовано net</div><div class="v" id="h_net">—</div><div class="s" id="h_net_s"></div></div>
      <div class="b"><div class="l">Відкрито net</div><div class="v" id="h_open">—</div><div class="s" id="h_open_s"></div></div>
    </div>
  </div>

  <div class="kpis">
    <div class="kpi"><div class="l">Winrate</div><div class="v" id="k_wr">—</div><div class="s" id="k_wr_s"></div></div>
    <div class="kpi"><div class="l">Profit factor</div><div class="v" id="k_pf">—</div><div class="s">вигр/програш</div></div>
    <div class="kpi"><div class="l">Маточ. / угоду</div><div class="v" id="k_exp">—</div><div class="s">expectancy</div></div>
    <div class="kpi"><div class="l">Payoff</div><div class="v" id="k_po">—</div><div class="s" id="k_po_s"></div></div>
    <div class="kpi"><div class="l">Угод</div><div class="v" id="k_tr">—</div><div class="s" id="k_tr_s"></div></div>
    <div class="kpi"><div class="l">Макс. просадка</div><div class="v neg" id="k_dd">—</div><div class="s">peak-to-trough</div></div>
    <div class="kpi"><div class="l">Сьогодні</div><div class="v" id="k_td">—</div><div class="s" id="k_td_s"></div></div>
    <div class="kpi"><div class="l">Серед. трим.</div><div class="v" id="k_hold">—</div><div class="s">хв</div></div>
  </div>

  <div class="card"><h3>Крива P&amp;L <span class="r" id="c_range"></span></h3>
    <svg id="curve" viewBox="0 0 600 150" preserveAspectRatio="none"></svg>
    <div class="axis"><span id="c_min"></span><span id="c_max"></span></div>
  </div>

  <div class="card"><h3>Відкриті позиції <span class="r" id="o_cnt"></span></h3><div id="open"></div></div>

  <div class="card"><h3>Де плюс / мінус — за джерелом</h3><div id="by_source"></div></div>

  <div class="card"><h3>За монетою</h3><div id="by_coin"></div></div>

  <div class="card"><h3>За причиною виходу</h3><div id="by_reason"></div></div>

  <div class="card"><h3>Лонг vs шорт</h3><div class="grid2" id="ls"></div></div>

  <div class="card"><h3>Калібрування стопів</h3><div class="grid2" id="calib"></div>
    <div class="hint" id="be_hint"></div></div>

  <div class="card"><h3>Останні угоди</h3><div id="recent"></div></div>

  <footer>оновлюється щодесять секунд • дані з журналу + біржі</footer>
</div>
<script>
const $=id=>document.getElementById(id);
const money=v=>(v>=0?"+$":"−$")+Math.abs(v).toFixed(2);
const money0=v=>(v>=0?"+$":"−$")+Math.abs(v).toFixed(0);
const cls=v=>v>0?"pos":(v<0?"neg":"zero");
const nf=v=>Number(v).toLocaleString("uk-UA");

function fill(d){
  $("ts").textContent=d.ts; document.body.classList.add("live");
  const s=d.stats;

  // hero
  $("h_eq").textContent="$"+nf(d.balance.equity);
  $("h_net").className="v "+cls(s.net); $("h_net").textContent=money(s.net);
  $("h_net_s").textContent="gross +$"+s.gross_win.toFixed(0)+" / −$"+s.gross_loss.toFixed(0);
  $("h_open").className="v "+cls(d.open_net); $("h_open").textContent=money(d.open_net);
  $("h_open_s").textContent=d.open.length+" поз. · L"+d.open_long+" / S"+d.open_short+(d.pending?(" · pend "+d.pending):"");
  const v=$("h_verdict");
  if(s.trades<5){ v.textContent="мало даних"; v.style.background="var(--card2)"; v.style.color="var(--ink3)"; }
  else if(s.pf>=1.3){ v.textContent="✓ є перевага"; v.style.background="var(--goodbg)"; v.style.color="var(--good)"; }
  else if(s.pf>=1.0){ v.textContent="≈ межа"; v.style.background="var(--warnbg)"; v.style.color="var(--warn)"; }
  else{ v.textContent="✕ немає переваги"; v.style.background="var(--badbg)"; v.style.color="var(--bad)"; }

  // kpi
  $("k_wr").textContent=s.winrate+"%";
  $("k_wr_s").textContent=s.wins+"W / "+s.losses+"L";
  $("k_pf").textContent=s.pf; $("k_pf").className="v "+(s.pf>=1?"pos":"neg");
  $("k_exp").textContent=money(s.expectancy); $("k_exp").className="v "+cls(s.expectancy);
  $("k_po").textContent=s.payoff.toFixed(2);
  $("k_po_s").textContent="$"+s.avg_win.toFixed(0)+" / $"+Math.abs(s.avg_loss).toFixed(0);
  $("k_tr").textContent=s.trades;
  $("k_tr_s").textContent="кращ "+money0(s.best)+" / гірш "+money0(s.worst);
  $("k_dd").textContent="−$"+s.max_dd.toFixed(2);
  $("k_td").textContent=money(s.today_net); $("k_td").className="v "+cls(s.today_net);
  $("k_td_s").textContent=s.today_n+" угод";
  $("k_hold").textContent=s.avg_hold;

  // кривая
  drawCurve(s.cum);

  // открытые
  $("o_cnt").textContent=d.open.length?("net "+money(d.open_net)):"";
  let o=d.open.length? '<table><thead><tr><th>Монета</th><th>Дж</th><th>Бік</th><th>Вхід→марк</th><th>Δ%</th><th>net</th></tr></thead><tbody>' : '<div class="empty">немає відкритих позицій'+(d.pending?(" · pending "+d.pending):"")+'</div>';
  d.open.forEach(p=>{ o+='<tr><td class="coin">'+p.coin+(p.trailing?' <span title="трейлинг активний">🎯</span>':'')+'</td>'+
    '<td><span class="chip">'+p.source+'</span></td>'+
    '<td><span class="badge '+p.side+'">'+p.side+'</span></td>'+
    '<td>'+fmtp(p.entry)+' → '+fmtp(p.mark)+'</td>'+
    '<td class="'+cls(p.chg)+'">'+(p.chg>=0?"+":"")+p.chg+'%</td>'+
    '<td class="'+cls(p.net)+'">'+money(p.net)+'</td></tr>'; });
  if(d.open.length) o+='</tbody></table>'; $("open").innerHTML=o;

  // разбивки
  $("by_source").innerHTML=bars(s.by_source);
  $("by_coin").innerHTML=bars(s.by_coin,8);
  $("by_reason").innerHTML=bars(s.by_reason);

  // лонг/шорт
  $("ls").innerHTML=
    mini("Лонг",money(s.long_net),s.long_n+" угод",cls(s.long_net))+
    mini("Шорт",money(s.short_net),s.short_n+" угод",cls(s.short_net));

  // калибровка
  $("calib").innerHTML=
    mini("max_net у збиткових","$"+s.loser_avg_maxnet,"бачили до розвороту","")+
    mini("MAE середній",s.avg_mae+"%","хід проти входу","")+
    mini("MAE виграшні",s.win_avg_mae+"%","","pos")+
    mini("MAE збиткові",s.loss_avg_mae+"%","","neg");
  $("be_hint").innerHTML="Потрібен winrate ≥ <b>"+s.be_winrate+"%</b> щоб виходити в нуль (payoff "+s.payoff.toFixed(2)+") — зараз <b class='"+(s.winrate>=s.be_winrate?"pos":"neg")+"'>"+s.winrate+"%</b>.";

  // последние
  let r=d.recent.length? '<table><thead><tr><th>Час</th><th>Монета</th><th>Дж</th><th>net</th><th>макс</th><th>вихід</th></tr></thead><tbody>' : '<div class="empty">угод ще немає</div>';
  d.recent.forEach(t=>{ r+='<tr><td>'+t.exit+'</td>'+
    '<td class="coin">'+t.coin+' <span class="badge '+t.side+'">'+t.side+'</span></td>'+
    '<td><span class="chip">'+t.source+'</span></td>'+
    '<td class="'+cls(t.net)+'">'+money(t.net)+'</td>'+
    '<td class="zero">'+t.maxnet+'</td>'+
    '<td><span class="chip">'+(t.reason||"")+'</span></td></tr>'; });
  if(d.recent.length) r+='</tbody></table>'; $("recent").innerHTML=r;
}

function fmtp(v){ v=Number(v); if(v>=1000) return v.toFixed(0); if(v>=1) return v.toFixed(3); return v.toPrecision(4); }
function mini(l,v,s,c){ return '<div class="mini"><div class="l">'+l+'</div><div class="v '+(c||"")+'">'+v+'</div>'+(s?'<div class="l" style="margin-top:2px">'+s+'</div>':'')+'</div>'; }

function bars(obj,limit){
  let keys=Object.keys(obj); if(!keys.length) return '<div class="empty">даних ще немає</div>';
  keys.sort((a,b)=>obj[b].net-obj[a].net);
  if(limit) keys=keys.slice(0,limit);
  let max=1; keys.forEach(k=>max=Math.max(max,Math.abs(obj[k].net)));
  let h="";
  keys.forEach(k=>{
    const x=obj[k], w=Math.max(2,Math.abs(x.net)/max*100), col=x.net>=0?"var(--good)":"var(--bad)";
    const wr=x.n?Math.round(x.wins/x.n*100):0;
    h+='<div class="bar"><div class="n">'+k+'</div>'+
       '<div class="t"><div class="f" style="width:'+w+'%;background:'+col+'"></div></div>'+
       '<div class="val"><b class="'+cls(x.net)+'">'+money(x.net)+'</b> · '+x.n+'шт · '+wr+'%</div></div>';
  });
  return h;
}

function drawCurve(cum){
  const svg=$("curve");
  if(!cum||!cum.length){ svg.innerHTML='<text x="12" y="75" fill="#727a88" font-size="12">немає даних</text>'; $("c_min").textContent="";$("c_max").textContent="";$("c_range").textContent=""; return; }
  const W=600,H=150,pad=8; const arr=[0].concat(cum);
  const min=Math.min(...arr,0), max=Math.max(...arr,0), rng=(max-min)||1;
  const X=i=>pad+i*(W-2*pad)/(arr.length-1||1), Y=v=>H-pad-(v-min)/rng*(H-2*pad);
  const line=arr.map((v,i)=>X(i)+","+Y(v)).join(" ");
  const area=line+" "+X(arr.length-1)+","+(H-pad)+" "+X(0)+","+(H-pad);
  const zeroY=Y(0), last=arr[arr.length-1];
  const col=last>=0?"#2ecc71":"#ff5b63", colf=last>=0?"rgba(46,204,113,.18)":"rgba(255,91,99,.16)";
  svg.innerHTML=
    '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'+
      '<stop offset="0" stop-color="'+colf+'"/><stop offset="1" stop-color="transparent"/></linearGradient></defs>'+
    '<line x1="'+pad+'" y1="'+zeroY+'" x2="'+(W-pad)+'" y2="'+zeroY+'" stroke="#262a31" stroke-dasharray="3 3"/>'+
    '<polygon points="'+area+'" fill="url(#g)"/>'+
    '<polyline points="'+line+'" fill="none" stroke="'+col+'" stroke-width="2.2" stroke-linejoin="round"/>';
  $("c_min").textContent="мін −$"+Math.abs(min).toFixed(0);
  $("c_max").textContent="макс +$"+Math.abs(max).toFixed(0);
  $("c_range").textContent=cum.length+" угод";
}

function tick(){ fetch("/api",{cache:"no-store"}).then(r=>r.json()).then(fill).catch(()=>{document.body.classList.remove("live");$("dot").style.background="var(--bad)";}); }
tick(); setInterval(tick, 10000);
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api":
            try:
                body = json.dumps(build_api(), ensure_ascii=False).encode("utf-8")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
        else:
            self.send_error(404)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    print("UNI дашборд, слухаю 0.0.0.0:%d" % PORT)
    Server(("0.0.0.0", PORT), Handler).serve_forever()
