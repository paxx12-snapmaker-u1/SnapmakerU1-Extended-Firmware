# St0rmingBr4in's mod

Personal overlays, built with:

```bash
./dev.sh make build PROFILE=extended-St0rmingBr4in
```

## 01-addon-installers

Adds an **Add-ons** settings section to the firmware-config web UI with an
Installed / Not Installed toggle for optional software that is intentionally
not baked into the firmware image:

- **HelixScreen** (install/uninstall) — replaces the stock touchscreen UI,
  keeps the Remote Screen feature working
- **Git** — Debian aarch64 binary + libcurl shim, for AFC updates and GitOps
- **Spoolman** — filament spool tracking wired into Moonraker, UI on port 7912

It also adds an **Overlay Persistence** setting (Settings > System) that
manages the `/oem/.debug` flag the installers require.

See [01-addon-installers/README.md](01-addon-installers/README.md) for
details and manual (SSH) usage of the recipes.
