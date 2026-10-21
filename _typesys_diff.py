"""一次性脚本（临时）：对比改前/改后的诊断快照，逐文件列出新增/消失的诊断。

不属于产品代码，交付前会删掉。
"""
import json
import sys

a = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open(sys.argv[2], encoding="utf-8"))


def key(d):
    return (d[0], d[1], d[3], d[4])


for f in sorted(set(a) | set(b)):
    old = {key(d) for d in a.get(f, [])}
    new = {key(d) for d in b.get(f, [])}
    added = [d for d in b.get(f, []) if key(d) not in old]
    gone = [d for d in a.get(f, []) if key(d) not in new]
    if added or gone:
        print(f"--- {f}")
        for d in added:
            print(f"   + {d[0]}:{d[1]} {d[2]}:{d[3]} {d[4][:78]}")
        for d in gone:
            print(f"   - {d[0]}:{d[1]} {d[2]}:{d[3]} {d[4][:78]}")
