"""``python -m autogenerator.plugin_host`` — показать найденные плагины.

``--json`` печатает манифест целиком (его читает ``discover_manifest_isolated``).
"""

from __future__ import annotations

import argparse
import sys

from autogenerator.contracts import PluginStatus

from .registry import PluginRegistry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autogenerator.plugin_host")
    ap.add_argument("--json", action="store_true", help="напечатать манифест в JSON")
    args = ap.parse_args(argv)
    manifest = PluginRegistry.discover().manifest()
    if args.json:
        sys.stdout.write(manifest.model_dump_json(indent=2))
        return 0
    print(f"API плагинов: {manifest.api_version}")
    for p in manifest.plugins:
        mark = "ok " if p.status == PluginStatus.OK else "ОШИБКА"
        print(f"  {mark} {p.kind:<12} {p.name:<22} {p.distribution or ''} {p.version or ''}")
        if p.error:
            print("      " + p.error.strip().splitlines()[-1])
    return 1 if manifest.broken() else 0


if __name__ == "__main__":
    sys.exit(main())
