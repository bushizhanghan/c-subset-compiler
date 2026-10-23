# -*- coding: utf-8 -*-
"""用 Edge DevTools Protocol 实测：textarea 的尺寸 + 点击后的光标位置。

无 playwright 时用 CDP over WebSocket。太复杂的话退化为静态分析。
"""
import io
import json
import os
import subprocess
import sys
import tempfile

os.chdir(r"E:\dsh\csub")

# 生成一个测试页面，内联 csub 的编辑器 HTML/CSS，用 JS 报告尺寸
html = io.open("web.py", encoding="utf-8").read()
# 提取 PAGE 字符串里的 HTML（简化：直接请求线上页面）
import urllib.request
page = urllib.request.urlopen("http://127.0.0.1:8777/", timeout=30).read().decode("utf-8")

# 注入诊断脚本：报告尺寸 + 模拟点击
diag = """
<script>
window.addEventListener('load', () => {
  setTimeout(() => {
    const code = document.getElementById('code');
    const hl = document.getElementById('hl');
    const wrap = document.querySelector('.code-wrap');
    const r = code.getBoundingClientRect();
    const hw = hl.getBoundingClientRect();
    const wr = wrap.getBoundingClientRect();
    // 在第 3 行中间放文字，然后模拟点击那个位置
    code.value = 'int main(){\\n    int abc = 123;\\n    return 0;\\n}';
    code.dispatchEvent(new Event('input'));
    // 点击第 2 行 "abc" 中间
    const x = wr.left + 18 + 8 * 7.5;   // padding + 8 个字符宽
    const y = wr.top + 16 + 1 * 22.25;  // padding + 第 2 行
    const before = code.selectionStart;
    code.dispatchEvent(new MouseEvent('mousedown', {
      clientX: x, clientY: y, bubbles: true
    }));
    code.dispatchEvent(new MouseEvent('mouseup', {
      clientX: x, clientY: y, bubbles: true
    }));
    code.dispatchEvent(new MouseEvent('click', {
      clientX: x, clientY: y, bubbles: true
    }));
    const after = code.selectionStart;
    document.title = 'DIAG:' + JSON.stringify({
      codeRect: {w: r.width, h: r.height, top: r.top, left: r.left},
      hlRect: {w: hw.width, h: hw.height, top: hw.top, left: hw.left},
      wrapRect: {w: wr.width, h: wr.height},
      selBefore: before, selAfter: after,
      scrollH: code.scrollHeight, clientH: code.clientHeight,
      lineH: getComputedStyle(code).lineHeight
    });
  }, 800);
});
</script>
"""
page2 = page.replace("</body>", diag + "</body>")
d = tempfile.mkdtemp()
p = os.path.join(d, "diag.html")
io.open(p, "w", encoding="utf-8", newline="\n").write(page2)

edge = None
for c in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
          r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]:
    if os.path.exists(c):
        edge = c
        break
if edge is None:
    print("SKIP 没有 Edge")
    sys.exit(0)

# 用 headless 打开，取 title
out = subprocess.run([edge, "--headless=new", "--disable-gpu",
                      "--virtual-time-budget=4000", "--dump-dom",
                      "file:///" + p.replace("\\", "/")],
                     capture_output=True, encoding="utf-8", timeout=120)
m = None
import re
mm = re.search(r"DIAG:(\{.*?\})", out.stdout)
if mm:
    print(mm.group(1))
else:
    print("没拿到诊断数据")
    print((out.stdout or "")[:300])
