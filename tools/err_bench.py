#!/usr/bin/env python3
"""真实错误检出率基准：把 examples/err_*.c 的验证从"手工"变成"可重复运行"。

每个用例文件顶部的注释块就是它的规格说明——错在哪、对应教材概念、
期望 csub 报什么。这个工具就是拿这份规格去**自动判卷**。

三态判定（每一个用例必落其中之一）
  检出   实际诊断里出现了期望码
  漏报   csub 一声不响，什么都没报
  误报   报了东西，但报的不是这个用例期望的那类错误
         （学生视角：工具说了话，却没帮上忙，甚至指错了地方）

另外单独统计
  崩溃   csub 抛异常或非正常退出。这是底线，任何一条都必须为 0。

用法
  python tools/err_bench.py              # 全部用例 + 汇总 + 分类统计
  python tools/err_bench.py -v           # 逐个显示 csub 的完整诊断
  python tools/err_bench.py --vs-gcc     # 追加与 gcc 的对照表
  python tools/err_bench.py --only 29    # 只跑某几个编号（可逗号分隔）
  python tools/err_bench.py --md         # 输出 Markdown 表格，用于贴报告
  python tools/err_bench.py --vs-gcc --md

用例头部的字段
  期望 csub 报什么：<自然语言描述，里面出现的 E/W/LEX/SYN 码即"期望码">
                    —— 这是与旧 28 个用例兼容的格式，不要改
  期望码：          <可选> 显式指定期望码，逗号分隔，覆盖上一行的自动抽取。
                    特殊值 `未实现` 表示「csub 目前根本没有对应诊断」——
                    这类用例按定义必然漏报，工具如实记 0，不给任何糊弄空间
  分类：            <可选> 错误类型。缺省时按 LEGACY_CATEGORY 表回溯，
                    表里没有就记作"未分类"。新用例一律写上。

判卷规则（写死，避免自欺）
  期望码非空      命中任一期望码 -> 检出；有诊断但都不对 -> 误报；无诊断 -> 漏报
  期望码 = 未实现 无诊断 -> 漏报；有诊断 -> 误报（说明报的不是这件事）
  期望码未写      旧用例的"一条 XX 警告"式模糊期望：报了任何东西 -> 检出
                  （这是为了兼容旧 28 个文件而保留的宽松规则，
                    所以本工具的所有统计里都应把它和严格判定分开看）

只读 examples/ 与 src/，不改任何被测代码。
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")
CSUB = os.path.join(ROOT, "csub.py")

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
DIAG_CODE_RE = re.compile(r"\[(ERROR|WARNING)\s+([A-Z]+\d*)\]")

# 旧 28 个用例的纠错类型归属。
# 为什么不从文件里读：它们先于本工具存在，头部只有"期望 csub 报什么"
# 一行，没有分类字段。为了不改动已有用例文件（保持"只新增"的边界），
# 这张表在工具内补齐。新用例用头部 `分类：` 字段自己声明，优先级更高。
LEGACY_CATEGORY = {
    "01": "未初始化", "02": "指针误用", "03": "循环边界",
    "04": "死循环倾向", "05": "类型混淆", "06": "表达式陷阱",
    "07": "表达式陷阱", "08": "指针误用", "09": "符号/拼写",
    "10": "函数签名与返回值", "11": "符号/拼写", "12": "循环边界",
    "13": "数组越界", "14": "数组越界", "15": "未初始化",
    "16": "数组与字符串", "17": "符号/拼写", "18": "词法错误",
    "19": "逻辑错误", "20": "类型混淆", "21": "代码规范",
    "22": "函数签名与返回值", "23": "作用域与生命周期",
    "24": "语法错误", "25": "语法错误", "26": "符号/拼写",
    "27": "表达式陷阱", "28": "死循环倾向",
}

# gcc 的对齐噪音：所有用例为了能被 csub 直接跑，都没有 #include
# <stdio.h>（csub 不处理头文件，且隐式声明在 csub 里是允许的标准库调用）。
# 由此产生的 implicit-declaration 类诊断与被测错误本身无关，
# 不滤掉会让 gcc 这一列变成一片红，对照表就失去信息量。
GCC_NOISE_FLAGS = {
    "-Wimplicit-function-declaration",
    "-Wbuiltin-declaration-mismatch",
    "-Wimplicit-int",
    "-Wdeclaration-missing-parameter-type",
}
GCC_DIAG_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<level>error|warning|note):\s*(?P<msg>.*)$")

HIT, MISS, MISFIRE, CRASH = "检出", "漏报", "误报", "崩溃"
ICON = {HIT: "✅", MISS: "❌", MISFIRE: "⚠️", CRASH: "💥"}


# ---------------------------------------------------------------- 用例解析

def read_header(path):
    """取出文件开头的一段注释块，返回 (头部文本, 行数)。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return f"(无法读取: {e})", 0
    end = text.find("*/")
    head = text if end < 0 else text[:end + 2]
    return head, head.count("\n") + 1


def parse_codes(s):
    """从一句中文描述里抽出所有诊断码：E2 / W5 / LEX / SYN。

    兼容旧格式的三种写法：
      "E2 使用未初始化的变量 'sum'"
      "E13 或边界警告"
      "LEX 非法字符 '；'，并提示是否使用了中文标点"
    """
    codes = re.findall(r"(?<![A-Za-z0-9_])(E\d+|W\d+|LEX|SYN)(?![A-Za-z0-9_])", s)
    seen, out = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def parse_case(path):
    fname = os.path.basename(path)
    m = re.match(r"err_(\d+)", fname)
    num = m.group(1) if m else "??"
    head, _ = read_header(path)

    expect_text = ""
    expect_decl = ""
    category = ""
    for line in head.splitlines():
        s = line.strip().lstrip("*/").strip()
        if s.startswith("期望 csub 报什么"):
            expect_text = re.split(r"[：:]", s, maxsplit=1)[-1].strip()
        elif s.startswith("期望码"):
            expect_decl = re.split(r"[：:]", s, maxsplit=1)[-1].strip()
        elif s.startswith("分类"):
            category = re.split(r"[：:]", s, maxsplit=1)[-1].strip()

    if expect_decl:
        if expect_decl in ("未实现", "无", "none", "N/A"):
            expect_codes = []
            unimplemented = True
        else:
            expect_codes = parse_codes(expect_decl)
            unimplemented = False
        expect_strict = True
    else:
        expect_codes = parse_codes(expect_text)
        unimplemented = False
        # 没有显式期望码、也就是旧用例的模糊期望：用宽松规则判
        expect_strict = bool(expect_codes)

    if not category:
        category = LEGACY_CATEGORY.get(num, "未分类")

    return {
        "num": num,
        "name": fname,
        "path": path,
        "expect_text": expect_text,
        "expect_codes": expect_codes,
        "expect_strict": expect_strict,
        "unimplemented": unimplemented,
        "category": category,
    }


# ---------------------------------------------------------------- 跑 csub

def run_csub(path, timeout=60):
    """跑一次 python csub.py check，返回 (诊断码列表, 原始输出, 是否崩溃)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"     # 子进程统一说 UTF-8，否则 Windows 下乱码
    try:
        p = subprocess.run(
            [sys.executable, CSUB, "check", path],
            cwd=ROOT, env=env, capture_output=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return [], f"[超时] 超过 {timeout}s 未返回", True
    out = (p.stdout or b"").decode("utf-8", "replace")
    err = (p.stderr or b"").decode("utf-8", "replace")
    full = (out + err).strip()

    crashed = (p.returncode not in (0, 1)
               or "Traceback (most recent call last)" in full)
    codes = []
    for _lvl, code in DIAG_CODE_RE.findall(out):
        if code not in codes:
            codes.append(code)
    return codes, full, crashed


# ---------------------------------------------------------------- 跑 gcc

def run_gcc(path, timeout=60, gcc="gcc"):
    """gcc -fsyntax-only -Wall -Wextra，返回 ([(级别, 消息, flag)], 是否可用)。"""
    env = dict(os.environ)
    env["LC_ALL"] = "C"      # 固定英文，避免本地化把关键词翻译掉
    env["LANG"] = "C"
    try:
        p = subprocess.run(
            [gcc, "-fsyntax-only", "-Wall", "-Wextra", path],
            cwd=ROOT, env=env, capture_output=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW)
    except FileNotFoundError:
        return None, False
    except subprocess.TimeoutExpired:
        return [("error", "gcc 超时", "")], True
    text = (p.stderr or b"").decode("utf-8", "replace")
    diags = []
    for line in text.splitlines():
        m = GCC_DIAG_RE.match(line)
        if not m:
            continue
        level = m.group("level")
        if level == "note":
            continue                       # note 只是解释，不算独立诊断
        msg = m.group("msg")
        flag = ""
        fm = re.search(r"\[(-W[^\]]+)\]", msg)
        if fm:
            flag = fm.group(1)
            msg = msg[:fm.start()].strip()
        if flag in GCC_NOISE_FLAGS:
            continue                       # 缺 #include 造成的噪音，见 GCC_NOISE_FLAGS
        diags.append((level, msg, flag))
    return diags, True


def gcc_summary(diags):
    """把 gcc 诊断压成一句短标签，进得了一张表。"""
    if diags is None:
        return "(未安装)"
    if not diags:
        return "(无)"
    hard = [d for d in diags if d[0] == "error"]
    tag = "error" if hard else "warning"
    n = len(diags)
    first = diags[0][1]
    if len(first) > 46:
        first = first[:44] + "…"
    return f"{tag}×{n}: {first}"


# ---------------------------------------------------------------- 判卷

def judge(case, codes, crashed):
    if crashed:
        return CRASH
    if case["unimplemented"]:
        # 用例明知 csub 没有这条诊断，写下来是为了让缺口可见。
        # 只要 csub 说了话，就说明它报的是别的事，按误报计——不能靠
        # 一条不相干的警告蒙混过关。
        return MISFIRE if codes else MISS
    if not codes:
        return MISS
    if not case["expect_codes"]:
        # 没有期望码（旧用例"一条 XX 警告"式模糊期望）：
        # 只要 csub 报了任何东西，就认为它注意到了这段代码有问题。
        return HIT if not case["expect_strict"] else MISFIRE
    for c in codes:
        if c in case["expect_codes"]:
            return HIT
    return MISFIRE


def collect(only=None, verbose=False, timeout=60):
    files = sorted(
        os.path.join(EXAMPLES, f) for f in os.listdir(EXAMPLES)
        if f.startswith("err_") and f.endswith(".c"))
    if only:
        keep = {n.zfill(2) for n in only}
        files = [f for f in files
                 if re.match(r"err_(\d+)", os.path.basename(f))
                 and re.match(r"err_(\d+)", os.path.basename(f)).group(1) in keep]

    rows = []
    for path in files:
        case = parse_case(path)
        codes, full, crashed = run_csub(path, timeout)
        case["codes"] = codes
        case["raw"] = full
        case["crashed"] = crashed
        case["state"] = judge(case, codes, crashed)
        rows.append(case)
        if verbose:
            print(f"\n===== {case['name']}  [{case['category']}] "
                  f"→ {ICON[case['state']]}{case['state']}")
            print(f"  期望：{case['expect_text'] or '(未写明)'}")
            if case["unimplemented"]:
                exp_disp = "未实现（csub 当前没有这条诊断，必然漏报）"
            elif case["expect_codes"]:
                exp_disp = ",".join(case["expect_codes"])
            else:
                exp_disp = "任意诊断（旧用例的模糊期望，判卷偏松）"
            print(f"  期望码：{exp_disp}")
            print("  实际：")
            for line in (case["raw"] or "(无输出)").splitlines():
                print("    " + line)
    return rows


# ---------------------------------------------------------------- 报告

def expect_str(row):
    """期望列的显示形式。三种情形必须一眼能分开，否则统计会骗人。"""
    if row["unimplemented"]:
        return "未实现"
    if row["expect_codes"]:
        return ",".join(row["expect_codes"])
    return "任意"


def actual_str(row):
    if row["crashed"]:
        first = (row["raw"] or "").splitlines()
        return "[崩溃] " + (first[0][:40] if first else "")
    return ",".join(row["codes"]) if row["codes"] else "(无)"


def report_plain(rows, show_gcc=False):
    print("=" * 78)
    print("csub 真实错误检出基准  (examples/err_*.c)")
    print("=" * 78)
    print(f"共 {len(rows)} 个用例\n")
    print(f"{'#':<4}{'用例':<34}{'分类':<16}{'期望':<10}{'实际':<16}三态")
    print("-" * 78)
    for r in rows:
        print(f"{r['num']:<4}{r['name']:<34}{r['category']:<16}"
              f"{expect_str(r):<10}{actual_str(r):<16}{ICON[r['state']]}{r['state']}")

    n = len(rows)
    cnt = {s: sum(1 for r in rows if r["state"] == s)
           for s in (HIT, MISS, MISFIRE, CRASH)}
    print("-" * 78)
    print(f"用例总数 {n} | 检出 {cnt[HIT]} | 漏报 {cnt[MISS]} | "
          f"误报 {cnt[MISFIRE]} | 崩溃 {cnt[CRASH]}")
    if n:
        # 崩溃的用例不计入检出率分母以外的任何修饰——它连"判卷"都没完成。
        graded = n - cnt[CRASH]
        rate = (cnt[HIT] / graded * 100) if graded else 0.0
        print(f"检出率 {cnt[HIT]}/{graded} = {rate:.1f}%"
              + (f"（另有 {cnt[CRASH]} 个崩溃未计入）" if cnt[CRASH] else ""))

    # 诚实性说明：把"期望码未实现"的用例单独标出来。
    # 这些用例写的时候就明知 csub 没有对应诊断，写下来是为了让缺口
    # 在统计里可见，而不是靠"随便报一条警告就算检出"把数字做上去。
    unimpl = [r for r in rows if r["unimplemented"]]
    loose = [r for r in rows if not r["expect_strict"] and not r["unimplemented"]]
    strict = [r for r in rows if r["expect_strict"] and not r["unimplemented"]]
    if unimpl or loose:
        print()
        print("判卷口径说明")
        print("-" * 78)
        if unimpl:
            uh = sum(1 for r in unimpl if r["state"] == HIT)
            print(f"  「未实现」用例 {len(unimpl)} 个（检出 {uh}）：用例头部写的是"
                  " `期望码：未实现`，")
            print("    即 csub 目前没有这条诊断。它们必然漏报，如实计入分母。")
            print("    编号：" + ",".join(r["num"] for r in unimpl))
        if loose:
            lh = sum(1 for r in loose if r["state"] == HIT)
            print(f"  「模糊期望」用例 {len(loose)} 个（检出 {lh}）：旧用例只写了"
                  "「一条 XX 警告」，")
            print("    没有具体码，判卷时放宽为「报了任何诊断即算检出」。")
            print("    编号：" + ",".join(r["num"] for r in loose))
            print("    这类判定偏松，看结论时请把这一组的检出率打折看待。")
        # 严格子集：期望码明确、且 csub 确实有这条诊断。
        # 只有这一组的检出率是"不含任何口径让步"的数字。
        sh = sum(1 for r in strict if r["state"] == HIT)
        print()
        print(f"  【严格口径】期望码明确且已实现的用例 {len(strict)} 个，"
              f"检出 {sh} 个 = {sh / len(strict) * 100:.1f}%")
        print("    这是唯一不含任何让步的检出率。上面那个 42 例总检出率")
        print("    把它们和「未实现」「模糊期望」混在一起算，看趋势可以，")
        print("    当成 csub 的能力上限会低估，当成真实水平会高估。")
        old = [r for r in rows if int(r["num"]) <= 28]
        new = [r for r in rows if int(r["num"]) > 28]
        if old and new:
            oh = sum(1 for r in old if r["state"] == HIT)
            nh = sum(1 for r in new if r["state"] == HIT)
            print()
            print(f"  旧 28 例：检出 {oh}/{len(old)} = {oh / len(old) * 100:.1f}%")
            print(f"  新增 {len(new)} 例：检出 {nh}/{len(new)} = "
                  f"{nh / len(new) * 100:.1f}%  ← 新增用例专挑薄弱面，"
                  "这个低数字才是真实短板所在")

    print()
    print("按错误类型分类")
    print("-" * 78)
    print(f"{'分类':<20}{'检出/总数':<14}{'检出率':<10}说明")
    print("-" * 78)
    cats = {}
    for r in rows:
        cats.setdefault(r["category"], []).append(r)
    for cat in sorted(cats, key=lambda c: (cats[c][0]["num"])):
        group = cats[cat]
        hit = sum(1 for r in group if r["state"] == HIT)
        rate = hit / len(group) * 100
        nums = ",".join(r["num"] for r in group)
        print(f"{cat:<20}{f'{hit}/{len(group)}':<14}{f'{rate:.0f}%':<10}{nums}")

    miss = [r for r in rows if r["state"] in (MISS, MISFIRE)]
    if miss:
        print()
        print("漏报/错报清单（按分类归并）")
        print("-" * 78)
        by_cat = {}
        for r in miss:
            by_cat.setdefault(r["category"], []).append(r)
        for cat in sorted(by_cat, key=lambda c: by_cat[c][0]["num"]):
            print(f"\n[{cat}]")
            for r in by_cat[cat]:
                print(f"  {r['state']}  {r['name']:<34} 期望 {expect_str(r):<8} "
                      f"实际 {actual_str(r)}")

    if show_gcc:
        print()
        report_vs_gcc(rows)


def classify_gcc(rows):
    """跑 gcc 并按四象限归类。返回 (buckets, ok_all)。"""
    buckets = {"两边都报": [], "gcc 报 / csub 漏": [], "csub 报 / gcc 放行": [],
               "两边都没报": [], "gcc 不可用": []}
    ok_all = True
    for r in rows:
        diags, ok = run_gcc(r["path"])
        r["gcc"] = diags
        r["gcc_ok"] = ok
        if not ok:
            ok_all = False
            buckets["gcc 不可用"].append(r)
            continue
        gcc_has = bool(diags)
        csub_has = bool(r["codes"]) and not r["crashed"]
        if gcc_has and csub_has:
            key = "两边都报"
        elif gcc_has:
            key = "gcc 报 / csub 漏"
        elif csub_has:
            key = "csub 报 / gcc 放行"
        else:
            key = "两边都没报"
        buckets[key].append(r)
    return buckets, ok_all


def report_vs_gcc(rows):
    print("=" * 78)
    print("与 gcc 对照  (gcc -fsyntax-only -Wall -Wextra，已滤掉缺 #include 的噪音)")
    print("=" * 78)
    print(f"{'用例':<36}{'gcc 报':<44}{'csub 报':<12}归类")
    print("-" * 78)
    buckets, _ = classify_gcc(rows)
    for r in rows:
        g = gcc_summary(r.get("gcc"))
        c = actual_str(r)
        key = next(k for k, v in buckets.items() if r in v)
        print(f"{r['name']:<36}{g:<44}{c:<12}{key}")

    print("-" * 78)
    print(f"用例总数 {len(rows)}")
    for key in ("两边都报", "gcc 报 / csub 漏", "csub 报 / gcc 放行",
                "两边都没报", "gcc 不可用"):
        if buckets[key]:
            print(f"  {key:<20}{len(buckets[key]):>3} 个："
                  + ",".join(r["num"] for r in buckets[key]))
    if buckets["gcc 不可用"]:
        print("\n  ⚠ gcc 不可用，对照表不完整：请把 gcc 放进 PATH 后重跑 --vs-gcc")

    gap = buckets["gcc 报 / csub 漏"]
    edge = buckets["csub 报 / gcc 放行"]
    print()
    print("怎么读这张表")
    print("-" * 78)
    print(f"  「gcc 报 / csub 漏」{len(gap)} 个 —— 这是 csub 的**能力缺口**，"
          "gcc 有更完整的类型树。")
    print(f"  「csub 报 / gcc 放行」{len(edge)} 个 —— 这是 csub 的**差异化价值**："
          "能编译但会出事的代码。")
    print(f"  「两边都没报」{len(buckets['两边都没报'])} 个 —— 当前全行业盲区，"
          "产品机会，不是缺陷。")


def report_gcc_md(rows):
    buckets, _ = classify_gcc(rows)
    print()
    print("### 与 gcc 对照（`gcc -fsyntax-only -Wall -Wextra`）")
    print()
    print("| 用例 | gcc 报 | csub 报 | 归类 |")
    print("|---|---|---|---|")
    for r in rows:
        g = gcc_summary(r.get("gcc")).replace("|", "\\|")
        key = next(k for k, v in buckets.items() if r in v)
        print(f"| `{r['name']}` | {g} | {actual_str(r)} | {key} |")


def report_md(rows):
    print()
    print("<!-- 由 tools/err_bench.py --md 生成，可直接贴进报告 -->")
    print("| # | 文件 | 分类 | 期望诊断 | 实际诊断 | 三态 |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {int(r['num'])} | `{r['name']}` | {r['category']} | "
              f"{expect_str(r)} | {actual_str(r)} | {ICON[r['state']]} {r['state']} |")


# ---------------------------------------------------------------- 入口

def main(argv):
    verbose = "-v" in argv or "--verbose" in argv
    vs_gcc = "--vs-gcc" in argv
    as_md = "--md" in argv
    only = None
    if "--only" in argv:
        i = argv.index("--only")
        if i + 1 < len(argv):
            only = [x.strip() for x in argv[i + 1].split(",") if x.strip()]

    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    rows = collect(only, verbose, timeout=60)
    if not rows:
        print("没有找到 examples/err_*.c 用例。")
        return 2
    if as_md:
        report_md(rows)
        if vs_gcc:
            report_gcc_md(rows)
    else:
        report_plain(rows, show_gcc=vs_gcc)

    crashed = [r for r in rows if r["state"] == CRASH]
    if crashed:
        print(f"\n💥 {len(crashed)} 个用例让 csub 崩溃，这是底线问题：")
        for r in crashed:
            print(f"   {r['name']}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
