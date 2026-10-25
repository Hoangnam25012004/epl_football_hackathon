"""One-way imports (design section 2.3; Day 5 adds `speech`, docs/day5-design.md 2.5; Day 6
adds `mcp_server`, docs/day6-design.md 2.2):
domain <- stats <- state <- engine <- agents <- llm <- orchestration <- speech <- mcp_server
<- api <- cli.

Agents never import Microsoft Agent Framework, FastAPI or HTTP clients (principle L6); MAF lives
in orchestration and llm.maf_backend only; FastAPI in api only; HTTP and the Azure SDK of the
voice in the speech modules that talk to Azure; the MCP SDK in mcp_server.server only. Day 7:
HTTP also in api.keepalive, which pings the app's own public URL (docs/day7-design.md 2.2).
"""

import ast
from pathlib import Path

import pundit

LAYERS = [
    "domain", "stats", "state", "engine", "agents", "llm", "orchestration", "speech",
    "mcp_server", "api", "cli",
]  # fmt: skip
PACKAGE = Path(pundit.__file__).parent
EXTERNAL = {
    "agent_framework": {"orchestration", "llm.maf_backend"},
    "openai": {"llm.maf_backend"},
    "httpx": {
        "llm.maf_backend",
        "speech.azure_tts",
        "speech.azure_stt",
        "speech.auth",
        "api.keepalive",
    },
    "azure": {"llm.maf_backend", "speech.auth", "speech.durable"},
    "fastapi": {"api"},
    "starlette": {"api", "mcp_server.server"},  # the SDK's HTTP transport is Starlette
    "uvicorn": {"cli"},
    "mcp": {"mcp_server.server"},
}


def modules() -> list[tuple[str, Path]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).with_suffix("")
        found.append((".".join(relative.parts), path))
    return found


def imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def layer_of(module: str) -> str | None:
    head = module.split(".")[0]
    return head if head in LAYERS else None


def test_layers_only_import_downwards() -> None:
    problems = []
    for module, path in modules():
        layer = layer_of(module)
        if layer is None:
            continue
        rank = LAYERS.index(layer)
        for name in imports(path):
            if not name.startswith("pundit."):
                continue
            target = layer_of(name.removeprefix("pundit."))
            if target is not None and LAYERS.index(target) > rank:
                problems.append(f"{module} imports {name}")
    assert problems == []


def test_frameworks_stay_in_their_layer() -> None:
    problems = []
    for module, path in modules():
        for name in imports(path):
            root = name.split(".")[0]
            allowed = EXTERNAL.get(root)
            if allowed is None:
                continue
            if not any(module == a or module.startswith(a + ".") for a in allowed):
                problems.append(f"{module} imports {name}")
    assert problems == []


def test_every_module_is_in_a_layer() -> None:
    unknown = [m for m, _ in modules() if layer_of(m) is None and m not in ("__init__", "__main__")]
    assert unknown == []
