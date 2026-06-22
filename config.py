import os


def _load_env(path=".env"):
    """Read KEY=VALUE lines from a .env file into the environment.

    Keeps secrets out of the source code. No external library needed.
    """
    if not os.path.exists(path):
        return

    with open(path) as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_env()


TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    print(
        "WARNING: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set. "
        "Copy .env.example to .env and fill them in."
    )
