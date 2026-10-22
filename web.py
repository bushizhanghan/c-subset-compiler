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


PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>csub · C 代码查错</title>
<style>
/* ══════════════════════════════════════════════════════════
   csub 诊断台
   深空黑 + 冷光 + 玻璃拟态
   ══════════════════════════════════════════════════════════ */
:root{
  /* 表面层次：从最底到最亮 */
  --void:#07090d;
  --bg:#0b0e14;
  --surface:#10141c;
  --surface-2:#171c26;
  --surface-3:#1f2632;
  --line:rgb(255 255 255 / .06);
  --line-2:rgb(255 255 255 / .10);
  --line-3:rgb(255 255 255 / .16);

  --text:#eef1f6;
  --text-2:#a2acc0;
  --text-3:#697386;

  --cyan:#22d3ee;
  --cyan-2:#0891b2;
  --cyan-glow:rgb(34 211 238 / .5);

  --rose:#fb7185;
  --rose-2:#9f1239;
  --amber:#fbbf24;
  --amber-2:#92400e;
  --emerald:#34d399;
  --emerald-2:#065f46;

  --mono:"Cascadia Code","JetBrains Mono","Fira Code",
         ui-monospace,Consolas,monospace;
  --sans:"Inter","Segoe UI","Microsoft YaHei",system-ui,sans-serif;

  --r-sm:8px; --r-md:12px; --r-lg:16px; --r-xl:20px;
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
  text-rendering:optimizeLegibility;
}

/* ── 背景层：网格 + 顶部光锥 + 噪点 ── */
.bg{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden}
.bg .mesh{
  position:absolute;inset:-50%;
  background-image:
    linear-gradient(rgb(255 255 255 / .022) 1px, transparent 1px),
    linear-gradient(90deg, rgb(255 255 255 / .022) 1px, transparent 1px);
  background-size:56px 56px;
  /* 网格往边缘淡出 */
  mask-image:radial-gradient(ellipse 70% 60% at 50% 38%,
              #000 25%, transparent 78%);
  transform:perspective(700px) rotateX(1.5deg);
}
.bg .beam{
  position:absolute;left:50%;top:-38vh;
  width:130vw;height:80vh;
  transform:translateX(-50%);
  background:radial-gradient(ellipse at 50% 0%,
    rgb(34 211 238 / .13), rgb(34 211 238 / .04) 38%, transparent 68%);
  filter:blur(28px);
}
.bg .beam-2{
  position:absolute;left:50%;top:-10vh;
  width:70vw;height:46vh;transform:translateX(-50%);
  background:radial-gradient(ellipse at 50% 0%,
    rgb(99 102 241 / .08), transparent 64%);
  filter:blur(40px);
}
/* 噪点：一层极细的 SVG 噪声，让深色面不平坦 */
.bg .noise{
  position:absolute;inset:0;opacity:.028;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)'/%3E%3C/svg%3E");
}
/* 光标跟随的冷光 */
.bg .cursor-light{
  position:absolute;width:680px;height:680px;
  border-radius:50%;pointer-events:none;
  background:radial-gradient(circle,
    rgb(34 211 238 / .075), transparent 62%);
  transform:translate(-50%,-50%);
  transition:opacity .4s;
  opacity:0;
}
body.has-cursor .bg .cursor-light{opacity:1}

/* ── 顶栏 ── */
header{
  position:relative;z-index:10;flex-shrink:0;
  height:62px;display:flex;align-items:center;gap:16px;
  padding:0 22px;
  background:linear-gradient(180deg,
    rgb(16 20 28 / .92), rgb(16 20 28 / .55));
  backdrop-filter:blur(20px) saturate(150%);
  -webkit-backdrop-filter:blur(20px) saturate(150%);
  border-bottom:1px solid var(--line-2);
}
/* 顶栏底部一道细渐变线 */
header::after{
  content:"";position:absolute;left:0;right:0;bottom:-1px;height:1px;
  background:linear-gradient(90deg,
    transparent, rgb(34 211 238 / .35) 22%,
    rgb(34 211 238 / .12) 50%, transparent 78%);
}

/* 品牌 */
.brand{display:flex;align-items:center;gap:11px;flex-shrink:0}
.mark{
  position:relative;width:32px;height:32px;flex-shrink:0;
  border-radius:9px;
  background:
    linear-gradient(145deg, rgb(34 211 238 / .9), rgb(8 145 178 / .85));
  display:grid;place-items:center;
  box-shadow:
    0 0 0 1px rgb(255 255 255 / .12) inset,
    0 4px 18px rgb(34 211 238 / .3),
    0 0 42px rgb(34 211 238 / .16);
}
.mark span{
  font:700 15px/1 var(--mono);color:#031018;
  letter-spacing:-.04em;
}
/* 标记外一圈很淡的呼吸环 */
.mark::after{
  content:"";position:absolute;inset:-4px;border-radius:12px;
  border:1px solid rgb(34 211 238 / .18);
  animation:breathe 4s ease-in-out infinite;
}
@keyframes breathe{
  0%,100%{opacity:.5;transform:scale(1)}
  50%{opacity:1;transform:scale(1.06)}
}

.title-wrap{display:flex;flex-direction:column;gap:1px;line-height:1.15}
.title{
  font-size:15px;font-weight:650;letter-spacing:-.015em;
  color:var(--text);
}
.title em{
  font-style:normal;
  color:#67e8f9;                       /* fallback：不支持 clip 时也可见 */
  background:linear-gradient(92deg,#67e8f9,#22d3ee 55%,#38bdf8);
  -webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent;
}
.subtitle{
  font:11px/1 var(--mono);color:var(--text-3);
  letter-spacing:.02em;
}
.spacer{flex:1}

/* ── 按钮 ── */
.btn{
  position:relative;
  display:inline-flex;align-items:center;gap:7px;
  height:34px;padding:0 14px;
  background:rgb(255 255 255 / .035);
  color:var(--text-2);
  border:1px solid var(--line-2);
  border-radius:var(--r-sm);
  font:500 12.5px/1 var(--sans);
  cursor:pointer;white-space:nowrap;
  transition:background .16s,border-color .16s,color .16s,
             transform .1s,box-shadow .16s;
}
.btn svg{width:14px;height:14px;flex-shrink:0;
         stroke:currentColor;fill:none;stroke-width:1.8;
         stroke-linecap:round;stroke-linejoin:round}
.btn:hover{
  background:rgb(255 255 255 / .07);
  border-color:var(--line-3);color:var(--text);
}
.btn:active{transform:translateY(1px)}
.btn:focus-visible{
  outline:2px solid var(--cyan);outline-offset:2px;
}
.btn.primary{
  background:linear-gradient(180deg,#2dd4ef,#0891b2);
  color:#02181d;border-color:rgb(103 232 249 / .6);
  font-weight:650;
  box-shadow:
    0 1px 0 rgb(255 255 255 / .25) inset,
    0 3px 14px rgb(34 211 238 / .28);
}
.btn.primary:hover{
  background:linear-gradient(180deg,#4ce0f7,#0ba5c9);
  box-shadow:
    0 1px 0 rgb(255 255 255 / .3) inset,
    0 4px 20px rgb(34 211 238 / .4);
}
.btn kbd{
  font:700 10.5px/1 var(--mono);
  padding:3px 5px;border-radius:4px;
  background:rgb(2 24 29 / .22);
  border:1px solid rgb(2 24 29 / .2);
  color:#02181d;
  opacity:.85;
}

/* ── 主区 ── */
main{
  position:relative;z-index:5;flex:1;
  display:grid;grid-template-columns:1fr 1fr;
  min-height:0;padding:14px;gap:14px;
}
.pane{
  position:relative;
  display:flex;flex-direction:column;
  min-width:0;min-height:0;
  background:linear-gradient(180deg,
    rgb(23 28 38 / .72), rgb(16 20 28 / .88));
  border:1px solid var(--line-2);
  border-radius:var(--r-lg);
  overflow:hidden;
  box-shadow:
    0 1px 0 rgb(255 255 255 / .04) inset,
    0 20px 50px rgb(0 0 0 / .45),
    0 0 0 1px rgb(0 0 0 / .2);
  backdrop-filter:blur(12px);
  -webkit-backdrop-filter:blur(12px);
}
/* 面板顶部一道高光 */
.pane::before{
  content:"";position:absolute;left:0;right:0;top:0;height:1px;
  background:linear-gradient(90deg,
    transparent, rgb(255 255 255 / .14) 35%,
    rgb(255 255 255 / .14) 65%, transparent);
  pointer-events:none;z-index:2;
}

.pane-head{
  flex-shrink:0;height:42px;
  display:flex;align-items:center;gap:9px;
  padding:0 16px;
  border-bottom:1px solid var(--line);
  background:rgb(11 14 20 / .5);
  user-select:none;
}
.pane-head .lamp{
  width:7px;height:7px;border-radius:50%;
  background:var(--text-3);flex-shrink:0;
  transition:background .2s,box-shadow .2s;
}
.pane-head .lamp.on{
  background:var(--cyan);
  box-shadow:0 0 10px var(--cyan-glow);
}
.pane-head .lamp.err{
  background:var(--rose);
  box-shadow:0 0 10px rgb(251 113 133 / .5);
}
.pane-head .name{
  font:600 12px/1 var(--sans);
  color:var(--text-2);letter-spacing:.01em;
}
.pane-head .meta{
  margin-left:auto;display:flex;align-items:center;gap:10px;
  font:11.5px/1 var(--mono);color:var(--text-3);
}
.pane-head .meta .sep{color:var(--line-3)}

/* ── 编辑器 ── */
.editor{flex:1;display:flex;min-height:0;position:relative}

.gutter{
  flex-shrink:0;width:58px;
  padding:16px 0;
  overflow:hidden;user-select:none;
  font:12.5px/1.78 var(--mono);
  text-align:right;
  color:#7d8798;
  background:rgb(11 14 20 / .42);
  border-right:1px solid var(--line);
}
.gutter .gl{
  padding-right:11px;
  border-right:2px solid transparent;
  transition:color .14s,border-color .14s,background .14s;
}
.gutter .gl.warn{
  color:var(--amber);font-weight:600;
  border-right-color:var(--amber);
  background:linear-gradient(90deg,
    transparent, rgb(251 191 36 / .1));
}
.gutter .gl.err{
  color:var(--rose);font-weight:700;
  border-right-color:var(--rose);
  background:linear-gradient(90deg,
    transparent, rgb(251 113 133 / .13));
}

/* 代码区：透明 textarea 叠在高亮层上 */
.code-wrap{flex:1;position:relative;min-width:0;overflow:hidden}
.code-wrap .hl{
  position:absolute;inset:0;
  padding:16px 18px;
  font:12.5px/1.78 var(--mono);
  tab-size:4;white-space:pre;overflow:hidden;
  pointer-events:none;color:var(--text);
  word-break:break-all;
}
.code-wrap .hl .c{color:#5a6b82;font-style:italic}   /* 注释 */
.code-wrap .hl .s{color:#7fd88f}                     /* 字符串 */
.code-wrap .hl .p{color:#c792ea}                     /* 预处理 */
.code-wrap .hl .k{color:#67e8f9;font-weight:500}     /* 关键字 */
.code-wrap .hl .n{color:#fbbf24}                     /* 数字 */
.code-wrap .hl .f{color:#a5b4fc}                     /* 函数名 */
.code-wrap .hl .t{color:#5eead4}                     /* 类型 */

textarea{
  position:relative;z-index:1;
  width:100%;height:100%;
  padding:16px 18px;
  background:transparent;color:transparent;
  border:0;outline:0;resize:none;
  font:12.5px/1.78 var(--mono);
  tab-size:4;
  caret-color:var(--cyan);
  scrollbar-width:thin;
  -webkit-text-fill-color:transparent;
}
textarea::selection{
  background:rgb(34 211 238 / .26);
  -webkit-text-fill-color:transparent;
}
textarea::placeholder{
  color:var(--text-3);line-height:2.1;
  -webkit-text-fill-color:var(--text-3);
}

/* 滚动条 */
textarea::-webkit-scrollbar,
#out::-webkit-scrollbar,
#irpane::-webkit-scrollbar{width:10px;height:10px}
textarea::-webkit-scrollbar-track,
#out::-webkit-scrollbar-track,
#irpane::-webkit-scrollbar-track{background:transparent}
textarea::-webkit-scrollbar-thumb,
#out::-webkit-scrollbar-thumb,
#irpane::-webkit-scrollbar-thumb{
  background:rgb(255 255 255 / .09);
  border-radius:10px;border:3px solid transparent;
  background-clip:padding-box;
}
textarea::-webkit-scrollbar-thumb:hover,
#out::-webkit-scrollbar-thumb:hover{
  background:rgb(255 255 255 / .16);
  background-clip:padding-box;
}

/* ── 诊断流 ── */
#out{
  flex:1;overflow-y:auto;padding:16px;
  scroll-behavior:smooth;
}

/* 空状态 */
.empty{
  height:100%;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:18px;
  text-align:center;padding:24px;
}
.empty .glyph{
  width:74px;height:74px;border-radius:20px;
  display:grid;place-items:center;
  background:linear-gradient(160deg,
    rgb(34 211 238 / .09), rgb(34 211 238 / .02));
  border:1px solid rgb(34 211 238 / .16);
  box-shadow:0 0 40px rgb(34 211 238 / .08) inset;
}
.empty .glyph svg{
  width:32px;height:32px;stroke:var(--cyan);opacity:.75;
  fill:none;stroke-width:1.5;
  stroke-linecap:round;stroke-linejoin:round;
}
.empty h3{
  font-size:14px;font-weight:600;color:var(--text-2);
  letter-spacing:.01em;
}
.empty p{
  font-size:12.5px;line-height:1.85;color:var(--text-3);
  max-width:280px;
}
.empty .caps{
  display:flex;flex-direction:column;gap:7px;
  margin-top:4px;width:100%;max-width:320px;
}
.empty .cap{
  display:flex;align-items:center;gap:9px;
  padding:8px 12px;
  background:rgb(255 255 255 / .022);
  border:1px solid var(--line);
  border-radius:8px;
  font-size:11.5px;line-height:1.5;
  color:var(--text-2);text-align:left;
}
.empty .cap i{
  width:6px;height:6px;border-radius:50%;flex-shrink:0;
}
.empty .cap.dim{opacity:.62}
.empty .cap.dim b{color:var(--rose);font-weight:600}
.empty kbd{
  font:600 11px/1 var(--mono);
  padding:3px 6px;border-radius:5px;
  background:var(--surface-3);
  border:1px solid var(--line-3);
  color:var(--text-2);
}

/* 摘要条 */
.summary{
  display:flex;align-items:center;gap:12px;
  padding:14px 16px;margin-bottom:14px;
  border-radius:var(--r-md);
  border:1px solid;
  animation:pop .3s cubic-bezier(.2,.9,.3,1.2) both;
}
.summary.good{
  background:linear-gradient(100deg,
    rgb(52 211 153 / .1), rgb(52 211 153 / .03));
  border-color:rgb(52 211 153 / .28);
  box-shadow:0 0 30px rgb(52 211 153 / .07) inset;
}
.summary.bad{
  background:linear-gradient(100deg,
    rgb(251 113 133 / .12), rgb(251 113 133 / .03));
  border-color:rgb(251 113 133 / .3);
  box-shadow:0 0 30px rgb(251 113 133 / .08) inset;
}
.summary .icon{
  width:34px;height:34px;border-radius:10px;flex-shrink:0;
  display:grid;place-items:center;
}
.summary.good .icon{
  background:rgb(52 211 153 / .13);
  border:1px solid rgb(52 211 153 / .26);
}
.summary.bad .icon{
  background:rgb(251 113 133 / .13);
  border:1px solid rgb(251 113 133 / .26);
}
.summary .icon svg{
  width:17px;height:17px;fill:none;stroke-width:2.2;
  stroke-linecap:round;stroke-linejoin:round;
}
.summary.good .icon svg{stroke:var(--emerald)}
.summary.bad .icon svg{stroke:var(--rose)}
.summary .text{display:flex;flex-direction:column;gap:2px}
.summary .big{
  font-size:14.5px;font-weight:700;letter-spacing:-.01em;
  color:var(--text);
  text-shadow:0 0 20px rgb(0 0 0 / .5);
}
.summary.good .big{color:#5eead4}
.summary.bad .big{color:#fda4af}
.summary .small{
  font-size:12px;color:var(--text-2);
  font-weight:450;
}
.summary .chips{margin-left:auto;display:flex;gap:7px}
.chip{
  display:inline-flex;align-items:center;gap:5px;
  padding:5px 10px;border-radius:7px;
  font:600 11.5px/1 var(--mono);
  border:1px solid var(--line-2);
  background:rgb(255 255 255 / .03);
  color:var(--text-2);
}
.chip i{width:5px;height:5px;border-radius:50%;flex-shrink:0}
.chip.e{color:var(--rose);border-color:rgb(251 113 133 / .3);
        background:rgb(251 113 133 / .08)}
.chip.e i{background:var(--rose);box-shadow:0 0 6px var(--rose)}
.chip.w{color:var(--amber);border-color:rgb(251 191 36 / .3);
        background:rgb(251 191 36 / .08)}
.chip.w i{background:var(--amber);box-shadow:0 0 6px var(--amber)}

/* 诊断流：左侧时间轴 */
#out .flow{position:relative;padding-left:18px}
#out .flow::before{
  content:"";position:absolute;left:5px;top:6px;bottom:6px;width:1px;
  background:linear-gradient(180deg,
    rgb(255 255 255 / .03), rgb(255 255 255 / .16) 8%,
    rgb(255 255 255 / .16) 92%, rgb(255 255 255 / .03));
  pointer-events:none;
}
/* 每条诊断在时间轴上的节点 */
#out .flow .diag::before{
  content:"";position:absolute;
  left:-15px;top:19px;
  width:5px;height:5px;border-radius:50%;
  background:var(--text-3);
  box-shadow:0 0 0 3px rgb(11 14 20);
}
#out .flow .diag.err::before{
  background:var(--rose);
  box-shadow:0 0 0 3px rgb(11 14 20), 0 0 10px var(--rose);
}
#out .flow .diag.warn::before{
  background:var(--amber);
  box-shadow:0 0 0 3px rgb(11 14 20), 0 0 10px var(--amber);
}
/* 摘要卡在时间轴上不画节点 */
#out .flow .summary{position:relative;z-index:1;margin-left:-8px}
/* 诊断卡 */
.diag{
  position:relative;
  display:flex;
  margin-bottom:10px;
  background:linear-gradient(180deg,
    rgb(255 255 255 / .028), rgb(255 255 255 / .012));
  border:1px solid var(--line);
  border-radius:var(--r-md);
  overflow:hidden;cursor:pointer;
  transition:border-color .16s,background .16s,transform .12s,
             box-shadow .16s;
}
.diag.enter{animation:slide .26s cubic-bezier(.2,.9,.3,1) both}
.diag:hover{
  border-color:rgb(255 255 255 / .2);
  background:linear-gradient(180deg,
    rgb(255 255 255 / .07), rgb(255 255 255 / .03));
  transform:translateX(3px);
  box-shadow:0 6px 24px rgb(0 0 0 / .3);
}
.diag:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}

/* 左侧严重度光条 */
.diag .edge{
  width:3px;flex-shrink:0;position:relative;
}
.diag .edge::after{
  content:"";position:absolute;inset:0;
  filter:blur(5px);opacity:.7;
}
.diag.err .edge{background:var(--rose)}
.diag.err .edge::after{background:var(--rose)}
.diag.warn .edge{background:var(--amber)}
.diag.warn .edge::after{background:var(--amber)}

.diag .content{padding:12px 15px;flex:1;min-width:0}
.diag .top{
  display:flex;align-items:center;gap:9px;flex-wrap:wrap;
  margin-bottom:5px;
}
.badge{
  display:inline-flex;align-items:center;
  padding:4px 8px;border-radius:6px;
  font:700 10.5px/1 var(--mono);letter-spacing:.04em;
  flex-shrink:0;
}
.diag.err .badge{
  background:rgb(251 113 133 / .14);
  color:var(--rose);
  border:1px solid rgb(251 113 133 / .28);
}
.diag.warn .badge{
  background:rgb(251 191 36 / .14);
  color:var(--amber);
  border:1px solid rgb(251 191 36 / .28);
}
.where{
  font:11.5px/1 var(--mono);color:var(--text-3);
  display:inline-flex;align-items:center;gap:4px;
  flex-shrink:0;
}
.where b{
  color:var(--cyan);font-weight:600;
  font-variant-numeric:tabular-nums;
}
.diag .what{
  font-size:13.5px;line-height:1.6;
  color:#e8edf5;
  font-weight:400;
  letter-spacing:.005em;
}
.diag .fix{
  margin-top:9px;
  padding:9px 12px;
  border-radius:8px;
  background:rgb(34 211 238 / .08);
  border:1px solid rgb(34 211 238 / .14);
  border-left:2px solid rgb(34 211 238 / .55);
  font-size:12.5px;line-height:1.68;color:#cdd7e5;
  display:flex;gap:9px;
}
.diag .fix .lbl{
  font:600 10.5px/1.7 var(--mono);
  color:var(--cyan);flex-shrink:0;
  letter-spacing:.03em;
}

/* 预处理说明 */
.notes{
  margin-top:16px;padding:13px 15px;
  background:rgb(255 255 255 / .022);
  border:1px solid var(--line);
  border-radius:var(--r-md);
}
.notes .h{
  display:flex;align-items:center;gap:7px;
  font:600 10.5px/1 var(--sans);
  color:var(--text-3);
  text-transform:uppercase;letter-spacing:.08em;
  margin-bottom:9px;
}
.notes .h svg{
  width:12px;height:12px;stroke:var(--text-3);fill:none;
  stroke-width:2;stroke-linecap:round;
}
.notes .item{
  display:flex;gap:8px;padding:3.5px 0;
  font-size:12.5px;line-height:1.6;color:var(--text-2);
}
.notes .item::before{
  content:"";width:4px;height:4px;border-radius:50%;
  background:var(--text-3);flex-shrink:0;margin-top:8px;
}

/* ── IR 面板 ── */
#irpane{display:none;flex:1;overflow:auto;padding:16px}
body.ir-mode #irpane{display:block}
body.ir-mode #out{display:none}
#ir{
  font:12.5px/1.75 var(--mono);
  color:#9fb0c6;white-space:pre;tab-size:2;
}
#ir .c{color:#4d5a6e;font-style:italic}
#ir .l{color:var(--cyan)}
#ir .k{color:var(--amber)}
#ir .n{color:var(--emerald)}

/* ── 底栏 ── */
footer{
  position:relative;z-index:10;flex-shrink:0;
  height:32px;display:flex;align-items:center;gap:18px;
  padding:0 22px;
  background:rgb(11 14 20 / .8);
  backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px);
  border-top:1px solid var(--line-2);
  font:11.5px/1 var(--mono);color:var(--text-3);
}
footer .st{display:inline-flex;align-items:center;gap:6px}
footer .st i{
  width:6px;height:6px;border-radius:50%;
  background:var(--text-3);transition:background .2s,box-shadow .2s;
}
footer .st.busy i{background:var(--cyan);box-shadow:0 0 8px var(--cyan)}
footer .st.ok i{background:var(--emerald);box-shadow:0 0 8px var(--emerald)}
footer .st.bad i{background:var(--rose);box-shadow:0 0 8px var(--rose)}
footer .div{color:var(--line-3)}
footer .right{margin-left:auto;display:flex;gap:18px;align-items:center}
footer b{color:var(--text-2);font-weight:600}

/* 动效 */
@keyframes pop{
  from{opacity:0;transform:scale(.97) translateY(4px)}
  to{opacity:1;transform:scale(1) translateY(0)}
}
@keyframes slide{
  from{opacity:0;transform:translateX(-6px)}
  to{opacity:1;transform:translateX(0)}
}
@media (prefers-reduced-motion:reduce){
  *{animation:none!important;transition:none!important}
  #out{scroll-behavior:auto}
}

/* 窄屏 */
@media (max-width:960px){
  main{grid-template-columns:1fr;grid-template-rows:1fr 1fr;padding:10px;gap:10px}
  .subtitle{display:none}
  header{height:56px;padding:0 14px;gap:10px}
  .btn span.lbl{display:none}
  .btn{padding:0 11px}
}
</style>
</head>
<body>

<!-- 背景层 -->
<div class="bg">
  <div class="beam"></div>
  <div class="beam-2"></div>
  <div class="mesh"></div>
  <div class="cursor-light" id="clight"></div>
  <div class="noise"></div>
</div>

<header>
  <div class="brand">
    <div class="mark"><span>&lt;/&gt;</span></div>
    <div class="title-wrap">
      <div class="title">csub <em>诊断台</em></div>
      <div class="subtitle">C SUBSET COMPILER · v1.0</div>
    </div>
  </div>
  <div class="spacer"></div>

  <button class="btn" id="btn-sample" title="载入一段有 4 个典型问题的代码">
    <svg viewBox="0 0 24 24"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M13 2v7h7"/></svg>
    <span class="lbl">示例</span>
  </button>
  <button class="btn" id="btn-ir" title="查看三地址码中间表示">
    <svg viewBox="0 0 24 24"><path d="M4 6h6M4 12h10M4 18h6"/><circle cx="18" cy="6" r="2"/><circle cx="18" cy="18" r="2"/></svg>
    <span class="lbl">三地址码</span>
  </button>
  <button class="btn" id="btn-clear" title="清空编辑器">
    <svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
    <span class="lbl">清空</span>
  </button>
  <button class="btn primary" id="btn-check">
    <svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>
    检查 <kbd>Ctrl ↵</kbd>
  </button>
</header>

<main>
  <!-- 编辑器 -->
  <section class="pane">
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
          autocapitalize="off" autofocus
          placeholder="在此粘贴 C 代码&#10;&#10;支持 #include / 宏 / 结构体 / 指针 / 递归&#10;&#10;Ctrl + Enter  检查"></textarea>
      </div>
    </div>
  </section>

  <!-- 诊断 -->
  <section class="pane">
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
          <div class="cap"><i style="background:var(--rose)"></i>
            类型混淆 · 未初始化 · scanf 漏 &amp;</div>
          <div class="cap"><i style="background:var(--amber)"></i>
            越界 · 死循环 · 漏 return · 冗余表达式</div>
          <div class="cap dim"><i style="background:var(--text-3)"></i>
            算法逻辑错、内存泄漏 → <b>查不到</b></div>
        </div>
      </div>
    </div>
    <div id="irpane"><pre id="ir"></pre></div>
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
const hl = $('hl');
const irPre = $('ir'), irPane = $('irpane');

const SAMPLE = `#include <stdio.h>

/* 这段代码埋了 4 个典型问题，点「检查」看能不能全查出来 */

int main() {
    int  a[5];
    int  i, sum;

    /* ① 循环越界：i 最大取 5，但 a[5] 不存在 */
    for (i = 0; i <= 5; i++) {
        a[i] = i * 2;
    }

    /* ② 赋值当比较：sum 还没初始化 */
    if (sum = 10) {
        printf("%d\\n", sum);
    }

    /* ③ scanf 漏写 & */
    int n;
    scanf("%d", n);

    /* ④ 用了未声明的变量 */
    printf("%d\\n", undeclared);

    return 0;
}
`;

/* ═══ 状态 ═══ */
let errLines = new Set(), warnLines = new Set();

/* ═══ 语法高亮 ═══
   透明 textarea 叠在 <pre class="hl"> 上。
   只做 C 的主要元素：注释 / 字符串 / 预处理 / 关键字 / 类型 / 数字。
   不做完美词法分析——够看就行，也不能拖慢输入。
*/
const KW = new Set(['if','else','while','for','return','break','continue',
  'switch','case','default','do','goto','sizeof','struct','union','enum',
  'typedef','static','const','extern','inline','volatile','restrict',
  'signed','unsigned','_Bool','bool','true','false','NULL']);
const TY = new Set(['int','char','void','float','double','long','short',
  'size_t','FILE','struct','union','enum']);

function hlLine(line) {
  let out = '', i = 0, n = line.length;
  while (i < n) {
    const ch = line[i];

    // 行注释
    if (ch === '/' && line[i+1] === '/') {
      out += `<span class="c">${esc(line.slice(i))}</span>`;
      break;
    }
    // 块注释（单行内闭合；跨行的由逐行处理兜底）
    if (ch === '/' && line[i+1] === '*') {
      const e = line.indexOf('*/', i + 2);
      if (e >= 0) {
        out += `<span class="c">${esc(line.slice(i, e + 2))}</span>`;
        i = e + 2; continue;
      }
      out += `<span class="c">${esc(line.slice(i))}</span>`;
      break;
    }
    // 字符串 / 字符
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
    // 预处理
    if (ch === '#' && line.slice(0, i).trim() === '') {
      out += `<span class="p">${esc(line.slice(i))}</span>`;
      break;
    }
    // 标识符
    if (/[A-Za-z_]/.test(ch)) {
      let j = i;
      while (j < n && /[A-Za-z0-9_]/.test(line[j])) j++;
      const w = line.slice(i, j);
      const cls = KW.has(w) ? 'k' : (TY.has(w) ? 't' : null);
      // 函数调用：后面紧跟 (
      const isCall = line[j] === '(';
      if (cls) out += `<span class="${cls}">${esc(w)}</span>`;
      else if (isCall) out += `<span class="f">${esc(w)}</span>`;
      else out += esc(w);
      i = j; continue;
    }
    // 数字
    if (/[0-9]/.test(ch)) {
      let j = i;
      while (j < n && /[0-9a-fA-FxX.]/.test(line[j])) j++;
      out += `<span class="n">${esc(line.slice(i, j))}</span>`;
      i = j; continue;
    }
    out += esc(ch);
    i++;
  }
  return out;
}

function renderHighlight() {
  const lines = code.value.split('\n');
  hl.innerHTML = lines.map(hlLine).join('\n');
  hl.scrollTop = code.scrollTop;
  hl.scrollLeft = code.scrollLeft;
}

/* ═══ 行号槽 ═══ */
function renderGutter() {
  const n = Math.max(code.value.split('\n').length, 1);
  const buf = [];
  for (let i = 1; i <= n; i++) {
    const c = errLines.has(i) ? ' err'
            : warnLines.has(i) ? ' warn' : '';
    // 必须是块级元素：inline 元素的 \n 不换行
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

/* 光标跟随光晕 */
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
window.addEventListener('mouseleave', () => {
  document.body.classList.remove('has-cursor');
});

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
      </svg></div>
      <h3>代码是空的</h3>
      <p>先粘贴一些 C 代码再检查</p></div>`;
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

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

/* ═══ 渲染 ═══ */
function render(data) {
  const ds = data.diags || [];
  const nE = ds.filter(d => d.level === 'error').length;
  const nW = ds.filter(d => d.level === 'warning').length;

  // 行号槽标记
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
        ${nE
          ? '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>'
          : '<path d="M20 6L9 17l-5-5"/>'}
      </svg></div>
      <div class="text">
        <div class="big">${nE ? '发现 ' + nE + ' 个错误' : '只有警告'}</div>
        <div class="small">${nE
          ? '这些问题通常会导致程序出错'
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
                  tabindex="0" role="button"
                  aria-label="${esc(d.code)} 第 ${d.line} 行: ${esc(d.msg)}">
      <div class="edge"></div>
      <div class="content">
        <div class="top">
          <span class="badge">${esc(d.code)}</span>
          <span class="where">
            <svg viewBox="0 0 24 24" width="11" height="11"
                 fill="none" stroke="currentColor" stroke-width="2">
              <path d="M4 6h16M4 12h16M4 18h10"/>
            </svg>
            第 <b>${d.line}</b> 行
          </span>
          ${d.hint ? '' : ''}
        </div>
        <div class="what">${esc(d.msg)}</div>
        ${d.hint ? `<div class="fix">
          <span class="lbl">建议</span>
          <span>${esc(d.hint)}</span>
        </div>` : ''}
      </div>
    </div>`;
  }

  if (data.notes && data.notes.length) {
    html += `<div class="notes">
      <div class="h">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>
          <path d="M12 16v-4M12 8h.01"/></svg>
        预处理说明
      </div>`;
    for (const n of data.notes)
      html += `<div class="item"><span>${esc(n)}</span></div>`;
    html += `</div>`;
  }

  out.innerHTML = `<div class="flow">${html}</div>`;

  // 交互绑定 + 动画清理
  out.querySelectorAll('.diag').forEach(el => {
    const go = () => jumpTo(parseInt(el.dataset.line, 10));
    el.onclick = go;
    el.onkeydown = e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
    };
    el.addEventListener('animationend',
      () => el.classList.remove('enter'), {once: true});
  });

  const lampOut = $('lamp-out');
  lampOut.className = 'lamp ' + (nE ? 'err' : (nW ? 'on' : 'on'));
  $('meta-out').textContent = ds.length ? ds.length + ' 条' : '';
  $('f-count').textContent = ds.length
    ? `${nE} 错误 · ${nW} 警告` : '0 条';
  setState(nE ? 'bad' : (nW ? '' : 'ok'),
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
}

/* ═══ IR ═══ */
async function showIr() {
  const src = code.value;
  if (!src.trim()) return;
  document.body.classList.add('ir-mode');
  $('name-out').textContent = '三地址码';
  irPre.textContent = '生成中…';
  try {
    const data = await post('/api/ir', {code: src});
    irPre.innerHTML = colorIr(data.ir || '（空）');
    setState('ok', 'IR 已生成');
  } catch (e) {
    irPre.textContent = '生成失败：' + e.message;
  }
}

function colorIr(s) {
  return esc(s)
    .replace(/^(\s*;.*)$/gm, '<span class="c">$1</span>')
    .replace(/\b(func\s+\w+|L\d+):/g, '<span class="l">$1:</span>')
    .replace(/\b(return|call|goto|ifFalse|if)\b/g, '<span class="k">$1</span>');
}

/* ═══ 按钮 ═══ */
$('btn-check').onclick = () => {
  document.body.classList.remove('ir-mode');
  $('name-out').textContent = '诊断';
  runCheck();
};
$('btn-ir').onclick = showIr;
$('btn-sample').onclick = () => {
  code.value = SAMPLE;
  document.body.classList.remove('ir-mode');
  $('name-out').textContent = '诊断';
  errLines.clear(); warnLines.clear();
  renderGutter(); refreshMeta(); renderHighlight();
  runCheck();
};
$('btn-clear').onclick = () => {
  code.value = '';
  document.body.classList.remove('ir-mode');
  $('name-out').textContent = '诊断';
  errLines.clear(); warnLines.clear();
  renderGutter(); refreshMeta(); renderHighlight();
  out.innerHTML = `<div class="empty">
    <div class="glyph"><svg viewBox="0 0 24 24">
      <path d="M12 9v4M12 17h.01"/>
      <path d="M10.3 3.9L2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>
    </svg></div>
    <h3>等待检查</h3>
    <p>把代码贴进左边，按 <kbd>Ctrl</kbd>+<kbd>Enter</kbd>。</p></div>`;
  $('meta-out').textContent = '';
  $('f-count').textContent = '—';
  $('lamp-out').className = 'lamp';
  setState('', '就绪');
  code.focus();
};

/* 快捷键 */
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    $('btn-check').click();
  }
});

/* 启动 */
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
