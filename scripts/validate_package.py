#!/usr/bin/env python3
"""Validate that the Plain national editorial package is complete/importable."""
from __future__ import annotations

import importlib
import json
import pkgutil
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    errors: list[str] = []
    try:
        package = importlib.import_module("plain_engine")
    except Exception as exc:  # pragma: no cover
        print(f"Package validation failed: cannot import plain_engine: {exc}", file=sys.stderr)
        return 1

    discovered = sorted(
        module.name
        for module in pkgutil.walk_packages(package.__path__, prefix=f"{package.__name__}.")
    )
    for module_name in discovered:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            errors.append(f"{module_name}: {type(exc).__name__}: {exc}")

    for export_name in getattr(package, "__all__", []):
        if not hasattr(package, export_name):
            errors.append(f"plain_engine.__all__ export is missing: {export_name}")

    required_files = [
        repo_root / "plain_engine" / "editorial_rules.py",
        repo_root / "plain_engine" / "national_relevance.py",
        repo_root / "plain_engine" / "story_registry.py",
        repo_root / "plain_engine" / "semantic_publication_gate.py",
        repo_root / "plain_engine" / "ranking_recommendations.py",
        repo_root / "scripts" / "editorial_runtime.py",
        repo_root / "scripts" / "generate.py",
    ]
    for path in required_files:
        if not path.is_file():
            errors.append(f"required file is missing: {path.relative_to(repo_root)}")

    policy_path = repo_root / "plain_engine" / "editorial_policy.yaml"
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        if policy.get("sources", {}).get("plainnews.app", {}).get("trust") != 100:
            errors.append("editorial policy is missing Plain's first-party source profile")
    except Exception as exc:
        errors.append(f"editorial policy could not be parsed: {exc}")

    if errors:
        print("Package validation failed:", file=sys.stderr)
        for error in errors:
            print(f" - {error}", file=sys.stderr)
        return 1

    print(
        "Package validation passed: "
        f"{len(discovered)} modules imported and "
        f"{len(getattr(package, '__all__', []))} public exports verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
