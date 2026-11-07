---
title: HTTPS (Experimental)
---

# HTTPS (Experimental)

Serve Fluidd/Mainsail, Moonraker's API, firmware-config, and the camera streams over HTTPS instead of plain HTTP. Optionally require browsers to present a client certificate (mTLS).

> **Note**: The ACME client, [acme.sh](https://github.com/acmesh-official/acme.sh), is included in the firmware. ACME works without internet access when the CA is on your network, for example [step-ca](https://smallstep.com/docs/step-ca/).

## Certificate Sources

| Mode | Certificate |
| --- | --- |
| `none` (default) | HTTPS disabled, plain HTTP on port 80 |
| `self_signed` | Generated on the printer. Browsers warn until you trust it |
| `manual` | Your own certificate and key, uploaded to `extended/tls/` |
| `acme` | Issued and renewed automatically by an ACME CA (Let's Encrypt, ZeroSSL, step-ca, ...) |

Whenever the configured certificate is unusable (manual files missing or mismatched, ACME not issued yet), the printer serves a self-signed certificate so the web interface stays reachable. Check `logs/https.log` when that happens.

## Using firmware-config Web UI (preferred)

Open [firmware-config](firmware_config.md), go to the **HTTPS** section, and pick a mode. Once HTTPS is on, more settings appear:

- **Plain HTTP on Port 80**: what port 80 does
- **Require Client Certificates (mTLS)**
- **Moonraker Port 7125**: whether Moonraker accepts direct connections from the network

The HTTPS status section of firmware-config shows the active certificate. **Actions > HTTPS** has *Apply HTTPS Configuration*, *Renew HTTPS Certificate*, *Regenerate Self-Signed Certificate* and *Show HTTPS Log*.

After switching modes, open the printer at `https://<printer-ip>/`.

## Files

Files you provide go in `extended/tls/`, which you can reach in Fluidd/Mainsail under **Configuration** (create the `tls` folder if it does not exist):

| File | Used for |
| --- | --- |
| `cert.pem` | `manual` mode: certificate, followed by any intermediate certificates (full chain) |
| `key.pem` | `manual` mode: private key for `cert.pem` |
| `client-ca.pem` | mTLS: CA certificate(s) that sign accepted client certificates |
| `acme-ca.pem` | `acme` mode: root certificate of a private ACME CA |
| `acme.env` | `acme` mode: EAB and DNS provider credentials, as `KEY=value` lines |

> **Warning**: Anyone who can open the Configuration section of Fluidd/Mainsail can read these files, including private keys and API tokens. Enable [Web Authentication](firmware_config.md) or mTLS to restrict who that is.

Generated certificates, ACME account keys and acme.sh state (including DNS credentials it remembers) live in `/home/lava/printer_data/tls/`, which is not exposed through the web interface.

> **Note**: nginx uses copies of `client-ca.pem`, `cert.pem` and `key.pem` that are made when the HTTPS configuration is applied. After adding or replacing one of them, run **Actions > HTTPS > Apply HTTPS Configuration**, or reboot the printer, which applies it too. Until then the old files stay in use, and the [client certificate test](#testing-a-client-certificate) is not available after uploading `client-ca.pem` for the first time. `acme-ca.pem` and `acme.env` are read on every certificate request and need no apply.

## Port 80

While HTTPS is enabled, `http` controls plain HTTP on port 80:

- `redirect` (default): redirect to HTTPS
- `serve`: serve the web interface on both HTTP and HTTPS
- `off`: answer every request with 404

ACME HTTP-01 challenges (`/.well-known/acme-challenge/`) are answered on port 80 in every mode.

> **Note**: Some slicers upload to the printer over plain `http://` and do not follow redirects or trust self-signed certificates. If uploads stop working, use `serve`, or point the slicer at `https://` with a trusted certificate.

## ACME

The firmware-config **ACME** option asks for the domain name(s) and the CA. Enter either a directory URL or one of acme.sh's shortcodes: `letsencrypt`, `letsencrypt_test`, `zerossl`, `google`, `googletest`, `sslcom`, `actalis`. It then requests the certificate right away and shows the output. If the request fails, the previous mode is restored.

Certificates are checked twice a day and renewed when due (30 days before expiry, or when the CA's renewal info suggests). After a reboot in `acme` mode without a certificate, one is requested about two minutes after boot.

Other options are set in `extended2.cfg`:

```ini
[https]
mode: acme
acme_server: letsencrypt
acme_domains: printer.example.com
# Optional contact address
acme_email: you@example.com
# http (default) or dns
acme_challenge: dns
# acme.sh DNS provider, see https://github.com/acmesh-official/acme.sh/wiki/dnsapi
acme_dns_provider: cf
# Fixed wait instead of checking public DNS, needed without internet
# acme_dns_sleep: 30
```

### Private CA (offline)

If your ACME CA uses a certificate from your own root (step-ca, Smallstep, an internal Vault or Boulder), upload that root certificate as `extended/tls/acme-ca.pem` and set `acme_server` to the CA's directory URL, for example `https://ca.lan/acme/acme/directory`. The printer then needs no internet access: use HTTP-01, or DNS-01 with [RFC 2136](#dns-01-without-internet-rfc-2136).

If the CA's certificate is not trusted, the request fails right away with a message pointing at `acme-ca.pem`.

### HTTP-01

`acme_challenge: http` (the default) needs the CA to reach port 80 of the printer at each domain name. For a public CA like Let's Encrypt that means port forwarding, which is **not recommended** for a printer. HTTP-01 is a good fit for an internal CA on your LAN.

### DNS-01 (recommended for public CAs)

DNS-01 proves domain ownership through a TXT record, so the printer never has to be reachable from the internet. Set `acme_challenge: dns`, set `acme_dns_provider` to an [acme.sh DNS provider](https://github.com/acmesh-official/acme.sh/wiki/dnsapi) (with or without the `dns_` prefix), and put the provider's credentials in `extended/tls/acme.env`. For example, for Cloudflare (`acme_dns_provider: cf`):

```sh
CF_Token=your-token
```

Then point the domain at the printer's LAN address on your local DNS server or router.

### DNS-01 without internet (RFC 2136)

`acme_dns_provider: rfc2136` updates your own DNS server (BIND, Knot, PowerDNS, Windows DNS, ...) with signed RFC 2136 dynamic updates. It does not need `nsupdate`. In `extended/tls/acme.env`:

```sh
RFC2136_SERVER=192.168.1.53
# Optional: TSIG key (recommended), port and zone
RFC2136_KEY_NAME=acme-key
RFC2136_KEY_SECRET=base64-secret
RFC2136_KEY_ALG=hmac-sha256
# RFC2136_PORT=53
# RFC2136_ZONE=example.lan
```

Without `RFC2136_ZONE`, the zone is found by asking the server. For BIND, allow the key to update TXT records:

```
key "acme-key" { algorithm hmac-sha256; secret "base64-secret"; };
zone "example.lan" { ...; update-policy { grant acme-key zonesub TXT; }; };
```

acme.sh normally confirms that the TXT record is visible through public DNS-over-HTTPS resolvers before continuing. With `rfc2136` it waits 10 seconds instead. For other providers on a network without internet, set `acme_dns_sleep`.

### External Account Binding (EAB)

Some CAs (Google Trust Services, SSL.com, many internal CAs) require EAB. Put the key ID and HMAC key from the CA in `extended/tls/acme.env`:

```sh
ACME_EAB_KID=your-key-id
ACME_EAB_HMAC_KEY=your-hmac-key
```

For ZeroSSL, setting `acme_email` is enough: acme.sh fetches the EAB credentials itself.

`acme.env` is read as plain `KEY=value` lines and is never executed. Variables that would change how acme.sh or the shell behave (`PATH`, `LD_*`, `LE_*`, `HOME`, ...) are ignored.

## Client Certificates (mTLS)

With mTLS on, nginx only accepts HTTPS connections from clients that present a certificate signed by `extended/tls/client-ca.pem`. Port 80 is forced to `redirect` so plain HTTP cannot bypass it.

The printer does not issue client certificates; use your own CA. A minimal example with `openssl` on your computer:

```sh
# CA (keep ca.key safe and off the printer)
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
  -subj "/CN=Printer Client CA" -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" -keyout ca.key -out client-ca.pem

# Client certificate for one browser/device
openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -subj "/CN=laptop" -keyout laptop.key -out laptop.csr
openssl x509 -req -in laptop.csr -CA client-ca.pem -CAkey ca.key -days 825 \
  -extfile <(echo "extendedKeyUsage=clientAuth") -out laptop.pem
openssl pkcs12 -export -inkey laptop.key -in laptop.pem -out laptop.p12
```

1. Upload `client-ca.pem` to `extended/tls/` and run **Actions > HTTPS > Apply HTTPS Configuration** (or reboot).
2. Import `laptop.p12` into your browser or OS certificate store.
3. [Test the certificate](#testing-a-client-certificate) at `https://<printer-ip>:4443/https-check`.
4. Enable **Require Client Certificates (mTLS)** in firmware-config.

Certificates from an ACME CA (for example step-ca) work too, as long as they are signed by the CA in `client-ca.pem` and include the `clientAuth` extended key usage.

### Testing a client certificate

Once `client-ca.pem` is uploaded and HTTPS is on, firmware-config shows **Quick Links > Test Client Certificate**. It opens a test page on port 4443, `https://<printer-ip>:4443/https-check`. It asks the browser for a client certificate without requiring one, and reports:

- `OK - this certificate is trusted by client-ca.pem`: enabling mTLS is safe for this browser.
- `no client certificate was sent by this browser`: none is installed, the browser did not offer it, or you dismissed the certificate picker. Restart the browser after importing a certificate.
- `REJECTED - ...` with the reason, for example `certificate has expired`, `self-signed certificate` or `unable to get local issuer certificate` (signed by a different CA), or `unsuitable certificate purpose` (missing `clientAuth`).

It also shows the certificate's subject, issuer, expiry and fingerprint. The web interface on port 443 never asks for a certificate until mTLS is enabled, so testing cannot affect it.

With mTLS enabled, the test port closes and the same page is at `https://<printer-ip>/https-check`. A browser without a valid certificate gets this page with the reason instead of a bare error.

To test from a command line:

```sh
curl -k --cert laptop.pem --key laptop.key https://<printer-ip>:4443/https-check
```

> **Warning**: Install a client certificate **before** enabling mTLS, or you will lock yourself out of the web interface. See [Recovering from a lockout](#recovering-from-a-lockout).

If `client-ca.pem` is missing or invalid at boot, client certificates are **not** required, and an error is logged to `logs/https.log`. This avoids a lockout.

### Recovering from a lockout

If you enabled mTLS without a working client certificate:

1. Create an empty file named `https-recover.txt` on a USB drive (FAT32 or exFAT).
2. Plug the drive into the printer. Client certificates are turned off within a few seconds. If the drive is already in the printer while it boots, they are turned off during boot.
3. Open `https://<printer-ip>/` again. HTTPS and its certificate stay as they were; only the mTLS requirement is turned off (`mtls: false`).

The printer deletes `https-recover.txt` from the drive once it has used it, so leaving the drive in does not undo a later re-enable. The recovery is recorded in `logs/https.log`.

Other options:

- Over [SSH](ssh_access.md) (SSH does not use client certificates): `/usr/local/sbin/https-setup recover`
- `extended-recover.txt` on a USB drive resets the whole extended configuration, including HTTPS (see [Firmware Configuration](firmware_config.md))

## Moonraker Port 7125

Moonraker also listens on port 7125, which goes around nginx, and therefore around HTTPS and client certificates. Set **Moonraker Port 7125** to **Printer Only** (`moonraker_bind: localhost`) so Moonraker only accepts connections from the printer itself. Connections from the printer itself and the web interface (through nginx) are still accepted. Slicers and apps configured with `<printer-ip>:7125` must be changed to the web address (`http(s)://<printer-ip>/`).

## Limitations

- Only the nginx web server (ports 80/443) is covered. Other services keep their own ports and protocols, such as the RTSP camera stream, the Klipper exporter, and the raw camera ports.
- Self-signed certificates include the printer's IP addresses at the time they are generated. After an IP change, use *Regenerate Self-Signed Certificate* or add the address to `self_signed_names`.

## Configuration Reference

```ini
[https]
# Serve the web interface over HTTPS: none, self_signed, manual, acme
mode: none
# Plain HTTP on port 80 while HTTPS is enabled: redirect, serve, off
# http: redirect
# HTTPS port
# port: 443
# Extra names/IPs for the self-signed certificate, comma separated
# self_signed_names: printer.lan, 192.168.1.50
# ACME CA directory URL or acme.sh shortcode (letsencrypt, letsencrypt_test, zerossl, google, ...)
# For a private CA, put its root certificate in extended/tls/acme-ca.pem
# acme_server: letsencrypt
# acme_domains: printer.example.com
# acme_email: you@example.com
# ACME challenge: http, dns
# acme_challenge: http
# acme.sh DNS provider, e.g. cf or rfc2136; credentials go in extended/tls/acme.env
# acme_dns_provider: cf
# Fixed wait in seconds instead of checking public DNS (needed without internet)
# acme_dns_sleep: 30
# Require client certificates signed by extended/tls/client-ca.pem: true, false
# mtls: false
# Moonraker port 7125 listens on: all, localhost
# moonraker_bind: all
```

Changes made directly in the file take effect after **Actions > HTTPS > Apply HTTPS Configuration**, a reboot, or `/usr/local/sbin/https-setup reload` over SSH. Changing `moonraker_bind` also needs Moonraker restarted.
