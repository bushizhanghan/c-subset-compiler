#!/usr/bin/env python3
"""IR 语义验证器：把"按 IR 执行"和"真实运行 C 程序"的结果摆在一起比对。

验证的是什么
------------
csub 的 IR 生成器（src/ir.py）会做常量折叠和死代码消除，也要处理
while/for/do-while/switch 的跳转。这些变换此前从未被"执行"验证过——
看起来合理的 `goto` 完全可能跳到错误的位置。本工具做三件事：

  A = 用 ir_exec.py 解释执行 csub 生成的 IR
  B = 用 gcc 编译后真实运行，取进程退出码
  比对 A 与 B。一致率就是 IR 语义正确性的直接度量。

两种 IR 都跑
------------
  raw  : 未优化 IR —— 只考验"语句 -> 跳转"的翻译是否正确
  opt  : 优化后 IR —— 额外考验常量折叠 / 死代码消除是否保语义
分开统计是因为这两类错误的修复方向完全不同：raw 错是翻译错，
只有 opt 错是优化遍错。

错在哪一侧？怎么归因？
----------------------
这是本工具最关键的部分，不能只报"不一致"就完事：

  1. 先用一组**手写 IR 片段**给解释器做自测（test_interpreter）。
     这些片段与 src/ir.py 完全无关，期望值由 C 语义手工推导。
     解释器自测全过，才说明"不一致"不是解释器的锅。
  2. 静态检查 goto/ifFalse/ifTrue 的目标标签是否都存在（check_targets）。
     悬空标签是确定的 IR 生成缺陷，不需要运行就能判定。
  3. 静态检查回边极性（check_backedge_polarity）：do-while 的正确形式
     是"条件为真跳回循环头"，若出现"条件为假跳回循环头"，语义正好相反。
  4. 归因规则：
       raw 不一致            -> IR 生成错（翻译阶段）
       仅 opt 不一致          -> IR 优化错，再逐遍定位到
                                const_fold 还是 dead_code_elim
       解释器自测有失败       -> 执行器错（需先修执行器再看其它结论）
       标签悬空 / 回边极性反  -> IR 生成错（且无需运行即可确认）

退出码约定
----------
gcc 程序的 `return v` 在 POSIX 上只保留低 8 位（v & 0xFF）。
Windows 上 subprocess 拿到的是完整 32 位有符号值（return -2 得到
4294967294），所以这里统一 `& 0xFF` 对齐 C 标准语义。IR 侧同样口径。

用法:
  python tools/ir_verify.py            跑全部程序
  python tools/ir_verify.py -v         显示每个程序的 A/B 明细
  python tools/ir_verify.py --ir       额外打印出错的 IR
  python tools/ir_verify.py --json     机器可读输出
  python tools/ir_verify.py --only switch    只跑名字含 switch 的
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from ir_exec import (                       # noqa: E402
    compile_source, execute_ir, parse_program, IRExecError,
    param_map_from_ast,
)

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
LINE = "=" * 78


# ================================================================ 测试程序
# 约定：只靠 return 值判断，不依赖输入、不依赖 printf 输出。
# 每个程序都必须能被 csub 前端吃下（子集内），也必须能被 gcc 编译。
# tags 用于覆盖度统计，直接对应任务要求里点名要覆盖的几类。

PROGRAMS = [
    ("arith_prec", ["基础"], """
        int main() {
            int a = 2 + 3 * 4 - 6 / 2;
            return a;
        }
    """),

    ("const_fold_chain", ["常量折叠"], """
        int main() {
            int a = 10 * 10 - 5;
            int b = (2 + 3) * (4 + 1) - 7;
            int c = a + b;
            return c;
        }
    """),

    ("if_else_chain", ["分支"], """
        int main() {
            int a = 5;
            int r = 0;
            if (a < 3) {
                r = 1;
            } else if (a < 7) {
                r = 2;
            } else {
                r = 3;
            }
            return r * 10 + a;
        }
    """),

    ("while_break_continue", ["循环", "break", "continue"], """
        int main() {
            int i = 0;
            int s = 0;
            while (i < 20) {
                i = i + 1;
                if (i == 12) { break; }
                if (i % 3 == 0) { continue; }
                s = s + i;
            }
            return s;
        }
    """),

    ("for_basic", ["循环"], """
        int main() {
            int i = 0;
            int s = 0;
            for (i = 0; i < 5; i = i + 1) {
                s = s + i;
            }
            return s;
        }
    """),

    ("nested_loop", ["循环", "嵌套循环"], """
        int main() {
            int i = 0;
            int j = 0;
            int s = 0;
            for (i = 0; i < 4; i = i + 1) {
                for (j = 0; j < 4; j = j + 1) {
                    s = s + i * j;
                }
            }
            return s;
        }
    """),

    ("nested_break_continue", ["循环", "嵌套循环", "break", "continue"], """
        int main() {
            int i = 0;
            int j = 0;
            int s = 0;
            for (i = 0; i < 6; i = i + 1) {
                if (i == 4) { break; }
                for (j = 0; j < 6; j = j + 1) {
                    if (j == 3) { break; }
                    if (j == 1) { continue; }
                    s = s + i * 10 + j;
                }
            }
            return s;
        }
    """),

    ("dowhile_basic", ["do-while"], """
        int main() {
            int i = 0;
            int s = 0;
            do {
                s = s + i;
                i = i + 1;
            } while (i < 4);
            return s;
        }
    """),

    ("dowhile_break_continue", ["do-while", "break", "continue"], """
        int main() {
            int i = 0;
            int s = 0;
            do {
                i = i + 1;
                if (i == 2) { continue; }
                if (i > 5) { break; }
                s = s + i;
            } while (i < 20);
            return s;
        }
    """),

    ("switch_break", ["switch"], """
        int main() {
            int x = 2;
            int s = 0;
            switch (x) {
                case 1: s = 10; break;
                case 2: s = 20; break;
                default: s = 30; break;
            }
            return s + x;
        }
    """),

    ("switch_fallthrough", ["switch"], """
        int main() {
            int x = 1;
            int s = 0;
            switch (x) {
                case 1: s = s + 1;
                case 2: s = s + 2;
                default: s = s + 3;
            }
            return s;
        }
    """),

    ("switch_default_only", ["switch"], """
        int main() {
            int x = 9;
            int s = 0;
            switch (x) {
                case 1: s = 11; break;
                case 2: s = 22; break;
                default: s = 33;
            }
            return s;
        }
    """),

    ("switch_neg_expr", ["switch", "边界"], """
        int main() {
            int x = -1;
            int s = 0;
            switch (x) {
                case -1: s = 7; break;
                case 1: s = 9; break;
                default: s = 8; break;
            }
            return s;
        }
    """),

    ("recursion_fib", ["递归"], """
        int fib(int n) {
            if (n < 2) { return n; }
            return fib(n - 1) + fib(n - 2);
        }
        int main() {
            return fib(8);
        }
    """),

    ("recursion_gcd", ["递归"], """
        int gcd(int a, int b) {
            if (b == 0) { return a; }
            return gcd(b, a % b);
        }
        int main() {
            return gcd(48, 18) * 10 + gcd(1071, 462) % 10;
        }
    """),

    ("recursion_fact", ["递归", "循环"], """
        int fact(int n) {
            int r = 1;
            while (n > 1) { r = r * n; n = n - 1; }
            return r;
        }
        int main() {
            return fact(5);
        }
    """),

    ("logic_ops", ["运算", "逻辑"], """
        int main() {
            int a = 3;
            int b = -3;
            int c = 0;
            if (a > 0 && b < 0) { c = c + 1; }
            if (a == 0 || b != 0) { c = c + 2; }
            if (!c) { c = 100; }
            if (a >= 3 && b <= -3) { c = c * 10; }
            return c;
        }
    """),

    ("neg_div_mod", ["运算", "边界"], """
        int main() {
            int a = -7;
            int b = 3;
            int c = a / b;
            int d = a % b;
            return c * 10 + d + 30;
        }
    """),

    ("array_loop", ["数组", "循环"], """
        int main() {
            int a[5];
            int i = 0;
            int s = 0;
            for (i = 0; i < 5; i = i + 1) {
                a[i] = i * i;
            }
            for (i = 0; i < 5; i = i + 1) {
                s = s + a[i];
            }
            return s;
        }
    """),

    ("func_multi_args", ["函数调用"], """
        int mix(int x, int y) {
            int r = x * 2 + y;
            if (r > 10) { r = r - 10; }
            return r;
        }
        int main() {
            return mix(3, 4) * 100 / 100 + mix(6, 7);
        }
    """),

    ("mixed_all", ["综合"], """
        int score(int n) {
            int s = 0;
            int i = 0;
            for (i = 0; i < n; i = i + 1) {
                switch (i % 3) {
                    case 0: s = s + 1; break;
                    case 1: s = s + 10; break;
                    default: s = s + 100; break;
                }
            }
            return s;
        }
        int main() {
            return score(7) % 256;
        }
    """),

    # 退出码边界：IR 侧返回负数/超过 255 时，必须与 gcc 的退出码同口径
    # （C 标准只管低 8 位，Windows 上 subprocess 会给出完整 32 位值）。
    ("exitcode_255", ["边界", "退出码"], """
        int main() {
            return -1;
        }
    """),

    ("exitcode_over255", ["边界", "退出码"], """
        int main() {
            return 300;
        }
    """),
]


# ================================================================ 解释器自测
# 手写 IR 片段，与 src/ir.py 无关。期望值按 C 语义人工推导。
# 这一组全过，后面任何 raw IR 不一致就都不是解释器的锅。

INTERPRETER_TESTS = [
    ("assign_const", ["t0 = 42", "return t0"], 42),
    ("arith", ["t0 = 2 + 3", "t1 = t0 * 4", "t2 = t1 - 6", "t3 = t2 / 2",
               "return t3"], 7),
    ("c_div_trunc", ["t0 = 0 - 7", "t1 = t0 / 2", "return t1"], -3),
    ("c_mod_sign", ["t0 = 0 - 7", "t1 = t0 % 2", "return t1"], -1),
    ("cmp_ops", ["t0 = 3 < 5", "t1 = 3 > 5", "t2 = 3 <= 3", "t3 = 3 >= 4",
                 "t4 = 3 == 3", "t5 = 3 != 3",
                 "t6 = t0 + t1", "t7 = t6 + t2", "t8 = t7 + t3",
                 "t9 = t8 + t4", "t10 = t9 + t5", "return t10"], 3),
    # 手算：t0=1(2&&3), t1=0(0||0), t2=1(2||0) -> 1+0+1 = 2
    ("logic_ops", ["t0 = 2 && 3", "t1 = 0 || 0", "t2 = 2 || 0",
                   "t3 = t0 + t1", "t4 = t3 + t2", "return t4"], 2),
    ("goto_loop", ["s = 0", "i = 0",
                   "L1:",
                   "t0 = i < 4",
                   "ifFalse t0 goto L2",
                   "t1 = s + i",
                   "s = t1",
                   "t2 = i + 1",
                   "i = t2",
                   "goto L1",
                   "L2:",
                   "return s"], 6),
    ("ifturn", ["s = 0", "t0 = 1", "if t0 goto L1", "s = 99", "L1:",
                "t1 = s + 1", "return t1"], 1),
    ("neg_not", ["t0 = -5", "t1 = !t0", "t2 = 0 - 5", "ifFalse t1 goto L1",
                 "return 1", "L1:", "t3 = 0 - 5", "ifFalse t0 goto L2",
                 "return 2", "L2:", "return 3"], 2),
    ("call_user", ["func add:", "t0 = a + b", "return t0", "end func add:",
                   "func main:", "t1 = call add(3, 4)", "return t1",
                   "end func main:"], 7),
    ("call_nested", ["func sq:", "t0 = n * n", "return t0", "end func sq:",
                     "func main:", "t1 = call sq(3)", "t2 = call sq(2)",
                     "t3 = t1 + t2", "return t3", "end func main:"], 13),
    ("call_abs_builtin", ["func main:", "t0 = 0 - 9", "t1 = call abs(t0)",
                          "return t1", "end func main:"], 9),
    ("array_store_load", ["a[0] = 11", "a[3] = 22", "t0 = a[0]", "t1 = a[3]",
                          "t2 = t0 + t1", "return t2"], 33),
    ("label_fallthrough", ["s = 0", "goto L2", "L1:", "s = s + 100", "L2:",
                           "t0 = s + 7", "return t0"], 7),
    ("empty_return", ["func f:", "return 0", "end func f:", "func main:",
                      "t0 = call f()", "return t0", "end func main:"], 0),
]

# 期望"必须失败"的片段：死循环必须被步数上限拦住，不能挂死。
INTERPRETER_FAILURE_TESTS = [
    ("step_limit", ["L1:", "goto L1"], "步数"),
    ("missing_label", ["goto LA"], "目标不存在"),
    ("div_zero", ["t0 = 1 / 0", "return t0"], "除零"),
]


def test_interpreter() -> dict:
    """跑解释器自测，返回 {'passed': n, 'failed': [(name, why)], ...}。"""
    ok, fails = 0, []
    for name, body, expect in INTERPRETER_TESTS:
        ir = "\n".join(body)
        r = execute_ir(ir, max_steps=50_000)
        if not r.ok:
            fails.append((name, f"执行失败: {r.error}"))
        elif r.value != expect:
            fails.append((name, f"期望 {expect} 实际 {r.value}"))
        else:
            ok += 1

    fail_ok = 0
    for name, body, keyword in INTERPRETER_FAILURE_TESTS:
        ir = "\n".join(body)
        r = execute_ir(ir, max_steps=2_000)
        if r.ok:
            fails.append((name, f"本应失败却成功返回 {r.value}"))
        elif keyword not in (r.error or ""):
            fails.append((name, f"失败原因不符预期（要含「{keyword}」）: {r.error}"))
        else:
            fail_ok += 1

    total = len(INTERPRETER_TESTS) + len(INTERPRETER_FAILURE_TESTS)
    return {
        "total": total,
        "passed": ok + fail_ok,
        "failed": fails,
        "sane": not fails,
    }


# ================================================================ 逐遍定位

def pass_trace(src: str) -> dict:
    """把 IR 逐遍跑一遍，定位是哪一个优化遍破坏了语义。

    阶段：raw -> +常量折叠 -> +死代码消除
    这样归因从"优化错"细化到"const_fold 错"或"dead_code_elim 错"，
    维修方向立刻明确。
    """
    from parser import parse as parse_c                    # noqa: E402
    from ir import IRGen, const_fold, dead_code_elim       # noqa: E402

    cprog = parse_c(src)
    pm = param_map_from_ast(cprog)
    raw_code = IRGen().gen_program(cprog)
    fold_code, folded = const_fold(raw_code)
    dce_code, removed = dead_code_elim(fold_code)

    def run(code):
        r = execute_ir([str(i) for i in code], param_map=pm,
                       max_steps=200_000)
        if not r.ok:
            return ("ERR", (r.error or "")[:60])
        return ("OK", r.value)

    return {
        "raw": run(raw_code),
        "fold": run(fold_code),
        "dce": run(dce_code),
        "folded": folded,
        "removed": removed,
    }


def localize(src: str, gcc_val) -> str:
    """在 const_fold / dead_code_elim 之间定位责任方。"""
    t = pass_trace(src)
    raw_v, fold_v, dce_v = t["raw"][1], t["fold"][1], t["dce"][1]

    if t["raw"][0] != "OK":
        return ""
    if fold_v != gcc_val or t["fold"][0] != "OK":
        return (f"常量折叠（const_fold）破坏语义：raw={raw_v} -> "
                f"折叠后={fold_v}（gcc={gcc_val}，本轮折叠 {t['folded']} 处）。"
                f"机制：const_fold 用一张线性推进的 consts 表做常量传播，"
                f"完全不看控制流。变量在条件分支里被赋值成常量后，"
                f"这张表就被污染了——汇合点之后（以及下一轮迭代时）"
                f"仍然拿最后一次看到的常量去做替换，于是"
                f"「a = b + c」被错换成「a = 常量」。循环里的 i = i + 1 "
                f"会因此被固定成 i = 1，循环永远不前进（表现为步数超限）。"
                f"正确做法是只在基本块内传播，或先重建控制流图、"
                f"对汇合点取交集。")
    if dce_v != gcc_val or t["dce"][0] != "OK":
        return (f"死代码消除（dead_code_elim）破坏语义：折叠正常，"
                f"消除后 {dce_v}（gcc={gcc_val}，消除 {t['removed']} 条）")
    return ""


# ================================================================ gcc 一侧

def gcc_available() -> bool:
    try:
        r = subprocess.run(["gcc", "--version"], capture_output=True,
                           timeout=20, creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def gcc_run_value(src: str):
    """编译并运行，返回 (退出码 0-255, 错误信息)。

    取 `& 0xFF` 是为了对齐 C 标准：main 的 return 值在 POSIX 上
    只保留低 8 位。Windows 上 subprocess 会拿到完整 32 位
    （return -2 -> 4294967294），不归一化就会和 POSIX 语义对不上。
    """
    d = tempfile.mkdtemp(prefix="csub_irverify_")
    cpath = os.path.join(d, "prog.c")
    exe = os.path.join(d, "prog.exe" if os.name == "nt" else "prog.out")
    with open(cpath, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        c = subprocess.run(["gcc", "-w", "-o", exe, cpath],
                           capture_output=True, timeout=60,
                           creationflags=CREATE_NO_WINDOW)
        if c.returncode != 0:
            return None, "gcc 编译失败：" + c.stderr.decode("utf-8",
                                                            "replace")[:200]
        r = subprocess.run([exe], capture_output=True, timeout=20,
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode & 0xFF, ""
    except subprocess.TimeoutExpired:
        return None, "gcc 编译或运行超时"
    except OSError as e:
        return None, f"无法执行 gcc：{e}"
    finally:
        for p in (exe, cpath):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


# ================================================================ 静态检查

def check_targets(ir_lines) -> list:
    """静态检查所有跳转目标是否都存在（悬空标签 = 确定的 IR 生成缺陷）。

    注意：按函数分别检查。goto 只能在本函数内跳转。
    """
    try:
        prog = parse_program(ir_lines)
    except IRExecError as e:
        return [f"IR 无法解析：{e}"]

    problems = []
    for fname, fn in prog.functions.items():
        for ins in fn.code:
            if ins.op in ("goto", "if", "ifTrue"):
                if ins.extra not in fn.index:
                    problems.append(
                        f"{fname}: {ins.raw} 跳到不存在的标签 {ins.extra}")
    return problems


def check_backedge_polarity(ir_lines) -> list:
    """静态检查"条件为假时跳回循环头"——这是 do-while 条件写反的特征。

    正确的 do-while 是：条件为真就跳回循环头（ifTrue -> Ltop）。
    如果 IR 里出现 ifFalse 且目标在它前面，语义正好相反：
    条件成立时反而往下走退出循环，条件不成立时反而再跑一遍。
    这个检查不需要运行，属于确定的 IR 生成缺陷。
    """
    try:
        prog = parse_program(ir_lines)
    except IRExecError:
        return []

    problems = []
    for fname, fn in prog.functions.items():
        for i, ins in enumerate(fn.code):
            if ins.op != "if":            # 只针对 ifFalse
                continue
            tgt = fn.index.get(ins.extra)
            if tgt is not None and tgt < i:
                problems.append(
                    f'{fname}: {ins.raw} —— "条件为假时跳回循环头"'
                    f"（目标 {ins.extra} 在它前面），do-while 的回边应该挂在"
                    f"条件为真上，这里极性反了")
    return problems


# ================================================================ 归因

def attribute(raw_res, opt_res, gcc_val, target_problems, interp_sane,
              backedge_problems=None) -> tuple:
    """判定"不一致"的责任方。返回 (分类, 说明)。

    分类取值：
      "一致"            全部吻合
      "执行器错"        解释器自测就没过，先修执行器
      "IR 生成错(标签)" 悬空跳转目标，静态即可确认
      "IR 生成错(跳转)" 回边条件极性反了（do-while），静态即可确认
      "IR 生成错"       raw IR 执行结果与 gcc 不符
      "IR 优化错"       raw 对但 opt 错 -> 常量折叠/死代码消除
      "无法执行"        IR 解释执行报错
      "gcc 不可用"      环境问题，不计入不一致
    """
    backedge_problems = backedge_problems or []
    if not interp_sane:
        return "执行器错", "解释器自测未全过，先修执行器再谈 IR"

    if gcc_val is None:
        return "gcc 不可用", "拿不到 gcc 结果，无法比对"

    if not raw_res.ok:
        if target_problems:
            return "IR 生成错(标签)", "跳转目标不存在：" + target_problems[0]
        if backedge_problems:
            return "IR 生成错(跳转)", backedge_problems[0]
        return "IR 生成错", f"raw IR 执行报错：{raw_res.error}"
    if not opt_res.ok:
        return "IR 优化错", f"优化后 IR 执行报错：{opt_res.error}"

    raw_ok = raw_res.exit_code == gcc_val
    opt_ok = opt_res.exit_code == gcc_val

    if raw_ok and opt_ok:
        return "一致", ""
    if not raw_ok and target_problems:
        return "IR 生成错(标签)", "跳转目标不存在：" + target_problems[0]
    if not raw_ok and backedge_problems:
        return "IR 生成错(跳转)", backedge_problems[0]
    if not raw_ok:
        return "IR 生成错", (f"未优化 IR 就已经偏离（raw={raw_res.value} "
                            f"gcc={gcc_val}），说明语句到跳转的翻译本身有错")
    return "IR 优化错", (f"未优化 IR 正确（{raw_res.value}），优化后变成 "
                        f"{opt_res.value}（gcc={gcc_val}），"
                        f"说明常量折叠/死代码消除破坏了语义")


# ================================================================ 主流程

def verify_one(name: str, src: str, verbose=False, dump_ir=False) -> dict:
    row = {
        "name": name,
        "raw": None, "opt": None, "gcc": None,
        "raw_exit": None, "opt_exit": None,
        "raw_err": "", "opt_err": "", "gcc_err": "",
        "raw_steps": 0, "opt_steps": 0,
        "targets": [], "backedge": [], "verdict": "", "why": "", "detail": "",
        "tags": [],
    }

    param_map = {}
    try:
        raw_lines, param_map = compile_source(src, optimize=False)
        opt_lines, _ = compile_source(src, optimize=True)
    except Exception as e:                      # 前端本身的错误
        row["verdict"] = "前端失败"
        row["why"] = f"{type(e).__name__}: {e}"
        return row

    row["targets"] = check_targets(raw_lines)
    row["backedge"] = check_backedge_polarity(raw_lines)

    raw_res = execute_ir(raw_lines, param_map=param_map, max_steps=200_000)
    opt_res = execute_ir(opt_lines, param_map=param_map, max_steps=200_000)
    row["raw"] = raw_res.value if raw_res.ok else None
    row["opt"] = opt_res.value if opt_res.ok else None
    # 比对用退出码（C 标准口径），原始返回值另存，便于看清 -1 vs 255
    row["raw_exit"] = raw_res.exit_code
    row["opt_exit"] = opt_res.exit_code
    row["raw_err"] = raw_res.error or ""
    row["opt_err"] = opt_res.error or ""
    row["raw_steps"] = raw_res.steps
    row["opt_steps"] = opt_res.steps
    if raw_res.notes:
        row["raw_err"] = (row["raw_err"] + "；" if row["raw_err"] else "") \
            + "；".join(raw_res.notes[:2])

    gcc_val, gcc_err = gcc_run_value(src)
    row["gcc"] = gcc_val
    row["gcc_err"] = gcc_err

    row["verdict"], row["why"] = attribute(
        raw_res, opt_res, gcc_val, row["targets"],
        interp_sane=TEST_RESULT["sane"],
        backedge_problems=row["backedge"],
    )
    # 优化错再往下钻一层：到底是常量折叠还是死代码消除
    if row["verdict"] == "IR 优化错" and gcc_val is not None:
        row["detail"] = localize(src, gcc_val)

    if dump_ir and row["verdict"] not in ("一致", "gcc 不可用"):
        row["raw_ir"] = raw_lines
        row["opt_ir"] = opt_lines
    return row


def fmt(v):
    return "-" if v is None else str(v)


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    verbose = "-v" in argv
    dump_ir = "--ir" in argv
    as_json = "--json" in argv
    only = None
    for i, a in enumerate(argv):
        if a == "--only" and i + 1 < len(argv):
            only = argv[i + 1]

    global TEST_RESULT
    TEST_RESULT = test_interpreter()

    if as_json:
        # 机器可读输出，便于其它工具/报告消费
        progs = [p for p in PROGRAMS if (only is None or only in p[0])]
        rows = []
        for name, tags, src in progs:
            row = verify_one(name, src)
            row["tags"] = tags
            rows.append(row)
        import json
        payload = {
            "interpreter_sane": TEST_RESULT["sane"],
            "interpreter_passed": TEST_RESULT["passed"],
            "interpreter_total": TEST_RESULT["total"],
            "total": len(rows),
            "raw_agree": sum(1 for r in rows
                             if r["raw_exit"] is not None
                             and r["raw_exit"] == r["gcc"]),
            "opt_agree": sum(1 for r in rows
                             if r["opt_exit"] is not None
                             and r["opt_exit"] == r["gcc"]),
            "both_agree": sum(1 for r in rows if r["verdict"] == "一致"),
            "rows": [{k: r[k] for k in
                      ("name", "raw", "opt", "gcc", "raw_exit", "opt_exit",
                       "verdict", "why", "detail", "targets", "backedge",
                       "tags")}
                     for r in rows],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["both_agree"] == len(rows) else 1

    print(LINE)
    print("IR 解释执行器自测（手写 IR 片段，与 src/ir.py 无关）")
    print(LINE)
    if TEST_RESULT["sane"]:
        print(f"  {TEST_RESULT['passed']}/{TEST_RESULT['total']} 全部通过 —— "
              f"解释器可以当作可信的判定基准")
    else:
        print(f"  {TEST_RESULT['passed']}/{TEST_RESULT['total']} 通过，"
              f"有 {len(TEST_RESULT['failed'])} 项失败：")
        for name, why in TEST_RESULT["failed"]:
            print(f"    [FAIL] {name}: {why}")
        print("  -> 解释器自身有问题，下面的比对结果不可信，先修解释器。")
    print()

    if not gcc_available():
        print("[warn] 未找到 gcc，无法做真实运行比对。")
        print("       只跑解释器自测与静态检查。")
        return 2 if TEST_RESULT["sane"] else 1

    progs = [p for p in PROGRAMS if (only is None or only in p[0])]

    rows = []
    for name, tags, src in progs:
        row = verify_one(name, src, verbose, dump_ir)
        row["tags"] = tags
        rows.append(row)

    print(LINE)
    print("A = 解释执行 IR 的退出码      B = gcc 真实运行退出码（两者都已 & 0xFF）")
    print(LINE)
    print(f"{'程序':<26}{'A(raw)':>8}{'A(opt)':>8}{'B(gcc)':>8}  结论")
    print("-" * 78)
    for row in rows:
        mark = "OK  " if row["verdict"] == "一致" else "!!  "
        print(f"{row['name']:<26}{fmt(row['raw_exit']):>8}"
              f"{fmt(row['opt_exit']):>8}{fmt(row['gcc']):>8}  "
              f"{mark}{row['verdict']}")
        # 原始返回值与退出码不同时额外标出，避免"显示 -1 却算一致"的困惑
        for tag, val, ex in (("raw", row["raw"], row["raw_exit"]),
                             ("opt", row["opt"], row["opt_exit"])):
            if val is not None and ex is not None and val != ex:
                print(f"{'':<26}{tag} 原始返回值 {val} -> 退出码 {ex}"
                      f"（C 只保留低 8 位）")
        if row["verdict"] != "一致" and verbose:
            print(f"{'':<26}原因: {row['why']}")
            if row["detail"]:
                print(f"{'':<26}定位: {row['detail'][:120]}")
            if row["raw_err"]:
                print(f"{'':<26}raw: {row['raw_err'][:90]}")
            if row["opt_err"]:
                print(f"{'':<26}opt: {row['opt_err'][:90]}")
            if row["raw_steps"] > 190_000:
                print(f"{'':<26}raw 走了 {row['raw_steps']} 步 —— 典型的死循环")

    # ---- 统计 ----
    n = len(rows)
    raw_ok = sum(1 for r in rows if r["raw_exit"] is not None
                 and r["raw_exit"] == r["gcc"])
    opt_ok = sum(1 for r in rows if r["opt_exit"] is not None
                 and r["opt_exit"] == r["gcc"])
    both_ok = sum(1 for r in rows if r["verdict"] == "一致")

    print()
    print(LINE)
    print("一致率统计（A 与 B 都按退出码口径比对）")
    print(LINE)
    if n:
        print(f"  程序总数              : {n}")
        print(f"  未优化 IR  A==B       : {raw_ok}/{n}  ({raw_ok / n:.1%})")
        print(f"  优化后 IR  A==B       : {opt_ok}/{n}  ({opt_ok / n:.1%})")
        print(f"  两者都一致            : {both_ok}/{n}  ({both_ok / n:.1%})")

    # ---- 归因汇总 ----
    by_verdict = {}
    for r in rows:
        by_verdict.setdefault(r["verdict"], []).append(r)
    bad = [r for r in rows if r["verdict"] != "一致"]
    if bad:
        print()
        print(LINE)
        print("不一致归因")
        print(LINE)
        # 让最严重的类型先出现，读起来有重点
        order = ["执行器错", "前端失败", "IR 生成错(标签)", "IR 生成错(跳转)",
                 "IR 生成错", "IR 优化错", "gcc 不可用"]
        for verdict in order:
            items = by_verdict.get(verdict) or []
            if not items:
                continue
            print(f"\n[{verdict}] {len(items)} 个：")
            for r in items:
                print(f"  - {r['name']}（raw 退出码={fmt(r['raw_exit'])} "
                      f"opt 退出码={fmt(r['opt_exit'])} "
                      f"gcc={fmt(r['gcc'])}）")
                print(f"    说明: {r['why']}")
                if r.get("detail"):
                    print(f"    定位: {r['detail']}")
                for t in r["targets"][:3]:
                    print(f"    标签问题: {t}")
                for t in r.get("backedge", [])[:3]:
                    print(f"    跳转问题: {t}")
                if r.get("raw_ir"):
                    print("    --- raw IR ---")
                    for ln in r["raw_ir"]:
                        print("      " + ln)
                    print("    --- opt IR ---")
                    for ln in r["opt_ir"]:
                        print("      " + ln)

    # ---- 覆盖度 ----
    print()
    print(LINE)
    print("覆盖度（任务点名要求覆盖的几类）")
    print(LINE)
    wanted = ["循环", "break", "continue", "嵌套循环", "switch", "do-while",
              "递归", "常量折叠"]
    for tag in wanted:
        hit = [r["name"] for r in rows if tag in r["tags"]]
        good = [r["name"] for r in rows
                if tag in r["tags"] and r["verdict"] == "一致"]
        status = "全部通过" if hit and len(good) == len(hit) else \
                 ("有失败" if hit else "未覆盖")
        print(f"  {tag:<10}: {len(good)}/{len(hit)}  {status}   "
              f"{', '.join(hit)}")

    real_bad = [r for r in rows if r["verdict"] not in ("一致", "gcc 不可用")]
    print()
    if not real_bad:
        print("结论：所有程序的 IR 语义与真实运行一致。")
        return 0
    print(f"结论：{len(real_bad)} 个程序的 IR 语义与真实运行不一致，"
          f"需要修 IR 生成或优化（见上方归因）。")
    return 1


TEST_RESULT = {"sane": True, "passed": 0, "total": 0, "failed": []}

if __name__ == "__main__":
    sys.exit(main(sys.argv))
