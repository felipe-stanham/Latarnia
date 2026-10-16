// T-0019 verification harness. Usage: node t19_status_reasons.js <t19-001..t19-006|syntax>
// Extracts appDisplayStatus/esc/buildStatusReasons from templates/dashboard.html
// by brace-matching and evaluates them with mock /api/apps-shaped objects.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const cp = require('child_process');
const root = path.resolve(__dirname, '..', '..');
const src = fs.readFileSync(path.join(root, 'templates/dashboard.html'), 'utf8');

function extract(name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`function ${name} not found`);
  const i = src.indexOf('{', start);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}' && --depth === 0) return src.slice(start, j + 1);
  }
  throw new Error('unbalanced braces for ' + name);
}
const code = ['appDisplayStatus', 'esc', 'buildStatusReasons'].map(extract).join('\n');
const ctx = vm.createContext({});
vm.runInContext(code, ctx);
const build = (app) => ctx.buildStatusReasons(app);
const lis = (html) => [...html.matchAll(/<li>(.*?)<\/li>/g)].map(m => m[1]);

const assert = require('assert');
const tests = {
  'syntax': () => {
    const m = src.match(/<script(?![^>]*src)[^>]*>([\s\S]*?)<\/script>/g) || [];
    const scripts = m.map(s => s.replace(/^<script[^>]*>|<\/script>$/g, ''));
    assert(scripts.length >= 1, 'no inline script');
    const body = scripts.join('\n;\n').replace(/\{%[\s\S]*?%\}/g, '').replace(/\{\{[\s\S]*?\}\}/g, '""');
    const tmp = path.join(process.env.TMPDIR || '/tmp', 't19_syntax_check.js');
    fs.writeFileSync(tmp, body);
    try { cp.execFileSync('node', ['--check', tmp], { stdio: 'pipe' }); }
    finally { fs.unlinkSync(tmp); }
    return `script parses (${scripts.length} inline script block(s), ${body.length} chars)`;
  },
  't19-001': () => {
    const h = build({ status: 'running', overall_status: 'yellow', overall_status_detail: 'Disk 91% full', dependencies: [] });
    assert.deepStrictEqual(lis(h), ['Disk 91% full']);
    assert(h.includes('text-warning-emphasis'), 'warning colour');
    return h;
  },
  't19-002': () => {
    const h = build({ status: 'running', overall_status: 'red', overall_status_detail: 'Upstream API down', dependencies: [] });
    assert.deepStrictEqual(lis(h), ['Upstream API down']);
    assert(h.includes('text-danger'), 'error colour');
    return h;
  },
  't19-003': () => {
    const h = build({ status: 'error', overall_status: 'red', overall_status_detail: 'boom',
      runtime_info: { error_message: 'boom' }, dependencies: [] });
    assert.deepStrictEqual(lis(h), ['boom']);
    assert.strictEqual((h.match(/boom/g) || []).length, 1);
    return h;
  },
  't19-004': () => {
    const h = build({ status: 'running', overall_status: 'red', overall_status_detail: 'systemd unit failed',
      dependencies: [{ app: 'db_app', satisfied: false }, { app: 'ok_app', satisfied: true }, { app: 'x_app', satisfied: false }] });
    assert.deepStrictEqual(lis(h), ['Unmet deps: db_app, x_app', 'systemd unit failed']);
    const h2 = build({ status: 'error', overall_status: 'red', overall_status_detail: 'detail',
      runtime_info: { error_message: 'detail' }, database_info: { provisioned: false },
      manifest: { config: { database: true } }, dependencies: [{ app: 'a', satisfied: false }] });
    assert.deepStrictEqual(lis(h2), ['DB provisioning failed', 'Unmet deps: a', 'detail']);
    return h + '\n' + h2;
  },
  't19-005': () => {
    for (const os of ['green', 'grey']) {
      const h = build({ status: os === 'green' ? 'running' : 'stopped', overall_status: os,
        overall_status_detail: os === 'green' ? 'healthy' : 'stopped', dependencies: [{ app: 'a', satisfied: true }],
        database_info: { provisioned: true }, manifest: { config: { database: true } } });
      assert.strictEqual(h, '', `${os} must be empty`);
    }
    assert.strictEqual(build({ status: 'running', dependencies: [] }), '');
    assert.strictEqual(build({ status: 'stopped' }), '');
    return 'green, grey, legacy running/stopped -> ""';
  },
  't19-006': () => {
    const h = build({ status: 'running', overall_status: 'yellow', overall_status_detail: '<b>x</b>', dependencies: [] });
    assert.deepStrictEqual(lis(h), ['&lt;b&gt;x&lt;/b&gt;']);
    assert(!h.includes('<b>'), 'raw <b> present');
    const h2 = build({ status: 'error', overall_status: 'red', runtime_info: { error_message: '<b>x</b>' }, dependencies: [{ app: '<i>', satisfied: false }] });
    assert(!h2.includes('<b>') && !h2.includes('<i>'), 'raw tags in error/deps');
    return h + '\n' + h2;
  },
};
const name = process.argv[2];
if (!tests[name]) { console.error('unknown test', name); process.exit(2); }
try { console.log('OUTPUT:', tests[name]()); console.log(`PASS ${name}`); }
catch (e) { console.error(`FAIL ${name}: ${e.message}`); process.exit(1); }
