# -*- coding: utf-8 -*-
"""探针：真实文件里所有 sizeof 调用点的静态可判定性。"""
import sys
sys.path.insert(0, "src")

from preprocess import preprocess
from parser import parse, UnOp, FuncDef, VarDecl, DeclGroup
from ir import IRGen


def find_sizeofs(node, out, depth=0):
    if depth > 60:
        return
    for attr in ("left", "right", "operand", "value", "init", "expr",
                 "cond", "idx", "base", "target", "then", "els", "stmt",
                 "args"):
        v = getattr(node, attr, None)
        if isinstance(v, (UnOp,)) or hasattr(v, "__dict__"):
            find_sizeofs(v, out, depth + 1)
        if isinstance(v, list):
            for x in v:
                find_sizeofs(x, out, depth + 1)
    if isinstance(node, UnOp) and node.op == "sizeof":
        out.append(node)


for path in ("examples/real_nmakehlp.c", "examples/real_fortranobject.c"):
    raw = open(path, encoding="utf-8").read()
    pr = preprocess(raw)
    prog = parse(pr.text)
    print("=" * 70)
    print(path)
    calls = []
    find_sizeofs(prog, calls)
    print(f"  sizeof 调用点 {len(calls)} 处：")
    for c in calls:
        op = c.operand
        desc = repr(op)
        if isinstance(op, UnOp) and op.op == "cast":
            desc = "cast:" + repr(op.operand)
        print(f"    line {c.line}: operand={desc[:70]}")
    # 检查 IR 生成是否安全
    try:
        gen = IRGen()
        code = gen.gen_program(prog)
        print(f"  IR 生成 OK，{len(code)} 条")
    except Exception as e:
        print(f"  IR 生成 EXC {type(e).__name__}: {e}")
