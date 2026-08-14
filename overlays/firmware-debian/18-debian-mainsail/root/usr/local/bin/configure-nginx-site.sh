#!/bin/bash

SITES_ENABLED="/etc/nginx/sites-enabled"
SITES_AVAILABLE="/etc/nginx/sites-available"

rm -f "$SITES_ENABLED/fluidd"
rm -f "$SITES_ENABLED/mainsail"

ln -sf "$SITES_AVAILABLE/fluidd" "$SITES_ENABLED/"

echo "Enabled Fluidd web interface"
