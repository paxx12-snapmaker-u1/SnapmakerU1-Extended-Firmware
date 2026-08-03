# shellcheck shell=sh disable=SC1091,SC2039,SC2166
# Add Entware to PATH if entware is active

[ -d /opt/bin ] && export PATH="/opt/bin:$PATH"
[ -d /opt/sbin ] && export PATH="/opt/sbin:$PATH"
[ -d /opt/usr/bin ] && export PATH="/opt/usr/bin:$PATH"
[ -d /opt/usr/sbin ] && export PATH="/opt/usr/sbin:$PATH"
