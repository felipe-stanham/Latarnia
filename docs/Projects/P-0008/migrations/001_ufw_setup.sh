#!/usr/bin/env bash
#
# P-0008 migration 001: firewall (ufw) setup for the Caddy ingress model.
#
# Idempotent. Re-running only re-asserts the same rules. Run on the Pi as a
# user with sudo. After P-0008, Caddy is the ONLY internet-facing service.
#
# Model:
#   - Internet-facing (NAT-forwarded) ports opened explicitly from anywhere.
#   - Latarnia-internal ports denied explicitly — prevents LAN devices from
#     bypassing Caddy auth by hitting the raw API/app/MCP ports directly.
#   - Everything else from the LAN subnet is allowed — covers Homebridge
#     (all bridges, UI, mDNS), Postgres, and any future co-located service
#     without requiring per-port rules.
#
# Allowed from anywhere (internet-facing):
#   22/tcp    SSH
#   80/tcp    ACME HTTP-01 challenge (Let's Encrypt) + HTTP->HTTPS redirect
#   443/tcp   Caddy HTTPS (PRD)
#   8443/tcp  Caddy HTTPS (TST)
#
# Denied from anywhere including LAN (Latarnia internals — must go via Caddy):
#   8000/tcp        Latarnia platform (tst)
#   8080/tcp        Latarnia platform (prd)
#   8100:8199/tcp   App REST API ports
#   9001:9099/tcp   App MCP ports
#
# Allowed from LAN (everything not matched above):
#   192.168.68.0/24 → any   covers Homebridge, Postgres, mDNS, future services
#
# The Caddy admin API (2019) is bound to localhost by Caddy itself.

set -euo pipefail

# LAN subnet — update if your router uses a different range.
LAN_SUBNET="192.168.68.0/24"

if ! command -v ufw >/dev/null 2>&1; then
    echo "ufw not installed; installing..."
    sudo apt-get update -y
    sudo apt-get install -y ufw
fi

echo "Applying ufw rules (idempotent)..."

# Default posture: deny inbound, allow outbound.
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Internet-facing services (NAT-forwarded — reachable from outside the LAN).
sudo ufw allow 22/tcp     comment 'SSH'
sudo ufw allow 80/tcp     comment 'ACME HTTP-01 + HTTP redirect'
sudo ufw allow 443/tcp    comment 'Caddy HTTPS (PRD)'
sudo ufw allow 8443/tcp   comment 'Caddy HTTPS (TST)'

# Latarnia-internal ports: deny even from LAN so clients must go via Caddy.
# These rules are evaluated before the broad LAN allow below.
sudo ufw deny 8000/tcp        comment 'Latarnia platform tst (Caddy only)'
sudo ufw deny 8080/tcp        comment 'Latarnia platform prd (Caddy only)'
sudo ufw deny 8100:8199/tcp   comment 'App REST API ports (Caddy only)'
sudo ufw deny 9001:9099/tcp   comment 'App MCP ports (Caddy only)'

# Trust the entire LAN for everything else.
# Covers: Homebridge (all bridge + UI ports, mDNS/UDP 5353), Postgres,
# and any future service without needing per-port rules.
sudo ufw allow from "$LAN_SUBNET" comment 'LAN — full access'

# Enable (no-op if already enabled). --force avoids the interactive prompt.
sudo ufw --force enable

echo
echo "Current ufw status:"
sudo ufw status verbose
