"""Validate source layout, Python syntax and complexity of changed functions."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STRICT = {'tools/check_project.py', 'tools/build_delivery.py',
          'backend/src/webui/server.py', 'backend/src/webui/wsgi.py',
          'backend/src/runner/paths.py'}


class Complexity(ast.NodeVisitor):
    def __init__(self):
        self.value = 1

    def visit_FunctionDef(self, node):
        # Nested functions are checked separately; exclude their branches from the parent.
        pass

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node):
        self.value += 1
        self.generic_visit(node)

    visit_For = visit_If
    visit_AsyncFor = visit_If
    visit_While = visit_If
    visit_IfExp = visit_If
    visit_ExceptHandler = visit_If

    def visit_BoolOp(self, node):
        self.value += len(node.values) - 1
        self.generic_visit(node)

    def visit_comprehension(self, node):
        self.value += 1 + len(node.ifs)
        self.generic_visit(node)

    def visit_Match(self, node):
        self.value += max(0, len(node.cases) - 1)
        self.generic_visit(node)


def functions(tree):
    # Python 3.12 adds an empty type_params field; keep baseline fingerprints portable.
    return {ast.dump(node, include_attributes=False).replace(', type_params=[]', ''): node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def check_python(path, git_available):
    name = path.relative_to(ROOT).as_posix()
    source = path.read_text(encoding='utf-8-sig')
    tree = ast.parse(source, filename=name)
    compile(tree, name, 'exec')
    baseline = json.loads((ROOT / 'tools/quality/python-complexity-baseline.json').read_text())
    allowed = set(baseline['functions'].get(name, []))
    errors = []
    for fingerprint, function in functions(tree).items():
        digest = hashlib.sha256(fingerprint.encode()).hexdigest()
        if name not in STRICT and digest in allowed:
            continue
        counter = Complexity()
        for statement in function.body:
            counter.visit(statement)
        if counter.value > 15:
            errors.append(f'{name}:{function.lineno} {function.name} 复杂度 {counter.value}，上限 15')
    return errors


def check_layout():
    required = ['frontend/package.json', 'frontend/package-lock.json', 'frontend/src/main.tsx',
                'deployment/单服务部署.md', 'backend/pyproject.toml',
                'backend/requirements.txt', 'backend/Dockerfile', 'backend/src/webui/server.py',
                'backend/src/runner/paths.py', 'scripts/project.mjs']
    obsolete = ['webui', 'runner', 'reviewers', 'suites', 'scoring', 'ui.py',
                'cloud.py', 'local.py', 'mcr.py', 'ccrab.py', 'Dockerfile',
                'frontend/Dockerfile', 'frontend/server']
    errors = ['缺少文件：' + name for name in required if not (ROOT / name).is_file()]
    errors.extend('请删除旧入口：' + name for name in obsolete if (ROOT / name).exists())
    return errors


def main():
    git_available = subprocess.run(['git', 'rev-parse', '--is-inside-work-tree'],
                                   cwd=ROOT, capture_output=True).returncode == 0
    errors = check_layout()
    count = 0
    for folder in ['backend/src', 'tools']:
        for path in (ROOT / folder).rglob('*.py'):
            if '__pycache__' in path.parts:
                continue
            errors.extend(check_python(path, git_available))
            count += 1
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'目录与 {count} 个 Python 文件语法通过；除已冻结历史基线外，函数复杂度不超过 15。')
    if not git_available:
        print('无 Git 目录，使用冻结历史基线检查复杂度，不跳过新增函数。')


if __name__ == '__main__':
    main()
