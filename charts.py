"""Рисует графики и карточку статуса в виде PNG для отправки в Telegram."""
import io
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

BG, CARD, TX, MU = "#0c1220", "#131b2d", "#e6ecf5", "#8190a8"
UP, DN, AC, BLUE, GRID = "#2fd08f", "#ff5a74", "#e0b04f", "#6c8cff", "#22304a"


def ema(vals, n):
    k = 2 / (n + 1)
    e, out = vals[0], [vals[0]]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def _axes(w=9, h=5):
    fig, ax = plt.subplots(figsize=(w, h), dpi=130)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(CARD)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=MU, labelsize=8)
    ax.grid(color=GRID, linewidth=.5, alpha=.7)
    return fig, ax


def _png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _day(iso):
    return mdates.date2num(datetime.fromisoformat(iso))


def price_chart(ohlcv, fast, slow, symbol, tf, pos=None, trades=()):
    """Свечи + две EMA + метки сделок + уровни SL/TP открытой позиции."""
    closes = [c[4] for c in ohlcv]
    ef, es = ema(closes, fast), ema(closes, slow)
    n = 100
    data, ef, es = ohlcv[-n:], ef[-n:], es[-n:]
    x = [mdates.date2num(datetime.fromtimestamp(c[0] / 1000, timezone.utc)) for c in data]
    w = (x[1] - x[0]) * 0.7

    fig, ax = _axes()
    for xi, c in zip(x, data):
        col = UP if c[4] >= c[1] else DN
        ax.vlines(xi, c[3], c[2], color=col, linewidth=.8)
        ax.bar(xi, abs(c[4] - c[1]) or c[4] * 1e-5, bottom=min(c[1], c[4]), width=w, color=col)
    ax.plot(x, ef, color=AC, linewidth=1.3, label=f"EMA {fast}")
    ax.plot(x, es, color=BLUE, linewidth=1.3, label=f"EMA {slow}")

    for t in trades:
        try:
            xi, xo = _day(t["t_in"]), _day(t["t"])
        except (KeyError, ValueError):
            continue
        if xo < x[0]:
            continue
        ax.scatter([xi], [t["entry"]], marker="^", color=UP, s=60, zorder=5)
        ax.scatter([xo], [t["exit"]], marker="v", color=UP if t["pnl"] >= 0 else DN, s=60, zorder=5)

    if pos:
        ax.axhline(pos["entry"], color=MU, ls=":", lw=1)
        ax.axhline(pos["sl"], color=DN, ls="--", lw=1)
        ax.axhline(pos["tp"], color=UP, ls="--", lw=1)
        try:
            ax.scatter([_day(pos["t"])], [pos["entry"]], marker="^", color=UP, s=90,
                       zorder=6, edgecolors=TX)
        except (KeyError, ValueError):
            pass

    ax.set_title(f"{symbol} · {tf}", color=TX, loc="left", fontsize=12, fontweight="bold")
    leg = ax.legend(loc="upper left", fontsize=8, facecolor=CARD, edgecolor=GRID)
    for t in leg.get_texts():
        t.set_color(TX)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
    ax.set_xlim(x[0] - w, x[-1] + w * 3)
    fig.autofmt_xdate()
    return _png(fig)


def equity_chart(hist, quote):
    """Кривая баланса. hist = [[epoch_sec, equity], ...]"""
    xs = [mdates.date2num(datetime.fromtimestamp(t, timezone.utc)) for t, _ in hist]
    ys = [v for _, v in hist]
    up = ys[-1] >= ys[0]
    col = UP if up else DN
    fig, ax = _axes()
    ax.axhline(ys[0], color=MU, ls="--", lw=1)
    ax.plot(xs, ys, color=col, linewidth=1.8)
    ax.fill_between(xs, ys, ys[0], color=col, alpha=.12)
    d = ys[-1] - ys[0]
    ax.set_title(f"Баланс: {ys[-1]:,.2f} {quote}  ({'+' if d >= 0 else '−'}{abs(d):,.2f})",
                 color=col, loc="left", fontsize=12, fontweight="bold")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
    fig.autofmt_xdate()
    return _png(fig)


def status_card(symbol, mode, quote, balance, day_pnl, day_pct, trades_n, win, price, pos_text, hist):
    fig = plt.figure(figsize=(9, 5), dpi=130)
    fig.patch.set_facecolor(BG)
    col = UP if day_pnl >= 0 else DN
    sign = "+" if day_pnl >= 0 else "−"
    fig.text(.06, .92, f"{symbol} · {mode}", color=MU, fontsize=11, va="top")
    fig.text(.06, .84, f"{balance:,.2f} {quote}", color=TX, fontsize=32, fontweight="bold", va="top")
    fig.text(.06, .66, f"{sign}{abs(day_pnl):,.2f} ({day_pct:+.2f}%) за день", color=col, fontsize=13, va="top")
    fig.text(.06, .57, f"Сделок: {trades_n}    Win rate: {win}    Цена: {price:,.2f}", color=MU, fontsize=11, va="top")
    fig.text(.06, .50, pos_text, color=TX, fontsize=11, va="top")
    ax = fig.add_axes([.06, .06, .88, .30])
    ax.set_facecolor(BG)
    ax.axis("off")
    if len(hist) > 1:
        ys = [v for _, v in hist][-300:]
        xs = list(range(len(ys)))
        c = UP if ys[-1] >= ys[0] else DN
        ax.plot(xs, ys, color=c, linewidth=1.8)
        ax.fill_between(xs, ys, min(ys), color=c, alpha=.12)
    return _png(fig)
