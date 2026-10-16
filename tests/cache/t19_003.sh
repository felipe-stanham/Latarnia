#!/usr/bin/env bash
[ "${ENV:-dev}" = "prod" ] && { echo "ENV=prod refused" >&2; exit 2; }
exec node "$(dirname "$0")/t19_status_reasons.js" t19-003
