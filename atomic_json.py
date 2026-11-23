"""原子写 JSON。

原先几处缓存/历史文件都是 `open(path,'w') + json.dump()` 直接覆盖：一旦进程在写入中途
被 PythonAnywhere 卸载、或被 OOM 杀掉，就会留下半截 JSON，下次读取直接解析失败，
表现为"战绩/赔率突然全空"。这里改为写临时文件 + fsync + os.replace 的原子替换。
"""
import json
import os
import tempfile


def dump(path, data, ensure_ascii=False, indent=None):
    """原子地把 data 写入 path（同目录临时文件 + os.replace）。失败会清理临时文件并抛出。"""
    abs_path = os.path.abspath(path)
    directory = os.path.dirname(abs_path) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.tmp-', suffix='.json', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, abs_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
