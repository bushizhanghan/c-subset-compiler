# -*- coding: utf-8 -*-
"""探针：真实文件里的 sizeof 究竟能不能静态定值。"""
import sys
sys.path.insert(0, "src")

from preprocess import preprocess
from parser import parse, VarDecl, DeclGroup, Type
from semantic import analyze

for path in ("examples/real_nmakehlp.c", "examples/real_fortranobject.c"):
    raw = open(path, encoding="utf-8").read()
    pr = preprocess(raw)
    print("=" * 70)
    print(path, "includes:", pr.includes)
    prog = parse(pr.text)
    # 收集所有变量声明的维度信息
    def walk(decls, depth=0):
        for d in decls:
            if isinstance(d, VarDecl):
                if d.vtype.dims or d.vtype.array is not None:
                    print(f"  VAR {d.name}: base={d.vtype.base!r} "
                          f"ptr={d.vtype.ptr} dims={d.vtype.dims}")
            elif isinstance(d, DeclGroup):
                walk(d.decls, depth + 1)
    walk(prog.decls)
    print("  macros:", sorted(pr.macros.items())[:10] if pr.macros else "无")
    diags = analyze(prog)
    print("  diags:", [(d.level, d.code, d.line, d.msg) for d in diags])
