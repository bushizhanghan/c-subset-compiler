# -*- coding: utf-8 -*-
"""csub 版本号管理：单一来源 + 按更新幅度自动递增。

规则（用户定）：
  补丁      -> +0.0.1    修 bug、补函数表、文案
  小更新    -> +0.1.0    新增功能/支持新语法
  大更新    -> +1.0.0    架构变化、后端重写

版本号唯一来源是仓库根目录的 VERSION 文件（纯文本，如 "v2.1.1"）。
build_exe.py 和 web.py 都从这里读，不再各自硬编码。

用法：
  python tools/bump_version.py            查看当前版本
  python tools/bump_version.py patch      补丁      v2.1.1 -> v2.1.2
  python tools/bump_version.py minor      小更新    v2.1.1 -> v2.2.0
  python tools/bump_version.py major      大更新    v2.1.1 -> v3.0.0
  python tools/bump_version.py set 2.5.0  直接指定
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(ROOT, "VERSION")

# 需要跟着版本号走的位置：(文件, 匹配正则, 替换模板)
# 替换模板里的 {ver} 是带 v 前缀的完整版本号，{bare} 是不带 v 的
TARGETS = [
    (os.path.join(ROOT, "build_exe.py"),
     r'VERSION = "v[0-9]+\.[0-9]+\.[0-9]+"',
     'VERSION = "{ver}"'),
    (os.path.join(ROOT, "web.py"),
     r'C SUBSET COMPILER · v[0-9]+\.[0-9]+\.[0-9]+',
     'C SUBSET COMPILER · {ver}'),
    (os.path.join(ROOT, "使用说明.md"),
     r'# csub 使用说明（v[0-9]+\.[0-9]+\.[0-9]+）',
     '# csub 使用说明（{ver}）'),
]


def read_version():
    if not os.path.exists(VERSION_FILE):
        return None
    v = io.open(VERSION_FILE, encoding="utf-8").read().strip()
    return v or None


def write_version(v):
    io.open(VERSION_FILE, "w", encoding="utf-8", newline="\n").write(v + "\n")


def parse(v):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", v or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def bump(v, kind):
    t = parse(v)
    if t is None:
        raise SystemExit("VERSION 文件里的版本号无法解析: %r" % v)
    a, b, c = t
    if kind == "patch":
        c += 1
    elif kind == "minor":
        b, c = b + 1, 0
    elif kind == "major":
        a, b, c = a + 1, 0, 0
    else:
        raise SystemExit("未知的递增类型: %s（可用 patch/minor/major）" % kind)
    return "v%d.%d.%d" % (a, b, c)


def sync_files(v):
    """把版本号同步进 build_exe.py / web.py / 使用说明.md。"""
    changed = []
    for path, pat, tmpl in TARGETS:
        if not os.path.exists(path):
            continue
        src = io.open(path, encoding="utf-8").read()
        new, n = re.subn(pat, tmpl.format(ver=v, bare=v[1:]), src)
        if n == 0:
            print("  ! %s 没找到版本号位置（可能已是最新或被改过）"
                  % os.path.basename(path))
            continue
        if new != src:
            io.open(path, "w", encoding="utf-8", newline="\n").write(new)
            changed.append(os.path.basename(path))
    return changed


def main(argv):
    cur = read_version()
    if not argv:
        print("当前版本: %s" % (cur or "（VERSION 文件缺失）"))
        print()
        print("递增规则：补丁 +0.0.1 / 小更新 +0.1.0 / 大更新 +1.0.0")
        print("用法: python tools/bump_version.py [patch|minor|major|set X.Y.Z]")
        return 0

    cmd = argv[0]
    if cmd == "set":
        if len(argv) < 2:
            raise SystemExit("set 需要版本号，如 set 2.5.0")
        new = argv[1]
        if parse(new) is None:
            raise SystemExit("版本号格式不对: %s（要 X.Y.Z）" % new)
        if not new.startswith("v"):
            new = "v" + new
    elif cmd in ("patch", "minor", "major"):
        new = bump(cur, cmd)
    else:
        raise SystemExit("未知命令: %s" % cmd)

    if cur == new:
        print("版本号没变（%s）" % new)
        return 0

    write_version(new)
    print("版本号: %s -> %s" % (cur, new))
    ch = sync_files(new)
    if ch:
        print("已同步: %s" % ", ".join(ch))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
