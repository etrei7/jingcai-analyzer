"""本地自检脚本：推送前跑一遍，避免语法错误/损坏字符/前端括号失衡上线。

检查项：
  1. 所有 *.py 语法（py_compile）
  2. 所有 *.py 是否含损坏字符（U+FFFD 或私用区），常见于错误的编码转换
  3. 所有 *.py 顶层是否有重名函数/类（后定义会静默覆盖先定义，极难发现）
  4. templates/index.html 最后一个 <script> 的圆括号/花括号/反引号是否平衡

用法：
  python tools/selfcheck.py
退出码：0=通过，1=有问题
"""
import ast
import io
import os
import re
import sys
import glob
import py_compile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = 0


def check_py_syntax(files):
    global FAIL
    for p in files:
        try:
            py_compile.compile(p, doraise=True)
        except Exception as e:
            FAIL += 1
            print('[SYNTAX] %s: %s' % (os.path.basename(p), e))
    print('[1/4] Python 语法: %d 个文件' % len(files))


def check_bad_chars(files):
    global FAIL
    for p in files:
        s = io.open(p, encoding='utf-8', errors='replace').read()
        bad = [i + 1 for i, l in enumerate(s.split('\n'))
               if ('\ufffd' in l) or any(0xE000 <= ord(ch) <= 0xF8FF for ch in l)]
        if bad:
            FAIL += 1
            print('[BADCHAR] %s 行 %s' % (os.path.basename(p), bad))
    print('[2/4] 损坏字符检查: %d 个文件' % len(files))


def check_duplicate_defs(files):
    """同文件顶层重名函数/类：Python 不报错，后者直接覆盖前者。

    真实事故：analysis.py 曾有两个 _kelly，新增的「凯利指数」覆盖了
    「凯利仓位」，仓位返回值由 0~0.25 变成可为负/None，测试与前端口径一起坏掉。
    """
    global FAIL
    for p in files:
        try:
            tree = ast.parse(io.open(p, encoding='utf-8').read(), filename=p)
        except SyntaxError:
            continue  # 语法错误已在第 1 项报告，这里跳过
        seen = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                seen.setdefault(node.name, []).append(node.lineno)
        dup = {k: v for k, v in seen.items() if len(v) > 1}
        if dup:
            FAIL += 1
            detail = ', '.join('%s@%s' % (k, '/'.join(str(x) for x in v)) for k, v in dup.items())
            print('[DUP] %s 顶层重名（后者覆盖前者）: %s' % (os.path.basename(p), detail))
    print('[3/4] 顶层重名检查: %d 个文件' % len(files))


def check_frontend():
    global FAIL
    h = os.path.join(ROOT, 'templates', 'index.html')
    if not os.path.exists(h):
        print('[4/4] templates/index.html 未找到，跳过')
        return
    c = io.open(h, encoding='utf-8').read()
    blocks = re.findall(r'<script>([\s\S]*?)</script>', c)
    js = blocks[-1] if blocks else ''
    op, cl = js.count('('), js.count(')')
    ob, cb = js.count('{'), js.count('}')
    bt = js.count('`')
    ok = (op == cl) and (ob == cb) and (bt % 2 == 0)
    if not ok:
        FAIL += 1
        print('[JS] index.html 不平衡: 圆括号 %d/%d 花括号 %d/%d 反引号 %d' % (op, cl, ob, cb, bt))
    else:
        print('[4/4] index.html JS 平衡: 圆括号 %d/%d 花括号 %d/%d 反引号 %d' % (op, cl, ob, cb, bt))


def main():
    files = sorted(glob.glob(os.path.join(ROOT, '*.py')))
    check_py_syntax(files)
    check_bad_chars(files)
    check_duplicate_defs(files)
    check_frontend()
    print('RESULT:', 'FAIL' if FAIL else 'ALL OK')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
