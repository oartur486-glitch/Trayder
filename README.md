# Telegram-бот: торговля по пересечению EMA

Режим по умолчанию — paper (виртуальные деньги, реальные цены).

## Запуск с телефона

### 1. Код на GitHub (в браузере телефона)
1. github.com → **New repository** → название `trading-bot` → **Private** → Create.
2. **Add file → Upload files** → выбери из «Файлов» эти 6 штук:
   `bot.py`, `charts.py`, `requirements.txt`, `Dockerfile`, `.gitignore`, `README.md`.
3. **Commit changes**.

Файлы `.env` и `state.json` в репозиторий не загружай: в них секреты и состояние.
Если кнопка загрузки не видна — открой github.com в режиме «Версия для ПК».

### 2. Токен и id
- @BotFather → `/newbot` → скопируй токен.
- @userinfobot → скопируй свой id.

### 3. Хостинг
Подойдёт любой сервис, который собирает проект из GitHub по Dockerfile
(Railway, Render, Koyeb, Fly.io). Условия бесплатных планов проверь на сайте сервиса.

1. New project → **Deploy from GitHub repo** → выбери `trading-bot`.
2. В разделе **Variables** добавь:

| Переменная | Значение |
|---|---|
| `TELEGRAM_TOKEN` | токен от BotFather |
| `OWNER_ID` | твой Telegram id |
| `MODE` | `paper` |
| `EXCHANGE` | `bybit`, `okx` или `kraken` (см. ниже) |
| `SYMBOL` | `BTC/USDT` (для kraken `BTC/USD`) |
| `STATE_PATH` | `/data/state.json` |

3. Подключи постоянный диск (Volume) с точкой монтирования `/data`.
   Без него при каждом перезапуске баланс и позиция сбрасываются.
4. Выбери регион в Европе.
5. Deploy → открой бота в Telegram → `/start`.

### Важно
- Binance блокирует IP американских дата-центров (ошибка 451). Если сервер в США, используй другую биржу или европейский регион.
- Переход на `MODE=live` делай только после нескольких недель в paper. Нужны `API_KEY` и `API_SECRET` в Variables, ключ без права вывода средств. Без постоянного диска live-режим запускать нельзя: после перезапуска бот забудет об открытой позиции.
- API-ключи хранятся только в переменных хостинга, никогда в репозитории.
