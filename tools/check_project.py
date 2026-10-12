"""Validate source layout, Python syntax and complexity of changed functions."""
import ast
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
    return {ast.dump(node, include_attributes=False): node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def previous_source(name):
    for candidate in dict.fromkeys([name, name.removeprefix('backend/src/')]):
        result = subprocess.run(['git', 'show', 'HEAD:' + candidate], cwd=ROOT,
                                capture_output=True, text=True, encoding='utf-8')
        if result.returncode == 0:
            return result.stdout
    return ''


def check_python(path, git_available):
    name = path.relative_to(ROOT).as_posix()
    source = path.read_text(encoding='utf-8-sig')
    tree = ast.parse(source, filename=name)
    compile(tree, name, 'exec')
    old = previous_source(name) if git_available else ''
    unchanged = functions(ast.parse(old)) if old else {}
    errors = []
    for fingerprint, function in functions(tree).items():
        if name not in STRICT and (fingerprint in unchanged or not git_available):
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
    print(f'目录与 {count} 个 Python 文件语法通过；改动函数复杂度不超过 15。')
    if not git_available:
        print('无 Git 基线，仅校验关键模块的复杂度；其余文件已校验语法。')


if __name__ == '__main__':
    main()
