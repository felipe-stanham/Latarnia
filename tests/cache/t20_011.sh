#!/usr/bin/env bash
# t20-011: App route at /docs/x -> reachable at /apps/{app}/docs/x only behind session forward_auth, identity headers passed.
# Caddyfile check + simulated forward_auth against /auth/verify, then the App's header-echo route with the copied headers.
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
load_principals
PYTHONPATH=src $PY $CACHE/t20_caddy_check.py 011 || FAILS=1
URI='X-Forwarded-Uri: /apps/example_full_app/docs/x'
check "verify anonymous" 401 "$(code $BASE/auth/verify -H "$URI")"
check "verify with session" 200 "$(code -b latarnia_session=$S_LOW $BASE/auth/verify -H "$URI")"
HDRS=$(curl -s -D - -o /dev/null -b latarnia_session=$S_LOW $BASE/auth/verify -H "$URI" | tr -d '\r')
for h in x-latarnia-user x-latarnia-app-role x-latarnia-is-super; do
  if echo "$HDRS" | grep -qi "^$h:"; then echo "  ok   verify returns $h"; else echo "  FAIL verify missing $h"; FAILS=$((FAILS+1)); fi
done
ROLE=$(echo "$HDRS" | awk -F': ' 'tolower($1)=="x-latarnia-app-role"{print $2}')
check "role header value" webUI-low "$ROLE"
# Simulate Caddy copy_headers into the proxied request: the App must receive them.
PORT=$(grep -A12 'handle_path /apps/example_full_app/' caddy/latarnia.caddyfile | grep -o 'reverse_proxy localhost:[0-9]*' | head -1 | cut -d: -f2)
U=$(echo "$HDRS" | awk -F': ' 'tolower($1)=="x-latarnia-user"{print $2}')
BODY=$(curl -s -H "X-Latarnia-User: $U" -H "X-Latarnia-App-Role: $ROLE" -H "X-Latarnia-Is-Super: false" localhost:$PORT/b/test)
if echo "$BODY" | grep -qi '"x-latarnia-app-role": *"webUI-low"'; then echo "  ok   App receives identity headers when forwarded (port $PORT)"; else echo "  FAIL App did not echo headers: $BODY"; FAILS=$((FAILS+1)); fi
finish t20-011
