# -*- coding: utf-8 -*-
"""D 版运行时诊断：rs 设初值 77，看 f 调用后变成什么。"""
import io
import os
import shutil
import subprocess
import sys
import tempfile

os.chdir(r"E:\dsh\csub")
sys.path.insert(0, 'src'); sys.path.insert(0, 'tools')
from codegen import compile_source

SRC = '''#include <stdio.h>
#include <stdlib.h>
char** f(int* target, int n, int* rs){ char** a=(char**)malloc(8); a[0]="Push"; *rs=n; return a; }
int main(){ int t[2]={1,2}; int rs=77; char** r=f(t,2,&rs);
 printf("after=%d\\n", rs); return 0; }'''

asm, _ = compile_source(SRC)
d = tempfile.mkdtemp()
s = os.path.join(d, "p.s")
exe = os.path.join(d, "p.exe")
io.open(s, "w", encoding="utf-8", newline="\n").write(asm)
r = subprocess.run(["gcc", "-O0", "-o", exe, s],
                   capture_output=True, encoding="utf-8", timeout=120)
if r.returncode == 0:
    p = subprocess.run([exe], input=b"", capture_output=True, timeout=20)
    print("csub:", repr(p.stdout.decode("utf-8", "replace")),
          "exit", p.returncode & 0xFF)
else:
    print("链接失败:", r.stderr[:200])
shutil.rmtree(d, ignore_errors=True)
