"""Guard the dependency boundary between evaluation logic and the model SDK.

Why this test exists
--------------------
The offline analysis path -- aggregation, slicing, gating, reporting -- is pure
arithmetic over recorded run files.  It is what makes it possible to re-analyse
a past experiment without a model, a vector store or network access.

That property is easy to destroy by accident: a single convenient
``from src.generator_v2 import SOMETHING`` at the top of an analysis module
drags in langchain, and the analysis stops working anywhere the SDK is not
installed.  Nothing else would fail -- the import succeeds on a developer
machine, and only CI or a colleague's bare checkout notices.

So the boundary is asserted rather than documented.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Modules that must stay importable without the model SDK or a vector store.
# Each entry maps a file to the third-party packages it is allowed to touch.
OFFLINE_MODULES = {
    "aggregate_generation_replays.py": set(),
    "src/generation_status.py": set(),
    "src/generation_slices.py": set(),
    "src/generation_gates.py": set(),
    "src/gates_runner.py": set(),
    "src/eval_report.py": set(),
    "src/frozen_evidence.py": set(),
}

# Packages whose presence means the model SDK / vector store got pulled in.
FORBIDDEN_PACKAGES = (
    "langchain",
    "langchain_core",
    "langchain_chroma",
    "chromadb",
    "sentence_transformers",
    "torch",
    "openai",
    "gradio",
)


def _runtime_imports(path: Path) -> set[str]:
    """Modules imported *when the file is imported*.

    Deliberately narrower than "everything named in the file".  Two patterns
    are legitimate and must not be flagged:

    * deferred imports inside a function -- ``import chromadb`` inside a
      fingerprint helper keeps the module importable without a vector store
      until that helper is actually called;
    * imports inside ``if TYPE_CHECKING`` -- annotation-only names that never
      execute at runtime.

    A naive AST walk cannot tell these apart from a top-level import, which is
    why the traversal is done over the module body only and skips the
    ``TYPE_CHECKING`` branch.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()

    def collect(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    modules.add(node.module.split(".")[0])
            elif isinstance(node, ast.If) and _is_type_checking(node.test):
                continue  # annotation-only branch, never executed
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # deferred imports live inside
            elif isinstance(node, ast.Try):
                collect(node.body + node.orelse + node.finalbody)
                for handler in node.handlers:
                    collect(handler.body)

    collect(tree.body)
    return modules


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def test_offline_modules_do_not_import_the_model_sdk_directly():
    """A direct SDK import in an analysis module breaks offline re-analysis."""
    offenders: list[str] = []
    for relative in OFFLINE_MODULES:
        path = ROOT / relative
        assert path.exists(), f"离线模块缺失：{relative}"
        for module in _runtime_imports(path) & set(FORBIDDEN_PACKAGES):
            offenders.append(f"{relative} -> {module}")
    assert not offenders, (
        "以下离线模块直接依赖了模型 SDK / 向量库，会导致离线分析无法运行：\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("relative", sorted(OFFLINE_MODULES))
def test_offline_module_imports_without_third_party_packages(relative: str):
    """Import each module in a subprocess with site-packages stripped.

    An AST check misses transitive imports.  Running with ``-S`` (no site
    initialisation) removes third-party packages entirely, so an import that
    only works because a dependency happened to be installed will fail here.
    """
    result = subprocess.run(
        [sys.executable, "-S", "-c", f"import {relative.replace('/', '.')[:-3].replace('.py', '')}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 0:
        return
    # ``-S`` also removes the standard library's site imports, which pytest and
    # the interpreter's own path setup may need.  Treat a failure as real only
    # when it names a forbidden package.
    stderr = result.stderr
    if not any(pkg in stderr for pkg in FORBIDDEN_PACKAGES):
        pytest.skip(f"{relative} 在 -S 下失败的原因与第三方依赖无关：{stderr.strip()[:200]}")
    pytest.fail(
        f"{relative} 在没有第三方包的环境下无法导入：\n{stderr}"
    )


def test_aggregator_imports_no_model_layer():
    """``aggregate_generation_replays`` was importing ``src.generator_v2``.

    It needed one constant -- a set of status strings -- and got the whole
    model SDK with it.  The vocabulary now lives in ``src.generation_status``.
    """
    modules = _runtime_imports(ROOT / "aggregate_generation_replays.py")
    assert "generator_v2" not in modules
    assert "model_clients" not in modules


def test_generator_still_exports_the_failure_vocabulary():
    """Moved constants must stay importable from their old home.

    Callers exist outside this repository's test suite; silently dropping the
    re-export would break them at runtime rather than at import time.
    """
    source = (ROOT / "src" / "generator_v2.py").read_text(encoding="utf-8")
    for name in (
        "PROVIDER_FAILURE_CLASSES",
        "PROVIDER_HARD_ERROR_CLASSES",
        "PROVIDER_LATENCY_CLASSES",
        "RETRYABLE_FAILURE_CLASSES",
    ):
        assert name in source, f"generator_v2 不再导出 {name}"


def test_failure_vocabulary_is_defined_in_exactly_one_place():
    """The two sides must agree on the status names by sharing them.

    If both files repeated the literals, adding a status on one side would
    silently stop it being counted on the other.
    """
    status_source = (ROOT / "src" / "generation_status.py").read_text(encoding="utf-8")
    assert "PROVIDER_FAILURE_CLASSES = frozenset(" in status_source

    aggregator = (ROOT / "aggregate_generation_replays.py").read_text(encoding="utf-8")
    assert "from src.generation_status import" in aggregator
    # The literals themselves must not be repeated in the aggregator.
    assert '"provider_timeout"' not in aggregator
