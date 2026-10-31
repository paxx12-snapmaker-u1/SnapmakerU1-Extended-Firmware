#!/usr/bin/env sh
# shellcheck disable=SC2034
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

# RFC 2136 dynamic DNS updates with TSIG, without `nsupdate`.
# Sends the updates through /usr/local/sbin/rfc2136-update (dnspython).
#
# RFC2136_SERVER      DNS server to update (IP or hostname)
# RFC2136_PORT        Port (default 53)
# RFC2136_KEY_NAME    TSIG key name (optional)
# RFC2136_KEY_SECRET  TSIG secret, base64
# RFC2136_KEY_ALG     TSIG algorithm (default hmac-sha256)
# RFC2136_ZONE        Zone (default: SOA lookup on the server)

dns_rfc2136_info='RFC 2136 dynamic update (TSIG), no nsupdate required
Options:
 RFC2136_SERVER Server
 RFC2136_PORT Port. Optional.
 RFC2136_KEY_NAME TSIG key name. Optional.
 RFC2136_KEY_SECRET TSIG secret, base64. Optional.
 RFC2136_KEY_ALG TSIG algorithm. Optional, default hmac-sha256.
 RFC2136_ZONE Zone. Optional.
'

dns_rfc2136_add() {
  fulldomain=$1
  txtvalue=$2
  _dns_rfc2136_load || return 1
  _info "Adding $fulldomain via RFC 2136 to $RFC2136_SERVER"
  _dns_rfc2136_run add "$fulldomain" "$txtvalue"
}

dns_rfc2136_rm() {
  fulldomain=$1
  txtvalue=$2
  _dns_rfc2136_load || return 1
  _info "Removing $fulldomain via RFC 2136 from $RFC2136_SERVER"
  _dns_rfc2136_run rm "$fulldomain" "$txtvalue"
}

_dns_rfc2136_load() {
  # The environment wins; values saved by an earlier run are the fallback
  for _var in RFC2136_SERVER RFC2136_PORT RFC2136_KEY_NAME RFC2136_KEY_SECRET RFC2136_KEY_ALG RFC2136_ZONE; do
    eval "[ -n \"\$$_var\" ] || $_var=\"\$(_readaccountconf_mutable $_var)\""
  done
  if [ -z "$RFC2136_SERVER" ]; then
    _err "RFC2136_SERVER is not set"
    return 1
  fi
  for _var in RFC2136_SERVER RFC2136_PORT RFC2136_KEY_NAME RFC2136_KEY_SECRET RFC2136_KEY_ALG RFC2136_ZONE; do
    eval "_saveaccountconf_mutable $_var \"\$$_var\""
  done
}

_dns_rfc2136_run() {
  RFC2136_SERVER="$RFC2136_SERVER" RFC2136_PORT="$RFC2136_PORT" \
    RFC2136_KEY_NAME="$RFC2136_KEY_NAME" RFC2136_KEY_SECRET="$RFC2136_KEY_SECRET" \
    RFC2136_KEY_ALG="$RFC2136_KEY_ALG" RFC2136_ZONE="$RFC2136_ZONE" \
    /usr/local/sbin/rfc2136-update "$@"
}
