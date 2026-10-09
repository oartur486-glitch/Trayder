"""
Телеграм-бот для спотовой торговли: пересечение EMA, только лонг.
Графики, карточка статуса и кнопки прямо в чате.
По умолчанию режим paper (виртуальные деньги, реальные цены).
Режим live включается только явно: MODE=live + API-ключи.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from functools import wraps

import ccxt.async_support as ccxt
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import charts

# ---------- Настройки (переменные окружения) ----------
TOKEN = os.environ["TELEGRAM_TOKEN"]
OWNER = int(os.environ["OWNER_ID"])
EXCHANGE = os.getenv("EXCHANGE", "binance")
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
TF = os.getenv("TIMEFRAME", "15m")
FAST = int(os.getenv("EMA_FAST", "20"))
SLOW = int(os.getenv("EMA_SLOW", "50"))
ALLOC = float(os.getenv("ALLOC_PCT", "25")) / 100
SL = float(os.getenv("STOP_LOSS_PCT", "1.5")) / 100
TP = float(os.getenv("TAKE_PROFIT_PCT", "3")) / 100
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS_PCT", "3")) / 100
START_BALANCE = float(os.getenv("START_BALANCE", "1000"))
LIVE = os.getenv("MODE", "paper").lower() == "live"
FEE = 0.001
STATE_FILE = os.getenv("STATE_PATH", "state.json")   # на хостинге укажи путь на постоянном диске
BASE, QUOTE = SYMBOL.split("/")
MODE = "LIVE" if LIVE else "paper"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

ex = getattr(ccxt, EXCHANGE)({
    "apiKey": os.getenv("API_KEY", ""),
    "secret": os.getenv("API_SECRET", ""),
    "enableRateLimit": True,
})
lock = asyncio.Lock()


# ---------- Состояние ----------
def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state():
    base = {"cash": START_BALANCE, "pos": None, "trades": [], "paused": False, "halted": False,
            "day": today(), "day_eq": START_BALANCE, "last_sig": 0, "eq_hist": []}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            base.update(json.load(f))
    return base


def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=1)


st = load_state()


def equity(price):
    q = st["pos"]["qty"] if st["pos"] else 0
    return st["cash"] + q * price


def fmt(v, d=2):
    return f"{v:,.{d}f}"


def money(v):
    return f"{'+' if v >= 0 else '−'}{fmt(abs(v))}"


def entries_state():
    return "пауза" if st["paused"] else "лимит дня" if st["halted"] else "разрешены"


# ---------- Кнопки и отправка ----------
def btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def keyboard():
    return InlineKeyboardMarkup([
        [btn("📊 Статус", "status"), btn("🕯 График", "chart")],
        [btn("📈 Баланс", "equity"), btn("🧾 Сделки", "history")],
        [btn("▶️ Возобновить" if st["paused"] else "⏸ Пауза", "toggle"),
         btn("🔴 Закрыть позицию", "close")],
    ])


async def send_text(ctx, text):
    await ctx.bot.send_message(OWNER, text, reply_markup=keyboard())


async def send_photo(ctx, buf, caption=""):
    await ctx.bot.send_photo(OWNER, buf, caption=caption, reply_markup=keyboard())


async def fetch_candles():
    return await ex.fetch_ohlcv(SYMBOL, TF, limit=max(SLOW * 4, 150))


async def notify_chart(ctx, text):
    """Сообщение о сделке с картинкой графика; если не вышло, обычным текстом."""
    try:
        buf = charts.price_chart(await fetch_candles(), FAST, SLOW, SYMBOL, TF, st["pos"], st["trades"])
        await send_photo(ctx, buf, text)
    except Exception as e:
        log.warning("График не отправлен: %s", e)
        await send_text(ctx, text)


# ---------- Сделки ----------
async def buy(ctx, price):
    quote = st["cash"] * ALLOC
    if quote < 10:
        return
    qty = quote / price
    if LIVE:
        qty = float(ex.amount_to_precision(SYMBOL, qty))
        order = await ex.create_market_buy_order(SYMBOL, qty)
        price = order.get("average") or order.get("price") or price
    else:
        st["cash"] -= qty * price * (1 + FEE)
    st["pos"] = {"qty": qty, "entry": price, "sl": price * (1 - SL), "tp": price * (1 + TP), "t": now_iso()}
    save_state()
    await notify_chart(ctx, f"🟢 Покупка {qty:.6f} {BASE} по {fmt(price)}\n"
                            f"SL {fmt(st['pos']['sl'])} · TP {fmt(st['pos']['tp'])}"
                            f"{'' if LIVE else '  (paper)'}")


async def sell(ctx, price, reason):
    pos = st["pos"]
    if not pos:
        return
    qty = pos["qty"]
    if LIVE:
        free = (await ex.fetch_balance())[BASE]["free"]
        qty = float(ex.amount_to_precision(SYMBOL, min(qty, free)))
        order = await ex.create_market_sell_order(SYMBOL, qty)
        price = order.get("average") or order.get("price") or price
        pnl = (price - pos["entry"]) * qty
    else:
        st["cash"] += qty * price * (1 - FEE)
        pnl = (price - pos["entry"]) * qty - qty * (price + pos["entry"]) * FEE
    st["trades"].append({"t_in": pos["t"], "t": now_iso(), "entry": pos["entry"], "exit": price,
                         "qty": qty, "pnl": pnl, "why": reason})
    st["trades"] = st["trades"][-200:]
    st["pos"] = None
    save_state()
    await notify_chart(ctx, f"{'✅' if pnl >= 0 else '🔻'} Продажа по {fmt(price)} ({reason})\n"
                            f"P&L: {money(pnl)} {QUOTE}")


# ---------- Основной цикл ----------
async def tick(ctx: ContextTypes.DEFAULT_TYPE):
    async with lock:
        try:
            ohlcv = await fetch_candles()
            price = (await ex.fetch_ticker(SYMBOL))["last"]
            if LIVE:
                st["cash"] = (await ex.fetch_balance())[QUOTE]["free"]
        except Exception as e:
            log.warning("Ошибка получения данных: %s", e)
            return

        closed = ohlcv[:-1]
        closes = [c[4] for c in closed]
        f, s = charts.ema(closes, FAST), charts.ema(closes, SLOW)
        sig_ts = closed[-1][0]
        cross_up = f[-2] <= s[-2] and f[-1] > s[-1]
        cross_dn = f[-2] >= s[-2] and f[-1] < s[-1]

        if st["day"] != today():
            st.update(day=today(), day_eq=equity(price), halted=False)

        pos = st["pos"]
        if pos:
            if price <= pos["sl"]:
                await sell(ctx, price, "стоп-лосс")
            elif price >= pos["tp"]:
                await sell(ctx, price, "тейк-профит")
            elif cross_dn and sig_ts != st["last_sig"]:
                await sell(ctx, price, "сигнал вниз")

        if not st["halted"] and equity(price) <= st["day_eq"] * (1 - MAX_DAILY_LOSS):
            if st["pos"]:
                await sell(ctx, price, "дневной лимит")
            st["halted"] = True
            await send_text(ctx, f"⛔ Дневной лимит убытка {MAX_DAILY_LOSS * 100:.1f}% достигнут. "
                                 f"Новые входы отключены до 00:00 UTC.")

        if (not st["pos"] and cross_up and sig_ts != st["last_sig"]
                and not st["paused"] and not st["halted"]):
            try:
                await buy(ctx, price)
            except Exception as e:
                log.error("Ошибка покупки: %s", e)
                await send_text(ctx, f"⚠️ Ошибка покупки: {e}")
        if cross_up or cross_dn:
            st["last_sig"] = sig_ts

        st["eq_hist"].append([int(time.time()), equity(price)])
        st["eq_hist"] = st["eq_hist"][-3000:]
        save_state()


# ---------- Действия (общие для кнопок и команд) ----------
async def act_status(ctx):
    price = (await ex.fetch_ticker(SYMBOL))["last"]
    eq = equity(price)
    day = eq - st["day_eq"]
    n = len(st["trades"])
    win = f"{sum(1 for t in st['trades'] if t['pnl'] > 0) / n * 100:.0f}%" if n else "—"
    if st["pos"]:
        p = st["pos"]
        u = (price - p["entry"]) * p["qty"]
        pos_text = f"Позиция: {p['qty']:.6f} {BASE} от {fmt(p['entry'])}  ·  P&L {money(u)}"
    else:
        pos_text = "Позиции нет, жду сигнал"
    buf = charts.status_card(SYMBOL, MODE, QUOTE, eq, day, day / st["day_eq"] * 100 if st["day_eq"] else 0,
                             n, win, price, pos_text, st["eq_hist"])
    await send_photo(ctx, buf, f"Входы: {entries_state()}")


async def act_chart(ctx):
    candles = await fetch_candles()
    buf = charts.price_chart(candles, FAST, SLOW, SYMBOL, TF, st["pos"], st["trades"])
    await send_photo(ctx, buf, f"{SYMBOL}: {fmt(candles[-1][4])}")


async def act_equity(ctx):
    if len(st["eq_hist"]) < 2:
        await send_text(ctx, "Данных для графика пока мало. Подожди несколько минут.")
        return
    await send_photo(ctx, charts.equity_chart(st["eq_hist"], QUOTE))


async def act_history(ctx):
    if not st["trades"]:
        await send_text(ctx, "Сделок пока нет.")
        return
    rows = [f"{'✅' if t['pnl'] >= 0 else '🔻'} {t['t'][5:16].replace('T', ' ')}  "
            f"{money(t['pnl'])} {QUOTE}  ·  {t['why']}" for t in st["trades"][-10:]]
    await send_text(ctx, "Последние сделки:\n" + "\n".join(rows))


async def act_toggle(ctx):
    st["paused"] = not st["paused"]
    save_state()
    await send_text(ctx, "⏸ Новые входы приостановлены." if st["paused"] else "▶️ Входы разрешены.")


async def act_close(ctx):
    async with lock:
        if not st["pos"]:
            await send_text(ctx, "Открытой позиции нет.")
            return
        await sell(ctx, (await ex.fetch_ticker(SYMBOL))["last"], "вручную")


ACTIONS = {"status": act_status, "chart": act_chart, "equity": act_equity,
           "history": act_history, "toggle": act_toggle, "close": act_close}


# ---------- Telegram-обработчики ----------
def owner_only(fn):
    @wraps(fn)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat is None or chat.id != OWNER:
            return
        return await fn(update, ctx)
    return wrapper


async def run_action(name, ctx):
    try:
        await ACTIONS[name](ctx)
    except Exception as e:
        log.error("Ошибка действия %s: %s", name, e)
        await send_text(ctx, f"⚠️ Ошибка: {e}")


@owner_only
async def cmd_start(update, ctx):
    await send_text(ctx, f"Бот торгует {SYMBOL} ({TF}), стратегия EMA {FAST}/{SLOW}, режим: {MODE}.\n"
                         f"Управляй кнопками ниже.")


@owner_only
async def on_command(update, ctx):
    await run_action(update.message.text.lstrip("/").split("@")[0], ctx)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.message is None or q.message.chat.id != OWNER:
        return
    await q.answer()
    await run_action(q.data, ctx)


async def health(reader, writer):
    """Мини-сервер для хостингов, которые требуют открытый порт (и для пинга uptime-сервисов)."""
    await reader.read(1024)
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
    await writer.drain()
    writer.close()


async def post_init(app: Application):
    if os.getenv("PORT"):
        await asyncio.start_server(health, "0.0.0.0", int(os.environ["PORT"]))
    await app.bot.set_my_commands([
        BotCommand("start", "Меню"), BotCommand("status", "Карточка счёта"),
        BotCommand("chart", "График с EMA и сделками"), BotCommand("equity", "График баланса"),
        BotCommand("history", "Последние сделки"), BotCommand("toggle", "Пауза / возобновить"),
        BotCommand("close", "Закрыть позицию")])
    app.job_queue.run_repeating(tick, interval=60, first=5)
    await app.bot.send_message(OWNER, f"Бот запущен. Режим: {MODE}{' ⚠️' if LIVE else ''}",
                               reply_markup=keyboard())


async def post_shutdown(app: Application):
    await ex.close()


def main():
    if LIVE and not (os.getenv("API_KEY") and os.getenv("API_SECRET")):
        raise SystemExit("MODE=live требует API_KEY и API_SECRET")
    app = Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler(list(ACTIONS), on_command))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()


if __name__ == "__main__":
    main()
