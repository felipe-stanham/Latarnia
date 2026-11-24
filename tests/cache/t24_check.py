"""T-0024 acceptance checks (docs/Tasks/T-0024.md), independent of tests/unit.

Usage: ENV=dev PYTHONPATH=src python tests/cache/t24_check.py <001|002|003|004>
  004 also parses <MANIFEST_ROOT>/apps/*/latarnia.json, where MANIFEST_ROOT defaults to the
  repo root; set it to the primary checkout to include the gitignored real apps/.
Exit 0 = PASS, 1 = FAIL, 2 = refused/usage.
"""
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

if os.environ.get("ENV", "dev") == "prod":
    print("ENV=prod refused", file=sys.stderr)
    sys.exit(2)

from pydantic import ValidationError  # noqa: E402

from latarnia.core.config import ConfigManager  # noqa: E402
from latarnia.managers.app_manager import AppManager, AppManifest  # noqa: E402
from latarnia.managers.port_manager import PortManager  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent


def manifest(name):
    return {"name": name, "type": "service", "description": "t24 check",
            "version": "1.0.0", "author": "t24", "main_file": "app.py"}


def t001():
    ok = True
    for name in ["latarnik", "example_full_app", "example_companion", "crm-app", "x", "a1", "a" * 50]:
        try:
            m = AppManifest(**manifest(name))
            assert m.name == name
            print(f"  valid   {name!r}")
        except ValidationError as e:
            ok = False
            print(f"  REJECTED (expected valid) {name!r}: {e}")
    return ok


def t002():
    ok = True
    bad = ["Camera Detection", "App_A", "latarnik ", "a{http.request.uri}", "a}b", "a/b", "../x",
           "a.b", "1app", "-app", "_app", "app\n", "app\r", "ápp", "", "a" * 51]
    for name in bad:
        try:
            AppManifest(**manifest(name))
            ok = False
            print(f"  ACCEPTED (expected ValidationError) {name!r}")
        except ValidationError as e:
            locs = [tuple(err["loc"]) for err in e.errors()]
            if locs == [("name",)]:
                print(f"  rejected {name!r} on 'name' ({e.errors()[0]['type']})")
            else:
                ok = False
                print(f"  rejected {name!r} but errors on {locs} (expected only ('name',))")
    return ok


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def t003():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "config").mkdir()
        apps = base / "apps"
        apps.mkdir()
        paths = {}
        for d, n in [("alpha", "alpha"), ("beta", "beta-app"), ("evil", "bad{x}")]:
            (apps / d).mkdir()
            paths[d] = apps / d / "latarnia.json"
            paths[d].write_text(json.dumps(manifest(n)))
            (apps / d / "app.py").write_text("# main\n")
            (apps / d / "requirements.txt").write_text("")

        cfg = Mock()
        cfg.process_manager.port_range.start = 8100
        cfg.process_manager.port_range.end = 8110
        cm = Mock(spec=ConfigManager)
        cm.config = cfg
        cm.get_data_dir.return_value = base / "config"
        pm = Mock(spec=PortManager)
        pm.allocate_port.return_value = 8100
        with patch("pathlib.Path.cwd", return_value=base):
            am = AppManager(cm, pm)
        cap = _Capture()
        am.logger.addHandler(cap)
        am.logger.setLevel(logging.DEBUG)

        count = am.discover_apps()
        names = sorted(a.name for a in am.registry.get_all_apps())
        print(f"  discover_apps() -> {count}; registered names: {names}")
        errors = [r.getMessage() for r in cap.records if r.levelno == logging.ERROR]
        for msg in errors:
            print("  ERROR log: " + msg.replace("\n", " | "))
        hit = [m for m in errors if "Invalid manifest" in m and str(paths["evil"]) in m]
        checks = {
            "count == 2": count == 2,
            "registered == [alpha, beta-app]": names == ["alpha", "beta-app"],
            "bad{x} not registered": am.registry.get_app_by_name("bad{x}") is None,
            "ERROR line with 'Invalid manifest' + manifest path": len(hit) == 1,
        }
        for k, v in checks.items():
            print(f"  {'ok  ' if v else 'FAIL'} {k}")
        return all(checks.values())


def t004():
    roots = {REPO}
    extra = os.environ.get("MANIFEST_ROOT")
    if extra:
        roots.add(Path(extra).resolve())
    files = sorted({p for r in roots for sub in ("apps", "examples")
                    for p in (r / sub).glob("*/latarnia.json")})
    if not files:
        print("  no manifests found")
        return False
    ok = True
    for f in files:
        try:
            m = AppManifest(**json.loads(f.read_text()))
            print(f"  ok   {f} (name={m.name!r})")
        except (ValidationError, ValueError) as e:
            ok = False
            print(f"  FAIL {f}: {e}")
    if extra and not any(str(f).startswith(str(Path(extra).resolve() / "apps")) for f in files):
        print("  FAIL no manifests found under MANIFEST_ROOT/apps")
        ok = False
    return ok


if __name__ == "__main__":
    fn = {"001": t001, "002": t002, "003": t003, "004": t004}.get(sys.argv[1] if len(sys.argv) > 1 else "")
    if not fn:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    res = fn()
    print(f"{'PASS' if res else 'FAIL'} t24-{sys.argv[1]}")
    sys.exit(0 if res else 1)
