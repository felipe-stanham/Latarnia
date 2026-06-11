#!/usr/bin/env bash
#
# P-0008 migration 001: firewall (ufw) setup for the Caddy ingress model.
#
# Idempotent. Re-running only re-asserts the same rules. Run on the Pi as a
# user with sudo. After P-0008, Caddy is the ONLY externally reachable
# service; all platform/app/MCP ports are blocked from off-host access.
#
# Allowed in:
#   22/tcp    SSH
#   80/tcp    ACME HTTP-01 challenge (Let's Encrypt) + HTTP->HTTPS redirect
#   443/tcp   Caddy HTTPS (PRD)
#   8443/tcp  Caddy HTTPS (TST)
#
# Denied in (defence in depth — default deny already blocks these, but we
# add explicit DENY rules so intent is auditable in `ufw status`):
#   8000/tcp        Latarnia platform (loopback only)
#   8100:8199/tcp   App REST API ports
#   9001:9099/tcp   App MCP ports
#
# The Caddy admin API (2019) is bound to localhost by Caddy itself and is not
# referenced here.
set -euo pipefail

if ! command -v ufw >/dev/null 2>&1; then
    echo "ufw not installed; installing..."
    sudo apt-get update -y
    sudo apt-get install -y ufw
fi

echo "Applying ufw rules (idempotent)..."

# Default posture: deny inbound, allow outbound.
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allowed services
sudo ufw allow 22/tcp     comment 'SSH'
sudo ufw allow 80/tcp     comment 'ACME HTTP-01 + HTTP redirect'
sudo ufw allow 443/tcp    comment 'Caddy HTTPS (PRD)'
sudo ufw allow 8443/tcp   comment 'Caddy HTTPS (TST)'

# Explicit denies for platform/app/MCP ports (loopback access is unaffected).
sudo ufw deny 8000/tcp        comment 'Latarnia platform (loopback only)'
sudo ufw deny 8100:8199/tcp   comment 'App REST API ports'
sudo ufw deny 9001:9099/tcp   comment 'App MCP ports'

# Enable (no-op if already enabled). --force avoids the interactive prompt.
sudo ufw --force enable

echo
echo "Current ufw status:"
sudo ufw status verbose
