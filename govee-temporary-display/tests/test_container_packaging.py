import ast
import unittest
from pathlib import Path

class ContainerPackagingTests(unittest.TestCase):
    def test_local_imports_are_packaged(self):
        root=Path(__file__).resolve().parents[1]
        docker=(root/"Dockerfile").read_text()
        modules={p.stem for p in root.glob("*.py")}
        for source in root.glob("*.py"):
            for node in ast.walk(ast.parse(source.read_text())):
                names=[a.name.split(".")[0] for a in node.names] if isinstance(node,ast.Import) else [node.module.split(".")[0]] if isinstance(node,ast.ImportFrom) and node.module else []
                for name in set(names)&modules:
                    with self.subTest(source=source.name,module=name):
                        self.assertIn(f"COPY {name}.py /{name}.py",docker)
