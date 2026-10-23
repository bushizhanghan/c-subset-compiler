#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""csub 网页版 —— 粘贴代码，看诊断。

零依赖（只用 Python 标准库），一条命令启动：

    python web.py                # 默认 http://127.0.0.1:8777
    python web.py --port 9000    # 换端口
    python web.py --open         # 启动后自动打开浏览器

它是 csub.py 的薄包装：复用同一套 preprocess/lexer/parser/semantic，
不重复实现任何诊断逻辑。
"""
import argparse
import json
import os
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
# tools/ 里的 codegen 供 /api/run 真实编译用
sys.path.insert(0, os.path.join(HERE, "tools"))

from lexer import tokenize, LexError          # noqa: E402
from parser import parse, ParseError          # noqa: E402
from semantic import analyze, Diag            # noqa: E402
from preprocess import preprocess             # noqa: E402

MAX_SRC = 200_000          # 200KB，防止误粘贴大文件把内存打满


def check_text(raw):
    """跑完整前端，返回结构化结果。

    和 csub.py 的 collect_diags 是同一套流程，
    只是输入从文件变成字符串、输出从文本变成 dict。
    """
    if len(raw) > MAX_SRC:
        return {
            "ok": False,
            "diags": [{"level": "error", "code": "E0", "line": 0, "col": 0,
                       "msg": "代码超过 200KB，请精简后再试", "hint": ""}],
            "notes": [], "stage": "io",
        }

    pr = preprocess(raw)
    notes = []
    if pr.includes:
        notes.append("忽略了 %d 个头文件包含：%s"
                     % (len(pr.includes), ", ".join(pr.includes)))
    obj = [n for n, m in pr.macros.items() if not m.is_function_like]
    fun = [n for n, m in pr.macros.items() if m.is_function_like]
    if obj:
        notes.append("展开了 %d 个常量宏：%s"
                     % (len(obj), ", ".join(obj[:8])
                        + ("..." if len(obj) > 8 else "")))
    if fun:
        notes.append("展开了 %d 个函数宏：%s"
                     % (len(fun), ", ".join(fun[:8])
                        + ("..." if len(fun) > 8 else "")))
    for ln, why in pr.unsupported:
        notes.append("第 %d 行未处理：%s" % (ln, why))

    src = pr.text

    def pack(diags, stage):
        out = []
        for d in diags:
            out.append({
                "level": d.level,
                "code": d.code,
                "line": pr.to_orig_line(d.line),
                "col": d.col,
                "msg": d.msg,
                "hint": d.hint or "",
            })
        out.sort(key=lambda x: (x["line"], x["col"]))
        n_err = sum(1 for x in out if x["level"] == "error")
        return {"ok": n_err == 0, "diags": out, "notes": notes,
                "stage": stage}

    try:
        tokenize(src)
    except LexError as e:
        return pack([Diag("error", "LEX", e.line, e.col, e.msg)], "lex")

    try:
        prog = parse(src)
    except ParseError as e:
        return pack([Diag("error", "SYN", e.line, e.col, e.msg)], "parse")

    syn = [Diag("error", "SYN", e.line, e.col, e.msg)
           for e in getattr(prog, "errors", [])]
    sem = analyze(prog, includes=pr.includes)
    return pack(syn + sem, "semantic")


def get_ir(raw):
    """生成三地址码（可选功能，坏了不影响主流程）。"""
    try:
        from ir import ir_to_text
        pr = preprocess(raw)
        prog = parse(pr.text)
        return ir_to_text(prog)
    except Exception as e:
        return "; 无法生成 IR：%s: %s" % (type(e).__name__, e)


def run_code(raw, stdin_data, timeout=8):
    """真实编译并运行：codegen -> gcc 汇编链接 -> 带输入执行。

    返回 dict：
      ok        程序是否跑完（编译失败/超时都是 False）
      stage     semantic / codegen / build / run
      stdout    程序输出
      stderr    编译器/运行期错误
      exit      退出码（跑完才有）
      diags     语义检查结果（编译前先查一遍，有 ERROR 直接拒绝）
    """
    import subprocess
    import tempfile
    import shutil

    # 1) 先跑语义检查：有 ERROR 就拒绝编译（和 csub.py 行为一致）
    result = check_text(raw)
    has_error = any(d["level"] == "error" for d in result["diags"])
    if has_error:
        return {"ok": False, "stage": "semantic", "stdout": "",
                "stderr": "代码有错误，先修好再运行", "exit": None,
                "diags": result["diags"]}

    workdir = tempfile.mkdtemp(prefix="csubrun_")
    try:
        s_path = os.path.join(workdir, "prog.s")
        exe_path = os.path.join(workdir, "prog.exe")

        # 2) 生成汇编（直接用 compile_source 的文本接口，
        #    免去临时 .c 文件和子进程开销）
        try:
            from codegen import compile_source, CodegenError
        except ImportError:
            from codegen import compile_source, CodegenError
        try:
            # compile_source 返回 (asm文本, 函数名列表)
            asm_text, _funcs = compile_source(raw)
        except CodegenError as e:
            return {"ok": False, "stage": "codegen", "stdout": "",
                    "stderr": str(e), "exit": None,
                    "diags": result["diags"]}
        except Exception as e:
            return {"ok": False, "stage": "codegen", "stdout": "",
                    "stderr": "代码生成失败：%s" % e, "exit": None,
                    "diags": result["diags"]}
        with open(s_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(asm_text)

        # 3) 汇编链接（gcc 带 CRT）
        r = subprocess.run(
            ["gcc", "-O0", "-o", exe_path, s_path],
            capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode != 0:
            msg = (r.stdout.decode("utf-8", "replace")
                   + r.stderr.decode("utf-8", "replace")).strip()
            return {"ok": False, "stage": "build", "stdout": "",
                    "stderr": msg[-2000:] or "gcc 汇编/链接失败",
                    "exit": None, "diags": result["diags"]}

        # 4) 带输入运行，限时防死循环
        try:
            inp = stdin_data.encode("utf-8") if stdin_data else b""
            r = subprocess.run([exe_path], input=inp,
                               capture_output=True, timeout=timeout,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            out = r.stdout.decode("utf-8", "replace")
            err = r.stderr.decode("utf-8", "replace")
            code = r.returncode
            # Windows 退出码按有符号解释（0xC0000005 -> 访问违例）
            if code < 0:
                code_u = code & 0xFFFFFFFF
                if code_u == 0xC0000005:
                    err += "\n[运行期] 访问违例（段错误）：越界/空指针"
                elif code_u == 0xC0000094:
                    err += "\n[运行期] 整数除零"
                elif code_u == 0xC00000FD:
                    err += "\n[运行期] 栈溢出（可能是无限递归）"
                else:
                    err += "\n[运行期] 异常退出，代码 0x%08X" % code_u
            return {"ok": True, "stage": "run", "stdout": out,
                    "stderr": err, "exit": code & 0xFF,
                    "diags": result["diags"]}
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "run",
                    "stdout": "", "stderr":
                    "运行超时（>%ds，可能是死循环）" % timeout,
                    "exit": None, "diags": result["diags"]}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>csub · C 代码查错</title>
<style>
:root{
  /* ── 多色体系 ── */
  --c-cyan:#22d3ee;   --c-blue:#3b82f6;  --c-violet:#a855f7;
  --c-pink:#ec4899;   --c-orange:#f97316;--c-emerald:#10b981;
  --c-amber:#fbbf24;  --c-rose:#f43f5e;

  /* ── 表面 ── */
  --void:#06070d;
  --bg:#0a0c14;
  --surface:#101320;
  --surface-2:#171a2c;
  --surface-3:#1f2338;

  --line:rgb(255 255 255 / .07);
  --line-2:rgb(255 255 255 / .12);
  --line-3:rgb(255 255 255 / .2);

  --text:#f0f2f8;
  --text-2:#a8b0c8;
  --text-3:#6b7490;

  --mono:"Cascadia Code","JetBrains Mono","Fira Code",
         ui-monospace,Consolas,monospace;
  --sans:"Inter","Segoe UI","Microsoft YaHei",system-ui,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{
  background:var(--void);
  color:var(--text);
  font:14px/1.6 var(--sans);
  display:flex;flex-direction:column;
  overflow:hidden;
  -webkit-font-smoothing:antialiased;
}

/* ══════════════════════════════════════════════
   背景：4 个彩色光斑 + 斜线 + 点阵 + 噪点
   ══════════════════════════════════════════════ */
.bg{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden}

/* 彩色光斑 */
.orb{
  position:absolute;border-radius:50%;filter:blur(80px);
  opacity:.72;mix-blend-mode:screen;
  animation:drift 28s ease-in-out infinite;
}
.orb-1{
  width:680px;height:680px;left:-40px;top:-160px;
  background:radial-gradient(circle,
    rgb(168 85 247 / .55), rgb(168 85 247 / .1) 55%, transparent 72%);
  animation-delay:0s;
}
.orb-2{
  width:620px;height:620px;right:-30px;top:-100px;
  background:radial-gradient(circle,
    rgb(34 211 238 / .5), rgb(34 211 238 / .08) 55%, transparent 72%);
  animation-delay:-7s;
}
.orb-3{
  width:780px;height:780px;left:26%;bottom:-300px;
  background:radial-gradient(circle,
    rgb(236 72 153 / .4), rgb(236 72 153 / .07) 55%, transparent 72%);
  animation-delay:-14s;
}
.orb-4{
  width:560px;height:560px;right:4%;bottom:-160px;
  background:radial-gradient(circle,
    rgb(249 115 22 / .38), rgb(249 115 22 / .06) 55%, transparent 72%);
  animation-delay:-21s;
}
@keyframes drift{
  0%,100%{transform:translate(0,0) scale(1)}
  33%{transform:translate(46px,-34px) scale(1.09)}
  66%{transform:translate(-34px,28px) scale(.94)}
}

/* 斜线纹理 */
.bg .stripes{
  position:absolute;inset:0;
  background-image:repeating-linear-gradient(
    -48deg, transparent 0 13px,
    rgb(168 85 247 / .05) 13px 14px,
    transparent 14px 27px,
    rgb(34 211 238 / .045) 27px 28px);
  mask-image:radial-gradient(ellipse 92% 82% at 50% 42%,
    #000 20%, transparent 88%);
}
/* 点阵 */
.bg .dots{
  position:absolute;inset:0;
  background-image:radial-gradient(
    rgb(255 255 255 / .11) 1px, transparent 1px);
  background-size:28px 28px;
  mask-image:radial-gradient(ellipse 78% 70% at 50% 38%,
    #000 15%, transparent 84%);
  opacity:.8;
}
/* 细网格 */
.bg .grid{
  position:absolute;inset:-40%;
  background-image:
    linear-gradient(rgb(168 85 247 / .075) 1px, transparent 1px),
    linear-gradient(90deg, rgb(34 211 238 / .075) 1px, transparent 1px);
  background-size:64px 64px;
  transform:perspective(880px) rotateX(8deg);
  mask-image:radial-gradient(ellipse 80% 68% at 50% 36%,
    #000 18%, transparent 84%);
}
/* 噪点 */
.bg .noise{
  position:absolute;inset:0;opacity:.04;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='150' height='150'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.8' numOctaves='4'/%3E%3C/filter%3E%3Crect width='150' height='150' filter='url(%23n)'/%3E%3C/svg%3E");
}
/* 装饰几何：大圆环 */
.bg .ring{
  position:absolute;border-radius:50%;
  border:1px solid rgb(168 85 247 / .13);
}
.bg .ring-1{width:540px;height:540px;left:-180px;bottom:-180px}
.bg .ring-2{width:340px;height:340px;left:-80px;bottom:-80px;
            border-color:rgb(34 211 238 / .1)}
/* 装饰：大号水印符号 */
.bg .glyph-watermark{
  position:absolute;right:-3%;top:16%;
  font:900 400px/1 var(--mono);
  color:transparent;
  -webkit-text-stroke:1px rgb(255 255 255 / .035);
  letter-spacing:-.06em;
  user-select:none;
  transform:rotate(-8deg);
}
/* 光标光晕 */
.cursor-light{
  position:absolute;width:560px;height:560px;border-radius:50%;
  pointer-events:none;opacity:0;transition:opacity .5s;
  background:radial-gradient(circle,
    rgb(139 92 246 / .1), rgb(34 211 238 / .05) 45%, transparent 68%);
  transform:translate(-50%,-50%);
}
body.has-cursor .cursor-light{opacity:1}

/* ══════════════════════════════════════════════
   顶栏
   ══════════════════════════════════════════════ */
header{
  position:relative;z-index:10;flex-shrink:0;
  height:64px;display:flex;align-items:center;gap:16px;
  padding:0 22px;
  background:linear-gradient(180deg,
    rgb(16 19 32 / .88), rgb(16 19 32 / .4));
  backdrop-filter:blur(22px) saturate(170%);
  -webkit-backdrop-filter:blur(22px) saturate(170%);
  border-bottom:1px solid var(--line-2);
}
/* 顶栏底部多彩渐变线 */
header::after{
  content:"";position:absolute;left:0;right:0;bottom:-1px;height:1.5px;
  background:linear-gradient(90deg,
    transparent 0%,
    rgb(168 85 247 / .9) 18%,
    rgb(59 130 246 / .9) 36%,
    rgb(34 211 238 / .9) 54%,
    rgb(16 185 129 / .7) 72%,
    rgb(251 191 36 / .5) 88%,
    transparent 100%);
  opacity:.85;
  filter:blur(.3px);
}

.brand{display:flex;align-items:center;gap:12px;flex-shrink:0}
/* 多色渐变标记 */
.mark{
  position:relative;width:36px;height:36px;flex-shrink:0;
  border-radius:11px;
  background:
    conic-gradient(from 200deg at 50% 50%,
      #a855f7, #3b82f6, #22d3ee, #10b981, #fbbf24, #f43f5e, #a855f7);
  display:grid;place-items:center;
  box-shadow:
    0 0 0 1px rgb(255 255 255 / .28) inset,
    0 4px 24px rgb(168 85 247 / .45),
    0 0 54px rgb(34 211 238 / .28);
  animation:spin-hue 9s linear infinite;
}
@keyframes spin-hue{
  to{filter:hue-rotate(360deg)}
}
.mark::before{
  content:"";position:absolute;inset:2px;
  border-radius:9px;
  background:linear-gradient(160deg,#12141f,#0a0c14);
}
.mark span{
  position:relative;z-index:1;
  font:800 14px/1 var(--mono);
  letter-spacing:-.05em;
  background:linear-gradient(120deg,#67e8f9,#c084fc,#f472b6);
  -webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent;
}

.title-wrap{display:flex;flex-direction:column;gap:2px;line-height:1.15}
.title{
  font-size:16px;font-weight:750;letter-spacing:-.02em;
  background:linear-gradient(100deg,
    #c084fc 0%, #818cf8 22%, #22d3ee 46%,
    #34d399 68%, #fbbf24 88%, #c084fc 100%);
  background-size:260% 100%;
  -webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent;
  animation:flow 8s linear infinite;
}
@keyframes flow{
  to{background-position:260% 0}
}
.subtitle{
  font:10.5px/1 var(--mono);color:var(--text-3);
  letter-spacing:.14em;text-transform:uppercase;
}
.spacer{flex:1}

/* 按钮 */
.btn{
  position:relative;display:inline-flex;align-items:center;gap:7px;
  height:35px;padding:0 15px;
  background:rgb(255 255 255 / .045);
  color:var(--text-2);
  border:1px solid var(--line-2);
  border-radius:9px;
  font:550 12.5px/1 var(--sans);
  cursor:pointer;white-space:nowrap;overflow:hidden;
  transition:color .16s,border-color .16s,transform .1s,
             box-shadow .2s;
}
/* hover 时的彩色渐变扫光 */
.btn::after{
  content:"";position:absolute;inset:0;z-index:-1;
  background:linear-gradient(100deg,
    rgb(168 85 247 / .2), rgb(34 211 238 / .2), rgb(236 72 153 / .18));
  opacity:0;transition:opacity .2s;
}
.btn:hover{
  color:#fff;border-color:rgb(168 85 247 / .45);
  box-shadow:0 0 22px rgb(168 85 247 / .22);
}
.btn:hover::after{opacity:1}
.btn:active{transform:translateY(1px)}
.btn:focus-visible{outline:2px solid var(--c-cyan);outline-offset:2px}
.btn svg{
  width:14px;height:14px;flex-shrink:0;
  stroke:currentColor;fill:none;stroke-width:1.9;
  stroke-linecap:round;stroke-linejoin:round;
}
/* 主按钮：多色渐变 + 发光 */
.btn.primary{
  background:linear-gradient(100deg,
    #7c3aed 0%, #2563eb 34%, #0891b2 62%, #0d9488 100%);
  background-size:200% 100%;
  color:#fff;border-color:rgb(255 255 255 / .3);
  font-weight:700;
  box-shadow:
    0 1px 0 rgb(255 255 255 / .3) inset,
    0 4px 22px rgb(124 58 237 / .45),
    0 0 42px rgb(37 99 235 / .22);
  animation:flow 6s linear infinite;
}
.btn.primary:hover{
  box-shadow:
    0 1px 0 rgb(255 255 255 / .38) inset,
    0 6px 30px rgb(124 58 237 / .6),
    0 0 62px rgb(37 99 235 / .35);
}
.btn kbd{
  font:700 10.5px/1 var(--mono);
  padding:3px 5px;border-radius:5px;
  background:rgb(0 0 0 / .26);
  border:1px solid rgb(255 255 255 / .16);
  color:#fff;opacity:.92;
}

/* ══════════════════════════════════════════════
   主区
   ══════════════════════════════════════════════ */
main{
  position:relative;z-index:5;flex:1;
  display:grid;grid-template-columns:1fr 1fr;
  min-height:0;padding:14px;gap:14px;
}
.pane{
  position:relative;display:flex;flex-direction:column;
  min-width:0;min-height:0;
  background:linear-gradient(165deg,
    rgb(24 27 45 / .58), rgb(13 15 26 / .72));
  border-radius:17px;
  overflow:hidden;
  backdrop-filter:blur(16px) saturate(140%);
  -webkit-backdrop-filter:blur(16px) saturate(140%);
  box-shadow:
    0 24px 60px rgb(0 0 0 / .55),
    0 0 0 1px rgb(255 255 255 / .05);
}
/* 渐变描边（用 mask 挖空实现） */
.pane::before{
  content:"";position:absolute;inset:0;border-radius:17px;
  padding:1px;
  background:linear-gradient(150deg,
    rgb(168 85 247 / .5) 0%,
    rgb(59 130 246 / .3) 28%,
    rgb(34 211 238 / .22) 55%,
    rgb(236 72 153 / .3) 100%);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite:xor;
  mask-composite:exclude;
  pointer-events:none;z-index:3;
}
/* 面板内装饰光斑：让面板内部也有色彩 */
.pane::after{
  content:"";position:absolute;width:320px;height:320px;
  left:-100px;top:-100px;border-radius:50%;
  background:radial-gradient(circle,
    rgb(168 85 247 / .22), transparent 68%);
  pointer-events:none;z-index:1;
  filter:blur(20px);
}
.pane:nth-child(2)::after{
  background:radial-gradient(circle,
    rgb(34 211 238 / .2), transparent 68%);
}
/* 右下角补一个，形成对角张力 */
.pane > .corner{
  position:absolute;width:280px;height:280px;
  right:-90px;bottom:-90px;border-radius:50%;
  pointer-events:none;z-index:1;filter:blur(24px);
  background:radial-gradient(circle,
    rgb(236 72 153 / .16), transparent 68%);
}
.pane:nth-child(2) > .corner{
  background:radial-gradient(circle,
    rgb(59 130 246 / .16), transparent 68%);
}

.pane-head{
  position:relative;z-index:4;flex-shrink:0;height:44px;
  display:flex;align-items:center;gap:10px;
  padding:0 16px;
  border-bottom:1px solid var(--line);
  background:rgb(10 12 20 / .5);
  user-select:none;
}
/* 头部指示灯：渐变 */
.lamp{
  width:8px;height:8px;border-radius:50%;flex-shrink:0;
  background:var(--text-3);transition:.25s;
}
.lamp.on{
  background:linear-gradient(135deg,#22d3ee,#3b82f6);
  box-shadow:0 0 12px rgb(34 211 238 / .8);
}
.lamp.err{
  background:linear-gradient(135deg,#f43f5e,#ec4899);
  box-shadow:0 0 12px rgb(244 63 94 / .8);
  animation:pulse 1.6s ease-in-out infinite;
}
@keyframes pulse{
  0%,100%{opacity:1;transform:scale(1)}
  50%{opacity:.55;transform:scale(.82)}
}
.pane-head .name{
  font:650 12.5px/1 var(--sans);color:var(--text-2);
  letter-spacing:.02em;
}
.pane-head .meta{
  margin-left:auto;display:flex;align-items:center;gap:9px;
  font:11.5px/1 var(--mono);color:var(--text-3);
}
.pane-head .meta .sep{color:var(--line-3)}

/* ══════════════════════════════════════════════
   编辑器
   ══════════════════════════════════════════════ */
.editor{position:relative;z-index:2;flex:1;display:flex;min-height:0}

.gutter{
  flex-shrink:0;width:60px;padding:16px 0;
  /* overflow 必须是可编程滚动的状态：hidden 时 JS 赋
     scrollTop 静默失败，行号不跟随代码滚动（实测）。
     auto + 隐藏滚动条 = 能编程滚动又看不见滚动条。 */
  overflow:auto;user-select:none;
  scrollbar-width:none;-ms-overflow-style:none;
  font:12.5px/1.78 var(--mono);text-align:right;
  color:#78829c;
  background:linear-gradient(180deg,
    rgb(10 12 20 / .55), rgb(10 12 20 / .35));
  border-right:1px solid var(--line);
}
.gutter::-webkit-scrollbar{display:none}
.gutter .gl{
  padding-right:12px;border-right:2px solid transparent;
  transition:.14s;
}
.gutter .gl.warn{
  color:var(--c-amber);font-weight:700;
  border-right-color:var(--c-amber);
  background:linear-gradient(90deg,
    transparent, rgb(251 191 36 / .14));
}
.gutter .gl.err{
  color:var(--c-rose);font-weight:800;
  border-right-color:var(--c-rose);
  background:linear-gradient(90deg,
    transparent, rgb(244 63 94 / .17));
}

.code-wrap{flex:1;position:relative;min-width:0;overflow:hidden}
.code-wrap .hl{
  position:absolute;inset:0;padding:16px 18px;
  font:12.5px/1.78 var(--mono);tab-size:4;
  /* overflow 必须是 auto/hidden 之外的"可编程滚动"状态：
     设成 hidden 时 JS 赋 scrollLeft 静默失败，textarea 横向
     滚动后高亮层不跟随，文字整体偏掉（实测竖杠对不上）。
     auto + 隐藏滚动条 = 既能编程滚动又看不见滚动条。 */
  white-space:pre;overflow:auto;pointer-events:none;
  color:var(--text);
  scrollbar-width:none;-ms-overflow-style:none;
  /* 不能加 word-break:break-all：textarea 关了软换行后按
     pre 不折行，这里若还按字符断行，行超宽时两层文字会错开，
     光标竖杠指向错误位置（实测对不上）。两层都必须不折行。 */
}
.code-wrap .hl::-webkit-scrollbar{display:none}
/* 语法配色：更鲜艳 */
.code-wrap .hl .c{color:#6b7a99;font-style:italic}
.code-wrap .hl .s{color:#7ee787}
.code-wrap .hl .p{color:#d2a8ff;font-weight:600}
.code-wrap .hl .k{color:#79c0ff;font-weight:600}
.code-wrap .hl .n{color:#ffa657}
.code-wrap .hl .f{color:#d2a8ff}
.code-wrap .hl .t{color:#56d4dd;font-weight:500}

/* 代码编辑区专用：文字透明（底下有语法高亮层）。
   必须限定 #code——通用 textarea 选择器会命中 stdin 输入框，
   -webkit-text-fill-color 优先级高于 color，导致输入的文字
   全透明看不见（实测踩坑）。

   white-space:pre + wrap=off：不软换行，和 .hl 层的断行行为
   保持一致。textarea 默认 soft wrap 按词边界断行，.hl 是
   word-break:break-all 按字符断——一旦行超宽，两层的文字
   就逐行错开，光标竖杠指向的字符和实际输入位置对不上
   （实测：输入到第 40 列后竖杠偏 2-3 个字符）。 */
#code{
  position:relative;z-index:1;width:100%;height:100%;
  padding:16px 18px;
  background:transparent;color:transparent;
  border:0;outline:0;resize:none;
  font:12.5px/1.78 var(--mono);tab-size:4;
  white-space:pre;overflow:auto;
  caret-color:var(--c-cyan);
  -webkit-text-fill-color:transparent;
  scrollbar-width:thin;
}
#code::placeholder{
  color:#5d6680;line-height:2.1;
  -webkit-text-fill-color:#5d6680;
}
#code::selection{
  background:rgb(168 85 247 / .32);
  -webkit-text-fill-color:transparent;
}
/* stdin 输入框：正常显示文字 */
#run-stdin{
  color:var(--text);
  -webkit-text-fill-color:var(--text);
  caret-color:var(--c-cyan);
}
#run-stdin::selection{
  background:rgb(168 85 247 / .32);
  -webkit-text-fill-color:var(--text);
}

/* 滚动条 */
#code::-webkit-scrollbar,#run-stdin::-webkit-scrollbar,
#out::-webkit-scrollbar,
#irpane::-webkit-scrollbar{width:10px;height:10px}
#code::-webkit-scrollbar-track,#run-stdin::-webkit-scrollbar-track,
#out::-webkit-scrollbar-track,
#irpane::-webkit-scrollbar-track{background:transparent}
#code::-webkit-scrollbar-thumb,#run-stdin::-webkit-scrollbar-thumb,
#out::-webkit-scrollbar-thumb,
#irpane::-webkit-scrollbar-thumb{
  background:linear-gradient(180deg,#a855f7,#22d3ee);
  border-radius:10px;border:3px solid transparent;
  background-clip:padding-box;opacity:.5;
}

/* ══════════════════════════════════════════════
   诊断流
   ══════════════════════════════════════════════ */
#out{flex:1;overflow-y:auto;padding:16px;scroll-behavior:smooth}

/* 空状态 */
.empty{
  height:100%;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:18px;
  text-align:center;padding:24px;
}
.empty .glyph{
  position:relative;width:84px;height:84px;border-radius:24px;
  display:grid;place-items:center;
  background:linear-gradient(150deg,
    rgb(168 85 247 / .16), rgb(34 211 238 / .1) 55%,
    rgb(236 72 153 / .12));
  box-shadow:
    0 0 0 1px rgb(168 85 247 / .3) inset,
    0 0 50px rgb(168 85 247 / .18),
    0 0 80px rgb(34 211 238 / .08);
}
.empty .glyph::after{
  content:"";position:absolute;inset:-7px;border-radius:30px;
  border:1px dashed rgb(168 85 247 / .2);
  animation:spin 22s linear infinite;
}
@keyframes spin{to{transform:rotate(360deg)}}
.empty .glyph svg{
  width:36px;height:36px;
  stroke:url(#emptyGrad);fill:none;stroke-width:1.6;
  stroke-linecap:round;stroke-linejoin:round;
}
.empty h3{
  font-size:15px;font-weight:700;
  background:linear-gradient(100deg,#c084fc,#67e8f9);
  -webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent;
}
.empty p{font-size:12.5px;line-height:1.9;color:var(--text-3);max-width:290px}
.empty kbd{
  font:700 11px/1 var(--mono);padding:3px 6px;border-radius:5px;
  background:rgb(168 85 247 / .16);
  border:1px solid rgb(168 85 247 / .3);
  color:#e9d5ff;
}
/* 能力卡：三种颜色 */
.empty .caps{
  display:flex;flex-direction:column;gap:8px;
  margin-top:6px;width:100%;max-width:330px;
}
.empty .cap{
  display:flex;align-items:center;gap:10px;
  padding:9px 13px;border-radius:10px;
  font-size:11.5px;line-height:1.5;text-align:left;
  color:var(--text-2);
  border:1px solid;
  transition:.2s;
}
.empty .cap:hover{transform:translateX(3px)}
.cap-1{
  background:linear-gradient(100deg,
    rgb(244 63 94 / .1), rgb(236 72 153 / .04));
  border-color:rgb(244 63 94 / .25);
}
.cap-2{
  background:linear-gradient(100deg,
    rgb(251 191 36 / .1), rgb(249 115 22 / .04));
  border-color:rgb(251 191 36 / .25);
}
.cap-3{
  background:linear-gradient(100deg,
    rgb(107 116 144 / .1), rgb(107 116 144 / .02));
  border-color:rgb(107 116 144 / .22);
  opacity:.72;
}
.cap .dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.cap-1 .dot{background:var(--c-rose);box-shadow:0 0 9px var(--c-rose)}
.cap-2 .dot{background:var(--c-amber);box-shadow:0 0 9px var(--c-amber)}
.cap-3 .dot{background:var(--text-3)}
.cap-3 b{color:#fb7185;font-weight:700}

/* 时间轴 */
#out .flow{position:relative;padding-left:18px}
#out .flow::before{
  content:"";position:absolute;left:5px;top:8px;bottom:8px;width:2px;
  border-radius:2px;
  background:linear-gradient(180deg,
    rgb(168 85 247 / .5), rgb(59 130 246 / .4) 35%,
    rgb(34 211 238 / .35) 65%, rgb(236 72 153 / .4));
  opacity:.55;
}
#out .flow .diag::before{
  content:"";position:absolute;left:-16px;top:20px;
  width:7px;height:7px;border-radius:50%;
  background:var(--text-3);
  box-shadow:0 0 0 3px rgb(10 12 20);
}
#out .flow .diag.err::before{
  background:linear-gradient(135deg,#f43f5e,#ec4899);
  box-shadow:0 0 0 3px rgb(10 12 20), 0 0 14px rgb(244 63 94 / .9);
}
#out .flow .diag.warn::before{
  background:linear-gradient(135deg,#fbbf24,#f97316);
  box-shadow:0 0 0 3px rgb(10 12 20), 0 0 14px rgb(251 191 36 / .9);
}

/* 摘要卡 */
.summary{
  position:relative;display:flex;align-items:center;gap:13px;
  padding:15px 17px;margin-bottom:15px;margin-left:-8px;
  border-radius:14px;overflow:hidden;
  border:1px solid;
  animation:pop .32s cubic-bezier(.2,.9,.3,1.2) both;
}
.summary::before{
  content:"";position:absolute;inset:0;z-index:0;
  opacity:.85;
}
.summary.bad{
  border-color:rgb(244 63 94 / .38);
  box-shadow:0 0 40px rgb(244 63 94 / .1) inset;
}
.summary.bad::before{
  background:linear-gradient(100deg,
    rgb(244 63 94 / .16), rgb(236 72 153 / .07) 45%,
    rgb(168 85 247 / .05));
}
.summary.good{
  border-color:rgb(16 185 129 / .38);
  box-shadow:0 0 40px rgb(16 185 129 / .1) inset;
}
.summary.good::before{
  background:linear-gradient(100deg,
    rgb(16 185 129 / .16), rgb(34 211 238 / .07) 45%,
    rgb(59 130 246 / .05));
}
.summary > *{position:relative;z-index:1}
.summary .icon{
  width:38px;height:38px;border-radius:11px;flex-shrink:0;
  display:grid;place-items:center;
}
.summary.bad .icon{
  background:linear-gradient(140deg,
    rgb(244 63 94 / .3), rgb(236 72 153 / .15));
  box-shadow:0 0 20px rgb(244 63 94 / .3);
}
.summary.good .icon{
  background:linear-gradient(140deg,
    rgb(16 185 129 / .3), rgb(34 211 238 / .15));
  box-shadow:0 0 20px rgb(16 185 129 / .3);
}
.summary .icon svg{
  width:18px;height:18px;fill:none;stroke-width:2.4;
  stroke-linecap:round;stroke-linejoin:round;
}
.summary.bad .icon svg{stroke:#fda4af}
.summary.good .icon svg{stroke:#6ee7b7}
.summary .text{display:flex;flex-direction:column;gap:3px}
.summary .big{
  font-size:15px;font-weight:750;letter-spacing:-.01em;
  text-shadow:0 1px 12px rgb(0 0 0 / .6);
}
.summary.bad .big{color:#fecdd3}
.summary.good .big{color:#a7f3d0}
.summary .small{font-size:12px;color:var(--text-2);font-weight:450}
.summary .chips{margin-left:auto;display:flex;gap:8px}
.chip{
  display:inline-flex;align-items:center;gap:6px;
  padding:6px 11px;border-radius:8px;
  font:700 11.5px/1 var(--mono);
  border:1px solid;backdrop-filter:blur(8px);
}
.chip i{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.chip.e{
  color:#fda4af;border-color:rgb(244 63 94 / .45);
  background:rgb(244 63 94 / .14);
}
.chip.e i{background:#f43f5e;box-shadow:0 0 8px #f43f5e}
.chip.w{
  color:#fcd34d;border-color:rgb(251 191 36 / .45);
  background:rgb(251 191 36 / .13);
}
.chip.w i{background:#fbbf24;box-shadow:0 0 8px #fbbf24}

/* 诊断卡 */
.diag{
  position:relative;display:flex;margin-bottom:11px;
  background:linear-gradient(165deg,
    rgb(255 255 255 / .042), rgb(255 255 255 / .014));
  border:1px solid var(--line);
  border-radius:13px;overflow:hidden;cursor:pointer;
  transition:border-color .18s,transform .14s,box-shadow .2s;
}
.diag.enter{animation:slide .28s cubic-bezier(.2,.9,.3,1) both}
.diag:hover{transform:translateX(3px)}
.diag.err:hover{
  border-color:rgb(244 63 94 / .45);
  box-shadow:0 6px 30px rgb(244 63 94 / .16);
}
.diag.warn:hover{
  border-color:rgb(251 191 36 / .45);
  box-shadow:0 6px 30px rgb(251 191 36 / .14);
}
.diag:focus-visible{outline:2px solid var(--c-violet);outline-offset:2px}

/* 左侧彩条 */
.diag .edge{width:4px;flex-shrink:0;position:relative}
.diag .edge::after{
  content:"";position:absolute;inset:0;filter:blur(7px);opacity:.85;
}
.diag.err .edge{background:linear-gradient(180deg,#f43f5e,#ec4899)}
.diag.err .edge::after{background:#f43f5e}
.diag.warn .edge{background:linear-gradient(180deg,#fbbf24,#f97316)}
.diag.warn .edge::after{background:#fbbf24}

.diag .content{padding:13px 16px;flex:1;min-width:0}
.diag .top{
  display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  margin-bottom:6px;
}
.badge{
  display:inline-flex;align-items:center;
  padding:4.5px 9px;border-radius:7px;
  font:800 10.5px/1 var(--mono);letter-spacing:.05em;
  flex-shrink:0;
}
.diag.err .badge{
  background:linear-gradient(100deg,
    rgb(244 63 94 / .26), rgb(236 72 153 / .16));
  color:#fecdd3;
  border:1px solid rgb(244 63 94 / .4);
  box-shadow:0 0 16px rgb(244 63 94 / .2);
}
.diag.warn .badge{
  background:linear-gradient(100deg,
    rgb(251 191 36 / .26), rgb(249 115 22 / .16));
  color:#fde68a;
  border:1px solid rgb(251 191 36 / .4);
  box-shadow:0 0 16px rgb(251 191 36 / .18);
}
.where{
  font:11.5px/1 var(--mono);color:var(--text-3);
  display:inline-flex;align-items:center;gap:5px;flex-shrink:0;
}
.where b{
  font-weight:700;font-variant-numeric:tabular-nums;
  background:linear-gradient(100deg,#67e8f9,#c084fc);
  -webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent;
}
.diag .what{
  font-size:13.5px;line-height:1.62;color:#e9edf7;font-weight:400;
}
.diag .fix{
  margin-top:10px;padding:9px 12px;border-radius:9px;
  border-left:2.5px solid;
  border-image:linear-gradient(180deg,#22d3ee,#a855f7) 1;
  background:linear-gradient(100deg,
    rgb(34 211 238 / .09), rgb(168 85 247 / .05));
  font-size:12.5px;line-height:1.7;color:#cbd5e8;
  display:flex;gap:9px;
}
.diag .fix .lbl{
  font:800 10.5px/1.75 var(--mono);flex-shrink:0;
  letter-spacing:.04em;
  background:linear-gradient(100deg,#67e8f9,#c084fc);
  -webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent;
}

/* 预处理说明 */
.notes{
  margin-top:17px;padding:14px 16px;border-radius:13px;
  background:linear-gradient(165deg,
    rgb(255 255 255 / .03), rgb(255 255 255 / .008));
  border:1px solid var(--line);
}
.notes .h{
  display:flex;align-items:center;gap:8px;
  font:750 10.5px/1 var(--sans);color:var(--text-3);
  text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px;
}
.notes .h svg{width:13px;height:13px;stroke:#818cf8;fill:none;
              stroke-width:2;stroke-linecap:round}
.notes .item{
  display:flex;gap:9px;padding:4px 0;
  font-size:12.5px;line-height:1.65;color:var(--text-2);
}
.notes .item::before{
  content:"";width:5px;height:5px;border-radius:50%;flex-shrink:0;
  margin-top:8px;
  background:linear-gradient(135deg,#22d3ee,#a855f7);
  box-shadow:0 0 7px rgb(168 85 247 / .6);
}

/* IR */
#irpane{display:none;flex:1;overflow:auto;padding:16px}
body.ir-mode #irpane{display:block}
body.run-mode #irpane{display:block}
body.ir-mode #out{display:none}
body.run-mode #out{display:none}
/* 运行结果面板 */
.run-box{margin-bottom:14px;border-radius:11px;overflow:hidden;
         border:1px solid var(--line)}
.run-box .rb-head{
  display:flex;align-items:center;gap:9px;padding:9px 13px;
  font:600 11.5px/1 var(--mono);letter-spacing:.06em;
  background:rgb(255 255 255 / .03);border-bottom:1px solid var(--line);
  color:var(--text-2);text-transform:uppercase;
}
.run-box .rb-head .st-ok{color:#6ee7b7}
.run-box .rb-head .st-bad{color:#fda4af}
.run-box pre{
  margin:0;padding:13px 15px;max-height:340px;overflow:auto;
  font:12.5px/1.7 var(--mono);white-space:pre-wrap;word-break:break-all;
}
#run-stdout{color:#dbe7f5;background:rgb(16 19 32 / .5)}
#run-stderr{color:#fda4af;background:rgb(60 20 28 / .25);
            border-top:1px solid rgb(244 63 94 / .15)}
#run-stdin-box{display:flex;flex-direction:column;gap:6px;margin-top:16px}
#run-stdin-box .lbl{
  font:600 10.5px/1 var(--mono);color:var(--text-3);
  text-transform:uppercase;letter-spacing:.08em;
}
#run-stdin{
  width:100%;min-height:64px;resize:vertical;
  background:rgb(16 19 32 / .55);color:var(--text);
  border:1px solid var(--line-2);border-radius:9px;
  padding:10px 13px;outline:0;
  font:12.5px/1.7 var(--mono);
}
#run-stdin:focus{border-color:rgb(34 211 238 / .5)}
#ir{font:12.5px/1.75 var(--mono);color:#a9b8d4;white-space:pre;tab-size:2}
#ir .c{color:#5a6884;font-style:italic}
#ir .l{color:#c084fc;font-weight:600}
#ir .k{color:#79c0ff;font-weight:600}
#ir .n{color:#ffa657}

/* 底栏 */
footer{
  position:relative;z-index:10;flex-shrink:0;height:34px;
  display:flex;align-items:center;gap:18px;padding:0 22px;
  background:rgb(10 12 20 / .82);
  backdrop-filter:blur(18px);
  -webkit-backdrop-filter:blur(18px);
  border-top:1px solid var(--line-2);
  font:11.5px/1 var(--mono);color:var(--text-3);
}
footer::before{
  content:"";position:absolute;left:0;right:0;top:-1px;height:1px;
  background:linear-gradient(90deg,
    transparent, rgb(168 85 247 / .55) 30%,
    rgb(34 211 238 / .55) 70%, transparent);
  opacity:.6;
}
footer .st{display:inline-flex;align-items:center;gap:7px}
footer .st i{
  width:6px;height:6px;border-radius:50%;
  background:var(--text-3);transition:.2s;
}
footer .st.busy i{
  background:linear-gradient(135deg,#22d3ee,#3b82f6);
  box-shadow:0 0 9px #22d3ee;animation:pulse 1s infinite;
}
footer .st.ok i{
  background:linear-gradient(135deg,#10b981,#34d399);
  box-shadow:0 0 9px #10b981;
}
footer .st.bad i{
  background:linear-gradient(135deg,#f43f5e,#ec4899);
  box-shadow:0 0 9px #f43f5e;
}
footer .div{color:var(--line-3)}
footer .right{margin-left:auto;display:flex;gap:18px;align-items:center}
footer b{
  font-weight:700;
  background:linear-gradient(100deg,#67e8f9,#c084fc);
  -webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent;
}

/* 动效 */
@keyframes pop{
  from{opacity:0;transform:scale(.96) translateY(6px)}
  to{opacity:1;transform:scale(1) translateY(0)}
}
@keyframes slide{
  from{opacity:0;transform:translateX(-8px)}
  to{opacity:1;transform:translateX(0)}
}
@media (prefers-reduced-motion:reduce){
  *{animation:none!important;transition:none!important}
  #out{scroll-behavior:auto}
}
@media (max-width:960px){
  main{grid-template-columns:1fr;grid-template-rows:1fr 1fr;
       padding:10px;gap:10px}
  .subtitle,.glyph-watermark{display:none}
  header{height:58px;padding:0 14px;gap:9px}
  .btn span.lbl{display:none}
  .btn{padding:0 11px}
  .mark{width:30px;height:30px}
}
</style>
</head>
<body>

<!-- 背景 -->
<div class="bg">
  <div class="orb orb-1"></div>
  <div class="orb orb-2"></div>
  <div class="orb orb-3"></div>
  <div class="orb orb-4"></div>
  <div class="grid"></div>
  <div class="stripes"></div>
  <div class="dots"></div>
  <div class="ring ring-1"></div>
  <div class="ring ring-2"></div>
  <div class="glyph-watermark">&lt;/&gt;</div>
  <div class="cursor-light" id="clight"></div>
  <div class="noise"></div>
</div>

<!-- 渐变定义（给空状态 SVG 用） -->
<svg width="0" height="0" style="position:absolute">
  <defs>
    <linearGradient id="emptyGrad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#c084fc"/>
      <stop offset="50%" stop-color="#22d3ee"/>
      <stop offset="100%" stop-color="#34d399"/>
    </linearGradient>
  </defs>
</svg>

<header>
  <div class="brand">
    <div class="mark"><span>&lt;/&gt;</span></div>
    <div class="title-wrap">
      <div class="title">csub 诊断台</div>
      <div class="subtitle">C SUBSET COMPILER · v2.2.1</div>
    </div>
  </div>
  <div class="spacer"></div>

  <button class="btn" id="btn-sample" title="载入含 4 个典型问题的示例">
    <svg viewBox="0 0 24 24"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M13 2v7h7"/></svg>
    <span class="lbl">示例</span>
  </button>
  <button class="btn" id="btn-ir" title="查看三地址码">
    <svg viewBox="0 0 24 24"><path d="M4 6h6M4 12h10M4 18h6"/><circle cx="18" cy="6" r="2"/><circle cx="18" cy="18" r="2"/></svg>
    <span class="lbl">三地址码</span>
  </button>
  <button class="btn" id="btn-clear" title="清空">
    <svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
    <span class="lbl">清空</span>
  </button>
  <button class="btn" id="btn-run" title="真实编译并运行（带输入输出）">
    <svg viewBox="0 0 24 24"><path d="M6 4l14 8-14 8z"/></svg>
    <span class="lbl">运行</span>
  </button>
  <button class="btn primary" id="btn-check">
    <svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>
    检查 <kbd>Ctrl ↵</kbd>
  </button>
</header>

<main>
  <section class="pane">
    <div class="corner"></div>
    <div class="pane-head">
      <span class="lamp on" id="lamp-src"></span>
      <span class="name">源代码</span>
      <div class="meta">
        <span id="meta-lines">0 行</span>
        <span class="sep">/</span>
        <span id="meta-chars">0 字符</span>
      </div>
    </div>
    <div class="editor">
      <div class="gutter" id="gutter">1</div>
      <div class="code-wrap">
        <pre class="hl" id="hl" aria-hidden="true"></pre>
        <textarea id="code" spellcheck="false" autocomplete="off"
          autocapitalize="off" autofocus wrap="off"
          placeholder="在此粘贴 C 代码&#10;&#10;支持 #include / 宏 / 结构体 / 指针 / 递归&#10;&#10;Ctrl + Enter  检查"></textarea>
      </div>
    </div>
  </section>

  <section class="pane">
    <div class="corner"></div>
    <div class="pane-head">
      <span class="lamp" id="lamp-out"></span>
      <span class="name" id="name-out">诊断</span>
      <div class="meta" id="meta-out"></div>
    </div>
    <div id="out">
      <div class="empty">
        <div class="glyph">
          <svg viewBox="0 0 24 24">
            <path d="M12 9v4M12 17h.01"/>
            <path d="M10.3 3.9L2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>
          </svg>
        </div>
        <h3>等待检查</h3>
        <p>把代码贴进左边，按 <kbd>Ctrl</kbd>+<kbd>Enter</kbd>。<br>
           每条诊断会带行号、严重级别和修改建议。</p>
        <div class="caps">
          <div class="cap cap-1"><i class="dot"></i>
            类型混淆 · 未初始化 · scanf 漏 &amp;</div>
          <div class="cap cap-2"><i class="dot"></i>
            越界 · 死循环 · 漏 return · 冗余表达式</div>
          <div class="cap cap-3"><i class="dot"></i>
            算法逻辑错、内存泄漏 → <b>查不到</b></div>
        </div>
      </div>
    </div>
    <div id="irpane">
      <div id="run-stdin-box">
        <span class="lbl">程序输入（stdin，scanf 从这里读）</span>
        <textarea id="run-stdin" spellcheck="false"
          placeholder="每行一个输入项，例如：&#10;3 5"></textarea>
      </div>
      <div id="run-out"></div>
    </div>
  </section>
</main>

<footer>
  <span class="st" id="f-state"><i></i><span>就绪</span></span>
  <span class="div">│</span>
  <span>39 条规则</span>
  <span class="div">│</span>
  <span>零误报验证 <b>42</b> 份真实代码</span>
  <div class="right">
    <span id="f-count">—</span>
    <span class="div">│</span>
    <span id="f-size">0 行</span>
  </div>
</footer>

<script>
'use strict';
const $ = id => document.getElementById(id);
const code = $('code'), gutter = $('gutter'), out = $('out');
const hl = $('hl'), irPre = $('ir');

const SAMPLE = `#include <stdio.h>
#include <stdlib.h>

/* 这段代码埋了 4 个典型问题 */

int main() {
    int  a[5];
    int  i, sum = 0;

    /* ① 循环越界 */
    for (i = 0; i <= 5; i++) {
        a[i] = i * 2;
    }

    /* ② 赋值当比较 */
    if (sum = 10) {
        printf("%d\\n", sum);
    }

    /* ③ scanf 漏写 & */
    int n;
    scanf("%d", n);

    /* ④ 未声明变量 */
    printf("%d\\n", undeclared);

    return 0;
}
`;

let errLines = new Set(), warnLines = new Set();

/* ═══ 语法高亮 ═══ */
const KW = new Set(['if','else','while','for','return','break','continue',
  'switch','case','default','do','goto','sizeof','struct','union','enum',
  'typedef','static','const','extern','inline','volatile','restrict',
  'signed','unsigned','_Bool','bool','true','false','NULL']);
const TY = new Set(['int','char','void','float','double','long','short',
  'size_t','FILE']);

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

function hlLine(line) {
  let out = '', i = 0, n = line.length;
  while (i < n) {
    const ch = line[i];
    if (ch === '/' && line[i+1] === '/') {
      out += `<span class="c">${esc(line.slice(i))}</span>`; break;
    }
    if (ch === '/' && line[i+1] === '*') {
      const e = line.indexOf('*/', i + 2);
      if (e >= 0) {
        out += `<span class="c">${esc(line.slice(i, e + 2))}</span>`;
        i = e + 2; continue;
      }
      out += `<span class="c">${esc(line.slice(i))}</span>`; break;
    }
    if (ch === '"' || ch === "'") {
      const q = ch; let j = i + 1;
      while (j < n) {
        if (line[j] === '\\') { j += 2; continue; }
        if (line[j] === q) { j++; break; }
        j++;
      }
      out += `<span class="s">${esc(line.slice(i, j))}</span>`;
      i = j; continue;
    }
    if (ch === '#' && line.slice(0, i).trim() === '') {
      out += `<span class="p">${esc(line.slice(i))}</span>`; break;
    }
    if (/[A-Za-z_]/.test(ch)) {
      let j = i;
      while (j < n && /[A-Za-z0-9_]/.test(line[j])) j++;
      const w = line.slice(i, j);
      const cls = KW.has(w) ? 'k' : (TY.has(w) ? 't' : null);
      if (cls) out += `<span class="${cls}">${esc(w)}</span>`;
      else if (line[j] === '(') out += `<span class="f">${esc(w)}</span>`;
      else out += esc(w);
      i = j; continue;
    }
    if (/[0-9]/.test(ch)) {
      let j = i;
      while (j < n && /[0-9a-fA-FxX.]/.test(line[j])) j++;
      out += `<span class="n">${esc(line.slice(i, j))}</span>`;
      i = j; continue;
    }
    out += esc(ch); i++;
  }
  return out;
}

function renderHighlight() {
  // 先记下滚动位置：innerHTML 重设会把 .hl 的 scrollLeft 归零，
  // 用户横向滚动到一半时输入，文字会跳回最左（实测错位加剧）。
  const sl = hl.scrollLeft, st = hl.scrollTop;
  hl.innerHTML = code.value.split('\n').map(hlLine).join('\n');
  hl.scrollLeft = sl;
  hl.scrollTop = st;
}

/* ═══ 行号 ═══ */
function renderGutter() {
  const n = Math.max(code.value.split('\n').length, 1);
  const buf = [];
  for (let i = 1; i <= n; i++) {
    const c = errLines.has(i) ? ' err' : warnLines.has(i) ? ' warn' : '';
    buf.push(`<div class="gl${c}">${i}</div>`);
  }
  gutter.innerHTML = buf.join('');
  gutter.scrollTop = code.scrollTop;
}

function refreshMeta() {
  const v = code.value;
  const lines = v ? v.split('\n').length : 0;
  $('meta-lines').textContent = lines + ' 行';
  $('meta-chars').textContent = v.length + ' 字符';
  $('f-size').textContent = lines + ' 行';
}

function setState(cls, text) {
  const el = $('f-state');
  el.className = 'st ' + cls;
  el.lastElementChild.textContent = text;
}

/* ═══ 事件 ═══ */
code.addEventListener('input', () => {
  errLines.clear(); warnLines.clear();
  renderGutter(); refreshMeta(); renderHighlight();
});
code.addEventListener('scroll', () => {
  gutter.scrollTop = code.scrollTop;
  hl.scrollTop = code.scrollTop;
  hl.scrollLeft = code.scrollLeft;
});
code.addEventListener('keydown', e => {
  if (e.key === 'Tab') {
    e.preventDefault();
    const s = code.selectionStart, t = code.selectionEnd;
    code.value = code.value.slice(0, s) + '    ' + code.value.slice(t);
    code.selectionStart = code.selectionEnd = s + 4;
    renderGutter(); refreshMeta(); renderHighlight();
  }
});

/* 光标光晕 */
const clight = $('clight');
let raf = null;
window.addEventListener('mousemove', e => {
  document.body.classList.add('has-cursor');
  if (raf) return;
  raf = requestAnimationFrame(() => {
    clight.style.left = e.clientX + 'px';
    clight.style.top = e.clientY + 'px';
    raf = null;
  });
});
window.addEventListener('mouseleave',
  () => document.body.classList.remove('has-cursor'));

/* ═══ 请求 ═══ */
async function post(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

async function runCheck() {
  const src = code.value;
  if (!src.trim()) {
    out.innerHTML = `<div class="empty">
      <div class="glyph"><svg viewBox="0 0 24 24">
        <path d="M12 9v4M12 17h.01"/>
        <path d="M10.3 3.9L2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>
      </svg></div><h3>代码是空的</h3><p>先粘贴一些 C 代码</p></div>`;
    setState('', '就绪');
    return;
  }
  setState('busy', '检查中…');
  try {
    render(await post('/api/check', {code: src}));
  } catch (e) {
    out.innerHTML = `<div class="summary bad">
      <div class="icon"><svg viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>
      </svg></div>
      <div class="text"><div class="big">请求失败</div>
        <div class="small">${esc(e.message)}</div></div></div>`;
    setState('bad', '请求失败');
  }
}

/* ═══ 渲染 ═══ */
function render(data) {
  const ds = data.diags || [];
  const nE = ds.filter(d => d.level === 'error').length;
  const nW = ds.filter(d => d.level === 'warning').length;

  errLines.clear(); warnLines.clear();
  for (const d of ds) {
    if (d.line > 0) {
      if (d.level === 'error') errLines.add(d.line);
      else warnLines.add(d.line);
    }
  }
  renderGutter();

  let html = '';

  if (!ds.length) {
    html += `<div class="summary good">
      <div class="icon"><svg viewBox="0 0 24 24">
        <path d="M20 6L9 17l-5-5"/></svg></div>
      <div class="text">
        <div class="big">没有发现问题</div>
        <div class="small">这段代码通过了 39 条检查</div>
      </div>
      <div class="chips"><span class="chip">0 条</span></div>
    </div>`;
  } else {
    html += `<div class="summary ${nE ? 'bad' : 'good'}">
      <div class="icon"><svg viewBox="0 0 24 24">
        ${nE ? '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>'
             : '<path d="M20 6L9 17l-5-5"/>'}
      </svg></div>
      <div class="text">
        <div class="big">${nE ? '发现 ' + nE + ' 个错误' : '只有警告'}</div>
        <div class="small">${nE ? '这些问题通常会导致程序出错'
                                : '不影响编译，但建议检查'}</div>
      </div>
      <div class="chips">
        ${nE ? `<span class="chip e"><i></i>${nE} 错误</span>` : ''}
        ${nW ? `<span class="chip w"><i></i>${nW} 警告</span>` : ''}
      </div>
    </div>`;
  }

  for (const d of ds) {
    const lv = d.level === 'error' ? 'err' : 'warn';
    html += `<div class="diag ${lv} enter" data-line="${d.line}"
                  tabindex="0" role="button">
      <div class="edge"></div>
      <div class="content">
        <div class="top">
          <span class="badge">${esc(d.code)}</span>
          <span class="where">
            <svg viewBox="0 0 24 24" width="11" height="11"
                 fill="none" stroke="currentColor" stroke-width="2">
              <path d="M4 6h16M4 12h16M4 18h10"/>
            </svg> 第 <b>${d.line}</b> 行
          </span>
        </div>
        <div class="what">${esc(d.msg)}</div>
        ${d.hint ? `<div class="fix"><span class="lbl">建议</span>
          <span>${esc(d.hint)}</span></div>` : ''}
      </div>
    </div>`;
  }

  if (data.notes && data.notes.length) {
    html += `<div class="notes"><div class="h">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>
        <path d="M12 16v-4M12 8h.01"/></svg>预处理说明</div>`;
    for (const n of data.notes)
      html += `<div class="item"><span>${esc(n)}</span></div>`;
    html += `</div>`;
  }

  out.innerHTML = `<div class="flow">${html}</div>`;

  out.querySelectorAll('.diag').forEach(el => {
    const go = () => jumpTo(parseInt(el.dataset.line, 10));
    el.onclick = go;
    el.onkeydown = e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
    };
    el.addEventListener('animationend',
      () => el.classList.remove('enter'), {once: true});
  });

  const lamp = $('lamp-out');
  lamp.className = 'lamp ' + (nE ? 'err' : (ds.length ? 'on' : ''));
  $('meta-out').textContent = ds.length ? ds.length + ' 条' : '';
  $('f-count').textContent = ds.length
    ? `${nE} 错误 · ${nW} 警告` : '0 条';
  setState(nE ? 'bad' : (ds.length ? '' : 'ok'),
           ds.length ? `${ds.length} 条诊断` : '通过');
}

function jumpTo(line) {
  if (!line || line < 1) return;
  const lines = code.value.split('\n');
  let pos = 0;
  for (let i = 0; i < line - 1 && i < lines.length; i++)
    pos += lines[i].length + 1;
  code.focus();
  code.setSelectionRange(pos, pos + (lines[line - 1] || '').length);
  code.scrollTop = Math.max(0, (line - 7) * 22.2);
  gutter.scrollTop = code.scrollTop;
  hl.scrollTop = code.scrollTop;
  hl.scrollLeft = code.scrollLeft;
}

/* ═══ IR ═══ */
async function showIr() {
  if (!code.value.trim()) return;
  document.body.classList.add('ir-mode');
  $('name-out').textContent = '三地址码';
  irPre.textContent = '生成中…';
  try {
    const d = await post('/api/ir', {code: code.value});
    irPre.innerHTML = esc(d.ir || '（空）')
      .replace(/^(\s*;.*)$/gm, '<span class="c">$1</span>')
      .replace(/\b(func\s+\w+|L\d+):/g, '<span class="l">$1:</span>')
      .replace(/\b(return|call|goto|ifFalse|if)\b/g,
               '<span class="k">$1</span>');
    setState('ok', 'IR 已生成');
  } catch (e) {
    irPre.textContent = '生成失败：' + e.message;
  }
}

/* ═══ 真实编译运行 ═══ */
async function runProgram() {
  const src = code.value;
  if (!src.trim()) { setState('', '代码是空的'); return; }
  document.body.classList.remove('ir-mode');
  document.body.classList.add('run-mode');
  $('name-out').textContent = '运行结果';
  const outBox = $('run-out');
  outBox.innerHTML = `<div class="empty" style="height:auto;padding:30px">
    <h3>编译运行中…</h3></div>`;
  setState('busy', '编译运行中…');
  const stdinData = $('run-stdin').value;
  try {
    const r = await post('/api/run', {code: src, stdin: stdinData});
    // 先显示诊断（编译前的检查结果）
    if (r.diags && r.diags.length) renderDiagsOnly(r.diags);
    const cls = r.ok ? 'ok' : 'bad';
    const stageTxt = {semantic: '语义检查未通过', codegen: '代码生成失败',
                      build: '汇编/链接失败', run: '运行完成'}[r.stage] || r.stage;
    let html = `<div class="run-box">
      <div class="rb-head">
        <span class="${r.ok ? 'st-ok' : 'st-bad'}">${r.ok ? '●' : '×'} ${stageTxt}</span>
        <span style="margin-left:auto">${r.exit !== null && r.exit !== undefined
          ? '退出码 ' + r.exit : ''}</span>
      </div>
      <pre id="run-stdout">${esc(r.stdout || '（无输出）')}</pre>` +
      (r.stderr ? `<pre id="run-stderr">${esc(r.stderr)}</pre>` : '') +
      `</div>`;
    outBox.innerHTML = html;
    setState(r.ok ? 'ok' : 'bad',
             r.ok ? '运行完成' : stageTxt);
  } catch (e) {
    outBox.innerHTML = `<div class="run-box">
      <div class="rb-head"><span class="st-bad">× 请求失败</span></div>
      <pre id="run-stderr">${esc(e.message)}</pre></div>`;
    setState('bad', '请求失败');
  }
}
function renderDiagsOnly(ds) {
  // 运行前把诊断也亮出来（不动主诊断面板）
  const nE = ds.filter(d => d.level === 'error').length;
  errLines.clear(); warnLines.clear();
  for (const d of ds) {
    if (d.line > 0) {
      if (d.level === 'error') errLines.add(d.line);
      else warnLines.add(d.line);
    }
  }
  renderGutter();
}

/* ═══ 按钮 ═══ */
$('btn-check').onclick = () => {
  document.body.classList.remove('ir-mode');
  document.body.classList.remove('run-mode');
  $('name-out').textContent = '诊断';
  runCheck();
};
$('btn-ir').onclick = showIr;
$('btn-run').onclick = runProgram;
$('btn-sample').onclick = () => {
  code.value = SAMPLE;
  document.body.classList.remove('ir-mode');
  document.body.classList.remove('run-mode');
  $('name-out').textContent = '诊断';
  errLines.clear(); warnLines.clear();
  renderGutter(); refreshMeta(); renderHighlight();
  runCheck();
};
$('btn-clear').onclick = () => {
  code.value = '';
  document.body.classList.remove('ir-mode');
  document.body.classList.remove('run-mode');
  $('name-out').textContent = '诊断';
  errLines.clear(); warnLines.clear();
  renderGutter(); refreshMeta(); renderHighlight();
  out.innerHTML = `<div class="empty">
    <div class="glyph"><svg viewBox="0 0 24 24">
      <path d="M12 9v4M12 17h.01"/>
      <path d="M10.3 3.9L2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>
    </svg></div><h3>等待检查</h3>
    <p>把代码贴进左边，按 <kbd>Ctrl</kbd>+<kbd>Enter</kbd>。</p></div>`;
  $('meta-out').textContent = '';
  $('f-count').textContent = '—';
  $('lamp-out').className = 'lamp';
  setState('', '就绪');
  code.focus();
};

document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    $('btn-check').click();
  }
});

renderGutter(); refreshMeta(); renderHighlight(); code.focus();
</script>
</body>
</html>
'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # 只在出错时打印，避免每次请求刷屏
        pass

    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/ping":
            self._send(200, json.dumps({"ok": True}),
                       "application/json; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_SRC + 4096:
                # 必须把请求体读掉再回响应，否则客户端收到
                # "连接被中止"（WinError 10053）而不是 413。
                try:
                    self.rfile.read(min(n, 1 << 20))
                except Exception:
                    pass
                self._send(413, json.dumps(
                    {"ok": False, "stage": "io",
                     "diags": [{"level": "error", "code": "E0",
                                "line": 0, "col": 0,
                                "msg": "代码超过 200KB，请精简后再试",
                                "hint": ""}],
                     "notes": []}, ensure_ascii=False),
                    "application/json; charset=utf-8")
                return
            payload = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            src = payload.get("code") or ""

            if self.path == "/api/check":
                result = check_text(src)
            elif self.path == "/api/ir":
                result = {"ir": get_ir(src)}
            elif self.path == "/api/run":
                stdin_data = payload.get("stdin") or ""
                result = run_code(src, stdin_data)
            else:
                self._send(404, json.dumps({"ok": False}),
                           "application/json; charset=utf-8")
                return

            self._send(200, json.dumps(result, ensure_ascii=False),
                       "application/json; charset=utf-8")
        except Exception:
            # 工具本身崩了也要让页面看到，不能白屏
            tb = traceback.format_exc()[-800:]
            self._send(500, json.dumps(
                {"ok": False, "stage": "crash",
                 "diags": [{"level": "error", "code": "E99", "line": 0,
                            "col": 0,
                            "msg": "工具内部错误（这是 csub 的 bug）",
                            "hint": tb}],
                 "notes": []}, ensure_ascii=False),
                "application/json; charset=utf-8")


def main():
    ap = argparse.ArgumentParser(description="csub 网页版")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%d" % (args.host, args.port)
    print("csub web 已启动: " + url)
    print("按 Ctrl+C 停止")
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
