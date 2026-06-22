# Deploy the bot 24/7 on GitHub Actions (free, NO card) — step by step

Goal: the bot runs on GitHub's free servers every 15 minutes and alerts your
phone, with no laptop and no credit card.

> Why this is safe: your Telegram token is NEVER put in the code. It goes into
> GitHub's encrypted "Secrets". The `.env` file stays on your laptop only
> (`.gitignore` blocks it from being uploaded).

---

## Part A — Create a GitHub account (skip if you have one)
1. Go to **https://github.com** → click **Sign up**.
2. Enter email, password, username. Verify your email. (No card, ever.)

## Part B — Install GitHub Desktop (easiest way to upload, no commands)
1. Go to **https://desktop.github.com** → download and install **GitHub Desktop**.
2. Open it → **Sign in** with the account from Part A.

## Part C — Upload your project
1. In GitHub Desktop: **File → Add local repository**.
2. Choose the folder `C:\Users\MEHBOOB\crypto-agent`.
3. If it says "this isn't a Git repository", click **create a repository** → keep
   the name `crypto-agent` → click **Create repository**.
4. You'll see a list of files to be added. **IMPORTANT:** confirm there is **no
   `.env`** in that list (it should be hidden by `.gitignore`). If you see `.env`,
   stop and tell me.
5. At the bottom-left, type a summary like `first upload` → click **Commit to main**.
6. Click **Publish repository** (top).
   - **Keep "Keep this code private" UNCHECKED (public).** Public repos get
     unlimited free run-time. Your token is safe because it's not in the code.
   - Click **Publish**.

## Part D — Add your Telegram secrets (so the bot can message you)
1. Go to **https://github.com** → open your **crypto-agent** repo.
2. Click **Settings** (top) → left menu **Secrets and variables** → **Actions**.
3. Click **New repository secret**:
   - Name: `TELEGRAM_TOKEN`  → Value: your full bot token → **Add secret**.
4. Click **New repository secret** again:
   - Name: `TELEGRAM_CHAT_ID` → Value: `7977155191` → **Add secret**.

## Part E — Turn on and test
1. In the repo, click the **Actions** tab.
2. If it asks to enable workflows, click **I understand my workflows, enable them**.
3. Click the **Crypto Agent Scan** workflow (left) → **Run workflow** button → **Run workflow**.
4. Wait ~1 minute. A green check = it ran. Click the run to see the log (same
   output you saw in PowerShell).
5. If a coin qualified, you get a **Telegram alert**. If it says "No valid
   signals found", that's normal — it'll keep checking every 15 minutes on its own.

## Done!
- The bot now runs **every 15 minutes, 24/7, for free**, with no laptop open.
- To change anything later: edit files in GitHub Desktop → Commit → Push, and the
  next run uses the new code.
- To pause it: Actions tab → Crypto Agent Scan → "..." menu → **Disable workflow**.

---

## How to update the code later (the normal cycle)
1. We change files on your laptop (like we do now).
2. Open GitHub Desktop → it shows the changed files → type a summary → **Commit to main**.
3. Click **Push origin**. The next scheduled run picks up the changes.

## Security checklist
- [ ] After publishing, open the repo on github.com and confirm there is **no
      `.env` file** listed. (Code is fine to be public; the token must not be.)
- [ ] Token lives only in **Settings → Secrets**, never in a file.
- [ ] Bot stays **read-only** — no trading, no orders.
