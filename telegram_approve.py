"""Interactive Telegram approval for CRT setups — real-time listener.

Two halves:
  * send_approval(pending_id, text)  — the scan (agent.py) posts a setup with
    ✅ Approve / ❌ Skip buttons.
  * run_listener()                   — a SEPARATE long-running process (its own
    systemd service) that long-polls getUpdates and acts on a button press
    within a second or two: Approve -> promote the PENDING paper trade to a
    tracked OPEN one and reply; Skip -> mark it SKIPPED and reply.

Only ONE process may consume getUpdates for a bot, so the listener is the single
consumer — the scan loop no longer polls. Uses the raw Bot API over `requests`,
independent of the async python-telegram-bot flow used elsewhere. All paper.
"""
import os
import time

import requests

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
import paper_trading

_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
_OFFSET_FILE = "database/tg_offset.txt"


def _read_offset():
    try:
        with open(_OFFSET_FILE) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def _write_offset(update_id):
    os.makedirs(os.path.dirname(_OFFSET_FILE), exist_ok=True)
    with open(_OFFSET_FILE, "w") as f:
        f.write(str(update_id))


def send_message(text):
    """Plain HTML message (confirmations). Returns True on success."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        r = requests.post(f"{_API}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                                "parse_mode": "HTML"}, timeout=20)
        return r.ok
    except requests.RequestException:
        return False


def send_approval(pending_id, text):
    """Post a setup with Approve / Skip buttons. Returns True on success."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"appr:{pending_id}"},
        {"text": "❌ Skip", "callback_data": f"skip:{pending_id}"},
    ]]}
    try:
        r = requests.post(f"{_API}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                                "parse_mode": "HTML", "reply_markup": keyboard},
                          timeout=20)
        return r.ok
    except requests.RequestException:
        return False


def _answer(callback_id, text):
    try:
        requests.post(f"{_API}/answerCallbackQuery",
                      json={"callback_query_id": callback_id, "text": text},
                      timeout=20)
    except requests.RequestException:
        pass


def _clear_buttons(chat_id, message_id, note):
    """Remove the buttons and stamp the decision onto the original message."""
    if chat_id is None or message_id is None:
        return
    try:
        requests.post(f"{_API}/editMessageReplyMarkup",
                      json={"chat_id": chat_id, "message_id": message_id,
                            "reply_markup": {"inline_keyboard": []}}, timeout=20)
    except requests.RequestException:
        pass


_EXCHANGE = None


def _current_price(coin):
    """Live last price for the market/limit decision. None on failure."""
    global _EXCHANGE
    try:
        if _EXCHANGE is None:
            from data_source import make_exchange
            _EXCHANGE = make_exchange()
        t = _EXCHANGE.fetch_ticker(coin)
        return float(t.get("last") or t.get("close"))
    except Exception:
        return None


def _handle(action, pid):
    """Act on one button press: update the DB and send a confirmation."""
    info = paper_trading.get_pending(pid)
    tag = f"{info['coin']} {info['direction']} ({info['timeframe']})" if info else f"#{pid}"
    if action == "appr":
        price = _current_price(info["coin"]) if info else None
        tr = paper_trading.approve_pending(pid, current_price=price)
        if tr and tr.get("mode") == "market":
            send_message(f"✅ <b>{tag}</b> entered at MARKET — "
                         f"<code>{tr['fill_price']:.6g}</code>. Tracking now.")
        elif tr and tr.get("mode") == "limit":
            send_message(f"⏳ <b>{tag}</b> — LIMIT set @ <code>{tr['fill_price']:.6g}</code>. "
                         f"Waiting for price to reach it before it starts tracking.")
        else:
            send_message(f"⚠️ Couldn't open <b>{tag}</b> — already open/waiting or no longer pending.")
    else:
        if paper_trading.reject_pending(pid):
            send_message(f"❌ Skipped <b>{tag}</b>.")


def poll_once(timeout=25):
    """Long-poll getUpdates once and act on every button press. Returns the
    number of decisions handled."""
    if not TELEGRAM_TOKEN:
        return 0
    offset = _read_offset()
    try:
        r = requests.get(f"{_API}/getUpdates",
                         params={"offset": offset + 1, "timeout": timeout,
                                 "allowed_updates": '["callback_query"]'},
                         timeout=timeout + 10)
        data = r.json()
    except (requests.RequestException, ValueError):
        return 0
    if not data.get("ok"):
        return 0

    handled = 0
    last_id = offset
    for upd in data.get("result", []):
        last_id = max(last_id, upd["update_id"])
        cq = upd.get("callback_query")
        if not cq:
            continue
        payload = cq.get("data", "")
        cb_id = cq.get("id")
        msg = cq.get("message", {})
        action, _, pid = payload.partition(":")
        if action not in ("appr", "skip") or not pid.isdigit():
            _answer(cb_id, "unrecognised")
            continue
        _answer(cb_id, "Approved ✅" if action == "appr" else "Skipped ❌")
        _clear_buttons(msg.get("chat", {}).get("id"), msg.get("message_id"), action)
        try:
            _handle(action, int(pid))
        except Exception as e:
            print(f"handle {action} #{pid} failed: {type(e).__name__}: {e}", flush=True)
        handled += 1
    if last_id != offset:
        _write_offset(last_id)
    return handled


def run_listener():
    """Forever: long-poll for button presses and act on them in near real time.
    Run as its own systemd service (the single getUpdates consumer)."""
    print("CRT approval listener started (long-poll).", flush=True)
    while True:
        try:
            n = poll_once(timeout=25)
            if n:
                print(f"handled {n} decision(s)", flush=True)
        except Exception as e:
            print(f"listener loop error: {type(e).__name__}: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    run_listener()
