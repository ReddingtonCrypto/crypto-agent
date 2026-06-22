# Reliable free trigger every 15 min (cron-job.org)

GitHub's built-in scheduler is unreliable (delays/skips runs). This sets up a
free external service that triggers your bot every 15 minutes, exactly.
No card needed.

There are two parts: (A) create a GitHub access key, (B) set up the free cron.

---

## Part A - Create a GitHub access key (token)
This lets the cron service start your workflow.

1. Go to github.com (signed in to your ReddingtonCrypto account).
2. Top-right profile picture -> **Settings**.
3. Left sidebar, scroll to the bottom -> **Developer settings**.
4. **Personal access tokens** -> **Fine-grained tokens** -> **Generate new token**.
5. Fill in:
   - **Token name:** `crypto-agent-cron`
   - **Expiration:** 1 year (or custom max)
   - **Repository access:** choose **Only select repositories** -> pick **crypto-agent**
   - **Permissions:** expand **Repository permissions** -> find **Actions** ->
     set it to **Read and write**. (Leave everything else as "No access".)
6. Click **Generate token**.
7. **Copy the token now** (it starts with `github_pat_...`). You won't see it again.
   Paste it somewhere safe for a moment.

## Part B - Set up the free cron (cron-job.org)
1. Go to **https://cron-job.org** -> **Sign up** (free, no card) -> verify email.
2. Click **Create cronjob**.
3. **Title:** `crypto-agent trigger`
4. **URL:**
   ```
   https://api.github.com/repos/ReddingtonCrypto/crypto-agent/actions/workflows/scan.yml/dispatches
   ```
5. **Schedule:** choose **Every 15 minutes** (or "Custom" -> every 15 min).
6. Open the **Advanced** section (or the "Request" / "Headers" tab):
   - **Request method:** `POST`
   - **Headers** — add these three (Name = Value):
     - `Accept` = `application/vnd.github+json`
     - `Authorization` = `Bearer github_pat_XXXX`  (paste your real token after "Bearer ")
     - `X-GitHub-Api-Version` = `2022-11-28`
   - **Request body:**
     ```
     {"ref":"main"}
     ```
7. **Save** / **Create**.

## Part C - Test it
1. In cron-job.org, open your job and click **Run now** (or **Test run**).
2. A green/success result (HTTP 204) = it worked.
3. Go to your repo's **Actions** tab — a new run should appear within a few seconds,
   and from now on one will appear every 15 minutes on its own.
4. Your Telegram alerts and dashboard will now update reliably.

## If the test fails
- **401 / 403** = the token is wrong or missing the Actions permission. Re-check Part A step 5 (Actions = Read and write) and that the header is `Bearer ` + token.
- **404** = the URL is mistyped, or the repo name/owner is wrong.
- Tell Claude the exact error number and it'll pinpoint it.

## Security
- This token only lets someone start your workflow on this one repo. It can't
  touch your code or other repos.
- You can delete it anytime: Settings -> Developer settings -> Fine-grained tokens
  -> Revoke.
- It is NOT stored in your code; it lives only in cron-job.org.
