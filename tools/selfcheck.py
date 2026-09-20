"""本地自检脚本：推送前跑一遍，避免语法错误/损坏字符/前端括号失衡上线。

检查项：
  1. 所有 *.py 语法（py_compile）
  2. 所有 *.py 是否含损坏字符（U+FFFD 或私用区），常见于错误的编码转换
  3. templates/index.html 最后一个 <script> 的圆括号/花括号/反引号是否平衡

用法：
  python tools/selfcheck.py
退出码：0=通过，1=有问题
"""
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
    print('[1/3] Python 语法: %d 个文件' % len(files))


def check_bad_chars(files):
    global FAIL
    for p in files:
        s = io.open(p, encoding='utf-8', errors='replace').read()
        bad = [i + 1 for i, l in enumerate(s.split('\n'))
               if ('\ufffd' in l) or any(0xE000 <= ord(ch) <= 0xF8FF for ch in l)]
        if bad:
            FAIL += 1
            print('[BADCHAR] %s 行 %s' % (os.path.basename(p), bad))
    print('[2/3] 损坏字符检查: %d 个文件' % len(files))


def check_frontend():
    global FAIL
    h = os.path.join(ROOT, 'templates', 'index.html')
    if not os.path.exists(h):
        print('[3/3] templates/index.html 未找到，跳过')
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
        print('[3/3] index.html JS 平衡: 圆括号 %d/%d 花括号 %d/%d 反引号 %d' % (op, cl, ob, cb, bt))


def main():
    files = sorted(glob.glob(os.path.join(ROOT, '*.py')))
    check_py_syntax(files)
    check_bad_chars(files)
    check_frontend()
    print('RESULT:', 'FAIL' if FAIL else 'ALL OK')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
