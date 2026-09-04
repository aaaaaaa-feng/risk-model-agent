from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# 每层只声明绝对禁止的反向依赖。API、evaluation 和 bootstrap/runtime 属于外层，
# 可以组合内部能力；领域、Worker 和 Provider 必须保持可独立测试。
FORBIDDEN_IMPORTS = {
    "core": {
        "agents",
        "api",
        "evaluation",
        "governance",
        "orchestration",
        "providers",
        "services",
        "tooling",
        "workers",
    },
    "domain": {
        "agents",
        "api",
        "core",
        "evaluation",
        "governance",
        "orchestration",
        "providers",
        "services",
        "tooling",
        "workers",
    },
    "workers": {
        "agents",
        "api",
        "evaluation",
        "orchestration",
        "providers",
        "services",
    },
    "providers": {
        "agents",
        "api",
        "evaluation",
        "orchestration",
        "services",
        "workers",
    },
    "agents": {"api", "evaluation", "orchestration", "services"},
    "services": {"api", "evaluation", "orchestration", "runtime"},
    "governance": {"api", "evaluation", "orchestration", "services"},
    "orchestration": {"api", "evaluation", "runtime"},
}


def _absolute_imports(path: Path, node: ast.ImportFrom) -> list[str]:
    if node.level == 0:
        return [node.module] if node.module else []
    package = list(path.relative_to(ROOT).parent.parts)
    keep = len(package) - (node.level - 1)
    if keep < 1:
        return []
    parts = package[:keep]
    if node.module:
        parts.extend(node.module.split("."))
        return [".".join(parts)]
    return [".".join([*parts, alias.name]) for alias in node.names]


def _app_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = _absolute_imports(path, node)
        for name in names:
            if name.startswith("app."):
                imports.add(name.split(".", 2)[1])
    return imports


def test_backend_layers_do_not_import_outward() -> None:
    violations: list[str] = []
    for layer, forbidden in FORBIDDEN_IMPORTS.items():
        directory = ROOT / "app" / layer
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.py")):
            invalid = sorted(_app_imports(path) & forbidden)
            if invalid:
                violations.append(f"{path.relative_to(ROOT)} -> {', '.join(invalid)}")
    assert not violations, "发现反向依赖：\n" + "\n".join(violations)


def test_legacy_graph_and_governance_implementations_are_removed() -> None:
    legacy_paths = (
        ROOT / "app" / "agents" / "graph.py",
        ROOT / "app" / "evaluation" / "manifest.py",
        ROOT / "app" / "evaluation" / "tracing.py",
        ROOT / "app" / "runtime.py",
    )
    assert not [str(path.relative_to(ROOT)) for path in legacy_paths if path.exists()]
