#!/usr/bin/env bash
# Live (dev server) regression checks for TESTS.md Integration + P-0008 entries. Run from anywhere.
# T-0023 ingress checks run only with INGRESS=<Caddy ingress base URL>; for tst pass sessions minted there:
#   INGRESS=https://<tst> INGRESS_SU=<superuser session> INGRESS_USER=<non-superuser session> bash tests/cache/regression_live.sh
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
load_principals
SU="-b latarnia_session=$S_SU"; LOW="-b latarnia_session=$S_LOW"; NONE="-b latarnia_session=$S_NONE"
APPS=$(curl -s $SU $BASE/api/apps)
jget() { echo "$APPS" | $PY -c "import sys,json; d=json.load(sys.stdin)['apps']; a=[x for x in d if x['name']=='example_full_app'][0]; print($1)"; }
echo "test_mcp_dynamic_port_allocation"
check enabled True "$(jget "a['mcp_info']['enabled']")"
check port_in_range True "$(jget "isinstance(a['mcp_info']['mcp_port'],int) and 9001<=a['mcp_info']['mcp_port']<=9099")"
echo "test_mcp_gateway_lists_example_tools"
check healthy True "$(jget "a['mcp_info']['healthy']")"
check tools True "$(jget "set(['list_items','add_item','get_status'])<=set(a['mcp_info']['registered_tools'])")"
echo "test_example_app_db_provisioned"
check provisioned True "$(jget "a['database_info']['provisioned']")"
check migrations True "$(jget "set(['001_initial.sql','002_add_tags.sql','003_add_status.sql'])<=set(a['database_info']['applied_migrations'])")"
PORT=$(jget "a['runtime_info']['assigned_port']")
echo "test_example_app_web_ui_loads (port $PORT)"
BODY=$(curl -s localhost:$PORT/)
echo "$BODY" | grep -q "Example Full App" && echo "  ok heading" || { echo "  FAIL heading"; FAILS=$((FAILS+1)); }
echo "$BODY" | grep -q "Add Item" && echo "  ok Add Item form" || { echo "  FAIL Add Item"; FAILS=$((FAILS+1)); }
echo "test_example_webui_role_header"
echo "$(curl -s -H 'X-Latarnia-App-Role: webUI-low' localhost:$PORT/)" | grep -q "Add Item" && { echo "  FAIL low sees Add Item"; FAILS=$((FAILS+1)); } || echo "  ok low: no Add Item"
FULL=$(curl -s -H 'X-Latarnia-App-Role: full' localhost:$PORT/); echo "$FULL" | grep -q "Add Item" && echo "$FULL" | grep -qi "Admin" && echo "  ok full: Add Item + Admin" || { echo "  FAIL full"; FAILS=$((FAILS+1)); }
echo "$BODY" | grep -q "Add Item" && echo "  ok no header defaults to full" || { echo "  FAIL default"; FAILS=$((FAILS+1)); }
echo "test_item_creation_publishes_event"
check create 200 "$(code -X POST "localhost:$PORT/api/items?name=stream-test")"
sleep 1
LAST=$(redis-cli XREVRANGE latarnia:streams:example.events.created + - COUNT 1)
echo "$LAST" | grep -q example_full_app && echo "$LAST" | grep -q item_created && echo "  ok stream entry source+item_created" || { echo "  FAIL stream entry: $LAST" | cut -c1-200; FAILS=$((FAILS+1)); }
echo "test_verify_headers / unauthenticated"
check anon_verify 401 "$(code $BASE/auth/verify)"
check anon_api_apps 401 "$(code $BASE/api/apps)"
check anon_mcp_sse 401 "$(code -m 3 $BASE/mcp/sse)"
echo "test_dashboard_tile_role_filtering"
check none_hides_tile False "$(curl -s $NONE $BASE/api/apps | $PY -c "import sys,json; print(any(a['name']=='example_full_app' for a in json.load(sys.stdin)['apps']))")"
check low_sees_tile True "$(curl -s $LOW $BASE/api/apps | $PY -c "import sys,json; print(any(a['name']=='example_full_app' for a in json.load(sys.stdin)['apps']))")"
echo "test_assign_full_requires_superuser"
UID_NONE=$(curl -s $SU $BASE/api/auth/users | $PY -c "import sys,json; d=json.load(sys.stdin); d=d if isinstance(d,list) else d['users']; print([u['id'] for u in d if u['username']=='t20-none'][0])")
check nonsu_assign_full 403 "$(code $LOW -X POST -H 'content-type: application/json' -d "{\"role\":\"full\",\"user_id\":\"$UID_NONE\"}" $BASE/api/auth/roles/example_full_app)"
assign() { curl -s -w '\n%{http_code}' $SU -X POST -H 'content-type: application/json' -d "{\"role\":\"$1\",\"user_id\":\"$UID_NONE\"}" $BASE/api/auth/roles/example_full_app; }
R=$(assign full); check su_assign_full 200 "$(echo "$R" | tail -1)"; [ "$(echo "$R" | tail -1)" = 200 ] || echo "    body: $(echo "$R" | head -1 | cut -c1-300)"
R=$(assign none); check su_restore_none 200 "$(echo "$R" | tail -1)"; [ "$(echo "$R" | tail -1)" = 200 ] || echo "    body: $(echo "$R" | head -1 | cut -c1-300)"
echo "test_token_issue_and_use / revocation"
tokid() { curl -s $SU $BASE/api/auth/tokens | $PY -c "import sys,json; d=[t for t in json.load(sys.stdin)['tokens'] if t['label']=='$1' and not t['revoked_at']]; print(sorted(d,key=lambda t:t['created_at'])[-1]['id'])"; }
sleep 1
RESP=$(curl -s $SU -X POST -H 'content-type: application/json' -d '{"label":"reg-agent","app_scope":{"example_full_app":"webUI-med"}}' $BASE/api/auth/tokens)
JWT=$(echo "$RESP" | $PY -c "import sys,json; print(json.load(sys.stdin)['token'])")
[ -n "$JWT" ] && echo "  ok raw JWT returned once (len ${#JWT})" || { echo "  FAIL no JWT"; FAILS=$((FAILS+1)); }
check bearer_apps 200 "$(code -H "Authorization: Bearer $JWT" $BASE/api/apps)"
check no_token 401 "$(code $BASE/api/apps)"
TID=$(tokid reg-agent)
check revoke 200 "$(code $SU -X DELETE $BASE/api/auth/tokens/$TID)"
check revoked_token 401 "$(code -H "Authorization: Bearer $JWT" $BASE/api/apps)"
echo "test_token_scope_enforced (non-superuser tokens from fixture)"
check in_scope_app 200 "$(code -H "Authorization: Bearer $TOK_OK" $BASE/api/apps/example-full-app)"
check other_scope_app_forbidden 403 "$(code -H "Authorization: Bearer $TOK_OTHER" $BASE/api/apps/example-full-app)"
echo "test_mcp_requires_bearer + gateway proxies tool call"
sleep 1
RESP=$(curl -s $SU -X POST -H 'content-type: application/json' -d '{"label":"reg-mcp","app_scope":{"example_full_app":"webUI-med"}}' $BASE/api/auth/tokens)
JWT2=$(echo "$RESP" | $PY -c "import sys,json; print(json.load(sys.stdin)['token'])")
TID2=$(tokid reg-mcp)
MCP_JWT="$JWT2" $PY - <<'PY' || FAILS=$((FAILS+1))
import asyncio, os, json
from mcp import ClientSession
from mcp.client.sse import sse_client
async def main():
    async with sse_client("http://localhost:8000/mcp/sse", headers={"Authorization": "Bearer " + os.environ["MCP_JWT"]}) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            assert any(t.startswith("example_full_app.") for t in tools), tools
            assert not any(t.startswith("example_companion.") for t in tools), tools
            print("  ok scoped tools:", [t for t in tools if t.startswith("example_full_app.")])
            res = await s.call_tool("example_full_app.get_status", {})
            txt = res.content[0].text
            d = json.loads(txt); assert d["health"] == "good" and d["db_connected"] is True, d
            print("  ok get_status health=good db_connected=true")
asyncio.run(main())
PY
curl -s $SU -o /dev/null -X DELETE $BASE/api/auth/tokens/$TID2
echo "test_api_never_returns_db_password (T-0021; superuser, App id example-full-app)"
DBPW=$($PY -c "import json; print(json.load(open('data/.db_credentials/latarnia_example_full_app_role.json'))['password'])" 2>/dev/null)
if [ -z "$DBPW" ]; then echo "  FAIL role password not readable"; FAILS=$((FAILS+1)); else
  for u in $BASE/api/apps $BASE/api/apps/example-full-app; do
    R=$(curl -s -w '\n%{http_code}' $SU "$u"); check "$(basename "$u")_status" 200 "$(echo "$R" | tail -1)"
    case "$R" in *"$DBPW"*) echo "  FAIL $u body contains the role password"; FAILS=$((FAILS+1));; *) echo "  ok   $u body has no role password";; esac
  done
  check single_app_db_keys "True|False|True" "$(curl -s $SU $BASE/api/apps/example-full-app | $PY -c "import sys,json; d=json.load(sys.stdin); d=d.get('app',d); i=d['database_info']; print('|'.join(str(x) for x in (all(k in i for k in ('database_name','role_name','applied_migrations')), 'connection_url' in i, bool(i['applied_migrations']))))")"
  check list_db_keys "True|False" "$(echo "$APPS" | $PY -c "import sys,json; i=[a for a in json.load(sys.stdin)['apps'] if a['name']=='example_full_app'][0]['database_info']; print(all(k in i for k in ('database_name','role_name','applied_migrations')), 'connection_url' in i, sep='|')")"
fi
echo "test_token_scope_capped_by_owner_role (T-0025; t20-none has none, t20-low has webUI-low on example_full_app)"
ntok() { curl -s $1 $BASE/api/auth/tokens | $PY -c "import sys,json; print(len(json.load(sys.stdin)['tokens']))"; }
mint() { curl -s -w '\n%{http_code}' $1 -X POST -H 'content-type: application/json' -d "{\"label\":\"reg-cap\",\"app_scope\":$2}" $BASE/api/auth/tokens; }
revoke_reg_cap() { for id in $(curl -s $1 $BASE/api/auth/tokens | $PY -c "import sys,json; print(' '.join(t['id'] for t in json.load(sys.stdin)['tokens'] if t['label']=='reg-cap' and not t['revoked_at']))"); do curl -s -o /dev/null $1 -X DELETE $BASE/api/auth/tokens/$id; done; }
N0=$(ntok "$NONE"); R=$(mint "$NONE" '{"example_full_app":"webUI-full"}')
check none_webui_full 403 "$(echo "$R" | tail -1)"
echo "$R" | head -1 | grep -q "example_full_app" && echo "$R" | head -1 | grep -q "'none'" && echo "  ok   403 names App and role: $(echo "$R" | head -1)" || { echo "  FAIL 403 detail: $(echo "$R" | head -1)"; FAILS=$((FAILS+1)); }
check none_no_token_created "$N0" "$(ntok "$NONE")"
check low_webui_low 200 "$(mint "$LOW" '{"example_full_app":"webUI-low"}' | tail -1)"
N1=$(ntok "$LOW")
check low_webui_med 403 "$(mint "$LOW" '{"example_full_app":"webUI-med"}' | tail -1)"
check low_webui_full 403 "$(mint "$LOW" '{"example_full_app":"webUI-full"}' | tail -1)"
check low_mixed_one_over 403 "$(mint "$LOW" '{"example_full_app":"webUI-low","example_companion":"webUI-full"}' | tail -1)"
check low_no_token_created "$N1" "$(ntok "$LOW")"
check su_any_scope 200 "$(mint "$SU" '{"example_full_app":"webUI-full","example_companion":"full"}' | tail -1)"
revoke_reg_cap "$LOW"; revoke_reg_cap "$SU"
echo "test_live_none_role_blocked_at_ingress / test_live_role_reaches_app_webui (T-0023, through Caddy)"
# Changes a user's role on the target, so it returns before any change unless the
# target says it is tst or dev and INGRESS_USER is a readable non-superuser.
ingress_checks() {
  ISU="-b latarnia_session=${INGRESS_SU:-$S_SU}"; IUSER="-b latarnia_session=${INGRESS_USER:-$S_NONE}"
  # The target's own ENV (the platform resolves it on its host; /api/secrets reports it).
  IENV=$(curl -s $ISU $INGRESS/api/secrets | $PY -c "import sys,json; print(json.load(sys.stdin)['env'])" 2>/dev/null)
  case "$IENV" in
    tst|dev) echo "  ok   target env is $IENV" ;;
    *) echo "  FAIL target env is '${IENV:-unreadable}', not tst/dev — refused, no role changed"; FAILS=$((FAILS+1)); return ;;
  esac
  ME=$(curl -s $IUSER $INGRESS/api/auth/roles)
  read -r IUNAME ISSU IORIG <<<"$(echo "$ME" | $PY -c "import sys,json; d=json.load(sys.stdin); print(d['username'], d['is_superuser'], d['roles'].get('example_full_app','none'))" 2>/dev/null)"
  if [ "$ISSU" != False ] || [ -z "$IUNAME" ] || [ -z "$IORIG" ]; then
    echo "  FAIL INGRESS_USER is a superuser or its /api/auth/roles is unreadable (is_superuser=${ISSU:-?}) — refused, no role changed"; FAILS=$((FAILS+1)); return
  fi
  echo "  ok   INGRESS_USER $IUNAME is not a superuser (role on example_full_app: $IORIG)"
  IUID=$(curl -s $ISU $INGRESS/api/auth/users | $PY -c "import sys,json; d=json.load(sys.stdin); d=d if isinstance(d,list) else d['users']; print([u['id'] for u in d if u['username']=='$IUNAME'][0])" 2>/dev/null)
  [ -n "$IUID" ] || { echo "  FAIL user id of $IUNAME not found — refused, no role changed"; FAILS=$((FAILS+1)); return; }
  iassign() { code $ISU -X POST -H 'content-type: application/json' -d "{\"role\":\"$1\",\"user_id\":\"$IUID\"}" $INGRESS/api/auth/roles/example_full_app; }
  check ingress_assign_none 200 "$(iassign none)"
  check none_blocked_at_ingress 403 "$(code $IUSER $INGRESS/apps/example_full_app/)"
  check ingress_assign_low 200 "$(iassign webUI-low)"
  R=$(curl -s -w '\n%{http_code}' $IUSER $INGRESS/apps/example_full_app/)
  check low_reaches_webui 200 "$(echo "$R" | tail -1)"
  echo "$R" | grep -q "<strong>webUI-low</strong>" && echo "  ok   App received role webUI-low" || { echo "  FAIL App did not receive role webUI-low"; FAILS=$((FAILS+1)); }
  echo "$R" | grep -q "Add Item" && { echo "  FAIL low sees Add Item"; FAILS=$((FAILS+1)); } || echo "  ok   low: no Add Item"
  # t23-027: a client Connection header naming the identity headers (hop-by-hop, HTTP/1.1 only) must not strip them.
  R=$(curl -s --http1.1 -w '\n%{http_code}' -H 'Connection: close, X-Latarnia-App-Role, X-Latarnia-User, X-Latarnia-Is-Super' $IUSER $INGRESS/apps/example_full_app/)
  check low_connection_header_reaches_webui 200 "$(echo "$R" | tail -1)"
  echo "$R" | grep -q "<strong>webUI-low</strong>" && echo "  ok   Connection header: App still received role webUI-low" || { echo "  FAIL Connection header: App did not receive role webUI-low"; FAILS=$((FAILS+1)); }
  echo "$R" | grep -q "Add Item" && { echo "  FAIL Connection header: low sees Add Item"; FAILS=$((FAILS+1)); } || echo "  ok   Connection header: low: no Add Item"
  check ingress_assign_full 200 "$(iassign webUI-full)"
  R=$(curl -s $IUSER $INGRESS/apps/example_full_app/)
  echo "$R" | grep -q "<strong>webUI-full</strong>" && echo "$R" | grep -q "Add Item" && echo "  ok   webUI-full: role received, Add Item" || { echo "  FAIL webUI-full: role not received or no Add Item"; FAILS=$((FAILS+1)); }
  check ingress_restore_role 200 "$(iassign "$IORIG")"
}
if [ -z "${INGRESS:-}" ]; then
  echo "  SKIP INGRESS not set — ingress checks NOT run (set INGRESS to the Caddy ingress base URL, e.g. tst)"
else
  ingress_checks
fi
[ "$FAILS" = 0 ] && echo "PASS regression_live" && exit 0 || { echo "FAIL regression_live ($FAILS)"; exit 1; }
