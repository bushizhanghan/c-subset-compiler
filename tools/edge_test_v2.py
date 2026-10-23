# -*- coding: utf-8 -*-
"""v2.1.1 迭代：25 个全新边界用例（前两轮没测过的形态）。"""
import os
import subprocess
import sys
import tempfile

os.chdir(r"E:\dsh\csub")
sys.path.insert(0, "tools")
sys.path.insert(0, "src")
NL = chr(10)
N = chr(92) + "n"

CASES = [
    # ── 运算符/表达式新形态 ──
    ("短路赋值", '''#include <stdio.h>
int main(){ int a=0, b=99; if(a!=0 && (b=1)){} printf("%d\\n", b); return 0; }''', None),
    ("链式赋值", '''#include <stdio.h>
int main(){ int a, b, c; a = b = c = 5; printf("%d %d %d\\n", a, b, c); return 0; }''', None),
    ("复合赋值数组", '''#include <stdio.h>
int main(){ int a[3]={1,2,3}; a[0]+=10; a[1]*=3; a[2]%=2; printf("%d %d %d\\n", a[0],a[1],a[2]); return 0; }''', None),
    ("前置后置混用", '''#include <stdio.h>
int main(){ int i=5, s=0; s = i++ + ++i; printf("%d %d\\n", i, s); return 0; }''', None),
    ("负号优先级", '''#include <stdio.h>
int main(){ int a=2; printf("%d\\n", -a*-a); return 0; }''', None),
    ("逻辑非组合", '''#include <stdio.h>
int main(){ int a=5; printf("%d %d\\n", !a, !!a); return 0; }''', None),

    # ── 控制流新形态 ──
    ("for 多初始化", '''#include <stdio.h>
int main(){ int i, j; for(i=0, j=10; i<3; i=i+1){ printf("%d ", i+j); } printf("\\n"); return 0; }''', None),
    ("while 复杂条件", '''#include <stdio.h>
int main(){ int n=100, c=0; while(n!=1){ if(n%2==0){ n=n/2; } else { n=3*n+1; } c=c+1; } printf("%d\\n", c); return 0; }''', None),
    ("嵌套三元", '''#include <stdio.h>
int main(){ int a=5, b=3; printf("%d\\n", a>b ? (a>10?1:2) : (b>2?3:4)); return 0; }''', None),
    ("switch 字符", '''#include <stdio.h>
int main(){ char c='B'; switch(c){ case 'A': printf("a\\n"); break; case 'B': printf("b\\n"); break; default: printf("?\\n"); } return 0; }''', None),
    ("goto 跳出嵌套", '''#include <stdio.h>
int main(){ int i, j; for(i=0;i<3;i=i+1){ for(j=0;j<3;j=j+1){ if(i*j==2){ goto done; } } } done: printf("%d %d\\n", i, j); return 0; }''', None),

    # ── 函数新形态 ──
    ("函数指针数组调用", '''#include <stdio.h>
int add(int a, int b){ return a+b; }
int sub(int a, int b){ return a-b; }
int main(){ int (*ops[2])(int, int); ops[0]=add; ops[1]=sub; printf("%d %d\\n", ops[0](3,4), ops[1](10,4)); return 0; }''', None),
    ("函数指针简单调用", '''#include <stdio.h>
int sq(int x){ return x*x; }
int main(){ int (*f)(int) = sq; printf("%d\\n", f(7)); return 0; }''', None),
    ("递归互调", '''#include <stdio.h>
int is_even(int n);
int is_odd(int n){ if(n==0){ return 0; } return is_even(n-1); }
int is_even(int n){ if(n==0){ return 1; } return is_odd(n-1); }
int main(){ printf("%d %d\\n", is_even(10), is_odd(7)); return 0; }''', None),
    ("六参数函数", '''#include <stdio.h>
int s6(int a,int b,int c,int d,int e,int f){ return a+b+c+d+e+f; }
int main(){ printf("%d\\n", s6(1,2,3,4,5,6)); return 0; }''', None),
    ("七参数函数", '''#include <stdio.h>
int s7(int a,int b,int c,int d,int e,int f,int g){ return a*1000000+b*100000+c*10000+d*1000+e*100+f*10+g; }
int main(){ printf("%d\\n", s7(1,2,3,4,5,6,7)); return 0; }''', None),

    # ── 数组/指针新形态 ──
    ("数组名传参修改", '''#include <stdio.h>
void fill(int a[], int n, int v){ int i; for(i=0;i<n;i=i+1){ a[i]=v; } }
int main(){ int a[4]; fill(a, 4, 7); printf("%d %d\\n", a[0], a[3]); return 0; }''', None),
    ("二维指针参数", '''#include <stdio.h>
int sum2(int a[][3], int rows){ int i,j,s=0; for(i=0;i<rows;i=i+1){ for(j=0;j<3;j=j+1){ s=s+a[i][j]; } } return s; }
int main(){ int a[2][3]={{1,2,3},{4,5,6}}; printf("%d\\n", sum2(a,2)); return 0; }''', None),
    ("指针遍历二维", '''#include <stdio.h>
int main(){ int a[2][3]={{1,2,3},{4,5,6}}; int *p=&a[0][0]; int i,s=0; for(i=0;i<6;i=i+1){ s=s+*p; p=p+1; } printf("%d\\n", s); return 0; }''', None),
    ("字符串字面量传参", '''#include <stdio.h>
int len(char *s){ int n=0; while(s[n]!=0){ n=n+1; } return n; }
int main(){ printf("%d\\n", len("hello world")); return 0; }''', None),
    ("const 指针", '''#include <stdio.h>
int main(){ const int *p; int a=5; p=&a; printf("%d\\n", *p); return 0; }''', None),

    # ── 结构体新形态 ──
    ("结构体指针参数", '''#include <stdio.h>
struct P { int x; int y; };
int dist2(struct P *p){ return p->x*p->x + p->y*p->y; }
int main(){ struct P p; p.x=3; p.y=4; printf("%d\\n", dist2(&p)); return 0; }''', None),
    ("结构体赋值", '''#include <stdio.h>
struct P { int x; int y; };
int main(){ struct P a; a.x=1; a.y=2; struct P b; b=a; b.x=99; printf("%d %d\\n", a.x, b.x); return 0; }''', None),

    # ── 综合 ──
    ("计数排序思想", '''#include <stdio.h>
int main(){ int a[8]={3,1,4,1,5,9,2,6}; int c[10]; int i; for(i=0;i<10;i=i+1){ c[i]=0; }
 for(i=0;i<8;i=i+1){ c[a[i]]=c[a[i]]+1; }
 int out=0; for(i=9;i>=0;i=i-1){ while(c[i]>0){ out=out*10+i; c[i]=c[i]-1; } }
 printf("%d\\n", out); return 0; }''', None),
    ("简单状态机", '''#include <stdio.h>
int main(){ int state=0; char in[8]="aabbb"; int i;
 for(i=0;in[i]!=0;i=i+1){ if(in[i]==97){ state=state+1; } else { state=state-1; } if(state<0){ state=0; } }
 printf("%d\\n", state); return 0; }''', None),
    ("矩阵对角线", '''#include <stdio.h>
int main(){ int a[3][3]={{1,2,3},{4,5,6},{7,8,9}}; int i,s=0;
 for(i=0;i<3;i=i+1){ s=s+a[i][i]; }
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
        return None, "codegen: " + str(e)[:130]
    except Exception as e:
        return None, "err: %s" % str(e)[:130]
    d = tempfile.mkdtemp()
    s = os.path.join(d, "p.s")
    exe = os.path.join(d, "p.exe")
    open(s, "w", encoding="utf-8", newline="\n").write(asm_text)
    r = subprocess.run(["gcc", "-O0", "-o", exe, s],
                       capture_output=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        return None, "build: " + (r.stderr or "")[:130]
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


passed = failed = 0
fails = []
for name, src, stdin_data in CASES:
    g, gerr = run_gcc(src, stdin_data)
    if gerr:
        print("SKIP %-14s %s" % (name, gerr[:50]))
        continue
    c, cerr = run_csub(src, stdin_data)
    if cerr:
        print("FAIL %-14s %s" % (name, cerr[:70]))
        failed += 1
        fails.append((name, cerr))
        continue
    if g == c:
        passed += 1
        print("PASS %-14s -> %r" % (name, c[0][:30]))
    else:
        failed += 1
        print("DIFF %-14s" % name)
        print("       gcc : %r" % g[0][:70])
        print("       csub: %r" % c[0][:70])
        fails.append((name, "gcc=%r csub=%r" % (g[0][:30], c[0][:30])))

print()
print("v2.1.1 边界测试: %d/%d 一致" % (passed, passed + failed))
if fails:
    print()
    print("失败清单:")
    for n, e in fails:
        print("  %-14s %s" % (n, e[:90]))
