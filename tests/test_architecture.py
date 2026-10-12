"""Architecture rule from ADR-04 / G2, checked on the source code.

Only Reservation Management changes Reservation lifecycle state:
- `Reservation.state` is assigned only in domain.py;
- the transition rules of domain.py are called only from service.py;
- the GUI depends on the service, never on the repository;
- domain.py has no I/O and depends on no other parking module.
"""
import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "parking"
TRANSITION_RULES = {"create_reservation", "confirm", "approve", "reject", "cancel", "expire_if_due"}


def imports(tree: ast.AST) -> set[str]:
    """Imported modules plus `module.name` for every `from module import name`."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def violations(src: Path = SRC) -> list[str]:
    found = []
    for path in sorted(src.glob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        name = path.name
        imported = imports(tree)

        for node in ast.walk(tree):
            targets = node.targets if isinstance(node, ast.Assign) else (
                [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else [])
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr == "state" and name != "domain.py":
                    found.append(f"{name}:{node.lineno} assigns .state")

        if name != "service.py":
            for rule in sorted(TRANSITION_RULES):
                if f"parking.domain.{rule}" in imported:
                    found.append(f"{name} imports transition rule domain.{rule}")
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and node.attr in TRANSITION_RULES
                        and isinstance(node.value, ast.Name) and node.value.id == "domain"):
                    found.append(f"{name}:{node.lineno} calls domain.{node.attr}")

        if name == "gui.py":
            for module in ("parking.repository", "sqlite3"):
                if any(i == module or i.startswith(module + ".") for i in imported):
                    found.append(f"gui.py imports {module}")

        if name == "domain.py":
            for i in sorted(imported):
                if i.split(".")[0] in ("parking", "sqlite3", "tkinter", "logging"):
                    found.append(f"domain.py imports {i}")
    return found


class ArchitectureRule(unittest.TestCase):
    def test_only_reservation_management_changes_lifecycle_state(self):
        self.assertEqual(violations(), [])


if __name__ == "__main__":
    unittest.main()
