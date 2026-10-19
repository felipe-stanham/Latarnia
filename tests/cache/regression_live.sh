#!/usr/bin/env bash
# Live (dev server) regression checks for TESTS.md Integration + P-0008 entries. Run from anywhere.
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
check anon_verify 401 "$(code -H 'X-Forwarded-Uri: /apps/example_full_app/' $BASE/auth/verify)"
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
[ "$FAILS" = 0 ] && echo "PASS regression_live" && exit 0 || { echo "FAIL regression_live ($FAILS)"; exit 1; }
