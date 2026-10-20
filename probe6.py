# -*- coding: utf-8 -*-
"""探针：typedef 名走到 sizeof 里时，Ident 的 name 是什么。"""
import sys
sys.path.insert(0, "src")

from parser import parse, UnOp, Ident, FuncDef

SRC = (
    "typedef struct node { int num; struct node *next; } Node, *LinkList;\n"
    "int main(){\n"
    "  Node n; LinkList h; int a[5]; char c; double d;\n"
    "  return sizeof(Node) + sizeof(LinkList) + sizeof(n) + sizeof(h);\n"
    "}\n"
)
prog = parse(SRC)
fn = [d for d in prog.decls if isinstance(d, FuncDef)][0]
ret = fn.body.stmts[-1]
# BinOp +
top = ret.value
print("top:", top.op, type(top.left).__name__, type(top.right).__name__)
def dump(e, d=0):
    pad = "  " * d
    if isinstance(e, UnOp) and e.op == "sizeof":
        op = e.operand
        print(f"{pad}sizeof(operand={type(op).__name__}"
              f"{' name=' + repr(op.name) if isinstance(op, Ident) else ''})")
    elif isinstance(e, BinOp):
        print(f"{pad}BinOp({e.op})")
        dump(e.left, d + 1)
        dump(e.right, d + 1)
    else:
        print(f"{pad}{type(e).__name__}")
dump(top)
