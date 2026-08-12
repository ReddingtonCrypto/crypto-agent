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


def send_message(text, reply_to=None):
    """Plain HTML message (confirmations). If reply_to (a message_id) is given,
    it is sent as a REPLY to that message so the original alert is tagged.
    Returns True on success."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    if reply_to:
        payload["reply_to_message_id"] = int(reply_to)
        payload["allow_sending_without_reply"] = True
    try:
        r = requests.post(f"{_API}/sendMessage", json=payload, timeout=20)
        return r.ok
    except requests.RequestException:
        return False


def send_approval(pending_id, text):
    """Post a setup with Approve / Decline buttons. Returns the sent message_id
    (so callers can thread later TP/loss replies to it), or None on failure."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"appr:{pending_id}"},
        {"text": "❌ Decline", "callback_data": f"skip:{pending_id}"},
    ]]}
    try:
        r = requests.post(f"{_API}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                                "parse_mode": "HTML", "reply_markup": keyboard},
                          timeout=20)
        if r.ok:
            return (r.json().get("result") or {}).get("message_id")
        return None
    except (requests.RequestException, ValueError):
        return None


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


STALE_FRAC = 0.30       # matches agent.PENDING_EXPIRE_FRAC


def _pending_state(row):
    """(label, is_stale) describing how far a waiting setup has already run."""
    price = _current_price(row[1])
    moved, invalid = paper_trading.pending_progress(row, price)
    if invalid:
        return "stop already hit", True
    if moved is None:
        return "no price", False
    if moved >= STALE_FRAC:
        return f"ran {moved * 100:.0f}% — too late", True
    if moved < 0:
        return "still ahead of entry", False
    return f"ran {moved * 100:.0f}%", False


def send_pending_list():
    """Post every setup still waiting, each with its own Drop button.

    The Approve/Decline buttons only live on the original alert, which scrolls
    away within hours. This is how you clear out something you decided against
    days ago, or that has simply sat too long to be worth entering.
    """
    rows = paper_trading.list_pending()
    if not rows:
        send_message("✅ Nothing is waiting for your approval.")
        return True

    lines = [f"🔔 <b>{len(rows)} setup(s) waiting for you</b>"]
    keyboard, stale = [], 0
    for r in rows[:20]:                      # keep the keyboard tappable
        tid, coin, direction, _e, _s, _t1, _t2, tf, strat, _o = r
        label, is_stale = _pending_state(r)
        if is_stale:
            stale += 1
        mark = "⚠️ " if is_stale else ""
        lines.append(f"{mark}<code>#{tid}</code> {coin} {tf} {direction}"
                     f" · {strat} · {label}")
        # Approve and Drop sit side by side, so the list is a place to act from
        # and not just to tidy up. "appl" rather than "appr" so the handler
        # knows the press came from the list and leaves the other rows' buttons
        # alone — clearing them would strip every setup at once.
        keyboard.append([
            {"text": f"✅ #{tid} {coin.split('/')[0]} {tf}",
             "callback_data": f"appl:{tid}"},
            {"text": "❌ Drop", "callback_data": f"drop:{tid}"},
        ])
    if len(rows) > 20:
        lines.append(f"…and {len(rows) - 20} more")
    if stale:
        keyboard.append([{"text": f"🧹 Drop all {stale} that are too late",
                          "callback_data": "dropstale:0"}])

    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": "\n".join(lines),
               "parse_mode": "HTML",
               "reply_markup": {"inline_keyboard": keyboard}}
    try:
        return requests.post(f"{_API}/sendMessage", json=payload, timeout=20).ok
    except requests.RequestException:
        return False


def _drop_stale():
    """Decline every waiting setup whose move has already gone. Returns how many."""
    n = 0
    for r in paper_trading.list_pending():
        _label, is_stale = _pending_state(r)
        if is_stale and paper_trading.reject_pending(r[0]):
            n += 1
    return n


def _handle(action, pid, reply_to=None):
    """Act on one button press: update the DB and send a confirmation that REPLIES
    to the original setup alert (reply_to = that alert's message_id)."""
    info = paper_trading.get_pending(pid)
    tag = f"{info['coin']} {info['direction']} ({info['timeframe']})" if info else f"#{pid}"
    if action == "appr":
        price = _current_price(info["coin"]) if info else None
        tr = paper_trading.approve_pending(pid, current_price=price)
        if tr and tr.get("mode") == "market":
            send_message(f"✅ <b>{tag}</b> entered at MARKET — "
                         f"<code>{tr['fill_price']:.6g}</code>. Tracking now.", reply_to=reply_to)
        elif tr and tr.get("mode") == "limit":
            send_message(f"⏳ <b>{tag}</b> — LIMIT set @ <code>{tr['fill_price']:.6g}</code>. "
                         f"Waiting for price to reach it before it starts tracking.", reply_to=reply_to)
        elif tr and tr.get("mode") == "expired":
            send_message(f"⚠️ <b>{tag}</b> — no trade: {tr['reason']} before you approved. "
                         f"Skipped (no room left).", reply_to=reply_to)
        else:
            send_message(f"⚠️ Couldn't open <b>{tag}</b> — already open/waiting or no longer pending.",
                         reply_to=reply_to)
    else:
        if paper_trading.reject_pending(pid):
            send_message(f"❌ Declined <b>{tag}</b>.", reply_to=reply_to)


def poll_once(timeout=25):
    """Long-poll getUpdates once and act on every button press. Returns the
    number of decisions handled."""
    if not TELEGRAM_TOKEN:
        return 0
    offset = _read_offset()
    try:
        r = requests.get(f"{_API}/getUpdates",
                         params={"offset": offset + 1, "timeout": timeout,
                                 # messages too, so /pending can be typed
                                 "allowed_updates": '["callback_query","message"]'},
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
        # Typed commands. /pending posts the waiting list with a Drop button on
        # each one — the Approve/Decline buttons only exist on the original
        # alert, which has long scrolled away by the time you want to clear up.
        text = ((upd.get("message") or {}).get("text") or "").strip().lower()
        if text:
            cmd = text.split()[0].split("@")[0]
            if cmd in ("/pending", "/p", "/waiting"):
                send_pending_list()
                handled += 1
            elif cmd in ("/dropstale", "/clean"):
                n = _drop_stale()
                send_message(f"🧹 Dropped {n} setup(s) that were too late."
                             if n else "Nothing was too late to enter.")
                handled += 1
            elif cmd in ("/help", "/start"):
                send_message("<b>Commands</b>\n"
                             "/pending — everything waiting, with Approve and "
                             "Drop buttons on each\n"
                             "/dropstale — drop the ones whose move already went")
                handled += 1
            continue

        cq = upd.get("callback_query")
        if not cq:
            continue
        payload = cq.get("data", "")
        cb_id = cq.get("id")
        msg = cq.get("message", {})
        action, _, pid = payload.partition(":")

        if action == "appl" and pid.isdigit():
            # Approved from the /pending list. Thread the confirmation to the
            # setup's ORIGINAL alert where we still have it, so this reads the
            # same as approving from the alert itself, and leave the list's
            # other buttons usable.
            info = paper_trading.get_pending(int(pid))
            _answer(cb_id, "Approving…" if info else "already gone")
            try:
                _handle("appr", int(pid),
                        reply_to=(info or {}).get("alert_msg_id")
                        or msg.get("message_id"))
            except Exception as e:
                print(f"handle appl #{pid} failed: {type(e).__name__}: {e}",
                      flush=True)
            handled += 1
            continue
        if action == "drop" and pid.isdigit():
            info = paper_trading.get_pending(int(pid))
            tag = (f"{info['coin']} {info['timeframe']}" if info else f"#{pid}")
            dropped = paper_trading.reject_pending(int(pid))
            _answer(cb_id, "Dropped ❌" if dropped else "already gone")
            send_message(f"❌ Dropped <b>{tag}</b> — never entered.")
            handled += 1
            continue
        if action == "dropstale":
            n = _drop_stale()
            _answer(cb_id, f"Dropped {n}")
            send_message(f"🧹 Dropped {n} setup(s) that were too late."
                         if n else "Nothing was too late to enter.")
            handled += 1
            continue

        if action not in ("appr", "skip") or not pid.isdigit():
            _answer(cb_id, "unrecognised")
            continue
        _answer(cb_id, "Approved ✅" if action == "appr" else "Declined ❌")
        orig_msg_id = msg.get("message_id")   # the original setup alert
        _clear_buttons(msg.get("chat", {}).get("id"), orig_msg_id, action)
        try:
            _handle(action, int(pid), reply_to=orig_msg_id)
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
