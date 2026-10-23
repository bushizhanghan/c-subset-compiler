# -*- coding: utf-8 -*-
"""round3：24 个全新边界用例（前三轮都没测过的形态）。"""
import os
import shutil
import subprocess
import sys
import tempfile

os.chdir(r"E:\dsh\csub")
sys.path.insert(0, "tools")
sys.path.insert(0, "src")
NL = chr(10)
N = chr(92) + "n"

CASES = [
    # ── 位运算（教学大纲有，前几轮没测） ──
    ("位运算全集", '''#include <stdio.h>
int main(){ int a=12, b=10;
 printf("%d %d %d %d\\n", a&b, a|b, a^b, (~a)&255); return 0; }''', None),
    ("移位运算", '''#include <stdio.h>
int main(){ int a=1; printf("%d %d %d\\n", a<<4, 256>>2, a<<10); return 0; }''', None),
    ("位运算复合赋值", '''#include <stdio.h>
int main(){ int a=255; a&=15; a|=256; a^=1; a<<=2; printf("%d\\n", a); return 0; }''', None),
    ("二进制位计数", '''#include <stdio.h>
int main(){ int n=255, c=0; while(n>0){ c=c+(n&1); n=n>>1; } printf("%d\\n", c); return 0; }''', None),

    # ── do-while / 控制流新形态 ──
    ("do-while 基本", '''#include <stdio.h>
int main(){ int i=0; do { printf("%d ", i); i=i+1; } while(i<3); printf("\\n"); return 0; }''', None),
    ("do-while 至少一次", '''#include <stdio.h>
int main(){ int i=10; do { printf("once"); } while(i<5); printf("\\n"); return 0; }''', None),
    ("continue 跳过", '''#include <stdio.h>
int main(){ int i, s=0; for(i=0;i<10;i=i+1){ if(i%2==0){ continue; } s=s+i; } printf("%d\\n", s); return 0; }''', None),
    ("嵌套 break 只出内层", '''#include <stdio.h>
int main(){ int i,j,c=0; for(i=0;i<3;i=i+1){ for(j=0;j<3;j=j+1){ if(j==1){ break; } c=c+1; } } printf("%d\\n", c); return 0; }''', None),

    # ── 递归经典 ──
    ("斐波那契递归", '''#include <stdio.h>
int fib(int n){ if(n<2){ return n; } return fib(n-1)+fib(n-2); }
int main(){ int i; for(i=0;i<10;i=i+1){ printf("%d ", fib(i)); } printf("\\n"); return 0; }''', None),
    ("阶乘", '''#include <stdio.h>
long long fact(int n){ if(n<=1){ return 1; } return n*fact(n-1); }
int main(){ printf("%lld\\n", fact(20)); return 0; }''', None),
    ("GCD 辗转相除", '''#include <stdio.h>
int gcd(int a, int b){ if(b==0){ return a; } return gcd(b, a%b); }
int main(){ printf("%d\\n", gcd(48, 36)); return 0; }''', None),
    ("幂运算递归", '''#include <stdio.h>
int pow2(int b, int e){ if(e==0){ return 1; } return b*pow2(b, e-1); }
int main(){ printf("%d\\n", pow2(2, 10)); return 0; }''', None),
    ("汉诺塔步数", '''#include <stdio.h>
int hanoi(int n){ if(n==0){ return 0; } return 2*hanoi(n-1)+1; }
int main(){ printf("%d\\n", hanoi(10)); return 0; }''', None),

    # ── 排序 / 查找 ──
    ("冒泡排序", '''#include <stdio.h>
int main(){ int a[7]={5,2,8,1,9,3,7}; int i,j,t;
 for(i=0;i<6;i=i+1){ for(j=0;j<6-i;j=j+1){ if(a[j]>a[j+1]){ t=a[j]; a[j]=a[j+1]; a[j+1]=t; } } }
 for(i=0;i<7;i=i+1){ printf("%d ", a[i]); } printf("\\n"); return 0; }''', None),
    ("选择排序", '''#include <stdio.h>
int main(){ int a[6]={4,7,1,9,2,8}; int i,j,m,t;
 for(i=0;i<5;i=i+1){ m=i; for(j=i+1;j<6;j=j+1){ if(a[j]<a[m]){ m=j; } } t=a[i]; a[i]=a[m]; a[m]=t; }
 for(i=0;i<6;i=i+1){ printf("%d ", a[i]); } printf("\\n"); return 0; }''', None),
    ("二分查找", '''#include <stdio.h>
int bsearch2(int a[], int n, int key){ int lo=0, hi=n-1, mid;
 while(lo<=hi){ mid=(lo+hi)/2; if(a[mid]==key){ return mid; } if(a[mid]<key){ lo=mid+1; } else { hi=mid-1; } }
 return -1; }
int main(){ int a[8]={1,3,5,7,9,11,13,15}; printf("%d %d\\n", bsearch2(a,8,7), bsearch2(a,8,8)); return 0; }''', None),

    # ── 指针 / 字符 ──
    ("字符大小写转换", '''#include <stdio.h>
int main(){ char s[]="Hello"; int i;
 for(i=0;s[i]!=0;i=i+1){ if(s[i]>=97 && s[i]<=122){ s[i]=s[i]-32; } }
 printf("%s\\n", s); return 0; }''', None),
    ("字符统计", '''#include <stdio.h>
int main(){ char s[]="aabbbccca"; int i, cnt=1;
 for(i=1;s[i]!=0;i=i+1){ if(s[i]==s[i-1]){ cnt=cnt+1; } else { printf("%d%c", cnt, s[i-1]); cnt=1; } }
 printf("%d%c\\n", cnt, s[i-1]); return 0; }''', None),
    ("指针交换", '''#include <stdio.h>
void swap(int *a, int *b){ int t=*a; *a=*b; *b=t; }
int main(){ int x=3, y=7; swap(&x, &y); printf("%d %d\\n", x, y); return 0; }''', None),
    ("指针遍历字符串", '''#include <stdio.h>
int slen(const char *s){ const char *p=s; while(*p!=0){ p=p+1; } return p-s; }
int main(){ printf("%d\\n", slen("hello world")); return 0; }''', None),

    # ── 类型转换 / 混合 ──
    ("int 与 float 混合", '''#include <stdio.h>
int main(){ int i=7; float f=2.5f; printf("%.1f\\n", i*f); return 0; }''', None),
    ("强制转换截断", '''#include <stdio.h>
int main(){ float f=3.99f; printf("%d %d\\n", (int)f, (int)(-3.99f)); return 0; }''', None),
    ("char 运算提升", '''#include <stdio.h>
int main(){ char c='A'; printf("%d %c\\n", c+1, c+1); return 0; }''', None),
    ("逻辑与算术混合", '''#include <stdio.h>
int main(){ int a=5, b=3; printf("%d %d\\n", a&&b, a||b); return 0; }''', None),
]


def run_gcc(src):
    d = tempfile.mkdtemp()
    c = os.path.join(d, "t.c")
    exe = os.path.join(d, "t.exe")
    open(c, "w", encoding="utf-8", newline="\n").write(src)
    r = subprocess.run(["gcc", "-w", "-O0", "-o", exe, c],
                       capture_output=True, encoding="utf-8", timeout=180)
    if r.returncode != 0:
        shutil.rmtree(d, ignore_errors=True)
        return None, "gcc 编译失败: " + (r.stderr or "")[:120]
    try:
        p = subprocess.run([exe], input=b"", capture_output=True, timeout=20)
        shutil.rmtree(d, ignore_errors=True)
        return (p.stdout.decode("utf-8", "replace").rstrip(),
                p.returncode & 0xFF), None
    except subprocess.TimeoutExpired:
        return None, "gcc 超时"


def run_csub(src):
    from codegen import compile_source, CodegenError
    try:
        asm_text, _ = compile_source(src)
    except CodegenError as e:
        return None, "codegen: " + str(e)[:130]
    except Exception as e:
        return None, "err: %s: %s" % (type(e).__name__, str(e)[:110])
    d = tempfile.mkdtemp()
    s = os.path.join(d, "p.s")
    exe = os.path.join(d, "p.exe")
    open(s, "w", encoding="utf-8", newline="\n").write(asm_text)
    r = subprocess.run(["gcc", "-O0", "-o", exe, s],
                       capture_output=True, encoding="utf-8", timeout=180)
    if r.returncode != 0:
        err = (r.stderr or "")[:130].replace("\n", " ")
        shutil.rmtree(d, ignore_errors=True)
        return None, "build: " + err
    try:
        p = subprocess.run([exe], input=b"", capture_output=True, timeout=20)
        shutil.rmtree(d, ignore_errors=True)
        return (p.stdout.decode("utf-8", "replace").rstrip(),
                p.returncode & 0xFF), None
    except subprocess.TimeoutExpired:
        return None, "csub 超时"
    finally:
        shutil.rmtree(d, ignore_errors=True)


passed = failed = skipped = 0
fails = []
for name, src, stdin_data in CASES:
    g, gerr = run_gcc(src)
    if gerr:
        skipped += 1
        print("SKIP %-14s %s" % (name, gerr[:44]))
        continue
    c, cerr = run_csub(src)
    if cerr:
        print("FAIL %-14s %s" % (name, cerr[:76]))
        failed += 1
        fails.append((name, cerr[:90]))
        continue
    if g == c:
        passed += 1
        print("PASS %-14s %r" % (name, (c[0] or "")[:34]))
    else:
        failed += 1
        print("DIFF %-14s" % name)
        print("       gcc : %r" % (g[0] or "")[:60])
        print("       csub: %r" % (c[0] or "")[:60])
        fails.append((name, "gcc=%r csub=%r" % ((g[0] or "")[:26],
                                               (c[0] or "")[:26])))

print()
print("round3 边界测试: %d/%d 一致（跳过 %d）"
      % (passed, passed + failed, skipped))
if fails:
    print()
    print("失败清单:")
    for n, e in fails:
        print("  %-14s %s" % (n, e[:96]))
