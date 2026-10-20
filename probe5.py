# -*- coding: utf-8 -*-
"""探针：sizeof(类型) 在 AST 里到底长什么样（struct 定义放顶层）。"""
import sys
sys.path.insert(0, "src")

from parser import parse, UnOp, Ident, FuncDef

CASES = [
    "sizeof(int)", "sizeof(char)", "sizeof(long)", "sizeof(short)",
    "sizeof(float)", "sizeof(double)", "sizeof(void)",
    "sizeof(int *)", "sizeof(char *)", "sizeof(int **)",
    "sizeof(unsigned char)", "sizeof(unsigned long)", "sizeof(long long)",
    "sizeof(size_t)", "sizeof(struct P)",
    "sizeof(a)", "sizeof(a[0])", "sizeof(a[1][2])", "sizeof(*p)",
    "sizeof(&a)", "sizeof(x.y)", "sizeof(p->next)", "sizeof(-a)",
    "sizeof(a + b)", "sizeof(1)", 'sizeof("abc")', "sizeof('a')",
    "sizeof(Node)",
]

for c in CASES:
    src = (
        "struct P { int x; };\n"
        "typedef struct P Node;\n"
        "int main(){\n"
        "  int a[3][4]; int *p; char *s; struct P x;\n"
        f"  return {c};\n"
        "}\n"
    )
    try:
        prog = parse(src)
        fn = [d for d in prog.decls if isinstance(d, FuncDef)][0]
        ret = fn.body.stmts[-1]
        op = ret.value.operand
        if isinstance(op, Ident):
            desc = f"Ident(name={op.name!r})"
        else:
            desc = f"{type(op).__name__}({op!r})"[:110]
        print(f"  {c:24s} -> {desc}")
    except Exception as e:
        print(f"  {c:24s} -> EXC {type(e).__name__}: {e}")
