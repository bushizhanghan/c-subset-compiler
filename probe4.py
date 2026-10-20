# -*- coding: utf-8 -*-
"""探针：直接遍历 decls 找 sizeof。"""
import sys
sys.path.insert(0, "src")

from preprocess import preprocess
from parser import parse, UnOp, FuncDef, VarDecl, DeclGroup, Block
from ir import IRGen


def walk(node, out, depth=0):
    if depth > 80 or node is None:
        return
    if isinstance(node, UnOp) and node.op == "sizeof":
        out.append(node)
        return
    if isinstance(node, (int, str, bool)) or node is None:
        return
    if isinstance(node, list):
        for x in node:
            walk(x, out, depth + 1)
        return
    d = getattr(node, "__dict__", None)
    if d:
        for k, v in d.items():
            if k in ("line", "col"):
                continue
            walk(v, out, depth + 1)


for path in ("examples/real_nmakehlp.c", "examples/real_fortranobject.c"):
    raw = open(path, encoding="utf-8").read()
    pr = preprocess(raw)
    prog = parse(pr.text)
    print("=" * 70)
    print(path)
    calls = []
    walk(prog, calls)
    print(f"  sizeof 调用点 {len(calls)} 处")
    for c in calls[:40]:
        op = c.operand
        print(f"    line {c.line}: {type(op).__name__} {op!r}"[:110])
