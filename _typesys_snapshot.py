"""一次性脚本（临时）：把所有 examples/*.c 的诊断快照成 JSON，用于改前/改后对比误报。

不属于产品代码，交付前会删掉。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.setrecursionlimit(10000)

from preprocess import preprocess
from lexer import tokenize, LexError
from parser import parse, ParseError
from semantic import analyze


def snapshot(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    pr = preprocess(raw)
    try:
        tokenize(pr.text)
    except LexError as e:
        return [["error", "LEX", e.line, e.col, e.msg]]
    try:
        prog = parse(pr.text)
    except ParseError as e:
        return [["error", "SYN", e.line, e.col, str(e)]]
    except RecursionError:
        return [["error", "RECURSION", 0, 0, ""]]
    return [[d.level, d.code, pr.to_orig_line(d.line), d.col, d.msg]
            for d in analyze(prog)]


def main():
    out = sys.argv[1]
    res = {}
    d = os.path.join(ROOT, "examples")
    for f in sorted(os.listdir(d)):
        if not f.endswith(".c"):
            continue
        res[f] = snapshot(os.path.join(d, f))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=0, sort_keys=True)
    n = sum(len(v) for v in res.values())
    print(f"{len(res)} files, {n} diags -> {out}")


if __name__ == "__main__":
    main()
