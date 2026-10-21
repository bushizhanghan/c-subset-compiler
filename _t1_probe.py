"""Scratch probe for t1 (integer overflow check). Not a deliverable."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

import preprocess as PP
import lexer as L
import parser as P

Num, BinOp, UnOp = P.Num, P.BinOp, P.UnOp
VarDecl, DeclGroup, Type = P.VarDecl, P.DeclGroup, P.Type
Ident, Assign, Str, Index, Member = P.Ident, P.Assign, P.Str, P.Index, P.Member
Call, Ternary, InitList, Return = P.Call, P.Ternary, P.InitList, P.Return
If, While, For, DoWhile = P.If, P.While, P.For, P.DoWhile
Switch, Case, Block, ExprStmt = P.Switch, P.Case, P.Block, P.ExprStmt
Break, Continue = P.Break, P.Continue
tokenize, LexError, parse, ParseError = (L.tokenize, L.LexError,
                                         P.parse, P.ParseError)

INT_MIN, INT_MAX = -2147483648, 2147483647


def fold(n, depth=0):
    if depth > 40:
        return None
    if isinstance(n, Num) and not n.is_float:
        return int(n.value)
    if isinstance(n, UnOp) and n.op in ("-", "+"):
        v = fold(n.operand, depth + 1)
        if v is None:
            return None
        return -v if n.op == "-" else v
    if isinstance(n, BinOp) and n.op in ("+", "-", "*", "/", "%",
                                         "<<", ">>", "&", "|", "^"):
        a = fold(n.left, depth + 1)
        b = fold(n.right, depth + 1)
        if a is None or b is None:
            return None
        try:
            return {"+": lambda: a + b, "-": lambda: a - b,
                    "*": lambda: a * b, "/": lambda: int(a / b) if b else None,
                    "%": lambda: a % b if b else None,
                    "<<": lambda: a << b if 0 <= b < 64 else None,
                    ">>": lambda: a >> b if 0 <= b < 64 else None,
                    "&": lambda: a & b, "|": lambda: a | b,
                    "^": lambda: a ^ b}[n.op]()
        except Exception:
            return None
    return None


def walk(n, fn, depth=0):
    if n is None or depth > 60:
        return
    fn(n)
    for attr in ("left", "right", "operand", "cond", "then", "els",
                 "base", "idx", "value", "target", "expr", "init", "body"):
        c = getattr(n, attr, None)
        if c is None:
            continue
        if isinstance(c, list):
            for x in c:
                walk(x, fn, depth + 1)
        else:
            walk(c, fn, depth + 1)
    if isinstance(n, Block):
        for s in n.stmts:
            walk(s, fn, depth + 1)
    if isinstance(n, DeclGroup):
        for vd in n.decls:
            walk(vd, fn, depth + 1)
    if isinstance(n, Case):
        if n.stmt is not None:
            walk(n.stmt, fn, depth + 1)


def analyze_file(path):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        pr = PP.preprocess(src)
        src = pr.text if hasattr(pr, "text") else str(pr)
    except Exception as e:
        return [("PRE", path, repr(e))]
    try:
        tokenize(src)
    except LexError as e:
        return [("LEX", path, str(e))]
    try:
        prog = parse(src)
    except ParseError as e:
        return [("SYN", path, str(e))]
    out = []

    def on_node(n):
        if isinstance(n, Num) and not n.is_float:
            v = int(n.value)
            if v < INT_MIN or v > INT_MAX:
                out.append(("LIT_OOR", n.line, n.col, v))
        if isinstance(n, BinOp) and n.op in ("+", "-", "*", "<<", ">>",
                                             "&", "|", "^"):
            v = fold(n)
            if v is not None and (v < INT_MIN or v > INT_MAX):
                out.append(("FOLD_OOR", n.line, n.col, n.op, v,
                            repr_src(n)))
        if isinstance(n, UnOp) and n.op == "-":
            v = fold(n)
            if v is not None and (v < INT_MIN or v > INT_MAX):
                out.append(("UNEG_OOR", n.line, n.col, v))

    walk(prog, on_node)
    return out


def repr_src(n, depth=0):
    if depth > 6:
        return "..."
    if isinstance(n, Num):
        return str(n.value)
    if isinstance(n, Ident):
        return n.name
    if isinstance(n, UnOp):
        return "-" + repr_src(n.operand, depth + 1)
    if isinstance(n, BinOp):
        return f"({repr_src(n.left, depth+1)} {n.op} {repr_src(n.right, depth+1)})"
    if isinstance(n, Call):
        return f"{n.func.name}(...)"
    return type(n).__name__


TARGETS = ["examples/real_nmakehlp.c", "examples/real_fortranobject.c",
           "examples/hw_josephus.c", "examples/mutation_bank.c",
           "examples/lab_optime.c", "examples/lab_optbench.c",
           "examples/lab_optbench2.c", "examples/lab_optimize.c",
           "examples/lab_madd.c", "examples/stress1.c",
           "examples/stress2.c", "examples/ir1.c", "examples/ir_arr.c",
           "examples/ir_cfg.c", "examples/bad1.c", "examples/bad2.c"]

for t in TARGETS:
    p = os.path.join(ROOT, t)
    if not os.path.exists(p):
        continue
    res = analyze_file(p)
    if res:
        print(f"--- {t}")
        for r in res:
            print("   ", r)

# err_* corpus: which would now gain a diagnostic?
print("\n=== err_* corpus ===")
for f in sorted(os.listdir(os.path.join(ROOT, "examples"))):
    if f.startswith("err_") and f.endswith(".c"):
        res = analyze_file(os.path.join(ROOT, "examples", f))
        if res:
            print(f"--- {f}")
            for r in res:
                print("   ", r)
