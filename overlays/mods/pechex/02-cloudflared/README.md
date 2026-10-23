# Cloudflared Remote Access MOD

This mod enables secure remote access to your Snapmaker U1 via **Cloudflare Tunnels** (`cloudflared`).

---

## 1-Click Automated Web Setup (Zero SSH Required)

The mod is fully integrated into the **Firmware Config** web interface:

1. **Open Firmware Config** in your browser (`http://<printer-ip>/config` or via Fluidd sidebar link).
2. Go to **Settings > Remote Access > Cloud Provider** and select **Cloudflared**.
3. A modal dialog will appear asking for your desired subdomain:
   ```text
   Domain / Subdomain: [ u1.yourdomain.com ]
   ```
4. Click **Confirm**.
5. An authorization link will appear directly in the web console:
   ```text
   Please open the URL below in a new tab, select your domain, and click 'Authorize':
   https://dash.cloudflare.com/argotunnel?callback=https%3A%2F%2Flogin...
   ```
6. Click the link in a new tab, select your domain on Cloudflare, and click **Authorize**.
7. **Done!** The printer automatically receives the certificate, creates the tunnel in your account, configures the DNS CNAME record, starts the service, and puts your printer online at `https://u1.yourdomain.com`.

---

## Securing with a Login Screen (Web Authentication)

To ensure only authorized users can access the printer over the tunnel:

1. In **Firmware Config > Settings > Remote Access > Web Authentication**, select **Enabled**.
2. Note the generated admin password.
3. When accessing the printer from the internet via your Cloudflare URL, Fluidd will present a graphical login screen asking for your username & password.

---

## Web Troubleshooting & Diagnostics

Under **Firmware Config > Actions**:
- **Cloudflared Status**: Inspect live tunnel status, active connection, and recent logs directly from the web app.
- **Upgrade Cloudflared**: Update to the latest pinned version with one click.
