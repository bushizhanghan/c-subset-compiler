#!/usr/bin/env python3
"""student_real 样本库的评估工具。

对 examples/student_real/*.c 逐个跑 csub，输出三态报告：
  真检出   文件头部标了「已知问题」且 csub 报出来了
  疑似误报 csub 报了，但文件没标已知问题（需人工确认）
  漏报     文件标了已知问题，csub 没报

用法:
    python tools/student_eval.py            # 汇总
    python tools/student_eval.py -v         # 逐个显示
    python tools/student_eval.py --add      # 交互式录入新样本
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLES = os.path.join(ROOT, "examples", "student_real")


def read_meta(path):
    """从文件头部注释提取元数据。

    返回 dict：source / scenario / known / size。
    known 是「已知问题」的原文，空字符串表示未知。
    """
    meta = {"source": "", "scenario": "", "known": "", "size": ""}
    try:
        head = open(path, encoding="utf-8", errors="replace").read(2500)
    except OSError:
        return meta
    for key in meta:
        m = re.search(rf"\*?\s*{key}\s*[:：]\s*(.+)", head)
        if m:
            meta[key] = m.group(1).strip()
    return meta


def run_csub(path):
    """跑 csub report，返回 (诊断列表, 是否崩溃)。"""
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "csub.py"), "report", path],
        capture_output=True, timeout=60,
        encoding="utf-8", errors="replace")
    out = r.stdout + r.stderr
    crash = ("Traceback" in out or "RecursionError" in out
             or "ValueError" in out)
    diags = []
    for line in out.splitlines():
        m = re.match(r"\[(ERROR|WARNING)\s+(\w+)\]\s*(.+)", line.strip())
        if m:
            diags.append({"level": m.group(1), "code": m.group(2),
                          "msg": m.group(3)})
    return diags, crash


def evaluate(verbose=False):
    files = sorted(f for f in os.listdir(SAMPLES) if f.endswith(".c"))
    if not files:
        print("examples/student_real/ 里还没有样本。")
        print("把学生写的 .c 文件放进去，按 README.md 的格式加头部注释。")
        return 0

    rows = []
    for f in files:
        p = os.path.join(SAMPLES, f)
        meta = read_meta(p)
        diags, crash = run_csub(p)
        known = meta["known"]
        has_known = bool(known) and known not in ("无", "未知", "没有")
        nlines = sum(1 for _ in open(p, encoding="utf-8", errors="replace"))

        # 三态判定
        if crash:
            state = "崩溃"
        elif diags and has_known:
            state = "真检出"
        elif diags and not has_known:
            state = "疑似误报"
        elif not diags and has_known:
            state = "漏报"
        else:
            state = "干净通过"

        rows.append({
            "file": f, "lines": nlines, "state": state,
            "diags": diags, "crash": crash,
            "known": known, "source": meta["source"],
            "scenario": meta["scenario"],
        })

    # ---- 汇总 ----
    print("=" * 68)
    print(f"真实学生代码评估（{len(rows)} 个样本）")
    print("=" * 68)

    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1

    print(f"{'样本数':<10} {len(rows)}")
    for k in ("真检出", "疑似误报", "漏报", "崩溃", "干净通过"):
        if counts.get(k):
            print(f"{k:<10} {counts[k]}")

    # 关键指标
    suspected = counts.get("疑似误报", 0)
    crashes = counts.get("崩溃", 0)
    missed = counts.get("漏报", 0)
    total_with_known = sum(counts.get(k, 0) for k in ("真检出", "漏报"))

    print()
    print("-" * 68)
    print("关键指标")
    print("-" * 68)
    if crashes:
        print(f"  ⚠️ 崩溃 {crashes} 个 —— 这是 P0，工具不可用")
    else:
        print(f"  ✅ 崩溃 0 个")
    if suspected:
        print(f"  ⚠️ 疑似误报 {suspected} 个 —— 需要人工确认后修规则")
    else:
        print(f"  ✅ 疑似误报 0 个")
    if total_with_known:
        print(f"  检出 {counts.get('真检出', 0)}/{total_with_known}"
              f"（仅统计标了已知问题的样本）")
    if missed:
        print(f"  ⚠️ 漏报 {missed} 个")

    # 按规则统计误报
    if suspected:
        print()
        print("-" * 68)
        print("疑似误报按规则分布")
        print("-" * 68)
        rule_count = {}
        for r in rows:
            if r["state"] != "疑似误报":
                continue
            for d in r["diags"]:
                k = f"{d['code']}"
                rule_count[k] = rule_count.get(k, 0) + 1
        for code, n in sorted(rule_count.items(), key=lambda x: -x[1]):
            print(f"  {code}  {n} 次")

    if verbose:
        print()
        print("=" * 68)
        print("逐个明细")
        print("=" * 68)
        for r in rows:
            print()
            print(f"[{r['state']}] {r['file']}  ({r['lines']} 行)")
            if r["scenario"]:
                print(f"  场景: {r['scenario']}")
            if r["known"]:
                print(f"  已知问题: {r['known']}")
            if not r["diags"]:
                print("  csub 未报任何问题")
            for d in r["diags"]:
                print(f"    [{d['level']} {d['code']}] {d['msg']}")

    # 退出码：有崩溃返回 3（底线），有疑似误报返回 2，否则 0
    if crashes:
        return 3
    if suspected:
        return 2
    return 0


def add_sample():
    """交互式录入新样本：从剪贴板/路径读代码，自动补头部注释。"""
    print("交互式录入学生代码样本")
    print("-" * 40)
    path = input("文件路径（或直接把 .c 拖进来）: ").strip().strip('"')
    if not path or not os.path.exists(path):
        print("文件不存在")
        return 2
    src = open(path, encoding="utf-8", errors="replace").read()
    n = len([l for l in src.splitlines() if l.strip()])

    existing = [f for f in os.listdir(SAMPLES) if f.endswith(".c")]
    nums = [int(f[4:6]) for f in existing
            if re.match(r"stu_\d\d", f) and f[4:6].isdigit()]
    next_num = (max(nums) + 1) if nums else 1

    print()
    print("这个样本的来源？（自己写的/同学的/课程示例/OJ 提交）")
    source = input("  > ").strip() or "未知"
    print("什么作业场景？（如 约瑟夫环/链表反转）")
    scenario = input("  > ").strip() or "未知"
    print("已知有哪里错吗？有老师批注就贴关键几句，没有就回车")
    known = input("  > ").strip() or "未知"

    name = input(f"文件名（回车用 stu_{next_num:02d}_sample.c）: ").strip()
    if not name:
        name = f"stu_{next_num:02d}_sample.c"
    if not name.endswith(".c"):
        name += ".c"

    header = f"""/*
 * 来源：{source}
 * 场景：{scenario}
 * 已知问题：{known}
 * 规模：约 {n} 行
 */
"""
    # 去掉原文件里已有的头部注释，避免重复
    body = src
    m = re.match(r"\s*/\*.*?\*/\s*", src, re.S)
    if m:
        body = src[m.end():]

    out = os.path.join(SAMPLES, name)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + body)
    print()
    print(f"已存入 {out}")
    diags, crash = run_csub(out)
    if crash:
        print("⚠️ csub 跑这个文件崩溃了")
    elif diags:
        print(f"csub 报了 {len(diags)} 条：")
        for d in diags:
            print(f"  [{d['level']} {d['code']}] {d['msg']}")
    else:
        print("csub 未报任何问题")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--add", action="store_true", help="交互式录入新样本")
    args = ap.parse_args()
    if args.add:
        sys.exit(add_sample())
    sys.exit(evaluate(verbose=args.verbose))
