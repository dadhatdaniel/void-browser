# Void Browser — experimental website theme (LAN preview)

This directory is a **local/LAN-only** marketing theme preview.

- **Prod (unchanged):** `website/` → `http://10.0.0.10:5080/` (`void-website`)
- **Dev preview:** `website-dev/` → `http://10.0.0.10:5081/` (`void-website-dev`)

Do **not** point Cloudflare / public DNS at :5081. Promote to prod only after explicit approval.

## Deploy / refresh on Unraid

```bash
cd /mnt/user/appdata/openclaw-localai/workspace/void-browser
docker build --pull=false -t void-website-dev ./website-dev
docker stop void-website-dev 2>/dev/null || true
docker rm void-website-dev 2>/dev/null || true
docker run -d --name void-website-dev --restart unless-stopped \
  -p 5081:80 --network proxynet --memory=128m \
  --security-opt no-new-privileges:true void-website-dev
```

## Theme notes

- Honest wrapper story (WebView2 / WebKit) front and center
- 90s **color-wave** energy — not Geocities kitsch
- Keeps `releases.json` download wiring, OS icons, tip link, Efficiency/Performance
