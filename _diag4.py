# -*- coding: utf-8 -*-
"""最小复现 + 逐段剥离，找出 buildArray 组合失败的根因。"""
import io
import os
import shutil
import subprocess
import sys
import tempfile

os.chdir(r"E:\dsh\csub")
sys.path.insert(0, 'src'); sys.path.insert(0, 'tools')
from codegen import compile_source

CASES = [
    ("A 纯出参", '''#include <stdio.h>
void f(int* target, int n, int* rs){ *rs=n; }
int main(){ int t[2]={1,2}; int rs; f(t,2,&rs); printf("%d\\n", rs); return 0; }'''),
    ("B +malloc返回", '''#include <stdio.h>
#include <stdlib.h>
void f(int* target, int n, int* rs){ char** a=(char**)malloc(8); *rs=n; }
int main(){ int t[2]={1,2}; int rs; f(t,2,&rs); printf("%d\\n", rs); return 0; }'''),
    ("C +char**返回", '''#include <stdio.h>
#include <stdlib.h>
char** f(int* target, int n, int* rs){ char** a=(char**)malloc(8); *rs=n; return a; }
int main(){ int t[2]={1,2}; int rs; char** r=f(t,2,&rs); printf("%d\\n", rs); return 0; }'''),
    ("D +字符串写入", '''#include <stdio.h>
#include <stdlib.h>
char** f(int* target, int n, int* rs){ char** a=(char**)malloc(8); a[0]="Push"; *rs=n; return a; }
int main(){ int t[2]={1,2}; int rs; char** r=f(t,2,&rs); printf("%s %d\\n", r[0], rs); return 0; }'''),
]

res = []
for name, src in CASES:
    d = tempfile.mkdtemp()
    c = os.path.join(d, "t.c")
    exe = os.path.join(d, "t.exe")
    io.open(c, "w", encoding="utf-8", newline="\n").write(src)
    rg = subprocess.run(["gcc", "-w", "-O0", "-o", exe, c],
                        capture_output=True, encoding="utf-8", timeout=120)
    if rg.returncode == 0:
        pg = subprocess.run([exe], input=b"", capture_output=True,
                            timeout=20)
        gout = pg.stdout.decode("utf-8", "replace").strip()
        gec = pg.returncode & 0xFF
    else:
        gout, gec = "gcc编译失败", -1
    try:
        asm, _ = compile_source(src)
        s = os.path.join(d, "p.s")
        pexe = os.path.join(d, "p.exe")
        io.open(s, "w", encoding="utf-8", newline="\n").write(asm)
        rc = subprocess.run(["gcc", "-O0", "-o", pexe, s],
                            capture_output=True, encoding="utf-8", timeout=120)
        if rc.returncode == 0:
            pc = subprocess.run([pexe], input=b"", capture_output=True,
                                timeout=20)
            cout = pc.stdout.decode("utf-8", "replace").strip()
            cec = pc.returncode & 0xFF
        else:
            cout, cec = "链接失败", -1
    except Exception as e:
        cout, cec = "csub异常:" + str(e)[:30], -1
    mark = "OK  " if (gout == cout and gec == cec) else "DIFF"
    res.append("%s %-16s gcc=%r(%d) csub=%r(%d)"
               % (mark, name, gout[:24], gec, cout[:24], cec))
    shutil.rmtree(d, ignore_errors=True)

with open("_d4.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(res) + "\n")
