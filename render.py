#!/usr/bin/env python3
"""把诊断结果和 IR 渲染成一个静态 HTML 页面。

为什么还要出 HTML：竞赛现场和给同学看的时候，
命令行输出不够直观。但这里不引入任何前端构建工具，
单一文件、双击就能开，这比"需要 npm install"的方案可靠得多。
"""
import html
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from lexer import tokenize, LexError
from parser import parse, ParseError
from semantic import analyze, Diag
from ir import generate


def analyze_source(src: str):
    """返回 (阶段, 诊断列表, 源码行)。任何阶段出错就停。"""
    from preprocess import split_lines
    lines = split_lines(src)
    if src.startswith("\ufeff"):
        src = src[1:]
    try:
        tokenize(src)
    except LexError as e:
        return "lex", [Diag("error", "LEX", e.line, e.col, e.msg)], lines
    try:
        prog = parse(src)
    except ParseError as e:
        return "syntax", [Diag("error", "SYN", e.line, e.col, e.msg)], lines
    return "semantic", analyze(prog), lines


def render(src, title="csub 诊断报告"):
    stage, diags, lines = analyze_source(src)
    prog = None
    ir_info = None
    if stage == "semantic":
        try:
            prog = parse(src)
            ir_info = generate(prog)
        except Exception:
            ir_info = None

    errs = [d for d in diags if d.level == "error"]
    warns = [d for d in diags if d.level == "warning"]
    by_line = {}
    for d in diags:
        by_line.setdefault(d.line, []).append(d)

    # ---- 源码高亮 ----
    src_html = []
    for i, ln in enumerate(lines, 1):
        cls = ""
        if any(d.level == "error" for d in by_line.get(i, [])):
            cls = "errline"
        elif any(d.level == "warning" for d in by_line.get(i, [])):
            cls = "warnline"
        esc = html.escape(ln)
        marks = "".join(
            f'<div class="diag {d.level}"><b>[{d.code}]</b> '
            f'{html.escape(d.msg)}'
            + (f'<div class="hint">提示：{html.escape(d.hint)}</div>'
               if d.hint else "")
            + "</div>"
            for d in by_line.get(i, [])
        )
        src_html.append(
            f'<tr class="{cls}"><td class="ln">{i}</td>'
            f'<td class="code"><pre>{esc}</pre>{marks}</td></tr>'
        )

    # ---- 数据流分析区 ----
    liveness_html = ""
    if ir_info and ir_info.get("liveness"):
        rows = ir_info["liveness"]
        body = []
        for row in rows:
            lines_txt = chr(10).join(row["lines"])
            body.append(
                f'<tr><td class="bb">BB{row["block"]}</td>'
                f'<td><pre class="ir sm">{html.escape(lines_txt)}</pre></td>'
                f'<td class="live">{", ".join(row["live_in"]) or "—"}</td>'
                f'<td class="live">{", ".join(row["live_out"]) or "—"}</td></tr>')
        liveness_html = f"""
        <h3 style="margin-top:22px">基本块与活跃变量
          <span class="badge">{ir_info.get('blocks')} 个基本块</span></h3>
        <p class="muted">活跃变量分析是寄存器分配的基础：
        若一个变量在某点之后不再被使用，它就可以释放寄存器。</p>
        <table class="live-tbl">
          <thead><tr><th>块</th><th>指令</th>
          <th>live_in（进入时活跃）</th><th>live_out（离开时活跃）</th></tr></thead>
          <tbody>{''.join(body)}</tbody>
        </table>
        """

    # ---- IR 区 ----
    ir_html = '<p class="muted">源码未通过语义分析，无 IR 输出。</p>'
    if ir_info:
        raw = "\n".join(ir_info["raw"])
        opt = "\n".join(ir_info["optimized"] or [])
        ir_html = f"""
        <div class="irgrid">
          <div>
            <h3>优化前 <span class="badge">{ir_info['raw_count']} 条</span></h3>
            <pre class="ir">{html.escape(raw)}</pre>
          </div>
          <div>
            <h3>优化后 <span class="badge">{ir_info['optimized_count']} 条</span></h3>
            <pre class="ir">{html.escape(opt)}</pre>
            <p class="muted">
              常量折叠 {ir_info['folded']} 处，死代码消除 {ir_info['removed']} 条，
              指令减少 {ir_info['raw_count'] - (ir_info['optimized_count'] or 0)} 条
            </p>
          </div>
        </div>
        {liveness_html}
        """

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  :root {{ --err:#d93025; --warn:#b26a00; --ok:#1e8e3e; --bd:#e0e0e0; }}
  * {{ box-sizing: border-box; }}
  body {{ font: 14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
         margin: 0; padding: 28px; background:#fafafa; color:#202124; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  h2 {{ font-size: 15px; margin: 26px 0 10px; padding-bottom:6px;
        border-bottom:1px solid var(--bd); }}
  h3 {{ font-size: 13px; margin: 0 0 8px; color:#5f6368; }}
  .muted {{ color:#5f6368; font-size:12px; }}
  .stats {{ display:flex; gap:10px; margin:14px 0 4px; flex-wrap:wrap; }}
  .stat {{ background:#fff; border:1px solid var(--bd); border-radius:8px;
           padding:10px 16px; min-width:110px; }}
  .stat b {{ display:block; font-size:20px; }}
  .stat.err b {{ color:var(--err); }}
  .stat.warn b {{ color:var(--warn); }}
  .stat.ok b {{ color:var(--ok); }}
  table.src {{ width:100%; border-collapse:collapse; background:#fff;
               border:1px solid var(--bd); border-radius:8px; overflow:hidden; }}
  td.ln {{ width:44px; text-align:right; padding:2px 10px; color:#9aa0a6;
           background:#f8f9fa; user-select:none; font-variant-numeric:tabular-nums; }}
  td.code {{ padding:2px 12px; vertical-align:top; }}
  td.code pre {{ margin:0; font:13px/1.55 Consolas,"Courier New",monospace;
                 white-space:pre-wrap; }}
  tr.errline td.ln {{ background:#fce8e6; color:var(--err); font-weight:600; }}
  tr.warnline td.ln {{ background:#fef7e0; color:var(--warn); font-weight:600; }}
  .diag {{ margin:4px 0 6px; padding:6px 10px; border-radius:6px; font-size:13px; }}
  .diag.error {{ background:#fce8e6; color:#a50e0e; }}
  .diag.warning {{ background:#fef7e0; color:#8a5300; }}
  .hint {{ color:#5f6368; font-size:12px; margin-top:2px; }}
  .irgrid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  @media (max-width:820px) {{ .irgrid {{ grid-template-columns:1fr; }} }}
  pre.ir {{ background:#202124; color:#e8eaed; padding:12px; border-radius:8px;
            font:12.5px/1.6 Consolas,monospace; overflow-x:auto; margin:0; }}
  .badge {{ background:#e8eaed; color:#3c4043; border-radius:10px;
            padding:1px 8px; font-size:11px; font-weight:400; }}
  footer {{ margin-top:32px; color:#9aa0a6; font-size:12px; }}
  table.live-tbl {{ width:100%; border-collapse:collapse; background:#fff;
                    border:1px solid var(--bd); border-radius:8px;
                    overflow:hidden; font-size:12px; margin-top:8px; }}
  table.live-tbl th {{ background:#f8f9fa; text-align:left; padding:7px 10px;
                       font-weight:600; color:#5f6368; }}
  table.live-tbl td {{ padding:6px 10px; border-top:1px solid #f1f3f4;
                       vertical-align:top; }}
  td.bb {{ font-weight:600; color:#1a73e8; white-space:nowrap; }}
  td.live {{ font-family:Consolas,monospace; color:#188038; font-size:11.5px; }}
  pre.ir.sm {{ padding:6px 8px; font-size:11px; line-height:1.45; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p class="muted">C 子集编译器与初学者错误诊断 · 分析阶段：{stage}</p>

<div class="stats">
  <div class="stat err"><b>{len(errs)}</b>错误</div>
  <div class="stat warn"><b>{len(warns)}</b>警告</div>
  <div class="stat ok"><b>{len(lines)}</b>行代码</div>
</div>

<h2>源码与诊断</h2>
<table class="src">{''.join(src_html)}</table>

<h2>中间表示（三地址码）</h2>
{ir_html}

<footer>由 csub 生成 · 诊断规则见 src/semantic.py</footer>
</body></html>"""


def main():
    if len(sys.argv) < 2:
        print("用法: python render.py file.c [-o out.html]")
        return 2
    path = sys.argv[1]
    out = "report.html"
    if "-o" in sys.argv:
        out = sys.argv[sys.argv.index("-o") + 1]
    with open(path, encoding="utf-8") as f:
        src = f.read()
    html_text = render(src, f"csub 诊断报告 · {os.path.basename(path)}")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_text)
    print(f"已生成 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
