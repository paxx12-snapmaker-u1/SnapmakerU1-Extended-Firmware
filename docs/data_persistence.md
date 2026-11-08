---
title: Data Persistence
---

# Data Persistence

By default, Snapmaker firmware resets all system changes on reboot for stability.

## Enable System Persistence

To persist system-level changes to `/etc` (SSH passwords, authorized keys, etc.):

```bash
touch /oem/.debug
```

To restore pristine system state:

```bash
rm /oem/.debug
reboot
```

## Selective Persistence

**Available in: Extended firmware only**

Instead of persisting all system changes with `/oem/.debug`, you can selectively preserve specific paths using `/etc/overlayfs.conf`.

Create the file with one path per line:

```bash
cat > /etc/overlayfs.conf << 'EOF'
/root
EOF
```

On reboot, only listed paths are preserved. Everything else resets to pristine state.

The following paths are always preserved regardless of configuration:

- `/etc/overlayfs.conf` itself
- `/var/lib/dhcpcd/*` (DHCP leases)
- `/etc/dropbear/*` (SSH host keys)

### Format

- One path per line
- Paths can be absolute or relative to root
- Trailing slashes are optional
- Lines starting with `#` are comments
- Empty lines are ignored

## Printer Data

The `/home/lava/printer_data` directory always persists, regardless of `/oem/.debug`.

## Firmware Upgrades

**Firmware upgrades automatically remove all persisted changes and delete `/oem/.debug`.**

After upgrading, you can re-enable persistence if needed:

```bash
touch /oem/.debug
```
