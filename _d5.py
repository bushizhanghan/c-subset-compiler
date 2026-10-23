# -*- coding: utf-8 -*-
"""看 D 版（字符串写入）的 main 汇编，找 rs 丢失原因。"""
import io
import os
import sys

os.chdir(r"E:\dsh\csub")
sys.path.insert(0, 'src'); sys.path.insert(0, 'tools')
from codegen import compile_source

SRC = '''#include <stdio.h>
#include <stdlib.h>
char** f(int* target, int n, int* rs){ char** a=(char**)malloc(8); a[0]="Push"; *rs=n; return a; }
int main(){ int t[2]={1,2}; int rs; char** r=f(t,2,&rs);
 printf("%s %d\\n", r[0], rs); return 0; }'''

asm, _ = compile_source(SRC)
with open("_d4.s", "w", encoding="utf-8", newline="\n") as f:
    f.write(asm)
lines = asm.split("\n")

out = []
started = False
for l in lines:
    if l.startswith("main:"):
        started = True
    if started:
        out.append(l)
    if started and l.strip() == "ret":
        break
with open("_d4_main.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
