"""pytest plugin (T-0023 tester): mutate the generated Caddyfile to prove the
real-chain tests in tests/integration/test_caddy_forward_auth.py detect each
bug they target. Selected by T23_MUTATION; loaded with `-p t23_mutation_plugin`
(tests/cache on PYTHONPATH). Does not touch src/ — patches the method at runtime.

Mutations:
  drop_app_header      remove `header_up X-Latarnia-App <name>` (verify not told the App)
  drop_identity_reset  remove the App proxy's identity `header_up ... {http.request.header.*}`
  drop_catchall_strip  remove `header_up -X-Latarnia-App` inside forward_auth (catch-all)
  drop_proxy_app_strip remove `header_up -X-Latarnia-App` inside App reverse_proxy blocks
"""
import os
import re


def _mutate(text: str, mode: str) -> str:
    out, in_fa, depth = [], False, 0
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("forward_auth "):
            in_fa, depth = True, 0
        if in_fa:
            depth += s.count("{") - s.count("}")
        drop = False
        if mode == "drop_app_header":
            drop = re.fullmatch(r"header_up X-Latarnia-App \S+", s) is not None
        elif mode == "drop_identity_reset":
            drop = re.fullmatch(r"header_up X-Latarnia-(User|App-Role|Is-Super) \{http\.request\.header\.[^}]+\}", s) is not None
        elif mode == "drop_catchall_strip":
            drop = in_fa and s == "header_up -X-Latarnia-App"
        elif mode == "drop_proxy_app_strip":
            drop = (not in_fa) and s == "header_up -X-Latarnia-App"
        if not drop:
            out.append(line)
        if in_fa and depth == 0:
            in_fa = False
    return "\n".join(out) + "\n"


def pytest_configure(config):
    mode = os.environ.get("T23_MUTATION")
    if not mode:
        return
    from latarnia.caddy.manager import CaddyConfigManager
    original = CaddyConfigManager.generate_config

    def mutated(self, *a, **kw):
        text = original(self, *a, **kw)
        new = _mutate(text, mode)
        assert new != text, f"mutation {mode} changed nothing"
        return new
    CaddyConfigManager.generate_config = mutated
