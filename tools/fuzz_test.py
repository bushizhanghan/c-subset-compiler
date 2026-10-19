#!/usr/bin/env python3
"""模糊测试：验证 csub 在任意输入下不崩溃、不死循环。

为什么需要这个工具：
  编译器前端是"喂什么吃什么"的组件——输入来自用户的 .c 文件，
  不可能是精心构造的。词法/语法/语义/IR 四层里任何一处假设
  （"这里一定有下一个 token""这里一定是数字""括号一定闭合"）
  被随机输入打破，学生看到的就是 Python 的 traceback，
  而不是"你的代码第几行有问题"。这是工具可信度的底线。

不变量（任何输入都必须满足）：
  I1  不崩溃：preprocess / tokenize / parse / analyze / generate
      要么正常返回，要么只抛 LexError 或 ParseError。
      抛出 IndexError / KeyError / AttributeError / TypeError /
      ValueError / RecursionError / ZeroDivisionError 等均记为缺陷。
  I2  不死循环：每个输入必须在 --timeout（默认 5 秒）内结束。

覆盖三类输入（对应任务要求 a/b/c）：
  (a) mutate   对 examples/ 下真实 .c 文件做字节级变异
               插入 / 删除 / 替换 / 截断 / 翻转 / 片段复制
  (b) tokens   随机生成"接近 C 语法"的 token 序列
               （纯 token 汤 + 括号配平 + 语句模板 + 随机破坏）
  (c) boundary 边界输入：空串、超长行、超深嵌套、只有注释、
               非法 UTF-8、非法字节、BOM、NUL、宏递归……

隔离方式：
  默认每个用例在**独立子进程**里跑（--jobs 并行）。这样做的理由：
    1) 超时是真的能杀掉进程，真正验证"不死循环"；
    2) RecursionError 之后解释器状态可能已不可靠，隔离避免污染后续用例；
    3) 子进程被强杀也能被记录为 HANG，而不是把整个 fuzz 挂死。

每个崩溃用例记录：
  最小复现输入（做 ddmin 块级 + 逐字节两轮最小化）、
  异常类型、完整 traceback（超长时保留头尾）、
  以及出错的源文件名与行号（取 src/ 里最深的一帧）。

用法:
  python tools/fuzz_test.py                      # 默认跑一轮（约 700 例）
  python tools/fuzz_test.py --quick              # 快速冒烟
  python tools/fuzz_test.py --seed 12345         # 复现某一轮
  python tools/fuzz_test.py --only mutate        # 只跑某一类
  python tools/fuzz_test.py --no-minimize        # 跳过最小化
  python tools/fuzz_test.py -v                   # 逐例打印结果

退出码: 0 = 无缺陷；1 = 发现崩溃或死循环。

注意：本工具只读 src/ 与 examples/，不会修改它们；
      产物写到 tools/fuzz_artifacts/（--artifacts 可改）。
"""
import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
EXAMPLES = os.path.join(ROOT, "examples")
DEFAULT_ARTIFACTS = os.path.join(ROOT, "tools", "fuzz_artifacts")

sys.path.insert(0, SRC)

try:
    from preprocess import preprocess                     # noqa: E402
    from lexer import tokenize, LexError                  # noqa: E402
    from parser import parse, ParseError                  # noqa: E402
    from semantic import analyze                          # noqa: E402
    from ir import generate                               # noqa: E402
except Exception as _import_err:                          # noqa: BLE001
    # src/ 被改坏时（比如并行改动写到一半），fuzz 本身也要给出可读的原因，
    # 而不是甩一段 ImportError traceback 让使用者以为是模糊测试器坏了。
    sys.stderr.write(
        "无法导入 csub 前端模块，模糊测试无法进行。\n"
        f"  导入路径: {SRC}\n"
        f"  原因: {type(_import_err).__name__}: {_import_err}\n"
        "  这通常说明 src/ 当前有语法错误或缺少依赖（可能是并行改动未完成）。\n")
    raise SystemExit(2)

#: 允许抛出的异常：词法错误与语法错误是"预期的拒绝"。
EXPECTED = (LexError, ParseError)

#: 检查顺序即各阶段名，报告里用它定位问题出在哪一层。
PHASES = ("preprocess", "tokenize", "parse", "semantic", "ir")

SRC_ABS = os.path.normcase(os.path.abspath(SRC)) + os.sep


# ===========================================================================
# 一、执行核心（在 worker 子进程 / 最小化子进程里都跑这段）
# ===========================================================================

def _decode(raw: bytes) -> str:
    """字节 -> 源码字符串。

    用 surrogateescape：真实的 .c 文件里混进 GBK 残留、乱码字节很常见，
    我们要测的是"前端拿到这些字符会不会炸"，而不是在解码这一步判死。
    """
    return raw.decode("utf-8", "surrogateescape")


def _frame_loc(tb) -> dict:
    """从 traceback 里挑出 src/ 下最深的一帧 —— 那才是真正出错的位置。"""
    best = None
    tb_walk = tb
    while tb_walk is not None:
        code = tb_walk.tb_frame.f_code
        fn = os.path.normcase(os.path.abspath(code.co_filename))
        if fn.startswith(SRC_ABS):
            rel = os.path.basename(fn)
            line = tb_walk.tb_lineno
            best = {
                "file": rel,
                "line": line,
                "func": code.co_name,
                "text": _line_text(code.co_filename, line),
            }
        tb_walk = tb_walk.tb_next
    return best


_LINE_CACHE = {}


def _line_text(path, lineno) -> str:
    key = (path, lineno)
    if key in _LINE_CACHE:
        return _LINE_CACHE[key]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if i == lineno:
                    _LINE_CACHE[key] = line.rstrip("\n")
                    return _LINE_CACHE[key]
    except OSError:
        pass
    _LINE_CACHE[key] = ""
    return ""


def _format_tb(exc, head=6, tail=24) -> str:
    """完整 traceback，但超长时折叠中间部分。

    递归溢出会有近千帧，原样打印既读不完也撑爆报告文件；
    头尾各留一段足以同时看清"入口"与"出错的循环点"。
    """
    try:
        lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    except Exception:                     # traceback 自身出错也不该拖垮 fuzz
        return f"<{type(exc).__name__}: {exc}>"
    text = "".join(lines)
    parts = text.splitlines()
    if len(parts) <= head + tail:
        return text
    omitted = len(parts) - head - tail
    mid = f"  ... [省略 {omitted} 行 traceback 帧] ..."
    return "\n".join(parts[:head] + [mid] + parts[-tail:])


def run_pipeline(src: str) -> dict:
    """按顺序跑完整前端，返回结构化结果。

    kind:
      ok       —— 全部阶段正常返回
      reject   —— 只抛了 LexError / ParseError（合规结果）
      crash    —— 抛了其它异常（缺陷）
    """
    t0 = time.perf_counter()
    phase = PHASES[0]
    try:
        pr = preprocess(src)

        phase = "tokenize"
        tokenize(pr.text)

        phase = "parse"
        prog = parse(pr.text)

        phase = "semantic"
        analyze(prog)

        phase = "ir"
        generate(prog)

    except EXPECTED as e:
        return {
            "kind": "reject", "phase": phase,
            "exc_type": type(e).__name__, "exc_msg": str(e),
            "tb": "", "loc": None,
            "duration": time.perf_counter() - t0,
            "detail": f"{getattr(e, 'line', '?')}:{getattr(e, 'col', '?')}",
        }
    except BaseException as e:            # noqa: BLE001 —— 这里就是要抓全部
        return {
            "kind": "crash", "phase": phase,
            "exc_type": type(e).__name__, "exc_msg": str(e),
            "tb": _format_tb(e), "loc": _frame_loc(e.__traceback__),
            "duration": time.perf_counter() - t0, "detail": "",
        }

    return {
        "kind": "ok", "phase": "done",
        "exc_type": None, "exc_msg": "", "tb": "", "loc": None,
        "duration": time.perf_counter() - t0, "detail": "",
    }


def coarse_signature(res: dict) -> str:
    """缺陷的粗签名：用于给崩溃分组、以及最小化时判断"还是同一个 bug"。

    刻意不含行号与异常消息——最小化会改变上下文，
    消息里也常常嵌着具体输入字符，那样永远配不上。
    只保留：阶段 + 异常类型 + 出错源文件 + 出错函数。
    """
    if res.get("kind") == "hang":
        return "HANG"
    loc = res.get("loc") or {}
    return "{}|{}|{}|{}".format(
        res.get("phase", "?"), res.get("exc_type", "?"),
        loc.get("file", "?"), loc.get("func", "?"))


# ===========================================================================
# 二、隔离执行：每个用例一个子进程 + 硬超时
# ===========================================================================

def _worker_main() -> int:
    """子进程入口：从 stdin 读一个 base64 用例，跑一遍，输出 JSON 结果行。"""
    payload = json.loads(sys.stdin.read())
    raw = _unb64(payload["raw"])
    res = run_pipeline(_decode(raw))
    sys.stdout.write(json.dumps(res, ensure_ascii=True))
    sys.stdout.flush()
    return 0


def _b64(raw: bytes) -> bytes:
    import base64
    return base64.b64encode(raw)


def _unb64(s) -> bytes:
    import base64
    if isinstance(s, str):
        s = s.encode("ascii")
    return base64.b64decode(s)


def run_isolated(raw: bytes, timeout: float) -> dict:
    """在独立子进程里跑一个用例。

    返回 kind ∈ {ok, reject, crash, hang, died}。
      hang —— 超时未结束（死循环），子进程已被杀掉；
      died —— 子进程非 0 退出或没给出结果（进程级崩溃）。
    """
    payload = json.dumps({"raw": _b64(raw).decode("ascii")}).encode("ascii")
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--_worker"],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"kind": "hang", "phase": "?", "exc_type": "HANG",
                "exc_msg": f"超过 {timeout:g} 秒未结束，疑似死循环",
                "tb": "", "loc": None,
                "duration": timeout, "detail": ""}

    wall = time.perf_counter() - t0
    out = proc.stdout.decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not out:
        err = proc.stderr.decode("utf-8", "replace")
        return {"kind": "died", "phase": "?", "exc_type": f"exit{proc.returncode}",
                "exc_msg": (err.strip().splitlines() or ["子进程异常退出"])[-1][:300],
                "tb": err[-4000:], "loc": None,
                "duration": wall, "detail": ""}
    try:
        res = json.loads(out)
    except ValueError:
        return {"kind": "died", "phase": "?", "exc_type": "badresult",
                "exc_msg": out[:300], "tb": "", "loc": None,
                "duration": wall, "detail": ""}
    res["duration"] = wall
    return res


def run_batch(cases, timeout: float, jobs: int, verbose: bool):
    """并行跑一批用例（每个用例内部仍是独立子进程 + 超时）。"""
    from concurrent.futures import ThreadPoolExecutor

    results = [None] * len(cases)
    done = 0
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = {ex.submit(run_isolated, c["raw"], timeout): i
                for i, c in enumerate(cases)}
        for fut, i in list(futs.items()):
            try:
                results[i] = fut.result()
            except Exception as e:                       # noqa: BLE001
                results[i] = {"kind": "died", "phase": "?", "exc_type": "harness",
                              "exc_msg": f"调度异常: {e}", "tb": "", "loc": None,
                              "duration": 0.0, "detail": ""}
            done += 1
            if verbose:
                c = cases[i]
                r = results[i]
                flag = {"ok": "OK  ", "reject": "REJ ", "crash": "CRASH",
                        "hang": "HANG", "died": "DIED"}.get(r["kind"], "???")
                print(f"  [{done:4d}/{len(cases)}] {flag} {c['kind']:8s} "
                      f"{c['label'][:44]:44s} {r['duration']:6.3f}s "
                      f"{r['phase']}/{r['exc_type'] or '-'}")
    return results, time.perf_counter() - t0


# ===========================================================================
# 三、最小化（在独立子进程里做，父进程再加一层超时保护）
# ===========================================================================

class _Prober:
    """在最小化子进程内部跑候选输入。

    为什么用线程 + join(timeout)：Windows 上没有 SIGALRM，
    没法在主线程里中断一个跑飞的候选。用守护线程兜住，
    候选超时就判成 hang 并放弃（泄漏的那条线程随子进程一起被父进程杀掉）。
    """

    def __init__(self, timeout: float, max_leaks: int = 4):
        self.timeout = timeout
        self.max_leaks = max_leaks
        self.leaks = 0
        self.probes = 0
        self.budget = 6000

    def __call__(self, raw: bytes):
        if self.probes >= self.budget or self.leaks >= self.max_leaks:
            return None
        self.probes += 1
        box = {}

        def target():
            try:
                box["res"] = run_pipeline(_decode(raw))
            except BaseException as e:                    # noqa: BLE001
                box["res"] = {"kind": "crash", "phase": "?",
                              "exc_type": type(e).__name__, "exc_msg": str(e),
                              "loc": None}

        th = threading.Thread(target=target, daemon=True)
        th.start()
        th.join(self.timeout)
        if th.is_alive():
            self.leaks += 1
            return {"kind": "hang", "phase": "?", "exc_type": "HANG",
                    "loc": None, "exc_msg": "timeout"}
        return box.get("res")


def _minimize_main() -> int:
    payload = json.loads(sys.stdin.read())
    raw = _unb64(payload["raw"])
    target = payload["signature"]
    timeout = payload.get("timeout", 5.0)
    prober = _Prober(timeout)

    def interesting(cand: bytes) -> bool:
        res = prober(cand)
        if res is None:
            return False
        return coarse_signature(res) == target

    minimized = ddmin(raw, interesting)
    sys.stdout.write(json.dumps({"minimized": _b64(minimized).decode("ascii"),
                                 "probes": prober.probes,
                                 "leaks": prober.leaks}))
    sys.stdout.flush()
    os._exit(0)                 # 别等那些泄漏的守护线程


def _drop_chunks(data: bytes, indices) -> bytes:
    drop = set(indices)
    return bytes(b for i, b in enumerate(data) if i not in drop)


def ddmin(data: bytes, interesting) -> bytes:
    """两轮最小化：先按块删（ddmin），再逐字节删到不可再缩。

    ddmin 的块级删除能把 47KB 的文件砍到几十字节而只花 O(n log n) 次探测，
    再补一轮逐字节删除收拾边界，结果就是"再删一个字符就不再崩溃"。
    """
    best = data
    n = 2
    while len(best) >= 2:
        chunk = max(1, len(best) // n)
        idx = [i for i in range(0, len(best), chunk)]
        reduced = False
        for start in idx:
            cand = bytes(best[:start]) + bytes(best[start + chunk:])
            if cand and interesting(cand):
                best = cand
                n = max(n - 1, 2)
                reduced = True
                break
        if not reduced:
            if chunk == 1:
                break
            n = min(n * 2, len(best))

    # 逐字节删除：保证"再删一个字符就不崩了"
    changed = True
    while changed:
        changed = False
        for i in range(len(best)):
            cand = best[:i] + best[i + 1:]
            if cand and interesting(cand):
                best = cand
                changed = True
                break
    return best


def minimize_isolated(raw: bytes, signature: str, timeout: float,
                      max_seconds: float = 180.0):
    """父进程侧调用：开一个最小化子进程，失败就返回原输入。"""
    payload = json.dumps({
        "raw": _b64(raw).decode("ascii"),
        "signature": signature,
        "timeout": timeout,
    }).encode("ascii")
    try:
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--_minimize"],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=max_seconds,
        )
    except subprocess.TimeoutExpired:
        return raw, 0, "最小化超时，保留原始复现输入"
    out = proc.stdout.decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not out:
        return raw, 0, "最小化子进程异常，保留原始复现输入"
    try:
        info = json.loads(out)
    except ValueError:
        return raw, 0, "最小化输出无法解析，保留原始复现输入"
    return _unb64(info["minimized"]), info.get("probes", 0), ""


# ===========================================================================
# 四、用例生成
# ===========================================================================

def load_examples():
    """读 examples/ 下的真实 .c 文件作为变异种子（只读）。"""
    blobs = []
    if not os.path.isdir(EXAMPLES):
        return blobs
    for name in sorted(os.listdir(EXAMPLES)):
        if not name.endswith(".c"):
            continue
        path = os.path.join(EXAMPLES, name)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        blobs.append((name, data))
    return blobs


#: 变异时优先插入这些"对前端有特殊含义"的字节。
HOT_BYTES = (b'{}()[];,.*&<>=-+/%!~^|?:#\'"\\\n\t\r 0123456789'
             b'abcdefxyzABCDEFXYZ_eEfFlLuU\x00\xff\x80\xc3')


def _rand_span(rng, n, lo=1, hi=None):
    hi = hi or max(2, n // 20)
    ln = rng.randint(lo, max(lo, hi))
    ln = min(ln, n)
    start = rng.randrange(0, max(1, n - ln + 1))
    return start, start + ln


def _mutate_once(rng, data: bytes) -> bytes:
    """一次字节级变异。刻意不保证变异后还是合法 UTF-8 / 合法 C。"""
    op = rng.choices(
        ["insert", "delete", "replace", "truncate", "flip", "dup", "hot"],
        weights=[30, 20, 18, 12, 8, 7, 5])[0]
    n = len(data)
    if op == "insert" and n:
        pos = rng.randrange(0, n + 1)
        ln = rng.randint(1, 8)
        ins = bytes(rng.choice(HOT_BYTES) for _ in range(ln))
        return data[:pos] + ins + data[pos:]
    if op == "delete" and n > 1:
        a, b = _rand_span(rng, n)
        return data[:a] + data[b:]
    if op == "replace" and n:
        a, b = _rand_span(rng, n)
        ins = bytes(rng.choice(HOT_BYTES) for _ in range(b - a))
        return data[:a] + ins + data[b:]
    if op == "truncate" and n:
        return data[:rng.randrange(0, n)]
    if op == "flip" and n:
        pos = rng.randrange(0, n)
        return data[:pos] + bytes([rng.randrange(0, 256)]) + data[pos + 1:]
    if op == "dup" and n > 4:
        a, b = _rand_span(rng, n, 1, max(2, n // 10))
        pos = rng.randrange(0, n + 1)
        return data[:pos] + data[a:b] + data[pos:]
    # hot：插入一个有语义的片段，最容易撞出解析器边界
    piece = rng.choice([
        b"/*", b"*/", b"//", b'"', b"'", b"\\", b"#define X ",
        b"#ifndef X\n", b"#endif\n", b"#include <a.h>\n",
        b"((", b"))", b"{{", b"}}", b"[", b"]", b"->", b"...",
        b"\xc3\x28", b"\xff\xfe", b"\x00", b"\xef\xbb\xbf",
    ])
    pos = rng.randrange(0, n + 1)
    return data[:pos] + piece + data[pos:]


def gen_mutations(rng, count, blobs):
    """类别 (a)：对真实 .c 文件做随机字节级变异。

    偏向小文件：大文件跑一遍前端就几十毫秒，会拖慢整轮 fuzz；
    但保留少量大文件变异（占比 1/8），因为长输入更容易撞出性能问题。
    """
    small = [b for b in blobs if len(b[1]) <= 8000]
    big = [b for b in blobs if len(b[1]) > 8000]
    cases = []
    for i in range(count):
        pool = big if (big and rng.random() < 0.125) else (small or blobs)
        name, data = rng.choice(pool)
        data = bytearray(data)
        nops = rng.choices([1, 2, 3, 5], weights=[55, 25, 15, 5])[0]
        try:
            for _ in range(nops):
                data = _mutate_once(rng, bytes(data))
        except Exception:                                # noqa: BLE001
            data = bytes(data)
        raw = bytes(data)
        if len(raw) > 200_000:                  # 兜底：别让单例跑太久
            raw = raw[:200_000]
        cases.append({
            "kind": "mutate", "raw": raw,
            "label": f"{name}#{i}({nops}次变异,{len(raw)}B)",
        })
    return cases


#: 接近 C 语法的 token 池。分类是为了让生成的序列"看起来像代码"。
KW = ["int", "char", "float", "double", "void", "if", "else", "while",
      "for", "return", "break", "continue", "sizeof", "struct", "const",
      "static", "typedef", "unsigned", "switch", "case", "default", "do"]
IDS = ["main", "i", "j", "n", "a", "b", "x", "p", "arr", "s", "Node",
       "head", "next", "data", "f", "g", "len", "buf", "tmp", "total"]
OPS = ["+", "-", "*", "/", "%", "=", "==", "!=", "<", ">", "<=", ">=",
       "&&", "||", "!", "++", "--", "+=", "-=", "<<", ">>", "&", "|", "^",
       "~", "?", ":", "->", "."]
PUNCT = [";", ",", "(", ")", "{", "}", "[", "]"]
NUMS = ["0", "1", "2", "42", "0x1F", "3.14", "1e9", "0.5f", "100L", "07"]


def _tok_pool():
    pool = []
    pool += [(t, 10) for t in KW]
    pool += [(t, 14) for t in IDS]
    pool += [(t, 8) for t in OPS]
    pool += [(t, 12) for t in PUNCT]
    pool += [(t, 8) for t in NUMS]
    pool += [('"str"', 4), ("'c'", 3), ("/*c*/", 2), ("//c\n", 2),
             ("#define A 1\n", 2), ("#include <a.h>\n", 2)]
    return pool


TOK_POOL = _tok_pool()


def _pick(rng, pool=None):
    pool = pool or TOK_POOL
    total = sum(w for _, w in pool)
    r = rng.randrange(total)
    acc = 0
    for tok, w in pool:
        acc += w
        if r < acc:
            return tok
    return pool[-1][0]


def _gen_token_soup(rng) -> str:
    n = rng.randint(3, 60)
    sep_choices = [(" ", 6), ("", 3), ("\n", 3), ("  ", 1), ("\t", 1)]
    out = []
    for _ in range(n):
        out.append(_pick(rng))
        out.append(_pick(rng, sep_choices))
    return "".join(out)


def _balanced(rng, depth=0) -> str:
    """生成带配平括号的片段，一半概率递归。"""
    if depth >= 3 or rng.random() < 0.35:
        return _pick(rng, [(t, w) for t, w in TOK_POOL
                           if t not in ("}", "{", ")", "(", "[", "]")])
    open_, close = rng.choice([("(", ")"), ("{", "}"), ("[", "]")])
    inner = "".join(_balanced(rng, depth + 1)
                    for _ in range(rng.randint(0, 3)))
    return open_ + inner + close


def _gen_template(rng) -> str:
    """语句模板：先按 C 的骨架拼，再以一定概率破坏它。"""
    expr = lambda: "".join([_pick(rng, [(t, w) for t, w in TOK_POOL[:40]])
                            for _ in range(rng.randint(1, 5))])
    stmt_templates = [
        "int {i} = {e};",
        "{i} = {e};",
        "if ({e}) {{ {s} }}",
        "if ({e}) {{ {s} }} else {{ {s} }}",
        "while ({e}) {{ {s} }}",
        "for ({i} = 0; {i} < {e}; {i}++) {{ {s} }}",
        "return {e};",
        "printf({e});",
        "{i}[{e}] = {e};",
        "struct S {{ int a; {t} b; }};",
        "int {i}[{n}];",
        "do {{ {s} }} while ({e});",
        "switch ({e}) {{ case 1: {s} break; default: {s} }}",
        "{i}.{i} = {e};",
        "{i}->{i} = {e};",
        "typedef struct {{ int a; }} {i};",
        "/* {i} */ {s}",
        "sizeof({e});",
    ]
    body = []
    for _ in range(rng.randint(1, 5)):
        tpl = rng.choice(stmt_templates)
        body.append(tpl.format(
            i=rng.choice(IDS), t=rng.choice(KW), n=rng.choice(NUMS),
            e=expr(),
            s=rng.choice(["", "break;", "continue;", "return 0;",
                          "{ }", "int x = 1;"]),
        ))
    src = "int main() {\n  " + "\n  ".join(body) + "\n}\n"
    if rng.random() < 0.40:              # 破坏：随机删/换一个 token
        toks = src.split(" ")
        if toks:
            k = rng.randrange(len(toks))
            if rng.random() < 0.5:
                del toks[k]
            else:
                toks[k] = _pick(rng)
            src = " ".join(toks)
    return src


#: 预置的结构片段：单靠 token 汤很难生成"能进语义层"的代码，
#: 而语义层的崩溃占了一半以上，必须专门喂它。
PROGRAM_SEEDS = [
    "int main() { return 0; }",
    "struct node { int data; struct node *next; };\n"
    "typedef struct node Node;\n"
    "int main() { Node *h; h->next = 0; return h->data; }",
    "typedef struct { int a; char b; } T;\n"
    "int main() { T t; t.a = 1; return t.a; }",
    "int f(int a, int b) { return a + b; }\n"
    "int main() { return f(1); }",
    "int main() { int a[3] = {1,2,3}; int i; "
    "for (i = 0; i < 3; i++) { a[i] = a[i] * 2; } return a[0]; }",
    "int main() { int *p; int a = 1; p = &a; *p = 2; return *p; }",
    "int main() { int i = 0; while (i < 10) { i++; } return i; }",
    "int main() { int a; a = 1; return a / 0; }",
    "typedef int (*FN)(int);\nint g(int x) { return x; }\n"
    "int main() { FN p; p = g; return p(1); }",
    "int main() { switch (1) { case 1: return 1; default: break; } }",
    "int main() { char *s = \"abc\"; return sizeof(int *); }",
    "enum E { A, B };\nint main() { return A; }",
    "int main() { goto end; end: return 0; }",
]


def gen_tokens(rng, count):
    """类别 (b)：随机生成接近 C 语法的 token 序列。"""
    cases = []
    for i in range(count):
        flavor = rng.choices(["soup", "balanced", "template", "seedmutate"],
                             weights=[30, 20, 30, 20])[0]
        if flavor == "soup":
            src = _gen_token_soup(rng)
        elif flavor == "balanced":
            src = "int main() { " + "".join(
                _balanced(rng) for _ in range(rng.randint(1, 8))) + " }"
        elif flavor == "template":
            src = _gen_template(rng)
        else:
            base = rng.choice(PROGRAM_SEEDS).encode("utf-8")
            data = base
            for _ in range(rng.randint(1, 3)):
                data = _mutate_once(rng, data)
            src = _decode(data)
        cases.append({"kind": "tokens", "raw": src.encode("utf-8",
                                                          "surrogateescape"),
                      "label": f"{flavor}#{i}"})
    return cases


def gen_boundary():
    """类别 (c)：边界输入。每条都是真实会遇到的极端情形。"""
    cases = []

    def add(label, raw):
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "surrogateescape")
        cases.append({"kind": "boundary", "raw": raw, "label": label})

    # ---- 空与纯空白 ----
    add("empty", b"")
    add("space", b" ")
    add("newlines", b"\n\n\n")
    add("crlf_only", b"\r\n\r\n")
    add("tabs_only", b"\t\t\t")

    # ---- 只有注释 ----
    add("line_comment_only", b"// just a comment")
    add("block_comment_only", b"/* abc */")
    add("comment_unclosed", b"/* abc")
    add("comment_with_star_slash", b"/*/")
    add("comment_then_eof", b"int main(){ /* x */ ")

    # ---- 未闭合的词法单元 ----
    add("string_unclosed", b'int main(){ char *s = "abc; }')
    add("string_backslash_eof", b'int main(){ char *s = "abc\\')
    add("char_unclosed", b"int main(){ return 'a; }")
    add("char_empty", b"int main(){ return ''; }")
    add("char_backslash_eof", b"int main(){ return '\\")

    # ---- 数字/字面量的边界 ----
    add("hex_no_digits", b"int main(){ return 0x; }")
    add("hex_upper_no_digits", b"int main(){ return 0X; }")
    add("big_int_4400_digits", b"int main(){ return " + b"9" * 4400 + b"; }")
    add("big_int_9000_digits", b"int main(){ return " + b"9" * 9000 + b"; }")
    add("float_dot_only", b"int main(){ return 1.; }")
    add("float_exp_only", b"int main(){ return 1e; }")
    add("float_exp_sign", b"int main(){ return 1e+; }")
    add("dot_leading", b"int main(){ return .5; }")
    add("huge_float_exp", b"int main(){ return 1e999999; }")
    add("num_suffix_garbage", b"int main(){ return 1ululu; }")

    # ---- 超长单行 / 超长标识符 / 超长字符串 ----
    add("long_line_120k",
        b"int main(){ int a = 1; " + b"a = a + 1; " * 10000 + b"return a; }")
    add("long_ident_80k", b"int main(){ int " + b"a" * 80000 + b" = 1; }")
    add("long_string_120k",
        b'int main(){ char *s = "' + b"x" * 120000 + b'"; }')

    # ---- 超深嵌套（递归深度）----
    for n in (10, 60, 120, 200, 500, 1000):
        add(f"deep_paren_{n}",
            b"int main(){ return " + b"(" * n + b"1" + b")" * n + b"; }")
        add(f"deep_brace_{n}", b"int main(){" + b"{" * n + b"}" * n + b"}")
    for n in (100, 400, 1000):
        add(f"deep_unary_{n}", b"int main(){ return " + b"-!" * n + b"1; }")
        add(f"deep_member_{n}", b"int main(){ return a" + b".b" * n + b"; }")
        add(f"deep_call_{n}", b"int main(){ return " + b"f(" * n + b"1"
            + b")" * n + b"; }")
        add(f"deep_index_{n}", b"int main(){ return a" + b"[0]" * n + b"; }")
        add(f"deep_ternary_{n}",
            b"int main(){ return " + b"1?" * n + b"1" + b":0" * n + b"; }")
        add(f"deep_if_{n}", b"int main(){ " + b"if(1)" * n
            + b" return 1; }")
        add(f"deep_ptr_{n}", b"int main(){ int " + b"*" * n + b"p; }")

    # ---- 控制流 / 声明的畸形组合 ----
    add("lonely_brace_open", b"{")
    add("lonely_brace_close", b"}")
    add("lonely_paren_open", b"(")
    add("lonely_paren_close", b")")
    add("lonely_semicolons", b";;;;")
    add("keyword_only_int", b"int")
    add("keyword_only_struct", b"struct")
    add("keyword_only_typedef", b"typedef")
    add("struct_brace_only", b"struct {")
    add("struct_no_name", b"struct ;")
    add("else_without_if", b"int main(){ else { } return 0; }")
    add("case_outside_switch", b"int main(){ case 1: return 0; }")
    add("default_outside_switch", b"int main(){ default: return 0; }")
    add("break_outside_loop", b"int main(){ break; return 0; }")
    add("continuation_after_continue", b"int main(){ continue; }")
    add("goto_no_label", b"int main(){ goto ; }")
    add("arrow_no_member", b"int main(){ a->; }")
    add("member_no_name", b"int main(){ a.; }")
    add("assign_no_rhs", b"int main(){ a = ; }")
    add("ternary_no_branch", b"int main(){ return 1 ? : 2; }")
    add("sizeof_empty", b"int main(){ return sizeof(); }")
    add("cast_incomplete", b"int main(){ return (int); }")
    add("empty_array_size", b"int main(){ int a[]; return 0; }")
    add("negative_array_size", b"int main(){ int a[-1]; return 0; }")
    add("zero_array_size", b"int main(){ int a[0]; return 0; }")
    add("huge_array_size", b"int main(){ int a[999999999999999999999]; }")
    add("func_no_body", b"int f(int a);")
    add("varargs_proto", b"int f(int, ...); int main(){ return 0; }")
    add("nested_func_def", b"int a(){ int b(){ return 1; } return 0; }")
    add("dup_struct_member", b"struct S { int a; int a; }; int main(){}")

    # ---- 预处理层 ----
    add("define_only", b"#define A 1")
    add("define_no_name", b"#define")
    add("define_self_ref", b"#define X X\nint main(){ return X; }")
    add("define_mutual", b"#define A B\n#define B A\nint main(){ return A; }")
    add("define_fn_unbalanced", b"#define F(a) a)\nint main(){ return F(1; }")
    add("define_fn_missing_args", b"#define F(a,b) a\nint main(){ return F(1); }")
    add("include_weird", b'#include <>\n#include ""\nint main(){ return 0; }')
    add("if_endif_unbalanced", b"#ifdef A\nint main(){ return 0; }\n")
    add("endif_alone", b"#endif\nint main(){ return 0; }")
    add("else_alone", b"#else\nint main(){ return 0; }")
    add("elif_alone", b"#elif X\nint main(){ return 0; }")
    add("nested_ifndef_500",
        b"".join(b"#ifndef G%d\n" % i for i in range(500))
        + b"int main(){ return 0; }\n"
        + b"".join(b"#endif\n" for _ in range(500)))
    add("backslash_at_eof", b"int main(){ return 0; }\\")
    add("backslash_continuation_all",
        b"\\\n" * 100 + b"int main(){ return 0; }")
    add("define_huge_body", b"#define BIG " + b"1+" * 5000 + b"1\n"
        b"int main(){ return BIG; }")
    add("macro_many_calls",
        b"#define F(a) (a)\nint main(){ int x = " + b"F(1)+" * 2000
        + b"0; return x; }")
    add("undef_nothing", b"#undef\nint main(){ return 0; }")
    add("unknown_directive", b"#pragma once\n#error boom\nint main(){}")

    # ---- 编码/字节层面的边界 ----
    add("bom_only", b"\xef\xbb\xbf")
    add("bom_then_code", b"\xef\xbb\xbfint main(){ return 0; }")
    add("nul_byte", b"int main(){ return 0; }\x00")
    add("nul_inside", b"int main(){ \x00 return 0; }")
    add("invalid_utf8_ff", b"int main(){ return 0; }\xff")
    add("invalid_utf8_truncated", b"int main(){ /* \xc3")
    add("invalid_utf8_latin1", b"// caf\xe9\nint main(){ return 0; }")
    add("invalid_utf8_overlong", b"int \xc0\xaf main(){ return 0; }")
    add("utf16le_content", "int main(){ return 0; }".encode("utf-16-le"))
    add("ctrl_chars", bytes(range(1, 32)) + b"int main(){}")
    add("del_char", b"int main(){ \x7f return 0; }")
    add("emoji_ident", "int main(){ int 😀 = 1; return 0; }")
    add("cjk_ident", "int main(){ int 变量 = 1; return 变量; }")
    add("cjk_in_string", "int main(){ char *s = \"中文\"; return 0; }")
    add("lone_surrogate", b"int main(){ return 0; }\xed\xa0\x80")

    return cases


# ===========================================================================
# 五、CLI 端到端检查（额外一层：csub.py 自身的健壮性）
# ===========================================================================

def cli_check(raw: bytes, timeout: float) -> dict:
    """把原始字节写成 .c 文件，跑 `python csub.py check`。

    这一层测的是入口脚本：encode/decode、文件读写、诊断渲染。
    与前端不变量分开统计——它证明问题会不会直接暴露给学生。
    """
    tmpdir = tempfile.mkdtemp(prefix="csubfuzz_")
    path = os.path.join(tmpdir, "case.c")
    try:
        with open(path, "wb") as f:
            f.write(raw)
        try:
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "csub.py"),
                 "check", path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, cwd=ROOT,
            )
        except subprocess.TimeoutExpired:
            return {"kind": "hang", "exc_type": "HANG", "tb": "",
                    "exc_msg": f"CLI 超过 {timeout:g} 秒", "loc": None}
        err = proc.stderr.decode("utf-8", "replace")
        # 正常路径：返回 0/1，且 stderr 只有预处理提示，不含 traceback
        if proc.returncode not in (0, 1, 2) or "Traceback (most recent call last)" in err:
            return {"kind": "crash", "exc_type": "CLI",
                    "exc_msg": (err.strip().splitlines() or ["?"])[-1][:300],
                    "tb": err[-4000:], "loc": _cli_loc(err),
                    "returncode": proc.returncode}
        return {"kind": "ok", "exc_type": None, "tb": "", "exc_msg": "",
                "loc": None, "returncode": proc.returncode}
    finally:
        try:
            os.remove(path)
            os.rmdir(tmpdir)
        except OSError:
            pass


def _cli_loc(stderr: str):
    """从 CLI 的 traceback 文本里解析出最后一帧的文件与行号。"""
    loc = None
    last_file = None
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith('File "'):
            try:
                body = line.split('"')[1]
                loc = {"file": os.path.basename(body), "line": 0,
                       "func": "", "text": ""}
                last_file = body
            except IndexError:
                last_file = None
        elif last_file and line.startswith("line ") and "," in line:
            if loc is not None:
                try:
                    loc["line"] = int(line.split()[1])
                except (IndexError, ValueError):
                    pass
    return loc


# ===========================================================================
# 六、报告
# ===========================================================================

KIND_LABEL = {
    "mutate": "examples 变异",
    "tokens": "语法近似 token 序列",
    "boundary": "边界输入",
}


def source_fingerprint():
    """给 src/ 与 csub.py 拍一份哈希快照。

    这不是花活儿：本项目是多人在同一份工作区并行改 src/ 的，
    跑 fuzz 的过程中源码可能被改掉（实测发生过，导致同一用例
    前后两次结果不同）。没有快照，缺陷报告就无法定位到"哪个版本"。
    """
    import hashlib
    snap = {}
    paths = [os.path.join(ROOT, "csub.py")]
    if os.path.isdir(SRC):
        paths += [os.path.join(SRC, n) for n in sorted(os.listdir(SRC))
                  if n.endswith(".py")]
    for p in paths:
        try:
            with open(p, "rb") as f:
                snap[os.path.relpath(p, ROOT).replace("\\", "/")] = \
                    hashlib.sha256(f.read()).hexdigest()[:12]
        except OSError:
            snap[os.path.relpath(p, ROOT)] = "<unreadable>"
    return snap


def diff_fingerprint(before, after):
    changed = [k for k in before if after.get(k) != before[k]]
    changed += [k for k in after if k not in before]
    return sorted(changed)


def _display_raw(raw: bytes, limit=400) -> str:
    """把复现输入渲染成可读的一行文本。

    优先按 UTF-8 解码后给 repr（\n 等不可见字符会转义，便于粘贴复现）；
    非法 UTF-8 就退回 bytes 字面量，避免报告本身就编码崩。
    """
    try:
        shown = repr(raw.decode("utf-8"))
    except UnicodeDecodeError:
        shown = repr(raw)
    if len(shown) <= limit:
        return shown
    return shown[:limit] + f"...[截断，共 {len(raw)} 字节]"


def _wrap(text: str, width=92, indent="      ") -> list:
    return [indent + text[i:i + width] for i in range(0, len(text), width)]


def build_defects(cases, results, timeout):
    """把崩溃/超时按粗签名分组，每组选最短的输入作为代表。"""
    groups = {}
    for case, res in zip(cases, results):
        if res["kind"] in ("ok", "reject"):
            continue
        sig = coarse_signature(res)
        g = groups.setdefault(sig, {
            "signature": sig,
            "phase": res.get("phase"),
            "exc_type": res.get("exc_type"),
            "loc": res.get("loc"),
            "count": 0,
            "cases": [],
        })
        g["count"] += 1
        g["cases"].append((len(case["raw"]), case, res))
    defects = []
    for sig, g in groups.items():
        g["cases"].sort(key=lambda x: x[0])
        _, case, res = g["cases"][0]
        g["smallest"] = {"raw": case["raw"], "label": case["label"],
                         "kind": case["kind"]}
        g["tb"] = res.get("tb", "")
        g["exc_msg"] = res.get("exc_msg", "")
        g.pop("cases")
        defects.append(g)
    defects.sort(key=lambda d: (-d["count"], d["exc_type"] or ""))
    return defects


def print_summary(cases, results, defects, elapsed, timeout, jobs, seed,
                  cli_results, cli_raws, only=None):
    total = len(cases)
    counts = {"ok": 0, "reject": 0, "crash": 0, "hang": 0, "died": 0}
    per_kind = {}
    slowest = (0.0, "")
    for case, res in zip(cases, results):
        k = res["kind"]
        counts[k] = counts.get(k, 0) + 1
        per_kind.setdefault(case["kind"], {"ok": 0, "reject": 0, "bad": 0})
        per_kind[case["kind"]][
            "ok" if k == "ok" else "reject" if k == "reject" else "bad"] += 1
        if res["duration"] > slowest[0]:
            slowest = (res["duration"], f"{case['kind']}/{case['label']}")

    bad = counts["crash"] + counts["hang"] + counts["died"]
    print()
    print("=" * 78)
    print("csub 模糊测试摘要")
    print("=" * 78)
    print(f"随机种子 seed={seed}   隔离=子进程   单例超时={timeout:g}s   "
          f"并行={jobs}")
    if total:
        print(f"总轮数 {total}   总耗时 {elapsed:.1f}s   平均 "
              f"{elapsed / total * 1000:.0f} ms/例")
    print("-" * 78)
    for kind in ("mutate", "tokens", "boundary"):
        if kind not in per_kind:
            continue
        s = per_kind[kind]
        print(f"  {KIND_LABEL[kind]:24s} {sum(s.values()):5d} 例："
              f"正常返回 {s['ok']:5d} | 合规拒绝(Lex/ParseError) "
              f"{s['reject']:5d} | 缺陷 {s['bad']:3d}")
    print("-" * 78)
    print(f"不变量 I1（不崩溃）  : 正常 {counts['ok']} + 合规拒绝 "
          f"{counts['reject']} + 缺陷 {counts['crash']}")
    print(f"不变量 I2（不死循环）: 超时 {counts['hang']}"
          + (f"，进程级异常退出 {counts['died']}" if counts["died"] else ""))
    if slowest[1]:
        print(f"最慢一例: {slowest[0]:.3f}s（上限 {timeout:g}s）  {slowest[1]}")

    if cli_results:
        cli_bad = sum(1 for r in cli_results if r["kind"] != "ok")
        print(f"CLI 端到端（python csub.py check）: {len(cli_results)} 例，"
              f"异常 {cli_bad} 例")

    if not defects:
        print("\n结论：未发现崩溃或死循环，两个不变量均成立。")
        return 0

    # ---- 按异常类型分布 ----
    print("\n" + "=" * 78)
    print(f"缺陷分布：共 {len(defects)} 类，{bad} 例")
    print("=" * 78)
    dist = {}
    for d in defects:
        dist[d["exc_type"]] = dist.get(d["exc_type"], 0) + d["count"]
    for name, n in sorted(dist.items(), key=lambda x: -x[1]):
        locs = [f"{d['loc']['file']}:{d['loc']['line']}"
                for d in defects if d["exc_type"] == name and d["loc"]]
        where = "、".join(sorted(set(locs))) or "-"
        print(f"  {name:16s} {n:4d} 例   位置: {where}")

    # ---- 逐条明细 ----
    for i, d in enumerate(defects, 1):
        print("\n" + "-" * 78)
        loc = d["loc"] or {}
        where = (f"{loc['file']}:{loc['line']} in {loc['func']}()"
                 if loc else "(无 src/ 帧)")
        print(f"[{i}] {d['exc_type']} × {d['count']}   阶段={d['phase']}   "
              f"位置={where}")
        if d.get("minimize_note"):
            print(f"    最小化：{d['minimize_note']}")
        print(f"    最小复现输入 ({len(d['smallest']['raw'])} 字节, "
              f"来自 {d['smallest']['kind']}/{d['smallest']['label']}):")
        for line in _wrap(_display_raw(d["smallest"]["raw"], 600)):
            print(line)
        if loc.get("text"):
            print(f"    出错代码: {loc['text'].strip()}")
        print(f"    异常信息: {d['exc_msg'][:300]}")
        tb_lines = (d["tb"] or "").splitlines()
        if tb_lines:
            print("    traceback（末 12 行）:")
            for line in tb_lines[-12:]:
                print(f"      {line}")
        if d.get("repro_path"):
            print(f"    复现文件: {os.path.relpath(d['repro_path'], ROOT)}")
    print("\n" + "=" * 78)
    return 1

def write_artifacts(defects, outdir, seed, summary, fingerprint=None,
                    changed=None):
    os.makedirs(outdir, exist_ok=True)
    repro_paths = []
    for i, d in enumerate(defects, 1):
        path = os.path.join(outdir, f"crash_{i:02d}_{d['exc_type']}.c")
        with open(path, "wb") as f:
            f.write(d["smallest"]["raw"])
        repro_paths.append(path)
        d["repro_path"] = path

    payload = {
        "seed": seed,
        "source_fingerprint": fingerprint or {},
        "source_changed_during_run": changed or [],
        "summary": summary,
        "defects": [{
            "index": i,
            "signature": d["signature"],
            "phase": d["phase"],
            "exc_type": d["exc_type"],
            "exc_msg": d["exc_msg"],
            "loc": d["loc"],
            "count": d["count"],
            "traceback": d["tb"],
            "minimized_bytes": len(d["smallest"]["raw"]),
            "minimized_repr": _display_raw(d["smallest"]["raw"], 4000),
            "repro_file": os.path.basename(d["repro_path"]),
        } for i, d in enumerate(defects, 1)],
    }
    with open(os.path.join(outdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # 人类可读版：给报告/文档工作直接引用
    lines = ["# csub 模糊测试缺陷清单", "",
             f"- 随机种子: `{seed}`",
             f"- 用例总数: {summary['total']}",
             f"- 缺陷类别: {len(defects)}，缺陷用例: {summary['bad']}",
             f"- 源码快照: " + "、".join(
                 f"`{k}`@{v}" for k, v in (fingerprint or {}).items()
                 if k.startswith("src/")),
             ""]
    if changed:
        lines.insert(3, f"- ⚠ 运行期间 src/ 被改动过: "
                        + "、".join(f"`{c}`" for c in changed))
    for i, d in enumerate(defects, 1):
        loc = d["loc"] or {}
        lines += [f"## {i}. `{d['exc_type']}` @ "
                  f"`{loc.get('file', '?')}:{loc.get('line', '?')}` "
                  f"({d['phase']} 阶段)",
                  "",
                  f"- 命中用例数: {d['count']}",
                  f"- 出错函数: `{loc.get('func', '?')}()`",
                  f"- 出错代码: `{(loc.get('text') or '').strip()}`",
                  f"- 异常信息: `{d['exc_msg'][:200]}`",
                  f"- 最小复现（{len(d['smallest']['raw'])} 字节，"
                  f"见 `{os.path.basename(d['repro_path'])}`）:", "",
                  "```python",
                  _display_raw(d["smallest"]["raw"], 2000),
                  "```", "",
                  "<details><summary>traceback</summary>", "",
                  "```", (d["tb"] or "").strip(), "```", "",
                  "</details>", ""]
    with open(os.path.join(outdir, "crashes.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return repro_paths


# ===========================================================================
# 七、主流程
# ===========================================================================

DEFAULT_COUNTS = {"mutate": 320, "tokens": 320}
QUICK_COUNTS = {"mutate": 60, "tokens": 60}


def build_cases(args):
    rng = random.Random(args.seed)
    blobs = load_examples()
    if not blobs:
        print(f"[warn] examples/ 下没有 .c 文件，跳过变异类：{EXAMPLES}",
              file=sys.stderr)
    if args.cases:
        counts = {"mutate": args.cases, "tokens": args.cases}
    else:
        counts = QUICK_COUNTS if args.quick else DEFAULT_COUNTS
    cases = []
    if args.only in (None, "mutate") and blobs:
        cases += gen_mutations(rng, counts["mutate"], blobs)
    if args.only in (None, "tokens"):
        cases += gen_tokens(rng, counts["tokens"])
    if args.only in (None, "boundary"):
        cases += gen_boundary()
    for i, c in enumerate(cases):
        c["id"] = i
    return cases


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="csub 模糊测试：验证任意输入下不崩溃、不死循环")
    ap.add_argument("--seed", type=int, default=None,
                    help="随机种子（默认随机，报告里会打印出来便于复现）")
    ap.add_argument("--cases", type=int, default=None,
                    help="每类用例数（覆盖默认值）")
    ap.add_argument("--only", choices=["mutate", "tokens", "boundary"],
                    help="只跑某一类输入")
    ap.add_argument("--jobs", type=int, default=min(8, (os.cpu_count() or 4)),
                    help="并行子进程数")
    ap.add_argument("--timeout", type=float, default=5.0,
                    help="单例超时秒数（不变量 I2，默认 5）")
    ap.add_argument("--quick", action="store_true", help="快速冒烟")
    ap.add_argument("--no-minimize", action="store_true", help="跳过最小化")
    ap.add_argument("--no-cli", action="store_true", help="跳过 CLI 端到端检查")
    ap.add_argument("--max-minimize", type=int, default=8,
                    help="最多对几类缺陷做最小化")
    ap.add_argument("--artifacts", default=DEFAULT_ARTIFACTS,
                    help="产物目录（默认 tools/fuzz_artifacts）")
    ap.add_argument("-v", "--verbose", action="store_true", help="逐例打印")
    ap.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--_minimize", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    # 内部模式：不在本进程里跑，而是被父进程当子进程拉起
    if args._worker:
        return _worker_main()
    if args._minimize:
        return _minimize_main()

    if args.seed is None:
        args.seed = random.randrange(1, 10 ** 9)

    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

    print(f"csub 模糊测试  seed={args.seed}  超时={args.timeout:g}s  "
          f"并行={args.jobs}")
    fp_before = source_fingerprint()
    print("源码快照: " + " ".join(f"{k}@{v}" for k, v in fp_before.items()
                                  if k.startswith("src/")))
    cases = build_cases(args)
    if not cases:
        print("没有可用用例。")
        return 2
    print(f"生成 {len(cases)} 个用例，开始跑（子进程隔离，超时即判定死循环）...")

    results, elapsed = run_batch(cases, args.timeout, args.jobs, args.verbose)
    defects = build_defects(cases, results, args.timeout)

    # ---- 最小化 ----
    if defects and not args.no_minimize:
        for d in defects[:args.max_minimize]:
            raw = d["smallest"]["raw"]
            if len(raw) <= 1:
                d["minimize_note"] = "已是单字节，无需最小化"
                continue
            t0 = time.perf_counter()
            new_raw, probes, note = minimize_isolated(
                raw, d["signature"], args.timeout)
            dt = time.perf_counter() - t0
            if note:
                d["minimize_note"] = note
            else:
                d["smallest"]["raw"] = new_raw
                d["minimize_note"] = (f"{len(raw)} -> {len(new_raw)} 字节"
                                      f"（{probes} 次探测, {dt:.1f}s）")
            # 用最小化后的输入重新确认一次（报告只认确认过的复现）
            confirm = run_isolated(d["smallest"]["raw"], args.timeout)
            d["confirmed"] = coarse_signature(confirm) == d["signature"]
            if d["confirmed"]:
                d["loc"] = confirm.get("loc") or d["loc"]
                d["tb"] = confirm.get("tb") or d["tb"]
                d["exc_msg"] = confirm.get("exc_msg") or d["exc_msg"]
                d["phase"] = confirm.get("phase") or d["phase"]

    # ---- CLI 端到端 ----
    cli_results, cli_raws = [], []
    if not args.no_cli:
        picks = []
        if args.only in (None, "boundary"):
            picks += [c for c in cases if c["kind"] == "boundary"]
        picks += [{"raw": d["smallest"]["raw"], "label": d["signature"],
                   "kind": "repro"} for d in defects]
        # 去重，避免同一份复现跑两遍
        seen = set()
        uniq = []
        for c in picks:
            if c["raw"] in seen:
                continue
            seen.add(c["raw"])
            uniq.append(c)
        if uniq:
            from concurrent.futures import ThreadPoolExecutor
            print(f"CLI 端到端检查 {len(uniq)} 例（python csub.py check）...")
            with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
                futs = [ex.submit(cli_check, c["raw"], args.timeout)
                        for c in uniq]
                cli_results = [f.result() for f in futs]
            cli_raws = uniq

    # ---- 汇总 ----
    fp_after = source_fingerprint()
    fp_changed = diff_fingerprint(fp_before, fp_after)
    if fp_changed:
        print(f"\n[!] 运行期间 src/ 被改动过：{'、'.join(fp_changed)}")
        print("    并行的源码修改会让本轮结果的归因变模糊，"
              "缺陷清单里已标注这些文件——复现前请先确认版本一致。")
    summary = {
        "total": len(cases),
        "bad": sum(1 for r in results
                   if r["kind"] in ("crash", "hang", "died")),
        "ok": sum(1 for r in results if r["kind"] == "ok"),
        "reject": sum(1 for r in results if r["kind"] == "reject"),
        "crash": sum(1 for r in results if r["kind"] == "crash"),
        "hang": sum(1 for r in results if r["kind"] == "hang"),
        "dedup_defects": len(defects),
        "elapsed": elapsed,
        "cli_cases": len(cli_results),
        "cli_bad": sum(1 for r in cli_results if r["kind"] != "ok"),
    }
    rc = print_summary(cases, results, defects, elapsed, args.timeout,
                       args.jobs, args.seed, cli_results, cli_raws,
                       only=args.only)

    if cli_results:
        bad_cli = [(c, r) for c, r in zip(cli_raws, cli_results)
                   if r["kind"] != "ok"]
        if bad_cli:
            print("\nCLI 端到端异常（csub.py 入口层）：")
            for c, r in bad_cli[:10]:
                loc = r.get("loc") or {}
                print(f"  - {r['exc_type']} @ "
                      f"{loc.get('file', '?')}:{loc.get('line', '?')}  "
                      f"{r['exc_msg'][:160]}")
                print(f"    输入: {_display_raw(c['raw'], 200)}")
            rc = 1

    repro_paths = write_artifacts(defects, args.artifacts, args.seed, summary,
                                  fingerprint=fp_before, changed=fp_changed)
    print(f"\n产物目录: {os.path.relpath(args.artifacts, ROOT)}"
          f"（report.json / crashes.md / {len(repro_paths)} 个复现 .c）")
    if defects:
        print(f"结论：发现 {len(defects)} 类缺陷，不变量 I1 被破坏"
              "（疑似死循环/进程级异常见 I2 计数）。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
