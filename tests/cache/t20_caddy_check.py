"""Shared Caddyfile analysis for t20-001/002/011. Usage: t20_caddy_check.py <001|002|011>"""
import re, sys, os, tempfile
from pathlib import Path
from types import SimpleNamespace


def parse_blocks(text):
    """Top-level (4-space indent) handle/handle_path blocks -> [(kind, pattern, body_lines)]."""
    blocks, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^    (handle_path|handle) (\S+) \{$", line)
        if m:
            cur = [m.group(1), m.group(2), []]
            continue
        if line == "    }" and cur:
            blocks.append(tuple(cur))
            cur = None
            continue
        if cur is not None:
            cur[2].append(line)
    return blocks


def matches(pattern, path):
    if pattern.endswith("*"):
        return path.startswith(pattern[:-1])
    return path == pattern


def matching(blocks, path):
    return [(k, p, "\n".join(b)) for k, p, b in blocks if matches(p, path)]


def generate(apps_spec):
    sys.path.insert(0, "src")
    os.environ["ENV"] = "dev"
    from latarnia.caddy.manager import CaddyConfigManager
    from latarnia.core.config import ConfigManager

    def mk(name, port, pub):
        return SimpleNamespace(
            name=name, type="service",
            manifest=SimpleNamespace(config=SimpleNamespace(has_web_ui=True, public_routes=pub)),
            runtime_info=SimpleNamespace(assigned_port=port))

    cfg = ConfigManager()
    cfg.load_config()
    cfg.config.process_manager.data_dir = tempfile.mkdtemp() + "/data"
    reg = SimpleNamespace(get_all_apps=lambda: [mk(*a) for a in apps_spec])
    return CaddyConfigManager(cfg, SimpleNamespace(registry=reg)).generate_config()


APPS = [("my_app", 8151, ["/b/"]), ("other_app", 8152, [])]
DOC_PATHS = ["/docs", "/docs/", "/docs/apps/my_app/ui", "/openapi.json",
             "/apps/my_app/docs", "/apps/my_app/docs/x", "/apps/my_app/openapi.json",
             "/apps/other_app/docs", "/apps/other_app/openapi.json"]
FA_HDRS = "copy_headers X-Latarnia-User X-Latarnia-App-Role X-Latarnia-Is-Super"


def main():
    mode = sys.argv[1]
    texts = {"in-process generate_config()": generate(APPS)}
    disk = Path("caddy/latarnia.caddyfile")
    if disk.exists():
        texts["on-disk caddy/latarnia.caddyfile"] = disk.read_text()
    bad = 0
    for label, text in texts.items():
        blocks = parse_blocks(text)
        print(f"[{label}] blocks: " + ", ".join(f"{k} {p}" for k, p, _ in blocks))
        paths = list(DOC_PATHS)
        if "on-disk" in label:
            paths += ["/apps/example_full_app/docs", "/apps/example_full_app/docs/x",
                      "/apps/example_full_app/openapi.json", "/apps/latarnik/docs",
                      "/apps/latarnik/openapi.json"]
        if mode == "001":
            for p in paths:
                ms = matching(blocks, p)
                if not ms:
                    print(f"  FAIL {p}: no block"); bad += 1; continue
                unauth = [(k, pat) for k, pat, body in ms if "forward_auth" not in body]
                if unauth:
                    print(f"  FAIL {p}: served without forward_auth by {unauth}"); bad += 1
                else:
                    print(f"  ok   {p}: matched by {[pat for _, pat, _ in ms]}, all with forward_auth")
            for k, pat, body in blocks:
                if re.search(r"docs|openapi", pat):
                    print(f"  FAIL docs-specific block present: {k} {pat}"); bad += 1
        elif mode == "002":
            for p in ["/docs", "/openapi.json"]:
                ms = matching(blocks, p)
                pats = [pat for _, pat, _ in ms]
                if pats != ["/*"]:
                    print(f"  FAIL {p}: matched blocks {pats}, expected only /*"); bad += 1; continue
                body = ms[0][2]
                ok = ("forward_auth" in body and "@auth_denied status 401" in body
                      and "redir * /auth/login?next={http.request.orig_uri} 302" in body
                      and "reverse_proxy localhost:8000" in body)
                print(f"  {'ok  ' if ok else 'FAIL'} {p}: only block '/*', forward_auth + 401->/auth/login redirect")
                bad += 0 if ok else 1
        elif mode == "011":
            names = ["example_full_app"] if "on-disk" in label else ["my_app", "other_app"]
            for name in names:
                p = f"/apps/{name}/docs/x"
                ms = matching(blocks, p)
                pats = [(k, pat) for k, pat, _ in ms]
                prot = [m for m in ms if m[0] == "handle_path" and m[1] == f"/apps/{name}/*"]
                ok = bool(prot) and all("forward_auth" in b for _, _, b in ms)
                if prot:
                    ok = ok and FA_HDRS in prot[0][2] and "reverse_proxy localhost:" in prot[0][2]
                print(f"  {'ok  ' if ok else 'FAIL'} {p}: matched {pats}; session-gated, identity headers copied")
                bad += 0 if ok else 1
    sys.exit(1 if bad else 0)


main()
