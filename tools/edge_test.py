# -*- coding: utf-8 -*-
"""第三轮边界测试：20 个更刁钻的用例。"""
import json
import os
import subprocess
import sys
import tempfile

os.chdir(r"E:\dsh\csub")
PY = sys.executable
NL = chr(10)
Q = chr(34)
N = chr(92) + "n"

CASES = [
    ("多维数组全遍历", '''#include <stdio.h>
int main(){ int a[2][3][2]; int i,j,k;
 for(i=0;i<2;i=i+1){ for(j=0;j<3;j=j+1){ for(k=0;k<2;k=k+1){ a[i][j][k]=i*6+j*2+k; } } }
 printf("%d %d %d\\n", a[0][0][0], a[1][2][1], a[1][1][0]); return 0; }''', None),
    ("数组当计数器", '''#include <stdio.h>
int main(){ int c[10]; int i; for(i=0;i<10;i=i+1){ c[i]=0; }
 int v[5]={3,7,3,9,3}; for(i=0;i<5;i=i+1){ c[v[i]]=c[v[i]]+1; }
 printf("%d %d\\n", c[3], c[7]); return 0; }''', None),
    ("字符串反转", '''#include <stdio.h>
int main(){ char s[16]="abcdef"; int n=0,i; char t;
 while(s[n]!=0){ n=n+1; }
 for(i=0;i<n/2;i=i+1){ t=s[i]; s[i]=s[n-1-i]; s[n-1-i]=t; }
 printf("%s\\n", s); return 0; }''', None),
    ("嵌套结构体数组", '''#include <stdio.h>
struct P { int x; int y; };
int main(){ struct P a[2][2]; int i,j;
 for(i=0;i<2;i=i+1){ for(j=0;j<2;j=j+1){ a[i][j].x=i; a[i][j].y=j; } }
 printf("%d %d\\n", a[1][0].x, a[0][1].y); return 0; }''', None),
    ("函数返回指针目标", '''#include <stdio.h>
int g[3]={10,20,30};
int *at(int i){ return &g[i]; }
int main(){ *at(1)=99; printf("%d %d\\n", g[1], *at(2)); return 0; }''', None),
    ("递归+数组", '''#include <stdio.h>
int f(int a[], int n){ if(n==0){ return 0; } return a[0]+f(a+1, n-1); }
int main(){ int a[4]={1,2,3,4}; printf("%d\\n", f(a,4)); return 0; }''', None),
    ("指针数组", '''#include <stdio.h>
int main(){ int a=1,b=2,c=3; int *p[3]; p[0]=&a; p[1]=&b; p[2]=&c;
 printf("%d\\n", *p[0]+*p[1]+*p[2]); return 0; }''', None),
    ("复杂条件", '''#include <stdio.h>
int main(){ int y=2000;
 int leap=(y%4==0 && y%100!=0) || (y%400==0);
 printf("%d\\n", leap); return 0; }''', None),
    ("类型提升", '''#include <stdio.h>
int main(){ char a=100, b=100; int c=a+b; printf("%d\\n", c); return 0; }''', None),
    ("负数除法", '''#include <stdio.h>
int main(){ printf("%d %d\\n", -17/5, -17%5); return 0; }''', None),
    ("数组冒泡降序", '''#include <stdio.h>
int main(){ int a[6]={3,1,4,1,5,9}; int i,j,t;
 for(i=0;i<5;i=i+1){ for(j=0;j<5-i;j=j+1){ if(a[j]<a[j+1]){ t=a[j]; a[j]=a[j+1]; a[j+1]=t; } } }
 for(i=0;i<6;i=i+1){ printf("%d ", a[i]); } printf("\\n"); return 0; }''', None),
    ("全局计数器+函数", '''#include <stdio.h>
int total=0;
void add(int x){ total=total+x; }
int main(){ int i; for(i=1;i<=10;i=i+1){ add(i); }
 printf("%d\\n", total); return 0; }''', None),
    ("二维数组行列互换", '''#include <stdio.h>
int main(){ int a[2][3]={{1,2,3},{4,5,6}}; int i,j,s=0;
 for(i=0;i<2;i=i+1){ for(j=0;j<3;j=j+1){ s=s+a[i][j]; } }
 printf("%d\\n", s); return 0; }''', None),
    ("char 运算", '''#include <stdio.h>
int main(){ char c='A'; c=c+1; printf("%c %d\\n", c, c); return 0; }''', None),
    ("for 里 continue 计数", '''#include <stdio.h>
int main(){ int i, c=0; for(i=1;i<=20;i=i+1){ if(i%3!=0){ continue; } c=c+1; }
 printf("%d\\n", c); return 0; }''', None),
    ("多函数协作", '''#include <stdio.h>
int sq(int x){ return x*x; }
int cube(int x){ return x*sq(x); }
int main(){ printf("%d %d\\n", sq(5), cube(3)); return 0; }''', None),
    ("数组最大值函数", '''#include <stdio.h>
int maxarr(int a[], int n){ int m=a[0], i;
 for(i=1;i<n;i=i+1){ if(a[i]>m){ m=a[i]; } } return m; }
int main(){ int a[5]={3,9,2,7,5}; printf("%d\\n", maxarr(a,5)); return 0; }''', None),
    ("位运算组合", '''#include <stdio.h>
int main(){ int a=0xF0, b=0x0F;
 printf("%d %d %d\\n", a&b, a|b, a^b); return 0; }''', None),
    ("嵌套循环累计", '''#include <stdio.h>
int main(){ int i,j,s=0; for(i=1;i<=3;i=i+1){ for(j=1;j<=3;j=j+1){ s=s+i*j; } }
 printf("%d\\n", s); return 0; }''', None),
    ("switch 分支求和", '''#include <stdio.h>
int main(){ int i,s=0; for(i=1;i<=5;i=i+1){ switch(i%2){ case 0: s=s+10; break; default: s=s+1; } }
 printf("%d\\n", s); return 0; }''', None),
]


def run_gcc(src, stdin_data):
    d = tempfile.mkdtemp()
    c = os.path.join(d, "t.c")
    exe = os.path.join(d, "t.exe")
    open(c, "w", encoding="utf-8", newline="\n").write(src)
    r = subprocess.run(["gcc", "-w", "-O0", "-o", exe, c],
                       capture_output=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        return None, "gcc 编译失败: " + (r.stderr or "")[:150]
    try:
        inp = stdin_data.encode() if stdin_data else b""
        p = subprocess.run([exe], input=inp, capture_output=True, timeout=20)
        out = p.stdout.decode("utf-8", "replace")
        for f in (c, exe):
            try:
                os.remove(f)
            except OSError:
                pass
        os.rmdir(d)
        return (out.rstrip(), p.returncode & 0xFF), None
    except subprocess.TimeoutExpired:
        return None, "gcc 超时"


def run_csub(src, stdin_data):
    from codegen import compile_source, CodegenError
    try:
        asm_text, _ = compile_source(src)
    except CodegenError as e:
        return None, "codegen: " + str(e)[:120]
    except Exception as e:
        return None, "err: %s" % e
    d = tempfile.mkdtemp()
    s = os.path.join(d, "p.s")
    exe = os.path.join(d, "p.exe")
    open(s, "w", encoding="utf-8", newline="\n").write(asm_text)
    r = subprocess.run(["gcc", "-O0", "-o", exe, s],
                       capture_output=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        return None, "build: " + (r.stderr or "")[:150]
    try:
        inp = stdin_data.encode() if stdin_data else b""
        p = subprocess.run([exe], input=inp, capture_output=True, timeout=20)
        out = p.stdout.decode("utf-8", "replace")
        return (out.rstrip(), p.returncode & 0xFF), None
    except subprocess.TimeoutExpired:
        return None, "csub 超时"
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


sys.path.insert(0, "tools")
sys.path.insert(0, "src")

passed = failed = 0
for name, src, stdin_data in CASES:
    g, gerr = run_gcc(src, stdin_data)
    if gerr:
        print("SKIP %-16s %s" % (name, gerr[:40]))
        continue
    c, cerr = run_csub(src, stdin_data)
    if cerr:
        print("FAIL %-16s %s" % (name, cerr[:60]))
        failed += 1
        continue
    if g == c:
        passed += 1
        print("PASS %-16s -> %r" % (name, c[0][:34]))
    else:
        failed += 1
        print("DIFF %-16s" % name)
        print("       gcc : %r" % g[0][:60])
        print("       csub: %r" % c[0][:60])

print()
print("边界测试: %d/%d 一致" % (passed, passed + failed))
