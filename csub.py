#!/usr/bin/env python3
"""csub —— C 子集编译器与初学者错误诊断器（命令行入口）。

用法:
  python csub.py check  file.c          只做检查，输出诊断
  python csub.py tokens file.c          输出词法 token
  python csub.py ast    file.c          输出 AST
  python csub.py report file.c          输出带源码上下文的诊断报告
  python csub.py ir     file.c          输出三地址码 IR
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from lexer import tokenize, LexError
from parser import parse, ParseError
from semantic import analyze, Diag
from preprocess import preprocess


def read_src(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def report_preprocess(pr, verbose=True):
    """把预处理的取舍明确告诉用户，不能静默丢弃。"""
    notes = []
    if pr.includes:
        notes.append(f"忽略了 {len(pr.includes)} 个头文件包含："
                     + ", ".join(pr.includes))
    obj = [n for n, m in pr.macros.items() if not m.is_function_like]
    fun = [n for n, m in pr.macros.items() if m.is_function_like]
    if obj:
        notes.append(f"展开了 {len(obj)} 个常量宏：" + ", ".join(obj[:8])
                     + ("..." if len(obj) > 8 else ""))
    if fun:
        notes.append(f"展开了 {len(fun)} 个函数宏：" + ", ".join(fun[:8])
                     + ("..." if len(fun) > 8 else ""))
    for ln, why in pr.unsupported:
        notes.append(f"第 {ln} 行未处理：{why}")
    if verbose and notes:
        for n in notes:
            print(f"[预处理] {n}", file=sys.stderr)
    return notes


def prepare(path, verbose=True):
    """读取 + 预处理，返回 (源码, 预处理结果) 或 (None, 错误诊断)。"""
    try:
        raw = read_src(path)
    except OSError as e:
        return None, None, [Diag("error", "E0", 0, 0, f"无法读取文件: {e}")]
    pr = preprocess(raw)
    if verbose:
        report_preprocess(pr)
    return pr.text, pr, None


def remap_diags(diags, pr):
    """把诊断的行号从"预处理后"映射回"原始文件"。

    不做这一步，学生会看到指向错误位置的提示——因为 #include
    和条件编译处理后会删行，行号整体前移。
    """
    if pr is None:
        return diags
    for d in diags:
        d.line = pr.to_orig_line(d.line)
    return diags


def collect_diags(path, verbose=True):
    """跑完整前端，返回 (diags, stage, pr)。

    先做预处理：任何真实 C 文件都有 #include，
    不做这一步整个工具在真实代码上就是不可用的。
    """
    src, pr, err = prepare(path, verbose)
    if err:
        return err, "io", None

    try:
        tokenize(src)
    except LexError as e:
        return remap_diags(
            [Diag("error", "LEX", e.line, e.col, e.msg)], pr), "lex", pr
    try:
        prog = parse(src)
    except ParseError as e:
        return remap_diags(
            [Diag("error", "SYN", e.line, e.col, e.msg)], pr), "parse", pr
    # 把预处理记录的头文件名传下去，W25（漏 include）要用
    return remap_diags(analyze(prog, includes=pr.includes),
                       pr), "semantic", pr


def cmd_tokens(path):
    src, _pr, err = prepare(path)
    if err:
        print(err[0])
        return 1
    for t in tokenize(src):
        print(t)
    return 0


def cmd_ast(path):
    src, _pr, err = prepare(path)
    if err:
        print(err[0])
        return 1
    prog = parse(src)
    for d in prog.decls:
        print(d)
    return 0


def cmd_ir(path):
    from ir import ir_to_text, generate
    src, _pr, err = prepare(path)
    if err:
        print(err[0])
        return 1
    try:
        prog = parse(src)
    except ParseError as e:
        print(f"[SYN] {e}")
        return 1
    diags = analyze(prog)
    errs = [d for d in diags if d.level == "error"]
    if errs:
        print("存在语义错误，IR 仅供参考：", file=sys.stderr)
        for d in errs:
            print(f"  {d}", file=sys.stderr)
    print(ir_to_text(prog))

    # 附带输出基本块与活跃变量，这是数据流分析的结果
    try:
        r = generate(prog, optimize=True)
        rows = r.get("liveness")
        if rows:
            print()
            print("; ---- 基本块与活跃变量（数据流分析）----")
            print(f"; 共 {r.get('blocks')} 个基本块")
            for row in rows:
                print(f"; BB{row['block']}  "
                      f"live_in={row['live_in']}  live_out={row['live_out']}")
    except Exception as e:
        print(f"; [数据流分析失败] {type(e).__name__}: {e}", file=sys.stderr)
    return 0


def cmd_check(path):
    diags, stage, _pr = collect_diags(path)
    if not diags:
        print("没有发现问题。")
        return 0
    errs = sum(1 for d in diags if d.level == "error")
    for d in sorted(diags, key=lambda x: (x.line, x.col)):
        print(d)
    print(f"\n共 {len(diags)} 条：{errs} 个错误，"
          f"{len(diags) - errs} 个警告。")
    return 1 if errs else 0


def cmd_report(path):
    """诊断 + 源码上下文。这是学生最需要的形式。"""
    diags, stage, _pr = collect_diags(path, verbose=False)
    try:
        from preprocess import split_lines
        lines = split_lines(read_src(path))
    except OSError:
        lines = []

    if not diags:
        print("没有发现问题。")
        return 0

    for d in sorted(diags, key=lambda x: (x.line, x.col)):
        print(f"\n{'=' * 60}")
        print(f"[{d.level.upper()} {d.code}] 第 {d.line} 行: {d.msg}")
        if lines and 1 <= d.line <= len(lines):
            lo = max(1, d.line - 2)
            hi = min(len(lines), d.line + 2)
            for ln in range(lo, hi + 1):
                mark = ">>" if ln == d.line else "  "
                print(f"{mark} {ln:4d} | {lines[ln - 1]}")
                if ln == d.line and d.col > 0:
                    pad = " " * (len(f"{mark} {ln:4d} | ") + d.col - 1)
                    print(f"{pad}^")
        if d.hint:
            print(f"   提示: {d.hint}")

    errs = sum(1 for d in diags if d.level == "error")
    print(f"\n{'=' * 60}")
    print(f"共 {len(diags)} 条：{errs} 个错误，{len(diags) - errs} 个警告。")
    return 1 if errs else 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    args = sys.argv[2:]

    table = {
        "tokens": cmd_tokens,
        "ast": cmd_ast,
        "ir": cmd_ir,
        "check": cmd_check,
        "report": cmd_report,
    }
    if cmd in table and args:
        return table[cmd](args[0])

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
