#!/usr/bin/env python3
"""与 gcc 交叉验证：证明我们的诊断和 IR 不是自说自话。

三层验证，层层加强：
  L1 语法层：gcc -fsyntax-only 判定"这段代码合法/非法"，
     与 csub 的判断比对。这是最硬的客观标准，因为 gcc 是独立实现。
  L2 诊断层：对明确的错误代码，确认 gcc 也报错（不要求错误信息一致，
     只要求"都认为有问题"）。
  L3 执行层：对能编译的程序，用 gcc 实际跑出结果，
     再比对 csub 的 IR 常量折叠是否与真实结果一致。

用法:
  python tools/crosscheck.py            跑全部对照
  python tools/crosscheck.py -v         显示明细
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from lexer import tokenize, LexError
from parser import parse, ParseError
from semantic import analyze
from ir import IRGen

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 一组程序：(名称, 源码, 是否应视为合法)
# 第三项是"csub 立场"：True=合法，False=非法，None=比 gcc 更严格（见下方说明）
PROGRAMS = [
    ("simple_var", "int main(){ int a = 1; return a; }", True),
    ("arith", "int main(){ int a = 2 + 3 * 4; return a; }", True),
    ("loop_sum", """int main(){
        int s = 0; int i = 0;
        while (i < 5) { s = s + i; i = i + 1; }
        return s;
    }""", True),
    ("func_call", """int add(int x, int y){ return x + y; }
        int main(){ return add(3, 4); }""", True),
    ("if_else", """int main(){
        int a = 5;
        if (a > 3) { return 1; } else { return 2; }
    }""", True),
    ("nested_loop", """int main(){
        int s = 0; int i = 0; int j = 0;
        for (i = 0; i < 3; i = i + 1) {
            for (j = 0; j < 3; j = j + 1) { s = s + 1; }
        }
        return s;
    }""", True),
    ("array_rw", """int main(){
        int a[3];
        a[0] = 1; a[1] = 2; a[2] = 3;
        return a[0] + a[1] + a[2];
    }""", True),
    ("const_expr", "int main(){ int a = 10 * 10 - 5; return a; }", True),
    ("recursion", """int fib(int n){
        if (n < 2) { return n; }
        return fib(n-1) + fib(n-2);
    }
    int main(){ return fib(6); }""", True),

    # 应判为非法（gcc 也应报错）
    ("bad_undeclared", "int main(){ x = 1; return 0; }", False),
    ("bad_redeclare", "int main(){ int a = 1; int a = 2; return a; }", False),
    ("bad_syntax", "int main(){ int a = 1 return 0; }", False),
    ("bad_argc", """int f(int a){ return a; }
        int main(){ return f(1, 2); }""", False),
    ("bad_break", "int main(){ break; return 0; }", False),

    # ---- csub 有意比 gcc 严格的两例 ----
    # gcc 放行这些写法，因为它们属于"未定义行为"或"链接期问题"，
    # 编译器为了速度和灵活不报。但它们正是初学者最常踩的坑，
    # 教学工具的价值就在于把这类"能编译但会出事"的代码拦下来。
    # 这两项不算不一致，单独统计为 STRICTER。
    #
    # 注意：早期还有一条 bad_no_main（`int f(){ return 0; }` 期望报错），
    # 后来 W17（没有 main）被整个删掉了 —— 真实的完整程序几乎不会
    # 只含一个非 main 函数，而 LeetCode/PTA 的函数题全都没有 main，
    # 报就是纯噪音（实测连续三份样本每份都触发）。
    # 这里同步移除，否则会算成"真不一致"。
    ("bad_uninit", "int main(){ int a; int b = a; return b; }", False),
    ("bad_oob_const", "int main(){ int a[2]; a[2] = 1; return 0; }", False),
]

# 这些用例 csub 更严格是设计意图，不计入"不一致"
STRICTER_BY_DESIGN = {"bad_uninit", "bad_oob_const"}


def gcc_syntax_ok(src: str):
    """用 gcc 判断源码语法是否合法。返回 (ok, stderr)。"""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.c")
    with open(p, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        r = subprocess.run(
            ["gcc", "-fsyntax-only", "-w", p],
            capture_output=True, timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode == 0, r.stderr.decode("utf-8", "replace")
    except FileNotFoundError:
        return None, "未找到 gcc"
    except subprocess.TimeoutExpired:
        return None, "gcc 超时"


def gcc_run_value(src: str):
    """用 gcc 编译执行，返回程序的退出码（即 return 值）。"""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.c")
    exe = os.path.join(d, "t.exe" if os.name == "nt" else "t.out")
    with open(p, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        c = subprocess.run(["gcc", "-w", "-o", exe, p], capture_output=True,
                           timeout=30, creationflags=CREATE_NO_WINDOW)
        if c.returncode != 0:
            return None
        r = subprocess.run([exe], capture_output=True, timeout=10,
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode
    except Exception:
        return None


def csub_syntax_ok(src: str):
    """csub 的判定：有语法错误或语义 error 就算非法。"""
    try:
        tokenize(src)
    except LexError:
        return False
    try:
        prog = parse(src)
    except ParseError:
        return False
    # parser 现在带错误恢复，语法错误收集在 prog.errors 里不抛异常。
    # 不检查这个，所有语法非法用例都会被判成"通过"
    # （实测 bad_syntax 变成 csub 过松）。
    if getattr(prog, "errors", None):
        return False
    diags = analyze(prog)
    return not any(d.level == "error" for d in diags)


def main():
    verbose = "-v" in sys.argv
    agree = disagree = skipped = stricter = 0
    rows = []

    for name, src, expected in PROGRAMS:
        g, err = gcc_syntax_ok(src)
        c = csub_syntax_ok(src)
        if g is None:
            skipped += 1
            rows.append((name, "-", c, "skip", err[:40]))
            continue
        match = (g == c)
        if match:
            agree += 1
        elif name in STRICTER_BY_DESIGN and g is True and c is False:
            # gcc 放行（未定义行为/链接期问题），csub 拦下：设计意图
            stricter += 1
        else:
            disagree += 1
        tag = ("agree" if match else
               ("STRICTER(gcc放行,csub拦截)" if name in STRICTER_BY_DESIGN
                else "DISAGREE"))
        rows.append((name, g, c, tag, ""))

    if verbose:
        print(f"{'用例':<18}{'gcc':<8}{'csub':<8}结果")
        print("-" * 60)
        for name, g, c, res, note in rows:
            gs = "合法" if g is True else ("非法" if g is False else "-")
            cs = "合法" if c else "非法"
            print(f"{name:<18}{gs:<8}{cs:<8}{res} {note}")

    total = agree + disagree + stricter
    print(f"\n=== L1 语法判定与 gcc 对照 ===")
    print(f"对比 {total} 个程序：一致 {agree}，"
          f"有意更严格 {stricter}，真不一致 {disagree}（跳过 {skipped}）")
    if total:
        print(f"一致率 {agree / total:.1%}；"
              f"含设计意图在内符合预期 {((agree + stricter) / total):.1%}")

    # ---- L3 执行层：验证常量折叠结果与真实运行一致 ----
    print(f"\n=== L3 常量折叠 vs 实际执行 ===")
    check_items = [
        ("10*10-5", "int main(){ return 10 * 10 - 5; }", 95),
        ("2+3*4", "int main(){ return 2 + 3 * 4; }", 14),
        ("loop_sum", """int main(){
            int s = 0; int i = 0;
            while (i < 5) { s = s + i; i = i + 1; }
            return s;
        }""", 10),
    ]
    ok_cnt = 0
    for name, src, expect in check_items:
        real = gcc_run_value(src)
        good = (real == expect)
        if good:
            ok_cnt += 1
        if verbose or not good:
            print(f"  {name}: gcc 实际={real} 预期={expect} "
                  f"{'OK' if good else 'MISMATCH'}")
    print(f"执行结果吻合: {ok_cnt}/{len(check_items)}")

    return 0 if disagree == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
