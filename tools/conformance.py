#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""csub 后端对照测试集 —— 每个用例同时跑 csub 和 gcc，比对输出。

为什么要有这个：
  靠人工想测试用例永远覆盖不全。上一轮 `scanf` 读到垃圾值、
  `&a[i]` 取错地址、`float` 转 `int` 存位模式，这三个都是
  "自己写的测试没覆盖到"才漏掉的。

设计原则（抄成熟编译器的做法）：
  1. **同一个 .c 文件同时喂给 csub 和 gcc**，比对 stdout + 退出码
  2. 用例按 C 语言特性分组，每组至少正反两例
  3. 覆盖真实作业里常见的写法，不追求语法完整性
  4. 任何一条不一致都是 bug，不许"跳过"或"已知问题"

用法：
    python tools/conformance.py            # 跑全部
    python tools/conformance.py -v         # 显示每条
    python tools/conformance.py --only io  # 只跑某组
"""
import argparse
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable

# ══════════════════════════════════════════════════════════════
# 用例：(组名, 名称, 源码, stdin 输入)
# 期望结果不写死 —— 直接跟 gcc 编译出来的程序比对
# ══════════════════════════════════════════════════════════════
CASES = [
    # ── 输入输出 ──
    ("io", "printf 整数", '#include <stdio.h>\n'
     'int main(){ printf("%d\\n", 42); return 0; }', None),
    ("io", "printf 多参数", '#include <stdio.h>\n'
     'int main(){ printf("%d %d %d\\n", 1, 2, 3); return 0; }', None),
    ("io", "printf 字符串", '#include <stdio.h>\n'
     'int main(){ printf("hello world\\n"); return 0; }', None),
    ("io", "printf 字符", '#include <stdio.h>\n'
     'int main(){ printf("%c%c\\n", 65, 66); return 0; }', None),
    ("io", "printf 浮点", '#include <stdio.h>\n'
     'int main(){ printf("%.2f\\n", 3.14159); return 0; }', None),
    ("io", "scanf 单个数", '#include <stdio.h>\n'
     'int main(){ int a; scanf("%d", &a); printf("%d\\n", a); return 0; }',
     "42\n"),
    ("io", "scanf 两个数", '#include <stdio.h>\n'
     'int main(){ int a, b; scanf("%d %d", &a, &b);'
     ' printf("%d\\n", a + b); return 0; }', "3 5\n"),
    ("io", "scanf 字符串", '#include <stdio.h>\n'
     'int main(){ char s[32]; scanf("%s", s); printf("%s\\n", s); return 0; }',
     "hello\n"),
    ("io", "scanf 浮点", '#include <stdio.h>\n'
     'int main(){ float f; scanf("%f", &f); printf("%.2f\\n", f); return 0; }',
     "2.5\n"),
    ("io", "scanf 数组", '#include <stdio.h>\n'
     'int main(){ int n, i; int a[20];\n'
     '  scanf("%d", &n);\n'
     '  for(i=0;i<n;i=i+1){ scanf("%d", &a[i]); }\n'
     '  int s=0; for(i=0;i<n;i=i+1){ s=s+a[i]; }\n'
     '  printf("%d\\n", s); return 0; }', "3\n10 20 30\n"),
    ("io", "多行输出", '#include <stdio.h>\n'
     'int main(){ int i; for(i=1;i<=3;i=i+1){'
     ' printf("line %d\\n", i); } return 0; }', None),
    ("io", "puts", '#include <stdio.h>\n'
     'int main(){ puts("via puts"); return 0; }', None),
    ("io", "putchar", '#include <stdio.h>\n'
     'int main(){ putchar(72); putchar(105); putchar(10); return 0; }', None),

    # ── 算术 ──
    ("arith", "加减乘", '#include <stdio.h>\n'
     'int main(){ int a=17,b=5;'
     ' printf("%d %d %d\\n", a+b, a-b, a*b); return 0; }', None),
    ("arith", "整数除法", '#include <stdio.h>\n'
     'int main(){ int a=17,b=5;'
     ' printf("%d %d\\n", a/b, a%b); return 0; }', None),
    ("arith", "负数除法", '#include <stdio.h>\n'
     'int main(){ int a=-17,b=5;'
     ' printf("%d %d\\n", a/b, a%b); return 0; }', None),
    ("arith", "位运算", '#include <stdio.h>\n'
     'int main(){ int a=12,b=10;'
     ' printf("%d %d %d %d %d\\n", a&b, a|b, a^b, a<<2, a>>1); return 0; }',
     None),
    ("arith", "取反", '#include <stdio.h>\n'
     'int main(){ printf("%d\\n", ~5); return 0; }', None),
    ("arith", "优先级", '#include <stdio.h>\n'
     'int main(){ printf("%d\\n", 2+3*4-6/2); return 0; }', None),
    ("arith", "括号", '#include <stdio.h>\n'
     'int main(){ printf("%d\\n", (2+3)*(4-1)); return 0; }', None),
    ("arith", "复合赋值", '#include <stdio.h>\n'
     'int main(){ int a=10; a+=5; a-=3; a*=2; a/=4;'
     ' printf("%d\\n", a); return 0; }', None),
    ("arith", "自增自减", '#include <stdio.h>\n'
     'int main(){ int a=5; int b=a++; int c=++a;'
     ' printf("%d %d %d\\n", a, b, c); return 0; }', None),
    ("arith", "一元负号", '#include <stdio.h>\n'
     'int main(){ int a=5; printf("%d\\n", -a); return 0; }', None),

    # ── 浮点 ──
    ("float", "float 声明", '#include <stdio.h>\n'
     'int main(){ float f=1.5f; printf("%.1f\\n", f); return 0; }', None),
    ("float", "float 运算", '#include <stdio.h>\n'
     'int main(){ float a=1.5f,b=2.5f;'
     ' printf("%.1f\\n", a+b); return 0; }', None),
    ("float", "float 转 int", '#include <stdio.h>\n'
     'int main(){ float f=3.7f; int i=(int)f;'
     ' printf("%d\\n", i); return 0; }', None),
    ("float", "int 转 float", '#include <stdio.h>\n'
     'int main(){ int i=7; float f=(float)i;'
     ' printf("%.1f\\n", f/2.0f); return 0; }', None),
    ("float", "float 比较", '#include <stdio.h>\n'
     'int main(){ float a=1.5f,b=2.5f;'
     ' if(a<b){ printf("less\\n"); } return 0; }', None),
    ("float", "平均分", '#include <stdio.h>\n'
     'int main(){ float a=90.0f,b=80.0f,c=70.0f;'
     ' float avg=(a+b+c)/3.0f; printf("%.2f\\n", avg); return 0; }', None),

    # ── 控制流 ──
    ("flow", "if else", '#include <stdio.h>\n'
     'int main(){ int a=5; if(a>3){ printf("big\\n"); }'
     ' else { printf("small\\n"); } return 0; }', None),
    ("flow", "if 嵌套", '#include <stdio.h>\n'
     'int main(){ int a=5; if(a>0){ if(a>3){ printf("gt3\\n"); }'
     ' else { printf("le3\\n"); } } return 0; }', None),
    ("flow", "while", '#include <stdio.h>\n'
     'int main(){ int i=0; while(i<3){ printf("%d ", i); i=i+1; }'
     ' printf("\\n"); return 0; }', None),
    ("flow", "do while", '#include <stdio.h>\n'
     'int main(){ int i=0; do { printf("%d ", i); i=i+1; }'
     ' while(i<3); printf("\\n"); return 0; }', None),
    ("flow", "for", '#include <stdio.h>\n'
     'int main(){ int i; for(i=0;i<3;i=i+1){ printf("%d ", i); }'
     ' printf("\\n"); return 0; }', None),
    ("flow", "break", '#include <stdio.h>\n'
     'int main(){ int i; for(i=0;i<10;i=i+1){'
     ' if(i==3){ break; } printf("%d ", i); }'
     ' printf("\\n"); return 0; }', None),
    ("flow", "continue", '#include <stdio.h>\n'
     'int main(){ int i; for(i=0;i<5;i=i+1){'
     ' if(i==2){ continue; } printf("%d ", i); }'
     ' printf("\\n"); return 0; }', None),
    ("flow", "嵌套循环", '#include <stdio.h>\n'
     'int main(){ int i,j; for(i=1;i<=2;i=i+1){'
     ' for(j=1;j<=2;j=j+1){ printf("%d%d ", i, j); } }'
     ' printf("\\n"); return 0; }', None),
    ("flow", "switch", '#include <stdio.h>\n'
     'int main(){ int a=2; switch(a){'
     ' case 1: printf("one\\n"); break;'
     ' case 2: printf("two\\n"); break;'
     ' default: printf("other\\n"); } return 0; }', None),
    ("flow", "switch 穿透", '#include <stdio.h>\n'
     'int main(){ int a=1; switch(a){'
     ' case 1: printf("one ");'
     ' case 2: printf("two "); break;'
     ' default: printf("d "); } printf("\\n"); return 0; }', None),
    ("flow", "三目", '#include <stdio.h>\n'
     'int main(){ int a=5; printf("%d\\n", a>3?100:200); return 0; }',
     None),
    ("flow", "逻辑短路", '#include <stdio.h>\n'
     'int main(){ int a=0; int b=0;'
     ' if(a!=0 && 10/a>1){ b=1; }'      # a=0 时右边不会求值
     ' printf("%d\\n", b); return 0; }', None),

    # ── 函数 ──
    ("func", "无参函数", '#include <stdio.h>\n'
     'int f(){ return 7; }\n'
     'int main(){ printf("%d\\n", f()); return 0; }', None),
    ("func", "带参函数", '#include <stdio.h>\n'
     'int add(int a, int b){ return a+b; }\n'
     'int main(){ printf("%d\\n", add(3,4)); return 0; }', None),
    ("func", "多参数", '#include <stdio.h>\n'
     'int sum6(int a,int b,int c,int d,int e,int f){'
     ' return a+b+c+d+e+f; }\n'
     'int main(){ printf("%d\\n", sum6(1,2,3,4,5,6)); return 0; }', None),
    ("func", "递归阶乘", '#include <stdio.h>\n'
     'int fact(int n){ if(n<=1){ return 1; } return n*fact(n-1); }\n'
     'int main(){ printf("%d\\n", fact(5)); return 0; }', None),
    ("func", "递归斐波那契", '#include <stdio.h>\n'
     'int fib(int n){ if(n<2){ return n; }'
     ' return fib(n-1)+fib(n-2); }\n'
     'int main(){ printf("%d\\n", fib(10)); return 0; }', None),
    ("func", "函数修改全局", '#include <stdio.h>\n'
     'int g = 0;\n'
     'void bump(){ g = g + 1; }\n'
     'int main(){ bump(); bump(); printf("%d\\n", g); return 0; }', None),
    ("func", "void 函数", '#include <stdio.h>\n'
     'void greet(){ printf("hi\\n"); }\n'
     'int main(){ greet(); return 0; }', None),
    ("func", "返回值当参数", '#include <stdio.h>\n'
     'int f(int x){ return x*2; }\n'
     'int main(){ printf("%d\\n", f(f(3))); return 0; }', None),

    # ── 数组 ──
    ("array", "一维数组", '#include <stdio.h>\n'
     'int main(){ int a[5]; int i;'
     ' for(i=0;i<5;i=i+1){ a[i]=i*i; }'
     ' for(i=0;i<5;i=i+1){ printf("%d ", a[i]); }'
     ' printf("\\n"); return 0; }', None),
    ("array", "数组初始化", '#include <stdio.h>\n'
     'int main(){ int a[3]={10,20,30};'
     ' printf("%d %d %d\\n", a[0], a[1], a[2]); return 0; }', None),
    ("array", "数组求和", '#include <stdio.h>\n'
     'int main(){ int a[5]={1,2,3,4,5}; int i,s=0;'
     ' for(i=0;i<5;i=i+1){ s=s+a[i]; }'
     ' printf("%d\\n", s); return 0; }', None),
    ("array", "数组求最大值", '#include <stdio.h>\n'
     'int main(){ int a[5]={3,9,2,7,5}; int i,m=a[0];'
     ' for(i=1;i<5;i=i+1){ if(a[i]>m){ m=a[i]; } }'
     ' printf("%d\\n", m); return 0; }', None),
    ("array", "数组反转输出", '#include <stdio.h>\n'
     'int main(){ int a[4]={1,2,3,4}; int i;'
     ' for(i=3;i>=0;i=i-1){ printf("%d ", a[i]); }'
     ' printf("\\n"); return 0; }', None),
    ("array", "字符数组", '#include <stdio.h>\n'
     'int main(){ char s[6]="hello"; int i;'
     ' for(i=0;s[i]!=0;i=i+1){ printf("%c", s[i]); }'
     ' printf("\\n"); return 0; }', None),
    ("array", "二维数组", '#include <stdio.h>\n'
     'int main(){ int a[2][2]; a[0][0]=1; a[0][1]=2;'
     ' a[1][0]=3; a[1][1]=4;'
     ' printf("%d %d %d %d\\n", a[0][0],a[0][1],a[1][0],a[1][1]);'
     ' return 0; }', None),

    # ── 指针 ──
    ("ptr", "取地址解引用", '#include <stdio.h>\n'
     'int main(){ int a=42; int *p=&a;'
     ' printf("%d\\n", *p); return 0; }', None),
    ("ptr", "指针改值", '#include <stdio.h>\n'
     'int main(){ int a=1; int *p=&a; *p=99;'
     ' printf("%d\\n", a); return 0; }', None),
    ("ptr", "指针算术", '#include <stdio.h>\n'
     'int main(){ int a[5]={10,20,30,40,50}; int *p=a;'
     ' printf("%d %d\\n", *p, *(p+2)); return 0; }', None),
    ("ptr", "指针遍历", '#include <stdio.h>\n'
     'int main(){ int a[4]={1,2,3,4}; int *p=a; int i;'
     ' for(i=0;i<4;i=i+1){ printf("%d ", *(p+i)); }'
     ' printf("\\n"); return 0; }', None),
    ("ptr", "指针交换", '#include <stdio.h>\n'
     'int main(){ int a=1,b=2; int *p=&a,*q=&b;'
     ' int t=*p; *p=*q; *q=t;'
     ' printf("%d %d\\n", a, b); return 0; }', None),
    ("ptr", "函数改指针目标", '#include <stdio.h>\n'
     'void set(int *p, int v){ *p = v; }\n'
     'int main(){ int a=0; set(&a, 55);'
     ' printf("%d\\n", a); return 0; }', None),
    ("ptr", "数组传参", '#include <stdio.h>\n'
     'int sum(int a[], int n){ int i,s=0;'
     ' for(i=0;i<n;i=i+1){ s=s+a[i]; } return s; }\n'
     'int main(){ int a[3]={1,2,3};'
     ' printf("%d\\n", sum(a,3)); return 0; }', None),

    # ── 字符串 ──
    ("str", "strlen", '#include <stdio.h>\n#include <string.h>\n'
     'int main(){ printf("%d\\n", (int)strlen("hello")); return 0; }', None),
    ("str", "strcmp", '#include <stdio.h>\n#include <string.h>\n'
     'int main(){ if(strcmp("abc","abd")<0){ printf("lt\\n"); }'
     ' return 0; }', None),
    ("str", "strcpy", '#include <stdio.h>\n#include <string.h>\n'
     'int main(){ char a[16]; strcpy(a, "copied");'
     ' printf("%s\\n", a); return 0; }', None),

    # ── 结构体 ──
    ("struct", "结构体基本", '#include <stdio.h>\n'
     'struct P { int x; int y; };\n'
     'int main(){ struct P p; p.x=1; p.y=2;'
     ' printf("%d %d\\n", p.x, p.y); return 0; }', None),
    ("struct", "结构体指针", '#include <stdio.h>\n'
     'struct P { int x; int y; };\n'
     'int main(){ struct P p; struct P *q=&p;'
     ' q->x=3; q->y=4;'
     ' printf("%d %d\\n", p.x, p.y); return 0; }', None),
    ("struct", "typedef 结构体", '#include <stdio.h>\n'
     'typedef struct { int r; int c; } Pos;\n'
     'int main(){ Pos p; p.r=5; p.c=6;'
     ' printf("%d %d\\n", p.r, p.c); return 0; }', None),
    ("struct", "结构体数组", '#include <stdio.h>\n'
     'struct P { int v; };\n'
     'int main(){ struct P a[3]; int i;'
     ' for(i=0;i<3;i=i+1){ a[i].v=i*10; }'
     ' for(i=0;i<3;i=i+1){ printf("%d ", a[i].v); }'
     ' printf("\\n"); return 0; }', None),

    # ── 全局变量 ──
    ("global", "全局 int", '#include <stdio.h>\n'
     'int g = 100;\n'
     'int main(){ printf("%d\\n", g); return 0; }', None),
    ("global", "全局修改", '#include <stdio.h>\n'
     'int g = 1;\n'
     'int main(){ g = 2; printf("%d\\n", g); return 0; }', None),
    ("global", "全局数组", '#include <stdio.h>\n'
     'int g[3] = {7,8,9};\n'
     'int main(){ printf("%d %d %d\\n", g[0], g[1], g[2]); return 0; }',
     None),

    # ── 综合（真实作业形态） ──
    ("real", "冒泡排序", '#include <stdio.h>\n'
     'int main(){ int a[5]={5,3,1,4,2}; int i,j,t;'
     ' for(i=0;i<4;i=i+1){ for(j=0;j<4-i;j=j+1){'
     ' if(a[j]>a[j+1]){ t=a[j]; a[j]=a[j+1]; a[j+1]=t; } } }'
     ' for(i=0;i<5;i=i+1){ printf("%d ", a[i]); }'
     ' printf("\\n"); return 0; }', None),
    ("real", "求素数", '#include <stdio.h>\n'
     'int main(){ int n,i,is_p; for(n=2;n<=20;n=n+1){'
     ' is_p=1; for(i=2;i*i<=n;i=i+1){'
     ' if(n%i==0){ is_p=0; break; } }'
     ' if(is_p){ printf("%d ", n); } }'
     ' printf("\\n"); return 0; }', None),
    ("real", "求最大公约数", '#include <stdio.h>\n'
     'int gcd(int a, int b){ while(b!=0){ int t=a%b; a=b; b=t; }'
     ' return a; }\n'
     'int main(){ printf("%d\\n", gcd(48,18)); return 0; }', None),
    ("real", "数字转字符串", '#include <stdio.h>\n'
     'int main(){ int n=1234; char s[16]; int i=0,j;'
     ' while(n>0){ s[i]=n%10+48; n=n/10; i=i+1; }'
     ' for(j=i-1;j>=0;j=j-1){ printf("%c", s[j]); }'
     ' printf("\\n"); return 0; }', None),
    ("real", "统计字符", '#include <stdio.h>\n'
     'int main(){ char s[32]; int i,c=0;'
     ' scanf("%s", s);'
     ' for(i=0;s[i]!=0;i=i+1){ c=c+1; }'
     ' printf("%d\\n", c); return 0; }', "abcdef\n"),
    ("real", "矩阵转置", '#include <stdio.h>\n'
     'int main(){ int a[2][3]; int i,j;'
     ' for(i=0;i<2;i=i+1){ for(j=0;j<3;j=j+1){ a[i][j]=i*3+j; } }'
     ' for(j=0;j<3;j=j+1){ for(i=0;i<2;i=i+1){'
     ' printf("%d ", a[i][j]); } }'
     ' printf("\\n"); return 0; }', None),
    ("real", "简单计算器", '#include <stdio.h>\n'
     'int main(){ int a,b; char op;'
     ' scanf("%d %c %d", &a, &op, &b);'
     ' if(op==43){ printf("%d\\n", a+b); }'
     ' else if(op==45){ printf("%d\\n", a-b); }'
     ' else if(op==42){ printf("%d\\n", a*b); }'
     ' return 0; }', "6 + 7\n"),
]


# ══════════════════════════════════════════════════════════════
# 第二轮扩充：真实学生作业高频写法（+54）
# ══════════════════════════════════════════════════════════════
CASES += [
    # ── 算术/边界 ──
    ("arith2", "负数取模", '#include <stdio.h>\n'
     'int main(){ printf("%d %d\n", -7%3, 7%-3); return 0; }', None),
    ("arith2", "long long", '#include <stdio.h>\n'
     'int main(){ long long x=123456789; x=x*x;'
     ' printf("%lld\n", x); return 0; }', None),
    ("arith2", "char 溢出", '#include <stdio.h>\n'
     'int main(){ char c=127; c=c+1; printf("%d\n", c); return 0; }', None),
    ("arith2", "混合类型运算", '#include <stdio.h>\n'
     'int main(){ int a=3; float b=2.5f;'
     ' printf("%.1f\n", a+b); return 0; }', None),
    ("arith2", "移位运算", '#include <stdio.h>\n'
     'int main(){ int x=1; printf("%d %d\n", x<<10, 1024>>3); return 0; }',
     None),
    ("arith2", "三元嵌套", '#include <stdio.h>\n'
     'int main(){ int a=5; printf("%d\n", a>10?1:a>3?2:3); return 0; }',
     None),
    ("arith2", "逗号表达式", '#include <stdio.h>\n'
     'int main(){ int a=1; int b=(a=5, a+1);'
     ' printf("%d %d\n", a, b); return 0; }', None),
    ("arith2", "复合位运算", '#include <stdio.h>\n'
     'int main(){ int x=255; x&=15; x|=256; x^=1;'
     ' printf("%d\n", x); return 0; }', None),

    # ── 控制流 ──
    ("flow2", "switch 穿透", '#include <stdio.h>\n'
     'int main(){ int x=3; switch(x){'
     ' case 1: case 2: printf("low "); break;'
     ' case 3: printf("mid ");'
     ' case 4: printf("high "); break; }'
     ' printf("\n"); return 0; }', None),
    ("flow2", "switch default 前置", '#include <stdio.h>\n'
     'int main(){ int x=9; switch(x){'
     ' default: printf("def "); break;'
     ' case 1: printf("one "); }'
     ' printf("\n"); return 0; }', None),
    ("flow2", "do while 至少一次", '#include <stdio.h>\n'
     'int main(){ int i=10; do { printf("once\n"); } while(i<5);'
     ' return 0; }', None),
    ("flow2", "continue 在 while", '#include <stdio.h>\n'
     'int main(){ int i=0, s=0; while(i<10){ i=i+1;'
     ' if(i%2==0){ continue; } s=s+i; }'
     ' printf("%d\n", s); return 0; }', None),
    ("flow2", "嵌套 break", '#include <stdio.h>\n'
     'int main(){ int i,j; for(i=0;i<3;i=i+1){'
     ' for(j=0;j<3;j=j+1){ if(j==2){ break; }'
     ' printf("%d%d ", i, j); } }'
     ' printf("\n"); return 0; }', None),
    ("flow2", "循环里定义变量", '#include <stdio.h>\n'
     'int main(){ int i; for(i=0;i<3;i=i+1){'
     ' int local=i*2; printf("%d ", local); }'
     ' printf("\n"); return 0; }', None),
    ("flow2", "if 块内变量", '#include <stdio.h>\n'
     'int main(){ int a=5; if(a>3){ int b=10; printf("%d\n", b); }'
     ' return 0; }', None),

    # ── 函数 ──
    ("func2", "函数内多变量", '#include <stdio.h>\n'
     'int f(int n){ int a=n, b=n+1, c=n+2; return a+b+c; }\n'
     'int main(){ printf("%d\n", f(10)); return 0; }', None),
    ("func2", "递归汉诺塔计数", '#include <stdio.h>\n'
     'int hanoi(int n){ if(n==1){ return 1; }'
     ' return 2*hanoi(n-1)+1; }\n'
     'int main(){ printf("%d\n", hanoi(4)); return 0; }', None),
    ("func2", "函数修改全局", '#include <stdio.h>\n'
     'int cnt=0;\nvoid inc(){ cnt=cnt+1; }\n'
     'int main(){ inc(); inc(); inc();'
     ' printf("%d\n", cnt); return 0; }', None),
    ("func2", "递归求和", '#include <stdio.h>\n'
     'int sum(int n){ if(n==0){ return 0; }'
     ' return n+sum(n-1); }\n'
     'int main(){ printf("%d\n", sum(100)); return 0; }', None),
    ("func2", "void 函数图案", '#include <stdio.h>\n'
     'void line(int n){ int i; for(i=0;i<n;i=i+1){'
     ' printf("*"); } printf("\n"); }\n'
     'int main(){ line(5); line(3); return 0; }', None),
    ("func2", "绝对值比较", '#include <stdio.h>\n'
     'int abs2(int x){ if(x<0){ return -x; } return x; }\n'
     'int main(){ if(abs2(-5)==5){ printf("ok\n"); } return 0; }', None),

    # ── 数组 ──
    ("array2", "数组部分初始化", '#include <stdio.h>\n'
     'int main(){ int a[5]={1,2};'
     ' printf("%d %d %d\n", a[0], a[1], a[4]); return 0; }', None),
    ("array2", "数组倒序", '#include <stdio.h>\n'
     'int main(){ int a[4]={1,2,3,4}; int i,j,t;'
     ' for(i=0;i<4/2;i=i+1){ j=3-i; t=a[i]; a[i]=a[j]; a[j]=t; }'
     ' for(i=0;i<4;i=i+1){ printf("%d ", a[i]); }'
     ' printf("\n"); return 0; }', None),
    ("array2", "数组查找", '#include <stdio.h>\n'
     'int main(){ int a[5]={1,2,3,4,5}; int i, found=-1;'
     ' for(i=0;i<5;i=i+1){ if(a[i]==3){ found=i; break; } }'
     ' printf("%d\n", found); return 0; }', None),
    ("array2", "数组统计", '#include <stdio.h>\n'
     'int main(){ int a[8]={1,2,2,3,3,3,4,5}; int i, c=0;'
     ' for(i=0;i<8;i=i+1){ if(a[i]==3){ c=c+1; } }'
     ' printf("%d\n", c); return 0; }', None),
    ("array2", "数组拷贝", '#include <stdio.h>\n'
     'int main(){ int a[3]={7,8,9}; int b[3]; int i;'
     ' for(i=0;i<3;i=i+1){ b[i]=a[i]; }'
     ' printf("%d %d %d\n", b[0], b[1], b[2]); return 0; }', None),
    ("array2", "字符数组统计元音", '#include <stdio.h>\n'
     'int main(){ char s[16]="hello world"; int i,c=0;'
     ' for(i=0;s[i]!=0;i=i+1){'
     ' if(s[i]==97||s[i]==101||s[i]==105||s[i]==111||s[i]==117){'
     ' c=c+1; } }'
     ' printf("%d\n", c); return 0; }', None),

    # ── 指针 ──
    ("ptr2", "指针当游标", '#include <stdio.h>\n'
     'int main(){ int a[4]={10,20,30,40}; int *p=a; int s=0; int i;'
     ' for(i=0;i<4;i=i+1){ s=s+*p; p=p+1; }'
     ' printf("%d\n", s); return 0; }', None),
    ("ptr2", "指针改数组", '#include <stdio.h>\n'
     'int main(){ int a[3]={1,2,3}; int *p=a; *(p+1)=99;'
     ' printf("%d %d %d\n", a[0], a[1], a[2]); return 0; }', None),
    ("ptr2", "swap 函数", '#include <stdio.h>\n'
     'void sw(int *a, int *b){ int t=*a; *a=*b; *b=t; }\n'
     'int main(){ int x=1, y=2; sw(&x, &y);'
     ' printf("%d %d\n", x, y); return 0; }', None),

    # ── 结构体 ──
    ("struct2", "结构体数组排序", '#include <stdio.h>\n'
     'struct S { int v; };\n'
     'int main(){ struct S a[4]; int i,j; struct S t;'
     ' a[0].v=3; a[1].v=1; a[2].v=4; a[3].v=2;'
     ' for(i=0;i<3;i=i+1){ for(j=0;j<3-i;j=j+1){'
     ' if(a[j].v>a[j+1].v){ t=a[j]; a[j]=a[j+1]; a[j+1]=t; } } }'
     ' for(i=0;i<4;i=i+1){ printf("%d ", a[i].v); }'
     ' printf("\n"); return 0; }', None),
    ("struct2", "结构体成员运算", '#include <stdio.h>\n'
     'struct P { int x; int y; };\n'
     'int main(){ struct P p; p.x=1; p.y=2; int s=p.x+p.y;'
     ' printf("%d\n", s); return 0; }', None),
    ("struct2", "typedef + 指针", '#include <stdio.h>\n'
     'typedef struct { int v; } T;\n'
     'int main(){ T a[2]; T *p=a; p->v=7; (p+1)->v=8;'
     ' printf("%d %d\n", a[0].v, a[1].v); return 0; }', None),

    # ── 字符串处理 ──
    ("str2", "字符转大写", '#include <stdio.h>\n'
     'int main(){ char s[16]="hello"; int i;'
     ' for(i=0;s[i]!=0;i=i+1){'
     ' if(s[i]>=97 && s[i]<=122){ s[i]=s[i]-32; } }'
     ' printf("%s\n", s); return 0; }', None),
    ("str2", "字符串比较", '#include <stdio.h>\n'
     'int main(){ char a[16]="abc"; char b[16]="abc";'
     ' int i, same=1;'
     ' for(i=0;a[i]!=0||b[i]!=0;i=i+1){'
     ' if(a[i]!=b[i]){ same=0; break; } }'
     ' printf("%d\n", same); return 0; }', None),
    ("str2", "数字字符求和", '#include <stdio.h>\n'
     'int main(){ char s[16]="12345"; int i, s2=0;'
     ' for(i=0;s[i]!=0;i=i+1){ s2=s2+(s[i]-48); }'
     ' printf("%d\n", s2); return 0; }', None),

    # ── 真实作业形态 ──
    ("real2", "判断闰年", '#include <stdio.h>\n'
     'int main(){ int y=2024;'
     ' if((y%4==0 && y%100!=0) || y%400==0){'
     ' printf("leap\n"); } else { printf("no\n"); }'
     ' return 0; }', None),
    ("real2", "水仙花数", '#include <stdio.h>\n'
     'int main(){ int n=153, a,b,c;'
     ' a=n/100; b=n/10%10; c=n%10;'
     ' if(a*a*a+b*b*b+c*c*c==n){ printf("yes\n"); }'
     ' return 0; }', None),
    ("real2", "斐波那契数组", '#include <stdio.h>\n'
     'int main(){ int f[10]; int i; f[0]=1; f[1]=1;'
     ' for(i=2;i<10;i=i+1){ f[i]=f[i-1]+f[i-2]; }'
     ' printf("%d\n", f[9]); return 0; }', None),
    ("real2", "选择排序", '#include <stdio.h>\n'
     'int main(){ int a[5]={5,2,4,1,3}; int i,j,m,t;'
     ' for(i=0;i<4;i=i+1){ m=i;'
     '  for(j=i+1;j<5;j=j+1){ if(a[j]<a[m]){ m=j; } }'
     '  t=a[i]; a[i]=a[m]; a[m]=t; }'
     ' for(i=0;i<5;i=i+1){ printf("%d ", a[i]); }'
     ' printf("\n"); return 0; }', None),
    ("real2", "插入排序", '#include <stdio.h>\n'
     'int main(){ int a[5]={5,2,4,1,3}; int i,j,t;'
     ' for(i=1;i<5;i=i+1){ t=a[i]; j=i-1;'
     '  while(j>=0 && a[j]>t){ a[j+1]=a[j]; j=j-1; }'
     '  a[j+1]=t; }'
     ' for(i=0;i<5;i=i+1){ printf("%d ", a[i]); }'
     ' printf("\n"); return 0; }', None),
    ("real2", "杨辉三角", '#include <stdio.h>\n'
     'int main(){ int t[5][5]; int i,j;'
     ' for(i=0;i<5;i=i+1){ t[i][0]=1;'
     '  for(j=1;j<=i;j=j+1){'
     '   t[i][j]=t[i-1][j-1]+t[i-1][j]; } }'
     ' printf("%d %d %d\n", t[4][0], t[4][2], t[4][4]);'
     ' return 0; }', None),
    ("real2", "统计平均", '#include <stdio.h>\n'
     'int main(){ int s[6]={85,92,78,90,88,76}; int i, sum=0;'
     ' for(i=0;i<6;i=i+1){ sum=sum+s[i]; }'
     ' printf("%d\n", sum/6); return 0; }', None),
    ("real2", "逆序输出数字", '#include <stdio.h>\n'
     'int main(){ int n=12345;'
     ' while(n>0){ printf("%d", n%10); n=n/10; }'
     ' printf("\n"); return 0; }', None),
    ("real2", "摄氏转华氏", '#include <stdio.h>\n'
     'int main(){ float c=37.0f; float f=c*9.0f/5.0f+32.0f;'
     ' printf("%.1f\n", f); return 0; }', None),
    ("real2", "一到一百求和", '#include <stdio.h>\n'
     'int main(){ int i, s=0; for(i=1;i<=100;i=i+1){ s=s+i; }'
     ' printf("%d\n", s); return 0; }', None),
    ("real2", "乘法表", '#include <stdio.h>\n'
     'int main(){ int i,j; for(i=1;i<=3;i=i+1){'
     ' for(j=1;j<=i;j=j+1){ printf("%d*%d=%d ", i, j, i*j); } }'
     ' printf("\n"); return 0; }', None),
    ("real2", "质数计数", '#include <stdio.h>\n'
     'int is_prime(int n){ int i;'
     ' if(n<2){ return 0; }'
     ' for(i=2;i*i<=n;i=i+1){ if(n%i==0){ return 0; } }'
     ' return 1; }\n'
     'int main(){ int c=0, n; for(n=2;n<=30;n=n+1){'
     ' if(is_prime(n)){ c=c+1; } }'
     ' printf("%d\n", c); return 0; }', None),
    ("real2", "gcd 递归", '#include <stdio.h>\n'
     'int gcd(int a, int b){ if(b==0){ return a; }'
     ' return gcd(b, a%b); }\n'
     'int main(){ printf("%d\n", gcd(1071, 462)); return 0; }', None),
    ("real2", "二进制转十进制", '#include <stdio.h>\n'
     'int main(){ int b=1101, d=0, w=1;'
     ' while(b>0){ d=d+(b%10)*w; b=b/10; w=w*2; }'
     ' printf("%d\n", d); return 0; }', None),
    ("real2", "三角形判断", '#include <stdio.h>\n'
     'int main(){ int a=3, b=4, c=5;'
     ' if(a+b>c && b+c>a && a+c>b){'
     ' printf("valid\n"); } else { printf("invalid\n"); }'
     ' return 0; }', None),
    ("real2", "第二大值", '#include <stdio.h>\n'
     'int main(){ int a[5]={3,9,2,7,5}; int i, m1=a[0], m2=a[0];'
     ' for(i=1;i<5;i=i+1){'
     '  if(a[i]>m1){ m2=m1; m1=a[i]; }'
     '  else if(a[i]>m2){ m2=a[i]; } }'
     ' printf("%d\n", m2); return 0; }', None),
]



def run_gcc(src, stdin_data):
    """用 gcc 编译并运行，返回 (stdout, 退出码) 或 (None, 错误)。"""
    d = tempfile.mkdtemp()
    c = os.path.join(d, "t.c")
    exe = os.path.join(d, "t.exe")
    open(c, "w", encoding="utf-8", newline="\n").write(src)
    r = subprocess.run(["gcc", "-w", "-O0", "-o", exe, c],
                       capture_output=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        return None, "gcc 编译失败: " + (r.stderr or "")[:200]
    try:
        inp = stdin_data.encode() if stdin_data is not None else b""
        p = subprocess.run([exe], input=inp, capture_output=True, timeout=30)
        out = p.stdout.decode("utf-8", "replace")
        code = p.returncode & 0xFF
        # 清理
        for f in (c, exe):
            try:
                os.remove(f)
            except OSError:
                pass
        os.rmdir(d)
        return (out, code), None
    except subprocess.TimeoutExpired:
        return None, "gcc 程序超时"


def run_csub(src, stdin_data):
    """用 csub 编译并运行，返回 (stdout, 退出码) 或 (None, 错误)。"""
    c = "_conf.c"
    s = "_conf.s"
    open(c, "w", encoding="utf-8", newline="\n").write(src)
    for f in (s, "_conf.exe"):
        if os.path.exists(f):
            os.remove(f)
    try:
        r = subprocess.run([PY, "tools/codegen.py", c, "-o", s],
                           capture_output=True, encoding="utf-8", timeout=120)
        if not os.path.exists(s) or os.path.getsize(s) == 0:
            err = (r.stdout + r.stderr).strip()
            lines = [l for l in err.splitlines()
                     if "codegen" in l or "不支持" in l]
            return None, (lines[0] if lines else err[:180])
        cmd = [PY, "tools/asm_run.py", s, "--quiet"]
        if stdin_data is not None:
            cmd += ["--stdin", stdin_data]
        r2 = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                            timeout=120)
        out = r2.stdout
        lines = [l for l in out.splitlines() if l.strip()]
        code = None
        if lines and lines[-1].strip().lstrip("-").isdigit():
            code = int(lines[-1]) & 0xFF
            out = "\n".join(lines[:-1])
            if out:
                out += "\n"
        return (out, code), None
    except subprocess.TimeoutExpired:
        return None, "csub 超时"
    finally:
        for f in (c, s, "_conf.exe"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass


def main():
    ap = argparse.ArgumentParser(description="csub vs gcc 对照测试")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--only", default=None, help="只跑某个组")
    args = ap.parse_args()

    groups = {}
    for g, name, src, stdin_data in CASES:
        if args.only and g != args.only:
            continue
        groups.setdefault(g, []).append((name, src, stdin_data))

    total = passed = 0
    fails = []

    for g, items in groups.items():
        print("[%s]" % g)
        for name, src, stdin_data in items:
            total += 1
            gcc_res, gcc_err = run_gcc(src, stdin_data)
            csub_res, csub_err = run_csub(src, stdin_data)

            if gcc_err:
                print("  SKIP %-18s gcc 都跑不了: %s"
                      % (name, gcc_err[:60]))
                total -= 1
                continue

            if csub_err:
                print("  FAIL %-18s csub 失败: %s"
                      % (name, csub_err[:70]))
                fails.append((g, name, "csub 报错", csub_err[:100]))
                continue

            g_out, g_code = gcc_res
            c_out, c_code = csub_res

            # 归一化：去掉尾部空白差异和换行符差异（\r\n vs \n），
            # 避免因为 Windows 换行判成失败
            g_norm = (g_out or "").replace("\r\n", "\n").rstrip()
            c_norm = (c_out or "").replace("\r\n", "\n").rstrip()

            if g_norm == c_norm and g_code == c_code:
                passed += 1
                if args.verbose:
                    print("  OK   %-18s -> %r"
                          % (name, c_out.rstrip()[:40]))
            else:
                print("  DIFF %-18s" % name)
                print("       gcc : %r (exit %s)"
                      % (g_out.rstrip()[:60], g_code))
                print("       csub: %r (exit %s)"
                      % (c_out.rstrip()[:60], c_code))
                fails.append((g, name, g_out.strip()[:50],
                              c_out.strip()[:50]))
        print()

    print("=" * 64)
    print("对照测试: %d/%d 一致" % (passed, total))
    if fails:
        print()
        print("不一致的:")
        for g, name, a, b in fails:
            print("  [%s] %s" % (g, name))
            print("      gcc : %s" % a)
            print("      csub: %s" % b)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
