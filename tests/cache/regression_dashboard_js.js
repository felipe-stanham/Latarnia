// Regression: dashboard JS function entries from TESTS.md (Dashboard Updates / Web Dashboard).
// Extracts real functions from templates/dashboard.html and evaluates them in a vm.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const src = fs.readFileSync(path.resolve(__dirname, '..', '..', 'templates/dashboard.html'), 'utf8');
function extract(name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`function ${name} not found`);
  const i = src.indexOf('{', start); let d = 0;
  for (let j = i; j < src.length; j++) { if (src[j] === '{') d++; else if (src[j] === '}' && --d === 0) return src.slice(start, j + 1); }
}
const ctx = vm.createContext({});
vm.runInContext(['esc', 'appDisplayStatus', 'buildCapabilityBadges', 'buildStatusReasons'].map(extract).join('\n'), ctx);
const T = {
  test_dashboard_capability_badges_mcp: () => { const h = ctx.buildCapabilityBadges({mcp_info:{enabled:true,healthy:true,registered_tools:['search','add']}}); assert(h.includes('MCP: 2 tools') && h.includes('bg-info'), h); },
  test_dashboard_capability_badges_db: () => { const h = ctx.buildCapabilityBadges({database_info:{provisioned:true,applied_migrations:['001','002','003']}}); assert(h.includes('DB (3 migrations)') && h.includes('bg-success'), h); },
  test_dashboard_capability_badges_streams: () => { const h = ctx.buildCapabilityBadges({stream_info:{publish_streams:['events'],subscribe_streams:['commands','data']}}); assert(h.includes('Streams: 1 pub / 2 sub'), h); },
  test_dashboard_capability_badges_legacy: () => { assert.strictEqual(ctx.buildCapabilityBadges({}), ''); },
  // TESTS.md names buildErrorAlerts; T-0019 renamed it to buildStatusReasons (same behaviour).
  test_dashboard_error_unmet_deps: () => { const h = ctx.buildStatusReasons({dependencies:[{app:'kb',satisfied:false}]}); assert(h.includes('Unmet deps: kb'), h); },
  test_dashboard_web_ui_button: () => { assert(src.includes('${hasWebUI ? `<a class="btn btn-sm btn-primary" href="/apps/${app.name}/" target="_blank">Web UI</a>`'), 'Web UI anchor template missing'); assert(src.includes("app.manifest?.config?.has_web_ui")); },
};
let fails = 0;
for (const [n, f] of Object.entries(T)) { try { f(); console.log('PASS', n); } catch (e) { fails++; console.log('FAIL', n, String(e.message).slice(0, 200)); } }
process.exit(fails ? 1 : 0);
