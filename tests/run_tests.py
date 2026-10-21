#!/usr/bin/env python3
"""测试套件：用带标注的用例集验证诊断器的准确性。

这个文件的用途不只是"跑通"，而是产出可写进报告的数字：
  召回率 = 检出的真实问题数 / 应检出总数
  误报率 = 对正确代码报出的问题数 / 正确代码数

每个用例是一个 (源码, 期望检出的错误码集合)。
期望集合为空表示这段代码是合法的，不该报 error。

用法:
  python tests/run_tests.py            跑全部
  python tests/run_tests.py -v         显示每条明细
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from lexer import tokenize, LexError
from parser import parse, ParseError
from semantic import analyze


# ---------------- 用例集 ----------------
# 期望：应被检出的 error 级错误码集合。
# 只比对 error，warning 属提示性，不计入召回。

CASES = [
    # ---- 合法代码：不该报 error（用于算误报率）----
    ("ok_basic", """
int main() {
    int a = 1;
    return a;
}
""", set()),

    ("ok_loop", """
int main() {
    int s = 0;
    int i;
    for (i = 0; i < 10; i = i + 1) {
        s = s + i;
    }
    return s;
}
""", set()),

    ("ok_func", """
int add(int x, int y) { return x + y; }
int main() {
    int r = add(2, 3);
    return r;
}
""", set()),

    ("ok_array", """
int main() {
    int arr[5];
    arr[0] = 1;
    arr[4] = 2;
    return arr[0];
}
""", set()),

    ("ok_if_else", """
int main() {
    int a = 1;
    if (a > 0) { return 1; } else { return 0; }
}
""", set()),

    ("ok_while", """
int main() {
    int i = 0;
    while (i < 5) { i = i + 1; }
    return i;
}
""", set()),

    ("ok_nested", """
int main() {
    int a = 1;
    { int b = 2; a = a + b; }
    return a;
}
""", set()),

    ("ok_ptr", """
int main() {
    int a = 1;
    int *p;
    p = &a;
    return 0;
}
""", set()),

    # ---- 应有 error 的代码（用于算召回）----
    ("err_undeclared", """
int main() {
    x = 1;
    return 0;
}
""", {"E1"}),

    ("err_redeclare", """
int main() {
    int a = 1;
    int a = 2;
    return a;
}
""", {"E3"}),

    ("err_uninit", """
int main() {
    int a;
    int b = a;
    return b;
}
""", {"E2"}),

    ("err_argc_low", """
int f(int a, int b) { return a + b; }
int main() { return f(1); }
""", {"E4"}),

    ("err_argc_high", """
int f(int a) { return a; }
int main() { return f(1, 2); }
""", {"E4"}),

    ("err_no_func", """
int main() {
    return notdefined(1);
}
""", {"E5"}),

    ("err_div_zero", """
int main() {
    int a = 5;
    return a / 0;
}
""", {"E12"}),

    ("err_oob", """
int main() {
    int arr[3];
    arr[3] = 1;
    return 0;
}
""", {"E13"}),

    ("err_oob_neg", """
int main() {
    int arr[3];
    arr[-1] = 1;
    return 0;
}
""", {"E13"}),

    ("err_break_outside", """
int main() {
    break;
    return 0;
}
""", {"E11"}),

    # 没有 main：单个函数片段报 W17 提示，不是 error。
    # 改成提示级是因为作业里的函数片段（stu_02 实测）会被 E10
    # 挡住，看不到后面真正的错误。
    ("err_no_main", """
int f() { return 0; }
""", {"W17"}),

    # ---- 语法/词法错误 ----
    ("err_syntax_semi", """
int main() {
    int a = 1
    return 0;
}
""", {"SYN"}),

    ("err_syntax_brace", """
int main() {
    if (1) {
    return 0;
""", {"SYN"}),

    ("err_lex_char", """
int main() {
    int a = 1 @ 2;
    return a;
}
""", {"LEX"}),

    ("err_unclosed_string", """
int main() {
    char *s = "abc;
    return 0;
}
""", {"LEX"}),
]

try:
    from cases_extra import EXTRA_CASES
    CASES += EXTRA_CASES
except ImportError:
    print("[warn] 未找到 cases_extra.py，仅运行基础用例集")


def run_one(src):
    """返回 (actual_error_codes, all_codes, diags)。

    actual 只算 error —— 误报率的定义是"合法代码被报成错误"，
    警告不算误报（W 系列本来就是"提示你注意"）。
    all    错误 + 警告全算 —— 用于期望码里含警告的用例
    （如 W17：没有 main 报提示而不是 error）。
    """
    try:
        tokenize(src)
    except LexError:
        return {"LEX"}, {"LEX"}, []
    try:
        prog = parse(src)
    except ParseError:
        return {"SYN"}, {"SYN"}, []
    diags = analyze(prog)
    errs = {d.code for d in diags if d.level == "error"}
    alls = {d.code for d in diags}
    return errs, alls, diags


def main():
    verbose = "-v" in sys.argv

    tp = fn = fp = tn = 0
    rows = []

    for name, src, expected in CASES:
        actual_err, actual_all, _ = run_one(src)
        # 期望里全是 E/SYN/L 开头 → 用 error 口径比对
        # 期望里含 W → 用全量口径比对（警告不进误报统计）
        if expected and any(c.startswith("W") for c in expected):
            actual = actual_all
        else:
            actual = actual_err
        ok = actual == expected
        if expected:
            if actual & expected:
                tp += 1
            else:
                fn += 1
        else:
            if actual_err:
                fp += 1
            else:
                tn += 1
        rows.append((name, expected, actual, ok))

    if verbose:
        for name, exp, act, ok in rows:
            flag = "PASS" if ok else "FAIL"
            print(f"  [{flag}] {name:22s} 期望={sorted(exp) or '无'} "
                  f"实际={sorted(act) or '无'}")

    total = len(CASES)
    passed = sum(1 for r in rows if r[3])
    pos = tp + fn
    neg = tn + fp
    recall = tp / pos if pos else 0.0
    fpr = fp / neg if neg else 0.0

    print(f"\n用例 {total} 个，通过 {passed} 个（{passed / total:.1%}）")
    print(f"召回率（有错代码被检出）: {tp}/{pos} = {recall:.1%}")
    print(f"误报率（合法代码被误报）: {fp}/{neg} = {fpr:.1%}")
    print(f"准确率: {(tp + tn)}/{total} = {(tp + tn) / total:.1%}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
