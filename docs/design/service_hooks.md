---
title: Service Hooks
---

# Service Hooks

This is the extension contract for firmware overlays. It replaces feature
patches to stock service init scripts with independently owned hook files.

The Klipper, Moonraker, and LMD init scripts load optional shell hooks before
handling a service action. Hooks provide a stable extension point for firmware
features and local overlays without adding further patches to stock init
scripts.

| Service | Hook directory |
| --- | --- |
| Klipper | `/etc/hooks/klipper.d/` |
| Moonraker | `/etc/hooks/moonraker.d/` |
| LMD | `/etc/hooks/lmd.d/` |

Every readable `*.sh` file is sourced in lexical order. A hook receives the
service action as `$1`: `start`, `stop`, or `restart`. Hooks share the init
script's shell environment, so they can adjust exported environment variables
or service variables before the service is started.

Use numeric filename prefixes when ordering matters. For example,
`20-camera-selection.sh` runs after `10-firmware-imposter.sh`.

## Example

Create `/etc/hooks/moonraker.d/10-example.sh`:

```sh
if [ "$1" = start ]; then
    export EXAMPLE_OPTION=enabled
fi
```

Hooks that must intentionally prevent an action can use `exit 0`; this exits
the invoking init script successfully. Do not use `exit` for normal setup,
because it prevents later hooks and the stock service action from running.
