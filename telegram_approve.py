"""Interactive Telegram approval for CRT-Scout setups.

The one-way `send_alert` in signal_pipeline can only PUSH messages. This adds the
missing half: send a setup with ✅ Approve / ❌ Skip buttons, and read the human's
button press back via getUpdates (long-poll, no webhook needed). Uses the raw Bot
API over `requests` so it stays independent of the async python-telegram-bot flow
the rest of the agent uses.

Flow:
  send_approval(pending_id, text)      -> posts the setup with two buttons
  poll_decisions()                     -> returns [(action, pending_id), ...] for
                                          every button pressed since last poll,
                                          answers the callback + clears the buttons

The update offset is persisted in database/tg_offset.txt so a restart never
re-reads old presses. All paper — approving only flips a PENDING paper trade to
OPEN on the scoreboard.
"""
import os
import requests

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
_OFFSET_FILE = "database/tg_offset.txt"
_TIMEOUT = 15


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


def send_approval(pending_id, text):
    """Post a setup with Approve / Skip buttons. Returns True on success."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"appr:{pending_id}"},
            {"text": "❌ Skip", "callback_data": f"skip:{pending_id}"},
        ]]
    }
    try:
        r = requests.post(
            f"{_API}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "reply_markup": keyboard},
            timeout=_TIMEOUT,
        )
        return r.ok
    except requests.RequestException:
        return False


def _answer(callback_id, text):
    try:
        requests.post(f"{_API}/answerCallbackQuery",
                      json={"callback_query_id": callback_id, "text": text},
                      timeout=_TIMEOUT)
    except requests.RequestException:
        pass


def _clear_buttons(chat_id, message_id, suffix):
    """Remove the buttons and append the decision to the original message so the
    Telegram history shows what was chosen."""
    try:
        requests.post(f"{_API}/editMessageReplyMarkup",
                      json={"chat_id": chat_id, "message_id": message_id,
                            "reply_markup": {"inline_keyboard": []}},
                      timeout=_TIMEOUT)
    except requests.RequestException:
        pass


def poll_decisions():
    """Fetch button presses since the last poll. Returns [(action, pending_id)]
    where action is 'appr' or 'skip'. Answers each callback and clears its
    buttons. Non-blocking-ish (short long-poll)."""
    if not TELEGRAM_TOKEN:
        return []
    offset = _read_offset()
    try:
        r = requests.get(
            f"{_API}/getUpdates",
            params={"offset": offset + 1, "timeout": 0,
                    "allowed_updates": '["callback_query"]'},
            timeout=_TIMEOUT,
        )
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    if not data.get("ok"):
        return []

    decisions = []
    last_id = offset
    for upd in data.get("result", []):
        last_id = max(last_id, upd["update_id"])
        cq = upd.get("callback_query")
        if not cq:
            continue
        payload = cq.get("data", "")
        cb_id = cq.get("id")
        msg = cq.get("message", {})
        if ":" not in payload:
            _answer(cb_id, "unrecognised")
            continue
        action, _, pid = payload.partition(":")
        if action not in ("appr", "skip") or not pid.isdigit():
            _answer(cb_id, "unrecognised")
            continue
        decisions.append((action, int(pid)))
        _answer(cb_id, "Approved ✅" if action == "appr" else "Skipped ❌")
        if msg:
            _clear_buttons(msg.get("chat", {}).get("id"), msg.get("message_id"),
                           action)
    if last_id != offset:
        _write_offset(last_id)
    return decisions
