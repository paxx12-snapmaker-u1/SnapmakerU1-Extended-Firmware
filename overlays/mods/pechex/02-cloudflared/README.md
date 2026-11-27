# Cloudflared Remote Access MOD

This mod enables secure remote access to your Snapmaker U1 via **Cloudflare Tunnels** (`cloudflared`).

---

## 1-Click Automated Web Setup (Zero SSH Required)

The mod is fully integrated into the **Firmware Config** web interface:

1. **Open Firmware Config** in your browser (`http://<printer-ip>/config` or via Fluidd sidebar link).
2. Go to **Settings > Remote Access > Cloud Provider** and select **Cloudflared**.
3. A modal dialog will appear asking for:
   - **Domain / Subdomain**: (e.g. `u1.yourdomain.com`)
   - **Admin Email (OTP Login)**: Your email address (e.g. `user@gmail.com`)
   - **Cloudflare API Token**: A Cloudflare token with `Access: Apps and Policies (Edit)` and `Access: Identity Providers (Edit)` permissions.
4. Click **Confirm**.
5. An authorization link will appear in the web console:
   ```text
   Please open the URL below in a new tab, select your domain, and click 'Authorize':
   https://dash.cloudflare.com/argotunnel?callback=https%3A%2F%2Flogin...
   ```
6. Click the link in a new tab, select your domain on Cloudflare, and click **Authorize**.
7. **Done!** In one single automated flow, the printer:
   - Creates the tunnel and credentials in your Cloudflare account.
   - Configures the DNS CNAME record for your subdomain.
   - Activates Cloudflare Zero Trust **One-Time PIN (OTP)** authentication.
   - Adds your email to the Access policy (preserving any other existing login methods or users).
   - Starts the `cloudflared` service and puts your printer securely online at `https://u1.yourdomain.com`.

---

## Zero Trust Edge Authentication (One-Time PIN)

When you access `https://u1.yourdomain.com` from anywhere in the world:
1. Cloudflare intercepts the connection at the edge before anything reaches your printer.
2. Prompts for your authorized email address.
3. Sends an instant 6-digit PIN code to your inbox.
4. You enter the PIN and access Fluidd immediately.

> [!NOTE]
> If your Cloudflare Zero Trust account already has other identity providers (such as Google, GitHub, Azure AD) or existing access policies configured, they are **preserved**. The One-Time PIN method and your authorized email are added seamlessly alongside them.

---

## Web Troubleshooting & Diagnostics

Under **Firmware Config > Actions**:
- **Cloudflared Status**: Inspect live tunnel status, Zero Trust protection state, active connection, and recent logs directly from the web app.

