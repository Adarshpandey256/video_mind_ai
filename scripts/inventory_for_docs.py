"""Dump a compact, accurate inventory of the project's Python modules.

Used only to build the project handbook: it reports the module docstring,
top-level imports and every class / function with its line number, signature and
the first line of its docstring.  Nothing is executed or imported.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOTS = ["app.py", "config", "src", "tests", "scripts"]


def first_line(node: ast.AST) -> str:
    doc = ast.get_docstring(node) or ""
    for line in doc.strip().splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({args}){returns}"


def describe(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    print(f"\n##### {path.as_posix()}  ({len(source.splitlines())} lines)")
    module_doc = first_line(tree)
    if module_doc:
        print(f"  MODULE DOC: {module_doc}")
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            print(f"  {node.lineno:>5} IMPORT: {ast.unparse(node)}")
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            print(f"  {node.lineno:>5} class {node.name}  # {first_line(node)}")
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    print(f"    {child.lineno:>5}   {signature(child)}  # {first_line(child)}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            print(f"  {node.lineno:>5} {signature(node)}  # {first_line(node)}")


def main() -> int:
    targets: list[Path] = []
    for root in ROOTS:
        candidate = Path(root)
        if candidate.is_file():
            targets.append(candidate)
        elif candidate.is_dir():
            targets.extend(sorted(candidate.rglob("*.py")))
    for path in targets:
        if "__pycache__" in path.parts:
            continue
        describe(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
