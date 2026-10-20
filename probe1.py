# -*- coding: utf-8 -*-
"""一次性探针：观察 sizeof 在 IR / 语义层的当前行为。"""
import sys
sys.path.insert(0, "src")

from parser import parse
from ir import ir_to_text
from semantic import analyze

SRCS = [
    ("long", "int main(){ return sizeof(long); }"),
    ("short", "int main(){ return sizeof(short); }"),
    ("intptr", "int main(){ int *p = 0; return sizeof(int *) + sizeof(p); }"),
    ("arr", "int main(){ int a[7]; return sizeof(a); }"),
    ("arr2d", "int main(){ int a[2][3]; return sizeof(a); }"),
    ("arr0", "int main(){ return sizeof(a[0]); }"),
    ("strlit", 'int main(){ return sizeof("abc"); }'),
    ("sizet", "int main(){ return sizeof(size_t); }"),
    ("struct", "struct P { int x; int y; };\n"
               "int main(){ struct P p; return sizeof(struct P) + sizeof(p); }"),
    ("idiom", 'int main(){ static const char *paths[] = {"..", "..\\\\.."};\n'
              ' int i; int n = 0;\n'
              ' for (i = 0; i < (sizeof(paths)/sizeof(paths[0])); ++i) { n = n + 1; }\n'
              ' return n; }'),
    ("charlit", "int main(){ return sizeof('a'); }"),
    ("member", "struct P { int x; int y; };\n"
               "int main(){ struct P p; return sizeof(p.x); }"),
    ("uninit", "int main(){ int a; return sizeof(a); }"),
]

for name, s in SRCS:
    print("=" * 60)
    print(f"[{name}] {s!r}")
    try:
        prog = parse(s)
    except Exception as e:
        print(f"  PARSE EXC {type(e).__name__}: {e}")
        continue
    try:
        out = ir_to_text(prog)
        tail = [l for l in out.splitlines() if l.strip()
                and not l.startswith(";")]
        print("  IR:", tail)
    except Exception as e:
        print(f"  IR EXC {type(e).__name__}: {e}")
    try:
        diags = analyze(prog)
        print("  DIAG:", [(d.level, d.code, d.msg) for d in diags])
    except Exception as e:
        print(f"  SEM EXC {type(e).__name__}: {e}")
