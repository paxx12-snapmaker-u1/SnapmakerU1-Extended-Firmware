---
title: Third-Party Integrations
---

# Third-Party Integrations

Third-party integrations in this firmware are handled through an on-demand download system with cryptographic verification.

## Design Principles

External components that are non-essential to core printer operations and of significant size are not bundled with the firmware image. Instead, they are:

1. Fetched on-demand when enabled by the user
2. Pinned to specific versions
3. Verified using SHA256 checksums

This reduces firmware image size, allows independent component updates, and maintains separation between core and optional functionality.

## Implementation Pattern

External components are installed by `extended-pkg`, a single package manager
shared by every integration. Fetching the archive, verifying its checksum,
unpacking it and repointing `latest` all happen there; an integration only
declares *what* to fetch, in a definition file that `extended-pkg` sources from
`/usr/local/share/extended-pkg/<name>`.

`extended-pkg` ships in the `01-system-utils` overlay; each definition ships
with the overlay that owns the app.

### Example

The VPN integration (`extended-pkg tailscale`) demonstrates this pattern:

```bash
PKG_LABEL="Tailscale"
PKG_VERSION=1.92.5
PKG_URL="https://pkgs.tailscale.com/stable/tailscale_${PKG_VERSION}_arm64.tgz"
PKG_SHA256=13a59c3181337dfc9fdf9dea433b04c1fbf73f72ec059f64d87466b79a3a313c
PKG_BINARIES="tailscale tailscaled"
```

Characteristics:
- Version pinned to `1.92.5`
- Downloads from upstream package repository
- SHA256 checksum hardcoded for verification
- Not included in firmware image
- Installed to `/oem/apps/tailscale/tailscale-${PKG_VERSION}`, with `/oem/apps/tailscale/latest` pointing at it

### Definition Reference

Three variables are required:

| Variable | Meaning |
| --- | --- |
| `PKG_VERSION` | Pinned version. |
| `PKG_URL` | Archive URL — a `.zip` or a gzipped tarball. |
| `PKG_SHA256` | Expected checksum of that archive. |

The rest are optional and default to something derived from `<name>`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PKG_LABEL` | `<name>` | Human-readable name used in messages. |
| `PKG_APP_DIR` | `/oem/apps/<name>` | Install root. |
| `PKG_PREFIX` | `<name>` | Versioned directory name, minus `-$PKG_VERSION`. Set it when the archive does not unpack to `<name>-<version>`. |
| `PKG_BINARIES` | *(none)* | Paths, relative to the install directory, that must exist and be executable for the package to count as installed. When empty, the directory merely has to exist. |
| `PKG_CLEAN_PATHS` | *(none)* | Extra paths `clean` removes, such as state directories living outside the install root. |
| `PKG_LEGACY_DIR` | *(none)* | A pre-`latest` install directory. Used until an upgrade replaces it, and removed once one does. |

A definition needing more than a download may define:

```bash
pkg_post_install() {
    local staging=$1
    ...
}
```

It runs after extraction but before the install is moved into place, against the
staging directory; returning non-zero fails the install. OctoEverywhere uses it
to build its virtualenv.

### Adding an Integration

1. Drop a definition at
   `overlays/firmware-extended/<overlay>/root/usr/local/share/extended-pkg/<name>`
   setting at least `PKG_VERSION`, `PKG_URL` and `PKG_SHA256`.
2. Set `PKG_BINARIES` so `check` can tell a complete install from a partial one.
3. Call `extended-pkg <name> download` from the overlay's firmware-config action,
   and `extended-pkg <name> exec <binary>` wherever the app is launched.
4. Document the component per the requirements below.

No changes to `extended-pkg` itself should be needed; if one is, it belongs
behind a new `PKG_*` knob rather than a special case keyed on the app name.

## Strict Versioning

Each external component is pinned to a specific version:
- Version numbers are hardcoded in the package definition
- No automatic updates
- Upgrades require firmware update with new version and checksum
- Same firmware version fetches the same external component version

Downloads are verified using SHA256 checksums. If verification fails, installation aborts.

## Package Manager Interface

`extended-pkg <name> <command>` provides:

- `check` - verify if component is installed
- `needs_upgrade` - report whether the installed version differs from the pinned one
- `download` - download, verify, and install component
- `upgrade` - re-download and reinstall over an existing installation
- `clean` - remove installed component
- `exec <binary> [args...]` - run a binary from the installed component

## Documentation Requirements

Third-party components must be documented in the relevant category file (e.g., `docs/vpn.md`, `docs/cloud.md`) with:

1. **Neutral technical description** - explain what the component does without promotional language
2. **Installation instructions** - how to enable and download the component
3. **Configuration** - any required setup or configuration steps
4. **Usage** - how to use the component once installed
5. **Limitations** - known constraints or issues
6. **Reference to this document** - link to this design document for technical details

Documentation must remain factual and neutral, avoiding marketing materials or subjective claims.
