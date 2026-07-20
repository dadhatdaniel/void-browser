# Google / YouTube sign-in (WebView2)

## What we fixed

Void previously listed `accounts.google.com` and `ssl.gstatic.com` in
`BLOCKED_TELEMETRY_DOMAINS`. Main-frame navigations to those hosts were
**hard-blocked**, so YouTube/Gmail sign-in could never load. Those hosts are
auth/CDN endpoints, not telemetry — they are no longer blocked.

On Windows, Void also sets a **persistent** WebView2 profile directory:

`%LOCALAPPDATA%\void-browser\webview2-profile`

(override with `WEBVIEW2_USER_DATA_FOLDER`) so cookies/sessions survive restarts.

`clear_on_exit` (Settings → Privacy) can wipe cookies/cache/storage/history on quit
via WebView2 `ClearBrowsingData`. Defaults keep **cookies** and **local storage** off
so Google sessions persist. Set `VOID_DISABLE_CLEAR_ON_EXIT=1` (RPA does this) to
skip clearing entirely.

## Remaining Google / WebView2 limitations

| Issue | Severity | Notes |
|-------|----------|-------|
| Google may challenge or refuse embedded WebView2 | Medium | Edge WebView2 UA is usually accepted; occasional “This browser may not be secure” or extra CAPTCHA is a Google policy, not something Void can fully override without spoofing a full Chrome install. |
| OAuth `window.open` / popup flows | Medium | Some Google flows open a secondary window. Void keeps a single content webview per tab; popups may need to continue in-page. If a flow stalls on a blank popup, try Standard security level and reload. |
| Third-party cookie partitioning | Low–Medium | Modern WebView2/Edge partitions third-party cookies. Same-site login on `accounts.google.com` then return to YouTube usually works; cross-site widgets may still fail. |
| Paranoid security level | High impact | Extra fingerprint spoofing / aggressive blocking can break auth. Prefer **Standard** or **Strict** for Google accounts. |

## Manual QA checklist

1. Open `https://www.youtube.com`
2. Click Sign in → `accounts.google.com` must load (not a Void block error)
3. Complete login (or stop at the password page for RPA smoke)
4. Restart Void → session should still be present if “Stay signed in” was used
5. RPA scenario `youtube_signin_page` screenshots the sign-in UI without credentials by default

## Credentials in automation

Never commit passwords. Optional full login only when both are set:

```powershell
$env:VOID_TEST_GOOGLE_USER = "you@example.com"
$env:VOID_TEST_GOOGLE_PASS = "..."   # local/CI secret only
```

Default RPA runs **load sign-in UI only**.
