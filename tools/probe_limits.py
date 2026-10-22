# -*- coding: utf-8 -*-
"""能力边界实测脚本 —— 文档里的每个数字都必须能由这个脚本复现。

用法：
    python tools/probe_limits.py

输出四张表：
  1. 前端语法支持（能不能解析）
  2. 后端代码生成支持（能不能编译成汇编）
  3. 诊断规则清单（从 semantic.py 里提取）
  4. 六套验证的当前结果（调用各工具）
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable

# ---------------- 前端语法 ----------------
FRONTEND = [
    ("基本语法", '#include <stdio.h>\nint main(){ int a=1; if(a) return a; return 0; }\n'),
    ("struct/typedef", 'typedef struct P { int r, c; } P;\nint main(){ P p; p.r=1; return p.r; }\n'),
    ("enum", 'typedef enum { A, B } E;\nint main(){ E e=B; return e; }\n'),
    ("union", 'typedef union { int i; float f; } U;\nint main(){ U u; u.i=3; return u.i; }\n'),
    ("位字段", 'struct F { unsigned a:1; unsigned b:3; };\nint main(){ struct F f; f.a=1; return f.a; }\n'),
    ("匿名 struct/union", 'struct S { int k; union { struct { int x, y; }; }; };\n'
                          'int main(){ struct S s; s.x=1; return s.x; }\n'),
    ("函数指针", 'struct S { int (*f)(int); };\nint g(int x){ return x; }\n'
                 'int main(){ struct S s; s.f=g; return s.f(1); }\n'),
    ("复合字面量", 'typedef struct { int a; } P;\nint h(P p){ return p.a; }\n'
                   'int main(){ return h((P){1}); }\n'),
    ("指定初始化器", 'int main(){ int a[5]={[0]=1,[2]=3}; return a[2]; }\n'),
    ("VLA", 'int main(){ int n=3; int a[n]; a[0]=1; return a[0]; }\n'),
    ("goto/label", 'int main(){ int i=0;\nL: i=i+1; if(i<3) goto L; return i; }\n'),
    ("_Bool", 'int main(){ _Bool b=1; return b; }\n'),
    ("restrict", '#include <string.h>\n'
                 'void g(char *restrict d, const char *restrict s, int n)\n'
                 '{ int i; for(i=0;i<n;i=i+1){ d[i]=s[i]; } }\n'
                 'int main(){ char b[4]; g(b,"ab",2); return 0; }\n'),
    ("__attribute__", 'static int f(void) __attribute__((unused));\n'
                      'static int f(void){ return 1; }\nint main(){ return f(); }\n'),
    ("头文件保护", '#ifndef H\n#define H\nstatic int v=1;\n#endif\n'
                   'int main(){ return v; }\n'),
    ("函数式宏", '#define SQ(x) ((x)*(x))\nint main(){ return SQ(3); }\n'),
    ("字符串化/拼接", '#define STR(x) #x\n#define CAT(a,b) a##b\n'
                      'int main(){ int CAT(v,ar)=1; return var; }\n'),
    ("va_list", '#include <stdarg.h>\n'
                'int s(int n, ...){ va_list ap; va_start(ap,n);\n'
                '  int t=va_arg(ap,int); va_end(ap); return t; }\n'
                'int main(){ return s(1,5); }\n'),
    ("宽字符", '#include <stdio.h>\nint main(){ printf("%ls", L"hi"); return 0; }\n'),
]

# ---------------- 后端代码生成 ----------------
BACKEND = [
    ("算术/逻辑/位运算", '#include <stdio.h>\n'
                        'int main(){ int a=5,b=3; printf("%d\\n", (a&b)|(a^b)); return 0; }\n'),
    ("if/while/for/switch", '#include <stdio.h>\nint main(){ int i,s=0;\n'
                            '  for(i=0;i<5;i=i+1){ s=s+i; }\n'
                            '  switch(s){ case 10: printf("10\\n"); break; default: break; }\n'
                            '  return 0; }\n'),
    ("函数调用（含栈传参）", '#include <stdio.h>\n'
                            'int add6(int a,int b,int c,int d,int e,int f)\n'
                            '{ return a+b+c+d+e+f; }\n'
                            'int main(){ printf("%d\\n", add6(1,2,3,4,5,6)); return 0; }\n'),
    ("递归", '#include <stdio.h>\n'
             'int fib(int n){ if(n<2) return n; return fib(n-1)+fib(n-2); }\n'
             'int main(){ printf("%d\\n", fib(10)); return 0; }\n'),
    ("一维数组", '#include <stdio.h>\n'
                 'int main(){ int a[5]; int i;\n'
                 '  for(i=0;i<5;i=i+1){ a[i]=i*i; }\n'
                 '  printf("%d\\n", a[4]); return 0; }\n'),
    ("数组传参", '#include <stdio.h>\n'
                 'int sum(int a[], int n){ int i,s=0;\n'
                 '  for(i=0;i<n;i=i+1){ s=s+a[i]; } return s; }\n'
                 'int main(){ int a[3]; a[0]=1;a[1]=2;a[2]=3;\n'
                 '  printf("%d\\n", sum(a,3)); return 0; }\n'),
    ("指针算术", '#include <stdio.h>\n'
                 'int main(){ int a[5]; int *p=a; p[0]=1; p[1]=2;\n'
                 '  printf("%d\\n", *(p+1)); return 0; }\n'),
    ("char* 解引用", '#include <stdio.h>\n'
                     'int main(){ char s[4]; s[0]=\'A\'; s[1]=0;\n'
                     '  printf("%c\\n", s[0]); return 0; }\n'),
    ("float", '#include <stdio.h>\n'
              'int main(){ float f=1.5f; f=f*2.0f; printf("%d\\n", (int)f); return 0; }\n'),
    ("double", '#include <stdio.h>\n'
               'int main(){ double d=1.5; d=d*3.0; printf("%d\\n", (int)d); return 0; }\n'),
    ("结构体（局部）", '#include <stdio.h>\n'
                       'int main(){ struct P { int r, c; } p; p.r=1; p.c=2;\n'
                       '  printf("%d\\n", p.r+p.c); return 0; }\n'),
    ("全局变量", '#include <stdio.h>\nint g=7;\n'
                 'int main(){ g=g+1; printf("%d\\n", g); return 0; }\n'),
    ("全局数组", '#include <stdio.h>\nint g[4];\n'
                 'int main(){ g[0]=9; printf("%d\\n", g[0]); return 0; }\n'),
    ("malloc/free", '#include <stdlib.h>\n#include <stdio.h>\n'
                    'int main(){ int *p=malloc(sizeof(int)*4); if(!p) return 1;\n'
                    '  p[0]=7; printf("%d\\n", p[0]); free(p); return 0; }\n'),
    ("字符串函数", '#include <stdio.h>\n#include <string.h>\n'
                   'int main(){ char s[16]; strcpy(s,"hi");\n'
                   '  printf("%s %d\\n", s, strlen(s)); return 0; }\n'),
    ("多维数组", '#include <stdio.h>\n'
                 'int main(){ int a[2][3]; a[1][2]=5;\n'
                 '  printf("%d\\n", a[1][2]); return 0; }\n'),
    ("函数返回结构体", '#include <stdio.h>\n'
                       'typedef struct { int a; } S;\n'
                       'S make(){ S s; s.a=42; return s; }\n'
                       'int main(){ S s=make(); printf("%d\\n", s.a); return 0; }\n'),
]


def run_check(src):
    open("_probe.c", "w", encoding="utf-8", newline="\n").write(src)
    r = subprocess.run([PY, "csub.py", "check", "_probe.c"],
                       capture_output=True, encoding="utf-8", timeout=180)
    out = r.stdout + r.stderr
    codes = re.findall(r"\[(?:ERROR|WARNING)\s+(\w+)\]", out)
    os.remove("_probe.c")
    return codes


def run_codegen(src):
    open("_probe.c", "w", encoding="utf-8", newline="\n").write(src)
    for f in ("_probe.s",):
        if os.path.exists(f):
            os.remove(f)
    r = subprocess.run([PY, "tools/codegen.py", "_probe.c", "-o", "_probe.s"],
                       capture_output=True, encoding="utf-8", timeout=240)
    ok = os.path.exists("_probe.s") and os.path.getsize("_probe.s") > 0
    os.remove("_probe.c")
    if os.path.exists("_probe.s"):
        os.remove("_probe.s")
    return ok


def main():
    print("=" * 72)
    print("csub 能力边界实测")
    print("=" * 72)
    print()

    print("[1] 前端语法支持（能否解析）")
    print("-" * 72)
    ok = 0
    for name, src in FRONTEND:
        codes = run_check(src)
        parse_fail = any(c in ("SYN", "LEX") for c in codes)
        errs = [c for c in codes if c.startswith("E")]
        if parse_fail:
            status = "不支持"
        elif errs:
            status = "通过（有诊断:" + ",".join(errs) + "）"
        else:
            status = "通过"
            ok += 1
        print("  %-20s %s" % (name, status))
    print("  → 可解析 %d/%d" % (ok, len(FRONTEND)))
    print()

    print("[2] 后端代码生成（能否编译成汇编）")
    print("-" * 72)
    ok = 0
    for name, src in BACKEND:
        good = run_codegen(src)
        if good:
            ok += 1
        print("  %-22s %s" % (name, "可编译" if good else "不支持"))
    print("  → 可编译 %d/%d" % (ok, len(BACKEND)))
    print()

    print("[3] 诊断规则统计")
    print("-" * 72)
    sem = open("src/semantic.py", encoding="utf-8").read()
    errs = sorted(set(re.findall(r'self\.err\("(E\d+)"', sem)),
                  key=lambda x: int(x[1:]))
    warns = sorted(set(re.findall(r'self\.warn\("(W\d+)"', sem)),
                   key=lambda x: int(x[1:]))
    print("  错误 %d 条: %s" % (len(errs), " ".join(errs)))
    print("  警告 %d 条: %s" % (len(warns), " ".join(warns)))
    print("  合计 %d 条" % (len(errs) + len(warns)))
    print()


if __name__ == "__main__":
    main()
