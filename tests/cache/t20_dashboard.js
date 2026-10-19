// T-0020 dashboard check. Usage: node t20_dashboard.js <base-url> <session-cookie-value>
// 1) GET /dashboard (session): header has the #hh-api-docs-btn link to /docs.
// 2) Extract the template's real loadApiDocsButtons(), run it over slots built from the real
//    /api/apps list (same condition as renderApps: Service App with assigned_port) against the
//    real /api/docs/apps/{app} responses, and inspect the resulting tile buttons.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const [base, session] = process.argv.slice(2);
const src = fs.readFileSync(path.resolve(__dirname, '..', '..', 'templates/dashboard.html'), 'utf8');

function extract(name) {
  const start = src.indexOf(`async function ${name}(`);
  if (start < 0) throw new Error(`function ${name} not found`);
  const i = src.indexOf('{', src.indexOf(')', start));
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}' && --depth === 0) return src.slice(start, j + 1);
  }
  throw new Error('unbalanced braces');
}

(async () => {
  const hdr = { headers: { cookie: `latarnia_session=${session}` } };
  // 1) header button (served by the server-rendered /dashboard page)
  const htmlResp = await fetch(`${base}/dashboard`, hdr);
  assert.strictEqual(htmlResp.status, 200, `/dashboard status ${htmlResp.status}`);
  const html = await htmlResp.text();
  const m = html.match(/<a[^>]*id="hh-api-docs-btn"[^>]*>([^<]*)<\/a>/);
  assert(m, 'hh-api-docs-btn not found in /dashboard HTML');
  assert(/href="\/docs"/.test(m[0]), `header button does not link to /docs: ${m[0]}`);
  console.log(`  ok   header button: ${m[0]}`);

  // 2) tile buttons
  const apps = (await (await fetch(`${base}/api/apps`, hdr)).json()).apps;
  const targets = apps.filter(a => a.type === 'service' && a.runtime_info?.assigned_port).map(a => a.name);
  console.log(`  service apps with port: ${targets.join(', ')}`);
  const slots = targets.map(n => ({ dataset: { app: n }, innerHTML: '' }));
  const grid = { querySelectorAll: () => slots };
  const ctx = vm.createContext({
    console, encodeURIComponent, Promise,
    esc: (s) => s,
    fetch: (url, opts) => fetch(base + url, { ...hdr, ...(opts || {}) }),
  });
  vm.runInContext(extract('loadApiDocsButtons'), ctx);
  await ctx.loadApiDocsButtons(grid);
  const byApp = Object.fromEntries(slots.map(s => [s.dataset.app, s.innerHTML]));
  for (const [a, h] of Object.entries(byApp)) console.log(`  tile ${a}: ${h || '(no docs buttons)'}`);
  const full = byApp['example_full_app'];
  assert(full, 'example_full_app not rendered');
  assert(/href="\/docs\/apps\/example_full_app\/ui"[^>]*>UI API</.test(full), 'missing UI API button');
  assert(/href="\/docs\/apps\/example_full_app\/rest"[^>]*>REST API</.test(full), 'missing REST API button');
  console.log('  ok   example_full_app tile: "UI API" -> /docs/apps/example_full_app/ui, "REST API" -> /docs/apps/example_full_app/rest');
  const comp = byApp['example_companion'];
  assert(comp !== undefined, 'example_companion not rendered');
  assert(!/REST API/.test(comp) && !/UI API/.test(comp), `companion tile has docs buttons: ${comp}`);
  console.log('  ok   example_companion tile: no UI API / REST API buttons');
  console.log('PASS t20-010');
})().catch(e => { console.log('  FAIL ' + e.message); console.log('FAIL t20-010'); process.exit(1); });
