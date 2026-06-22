# Deploy the bot 24/7 on Oracle Cloud (Always Free) — step by step

Goal: the bot runs on Oracle's free server forever, sends alerts to your phone,
and does NOT need your laptop open.

> Honest note on "free": Oracle's **Always Free** resources cost $0 forever, but
> the signup asks for a **credit/debit card to verify you're a real person**.
> You are NOT charged for Always Free resources. (If you'd rather avoid giving a
> card at all, the backup plan is GitHub Actions — no card needed. Tell me and
> I'll switch.)

---

## Part A — Create the free account (~10 min, do on laptop)
1. Go to **https://www.oracle.com/cloud/free/**
2. Click **Start for free**.
3. Enter your email, country, and verify the email.
4. Fill in your details. Choose account type **Individual**.
5. Verify your phone number (SMS code).
6. Add a card for verification (no charge on Always Free).
7. Pick a **Home Region** close to you and finish. Wait for the account to be ready.

## Part B — Create the free server (VM) (~10 min)
1. In the Oracle Cloud menu (top-left ☰) → **Compute** → **Instances**.
2. Click **Create instance**.
3. Name it `crypto-agent`.
4. Under **Image and shape**:
   - Image: **Canonical Ubuntu** (latest LTS).
   - Shape: click **Change shape** → **Ampere** (ARM) → pick **VM.Standard.A1.Flex** →
     set **1 OCPU** and **6 GB memory** (well within Always Free).
   - If Ampere says "out of capacity", try again later or switch Home Region.
5. Under **SSH keys**: choose **Generate a key pair for me** and click **Download
   private key** AND **Download public key**. Save both — you need the private key
   to log in. (Keep the private key safe; it's like a password.)
6. Click **Create**. Wait until the instance shows **RUNNING**.
7. Copy the **Public IP address** shown on the instance page.

## Part C — Open the connection (one network setting)
1. On the instance page, click the **Virtual Cloud Network / subnet** link.
2. Open **Security Lists** → the default list → **Add Ingress Rules** only if we
   later add a dashboard. For Telegram-only alerts you need **nothing** here —
   the bot makes outbound connections, which are allowed by default.

## Part D — Tell me, and I take over
Once the instance is **RUNNING**, send me:
- the **Public IP address**, and
- confirm you have the **private key file** downloaded.

Then I'll give you the exact copy-paste commands to:
1. Connect to the server (SSH).
2. Install Python + your bot's libraries.
3. Upload your `crypto-agent` code.
4. Put your Telegram token in a `.env` on the server (never in code).
5. Run the bot as a **background service** so it auto-starts and stays alive 24/7
   (using `systemd`), and restarts itself if the server reboots.

---

## What "operate from phone" looks like after this
- Alerts arrive in **Telegram** on your phone (already working).
- Next feature we add: **interactive Telegram commands** (`/status`, `/scan`,
  `/top`, `/pause BTC`) so you can control the bot from your phone with no laptop.
- The Oracle server just runs quietly in the background.

## Security checklist (all free)
- [x] Token in `.env`, not in code (done).
- [x] `.gitignore` blocks `.env` from leaking (done).
- [ ] On the server: keep the private SSH key safe; don't share it.
- [ ] Bot stays **read-only** — no trading, no orders, no withdrawals.
