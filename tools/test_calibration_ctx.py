# -*- coding: utf-8 -*-
"""回归：缓存构建线程必须有 Flask 应用上下文；校准计算失败结果不得缓存。

真实事故（2026-11-26，线上）：`cache._build_payload_with_timeout()` 在 daemon 线程里执行
`_build_payload()`，而该线程没有 Flask 应用上下文；线程内 `build_report` ->
`calibration.get_calibration` -> `BtBet.query` 抛出
`Working outside of application context`。更糟的是错误结果被写入 TTL 120s 的缓存，
于是线上 `/api/calibration` 连续报错、Edge 差值榜的「同类场次命中率」整列为空。

两道防线（任一缺失都会复发）：
1. 构建线程自己压入 `app.app_context()`；
2. `get_calibration()` 只缓存成功结果，失败在下次调用时重算。

无需数据库数据/网络：只断言"不报上下文错误"与"缓存行为"。
"""
import io
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import app  # noqa: F401  —— 导入即建库/迁移，并提供 app 对象
import cache
import calibration

_ok = 0
_fail = 0


def check(name, cond, detail=''):
    global _ok, _fail
    if cond:
        _ok += 1
        print('  OK  %s' % name)
    else:
        _fail += 1
        print('  FAIL %s%s' % (name, (' :: ' + str(detail)) if detail else ''))


def test_thread_has_context():
    """构建线程里能完成一次真实的校准计算（不再抛上下文错误）。"""
    seen = {}

    def fake_build():
        cal = calibration.get_calibration(force=True)
        seen['error'] = cal.get('error')
        seen['total'] = cal.get('total')
        return {'matches': [], 'stats': {'source': '模拟数据 (测试)'}}

    real_build = cache._build_payload
    cache._build_payload = fake_build
    try:
        payload = cache._build_payload_with_timeout(30)
    finally:
        cache._build_payload = real_build
    check('构建线程返回 payload', payload is not None, payload)
    check('线程内校准无上下文错误', seen.get('error') is None, seen.get('error'))
    check('线程内校准算到 total 字段', 'total' in seen, seen)


def test_error_not_cached():
    """计算失败（error）不能污染缓存：下次调用必须重算。"""
    calls = {'n': 0}

    def bad_compute():
        calls['n'] += 1
        return {'buckets': {}, 'segments': {}, 'total': 0, 'error': 'boom'}

    real_compute = calibration._compute
    real_cache = calibration._cache
    calibration._compute = bad_compute
    calibration._cache = {}
    try:
        a = calibration.get_calibration()
        b = calibration.get_calibration()
    finally:
        calibration._compute = real_compute
        calibration._cache = real_cache
    check('失败结果原样返回', a.get('error') == 'boom' and b.get('error') == 'boom', (a, b))
    check('失败不写缓存（每次都重算）', calls['n'] == 2, calls['n'])


def test_success_cached():
    """成功结果仍然缓存，避免每次请求都全表扫描。"""
    calls = {'n': 0}

    def good_compute():
        calls['n'] += 1
        return {'buckets': {}, 'segments': {}, 'total': 0}

    real_compute = calibration._compute
    real_cache = calibration._cache
    calibration._compute = good_compute
    calibration._cache = {}
    try:
        calibration.get_calibration()
        calibration.get_calibration()
    finally:
        calibration._compute = real_compute
        calibration._cache = real_cache
    check('成功结果命中缓存（只算一次）', calls['n'] == 1, calls['n'])


def main():
    test_thread_has_context()
    test_error_not_cached()
    test_success_cached()
    print('')
    print('%d OK, %d FAILED' % (_ok, _fail))
    print('RESULT: ' + ('ALL OK' if _fail == 0 else 'FAILED'))
    return 0 if _fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
