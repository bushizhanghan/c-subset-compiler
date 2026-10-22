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
:root{
  --bg:#0a0c11;
  --surface:#11141c;
  --surface-2:#161a24;
  --surface-3:#1c2130;
  --line:#232838;
  --line-bright:#2e3648;
  --text:#e4e8f0;
  --text-2:#9aa4b8;
  --text-3:#636d82;
  --accent:#38bdf8;
  --accent-dim:#0e7490;
  --err:#fb7185;
  --err-bg:#2a1218;
  --warn:#fbbf24;
  --warn-bg:#2a2210;
  --ok:#34d399;
  --ok-bg:#0f241d;
  --mono:"Cascadia Code","JetBrains Mono","Fira Code",Consolas,monospace;
  --sans:"Inter","Segoe UI","Microsoft YaHei",system-ui,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{
  background:var(--bg);
  color:var(--text);
  font:14px/1.6 var(--sans);
  display:flex;flex-direction:column;
  overflow:hidden;
  -webkit-font-smoothing:antialiased;
}

/* 背景：极淡的网格 + 顶部光晕，不抢内容 */
body::before{
  content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
  background:
    radial-gradient(ellipse 80% 50% at 50% -20%,
      rgb(56 189 248 / .10), transparent 70%),
    linear-gradient(var(--line) 1px, transparent 1px) 0 0/100% 48px,
    linear-gradient(90deg, var(--line) 1px, transparent 1px) 0 0/48px 100%;
  opacity:.35;
  mask-image:radial-gradient(ellipse 100% 70% at 50% 0%, #000 40%, transparent 100%);
}

/* ── 顶栏 ── */
header{
  position:relative;z-index:2;flex-shrink:0;
  display:flex;align-items:center;gap:14px;
  padding:0 20px;height:56px;
  background:linear-gradient(180deg,var(--surface),transparent);
  border-bottom:1px solid var(--line);
}
.brand{display:flex;align-items:center;gap:10px;flex-shrink:0}
.logo{
  width:26px;height:26px;border-radius:7px;
  background:linear-gradient(135deg,var(--accent),var(--accent-dim));
  display:grid;place-items:center;
  font:600 13px/1 var(--mono);color:#001018;
  box-shadow:0 0 20px rgb(56 189 248 / .3);
}
.brand h1{font-size:15px;font-weight:600;letter-spacing:-.01em}
.brand .sub{
  font:11px/1 var(--mono);color:var(--text-3);
  border:1px solid var(--line-bright);border-radius:4px;
  padding:3px 5px;margin-left:2px;
}
.spacer{flex:1}

/* 按钮 */
.btn{
  display:inline-flex;align-items:center;gap:6px;
  height:32px;padding:0 13px;
  background:var(--surface-2);color:var(--text-2);
  border:1px solid var(--line-bright);border-radius:7px;
  font:500 12.5px/1 var(--sans);cursor:pointer;
  transition:background .15s,border-color .15s,color .15s,
             transform .1s;
  white-space:nowrap;
}
.btn:hover{background:var(--surface-3);color:var(--text);
           border-color:#3d475e}
.btn:active{transform:translateY(1px)}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.btn.primary{
  background:linear-gradient(180deg,#3ec8fb,#0ea5d9);
  color:#001820;border-color:#5fd4ff;font-weight:600;
  box-shadow:0 2px 12px rgb(56 189 248 / .25);
}
.btn.primary:hover{filter:brightness(1.07)}
.btn kbd{
  font:11px/1 var(--mono);opacity:.6;
  border:1px solid rgb(0 0 0 / .25);border-radius:3px;
  padding:2px 4px;margin-left:1px;
}

/* ── 主体三栏 ── */
main{
  position:relative;z-index:1;flex:1;
  display:grid;grid-template-columns:1fr 1fr;
  min-height:0;
}
.pane{display:flex;flex-direction:column;min-width:0;min-height:0}
.pane+.pane{border-left:1px solid var(--line)}

.pane-head{
  flex-shrink:0;height:38px;
  display:flex;align-items:center;gap:8px;
  padding:0 16px;
  background:var(--surface);
  border-bottom:1px solid var(--line);
  font-size:12px;color:var(--text-2);
  user-select:none;
}
.pane-head .dot{
  width:6px;height:6px;border-radius:50%;
  background:var(--accent);box-shadow:0 0 8px var(--accent);
}
.pane-head .dot.muted{background:var(--text-3);box-shadow:none}
.pane-head .right{margin-left:auto;display:flex;gap:10px;
                   align-items:center;font:11.5px var(--mono);
                   color:var(--text-3)}

/* ── 编辑器 ── */
.editor{flex:1;display:flex;min-height:0;position:relative}
.gutter{
  flex-shrink:0;width:56px;
  padding:14px 0;
  text-align:right;
  font:12.5px/1.75 var(--mono);
  color:var(--text-3);
  background:var(--surface);
  border-right:1px solid var(--line);
  overflow:hidden;
  user-select:none;
}
/* 每行一个块：行号必须块级，否则挤成一行 */
.gutter .gl{
  padding-right:10px;
  border-right:2px solid transparent;
  transition:color .12s,border-color .12s;
}
.gutter .gl.hi{
  color:var(--warn);font-weight:600;
  border-right-color:var(--warn);
  background:rgb(251 191 36 / .07);
}
.gutter .gl.hi-e{
  color:var(--err);font-weight:700;
  border-right-color:var(--err);
  background:rgb(251 113 133 / .09);
}
textarea{
  flex:1;width:100%;
  padding:14px 16px;
  background:transparent;color:var(--text);
  border:0;outline:0;resize:none;
  font:12.5px/1.75 var(--mono);
  tab-size:4;
  caret-color:var(--accent);
}
textarea::placeholder{color:var(--text-3)}
textarea::selection{background:rgb(56 189 248 / .25)}

/* ── 诊断输出 ── */
#out{flex:1;overflow-y:auto;padding:16px;scroll-behavior:smooth}
#out::-webkit-scrollbar,.gutter::-webkit-scrollbar{
  width:9px;height:9px}
#out::-webkit-scrollbar-thumb{
  background:var(--line-bright);border-radius:9px;
  border:2px solid var(--bg)}
#out::-webkit-scrollbar-thumb:hover{background:#3d475e}

.placeholder{
  height:100%;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:14px;
  color:var(--text-3);text-align:center;padding:20px;
}
.placeholder .big{
  font-size:38px;opacity:.15;
  font-family:var(--mono);
}
.placeholder .tip{font-size:13px;line-height:1.8;max-width:300px}
.placeholder kbd{
  font:11.5px var(--mono);color:var(--text-2);
  background:var(--surface-2);border:1px solid var(--line-bright);
  border-radius:4px;padding:2px 6px;
}

/* 摘要卡 */
.summary{
  display:flex;align-items:center;gap:12px;
  padding:13px 16px;margin-bottom:14px;
  border-radius:9px;border:1px solid;
  animation:rise .25s ease both;
}
.summary.good{background:var(--ok-bg);border-color:#1d5c48}
.summary.bad{background:var(--err-bg);border-color:#6b2634}
.summary .verdict{
  font-size:13.5px;font-weight:600;
}
.summary.good .verdict{color:var(--ok)}
.summary.bad .verdict{color:var(--err)}
.summary .counts{
  margin-left:auto;display:flex;gap:6px;
  font:12px var(--mono);
}
.chip{
  padding:3px 8px;border-radius:5px;
  border:1px solid var(--line-bright);
  color:var(--text-2);background:var(--surface-2);
}
.chip.e{color:var(--err);border-color:#6b2634;background:var(--err-bg)}
.chip.w{color:var(--warn);border-color:#5c4a18;background:var(--warn-bg)}

/* 诊断条目 */
.diag{
  display:flex;gap:0;
  margin-bottom:9px;
  background:var(--surface);
  border:1px solid var(--line);
  border-radius:9px;
  overflow:hidden;cursor:pointer;
  transition:border-color .15s,background .15s,transform .1s;
}
.diag.enter{animation:rise .22s ease both}
.diag:hover{
  background:var(--surface-2);
  border-color:var(--line-bright);
  transform:translateX(2px);
}
.diag:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.diag .bar{width:3px;flex-shrink:0;background:var(--text-3)}
.diag.error .bar{background:var(--err)}
.diag.warning .bar{background:var(--warn)}
.diag .body{padding:11px 14px;flex:1;min-width:0}
.diag .row1{
  display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;
  margin-bottom:2px;
}
.code-tag{
  font:600 11px/1 var(--mono);
  padding:3.5px 6px;border-radius:4px;
  background:var(--surface-3);color:var(--text-2);
  flex-shrink:0;letter-spacing:.02em;
}
.diag.error .code-tag{background:#3a1620;color:var(--err)}
.diag.warning .code-tag{background:#3a2f12;color:var(--warn)}
.loc{
  font:11.5px/1 var(--mono);color:var(--text-3);
  flex-shrink:0;
}
.loc b{color:var(--accent);font-weight:500}
.msg{flex:1;min-width:180px;font-size:13.5px;line-height:1.55}
.hint{
  margin-top:7px;padding-left:9px;
  border-left:2px solid var(--line-bright);
  font-size:12.5px;line-height:1.7;color:var(--text-2);
}

/* 预处理说明 */
.notes{
  margin-top:16px;padding:12px 14px;
  background:var(--surface);
  border:1px solid var(--line);border-radius:9px;
  font-size:12.5px;color:var(--text-2);
}
.notes .t{
  font-size:11px;font-weight:600;color:var(--text-3);
  text-transform:uppercase;letter-spacing:.06em;
  margin-bottom:8px;
}
.notes .n{display:flex;gap:7px;padding:3px 0;line-height:1.6}
.notes .n::before{content:"·";color:var(--accent);flex-shrink:0}

/* IR 面板 */
#irpane{display:none;flex:1;overflow:auto;padding:16px}
body.ir-mode #irpane{display:block}
body.ir-mode #out{display:none}
#ir{
  font:12.5px/1.7 var(--mono);
  color:#a8b6cc;white-space:pre;
  tab-size:2;
}
#ir .cmt{color:var(--text-3)}
#ir .lbl{color:var(--accent)}
#ir .kw{color:var(--warn)}

/* 底栏 */
footer{
  position:relative;z-index:2;flex-shrink:0;
  height:28px;display:flex;align-items:center;gap:16px;
  padding:0 20px;
  background:var(--surface);
  border-top:1px solid var(--line);
  font:11.5px/1 var(--mono);color:var(--text-3);
}
footer .sep{color:var(--line-bright)}
footer .ok{color:var(--ok)}
footer .bad{color:var(--err)}
footer .right{margin-left:auto;display:flex;gap:16px}

@keyframes rise{
  from{opacity:0;transform:translateY(4px)}
  to{opacity:1;transform:translateY(0)}
}
@media (prefers-reduced-motion:reduce){
  *{animation:none!important;transition:none!important}
  #out{scroll-behavior:auto}
}
/* 窄屏：上下堆叠 */
@media (max-width:900px){
  main{grid-template-columns:1fr;grid-template-rows:1fr 1fr}
  .pane+.pane{border-left:0;border-top:1px solid var(--line)}
  .brand .sub{display:none}
}
</style>
</head>
<body>

<header>
  <div class="brand">
    <div class="logo">c</div>
    <h1>csub</h1>
    <span class="sub">C 代码查错 · v1.0</span>
  </div>
  <div class="spacer"></div>
  <button class="btn" id="btn-sample">示例代码</button>
  <button class="btn" id="btn-ir">三地址码</button>
  <button class="btn" id="btn-clear">清空</button>
  <button class="btn primary" id="btn-check">
    检查 <kbd>Ctrl ↵</kbd>
  </button>
</header>

<main>
  <section class="pane">
    <div class="pane-head">
      <span class="dot"></span>
      <span>源代码</span>
      <span class="right" id="stat"></span>
    </div>
    <div class="editor">
      <div class="gutter" id="gutter">1</div>
      <textarea id="code" spellcheck="false" autocomplete="off"
        autocapitalize="off"
        placeholder="在这里粘贴 C 代码&#10;&#10;或点右上角「示例代码」看它能查出什么"></textarea>
    </div>
  </section>

  <section class="pane">
    <div class="pane-head">
      <span class="dot muted" id="outdot"></span>
      <span id="outtitle">诊断</span>
      <span class="right" id="outstat"></span>
    </div>
    <div id="out">
      <div class="placeholder">
        <div class="big">{ }</div>
        <div class="tip">
          粘贴代码后按 <kbd>Ctrl</kbd>+<kbd>Enter</kbd> 检查<br>
          诊断会带行号、严重级别和修改建议
        </div>
      </div>
    </div>
    <div id="irpane"><pre id="ir"></pre></div>
  </section>
</main>

<footer>
  <span id="f-status">就绪</span>
  <span class="sep">|</span>
  <span>39 条规则</span>
  <span class="sep">|</span>
  <span>零误报验证：42 份真实代码</span>
  <div class="right">
    <span id="f-lines">0 行</span>
    <span id="f-errs">—</span>
  </div>
</footer>

<script>
const code    = document.getElementById('code');
const gutter  = document.getElementById('gutter');
const out     = document.getElementById('out');
const irPre   = document.getElementById('ir');
const stat    = document.getElementById('stat');
const outstat = document.getElementById('outstat');
const outdot  = document.getElementById('outdot');
const outtitle= document.getElementById('outtitle');
const fStatus = document.getElementById('f-status');
const fLines  = document.getElementById('f-lines');
const fErrs   = document.getElementById('f-errs');

const SAMPLE = `#include <stdio.h>

/* 这段代码有 4 个典型问题，点「检查」看它能不能全查出来 */

int main() {
    int a[5];
    int i, sum;

    /* 1. 循环越界：i 最大取 5，但 a[5] 不存在 */
    for (i = 0; i <= 5; i++) {
        a[i] = i * 2;
    }

    /* 2. 赋值当比较：sum 还没初始化 */
    if (sum = 10) {
        printf("%d\\n", sum);
    }

    /* 3. scanf 漏写 & */
    int n;
    scanf("%d", n);

    /* 4. 用了未声明的变量 */
    printf("%d\\n", undeclared);

    return 0;
}
`;

/* ── 行号槽 ── */
let errLines = new Set(), warnLines = new Set();
function renderGutter() {
  const n = Math.max(code.value.split('\n').length, 1);
  const parts = [];
  for (let i = 1; i <= n; i++) {
    const cls = errLines.has(i) ? ' hi-e'
              : (warnLines.has(i) ? ' hi' : '');
    // 每个行号必须是块级元素：span 是 inline，拼 \n 不会换行，
    // 所有行号会挤成一行（早期就踩过这个）。
    parts.push(`<div class="gl${cls}">${i}</div>`);
  }
  gutter.innerHTML = parts.join('');
  gutter.scrollTop = code.scrollTop;
}
function refreshStat() {
  const v = code.value;
  const lines = v ? v.split('\n').length : 0;
  stat.textContent = v ? `${lines} 行 · ${v.length} 字符` : '';
  fLines.textContent = `${lines} 行`;
}
function syncScroll() {
  gutter.scrollTop = code.scrollTop;
}

code.addEventListener('input', () => {
  errLines.clear(); warnLines.clear();
  renderGutter(); refreshStat();
});
code.addEventListener('scroll', syncScroll);
code.addEventListener('keydown', e => {
  if (e.key === 'Tab') {
    e.preventDefault();
    const s = code.selectionStart, t = code.selectionEnd;
    code.value = code.value.slice(0, s) + '    ' + code.value.slice(t);
    code.selectionStart = code.selectionEnd = s + 4;
    renderGutter(); refreshStat();
  }
});

/* ── 请求 ── */
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
    out.innerHTML = `<div class="placeholder">
      <div class="big">{ }</div>
      <div class="tip">代码是空的</div></div>`;
    fStatus.textContent = '就绪';
    return;
  }
  fStatus.textContent = '检查中…';
  outdot.classList.add('muted');
  try {
    const data = await post('/api/check', {code: src});
    render(data);
  } catch (e) {
    out.innerHTML = `<div class="summary bad">
      <span class="verdict">请求失败</span>
      <span class="counts"><span class="chip">${esc(e.message)}</span></span>
    </div>`;
    fStatus.textContent = '请求失败';
  }
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

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
      <span class="verdict">没有发现问题</span>
      <span class="counts"><span class="chip">0 条</span></span>
    </div>`;
  } else {
    html += `<div class="summary ${nE ? 'bad' : 'good'}">
      <span class="verdict">${nE ? '发现 ' + nE + ' 个错误' : '只有警告'}</span>
      <span class="counts">
        ${nE ? `<span class="chip e">${nE} 错误</span>` : ''}
        ${nW ? `<span class="chip w">${nW} 警告</span>` : ''}
      </span>
    </div>`;
  }

  for (const d of ds) {
    html += `<div class="diag ${d.level} enter" data-line="${d.line}"
                  tabindex="0" role="button">
      <div class="bar"></div>
      <div class="body">
        <div class="row1">
          <span class="code-tag">${esc(d.code)}</span>
          <span class="loc">第 <b>${d.line}</b> 行</span>
          <span class="msg">${esc(d.msg)}</span>
        </div>
        ${d.hint ? `<div class="hint">${esc(d.hint)}</div>` : ''}
      </div>
    </div>`;
  }

  if (data.notes && data.notes.length) {
    html += `<div class="notes"><div class="t">预处理说明</div>`;
    for (const n of data.notes)
      html += `<div class="n"><span>${esc(n)}</span></div>`;
    html += `</div>`;
  }

  out.innerHTML = html;
  out.querySelectorAll('.diag').forEach(el => {
    const go = () => jumpToLine(parseInt(el.dataset.line, 10));
    el.onclick = go;
    el.onkeydown = e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
    };
    // 动画跑完就摘掉 enter，避免后续交互受动画影响
    el.addEventListener('animationend', () => el.classList.remove('enter'),
                        {once: true});
  });

  outdot.classList.toggle('muted', ds.length === 0);
  outstat.textContent = ds.length
    ? `${ds.length} 条` : '';
  fStatus.textContent = ds.length
    ? (nE ? `${nE} 个错误` : '只有警告')
    : '没有发现问题';
  fErrs.textContent = ds.length ? `${nE}E / ${nW}W` : '0';
  fErrs.className = nE ? 'bad' : (nW ? '' : 'ok');
}

function jumpToLine(line) {
  if (!line || line < 1) return;
  const lines = code.value.split('\n');
  let pos = 0;
  for (let i = 0; i < line - 1 && i < lines.length; i++)
    pos += lines[i].length + 1;
  code.focus();
  code.setSelectionRange(pos, pos + (lines[line - 1] || '').length);
  const lh = 21.9;
  code.scrollTop = Math.max(0, (line - 6) * lh);
  syncScroll();
}

/* ── IR ── */
async function showIr() {
  const src = code.value;
  if (!src.trim()) return;
  document.body.classList.add('ir-mode');
  outtitle.textContent = '三地址码';
  irPre.textContent = '生成中…';
  try {
    const data = await post('/api/ir', {code: src});
    irPre.innerHTML = colorIr(data.ir || '（空）');
    fStatus.textContent = 'IR 已生成';
  } catch (e) {
    irPre.textContent = '生成失败：' + e.message;
  }
}
function colorIr(s) {
  return esc(s)
    .replace(/^(;[^\n]*)$/gm, '<span class="cmt">$1</span>')
    .replace(/^((?:func |L)\w+:?)/gm, '<span class="lbl">$1</span>');
}

/* ── 按钮 ── */
document.getElementById('btn-check').onclick = () => {
  document.body.classList.remove('ir-mode');
  outtitle.textContent = '诊断';
  runCheck();
};
document.getElementById('btn-ir').onclick = showIr;
document.getElementById('btn-sample').onclick = () => {
  code.value = SAMPLE;
  document.body.classList.remove('ir-mode');
  outtitle.textContent = '诊断';
  errLines.clear(); warnLines.clear();
  renderGutter(); refreshStat(); runCheck();
};
document.getElementById('btn-clear').onclick = () => {
  code.value = '';
  document.body.classList.remove('ir-mode');
  outtitle.textContent = '诊断';
  errLines.clear(); warnLines.clear();
  renderGutter(); refreshStat();
  out.innerHTML = `<div class="placeholder">
    <div class="big">{ }</div>
    <div class="tip">已清空</div></div>`;
  outstat.textContent = '';
  fStatus.textContent = '就绪';
  fErrs.textContent = '—';
  fErrs.className = '';
  code.focus();
};
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    document.getElementById('btn-check').click();
  }
});

renderGutter(); refreshStat(); code.focus();
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
