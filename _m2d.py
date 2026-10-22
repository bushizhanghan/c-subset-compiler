# -*- coding: utf-8 -*-
import os, sys, subprocess
os.chdir(r"E:\dsh\csub")

src = '''#include <stdio.h>
int main(){
    int a[2][3]; int i,j;
    for(i=0;i<2;i=i+1){ for(j=0;j<3;j=j+1){ a[i][j]=i*3+j; } }
    printf("%d %d %d %d %d %d\\n", a[0][0],a[1][0],a[0][1],a[1][1],a[0][2],a[1][2]);
    return 0;
}
'''
open("_m.c","w",encoding="utf-8",newline="\n").write(src)
subprocess.run([sys.executable,"tools/codegen.py","_m.c","-o","_m.s"],
               capture_output=True,encoding="utf-8",timeout=60)
asm = open("_m.s",encoding="utf-8").read()
lines = asm.splitlines()
# 打印 store 那行前 12 行
for i,l in enumerate(lines):
    if "mov DWORD PTR [rdx+rcx*4], r8d" in l:
        for j in range(max(0,i-12), i+1):
            print("  %4d | %s" % (j+1, lines[j]))
        print()
        break
