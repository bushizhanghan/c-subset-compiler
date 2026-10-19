"""扩充用例集：覆盖更多真实错误形态。

原则：每个用例都对应教材里学生实际会写错的代码，
不是为了刷数量。期望只写 error 级错误码。
"""

# (名称, 源码, 期望检出的 error 码集合)

EXTRA_CASES = [
    # ---- 合法（正常写法，不应报 error）----
    ("ok_multi_func", """
int sq(int x) { return x * x; }
int cube(int x) { return x * sq(x); }
int main() {
    int r = cube(3);
    return r;
}
""", set()),

    ("ok_nested_if", """
int main() {
    int a = 2;
    if (a > 1) {
        if (a < 5) { return 1; }
        else { return 2; }
    }
    return 0;
}
""", set()),

    ("ok_while_break", """
int main() {
    int i = 0;
    while (i < 10) {
        if (i == 5) { break; }
        i = i + 1;
    }
    return i;
}
""", set()),

    ("ok_compound", """
int main() {
    int a = 1;
    a += 2;
    a -= 1;
    return a;
}
""", set()),

    ("ok_shadow_inner", """
int main() {
    int a = 1;
    {
        int a2 = 2;
        a = a + a2;
    }
    return a;
}
""", set()),

    ("ok_call_chain", """
int f(int x) { return x + 1; }
int g(int x) { return f(f(x)); }
int main() { return g(0); }
""", set()),

    ("ok_empty_body", """
int noop() { return 0; }
int main() { noop(); return 0; }
""", set()),

    ("ok_comparison", """
int main() {
    int a = 3;
    if (a >= 3 && a != 0) { return a; }
    return 0;
}
""", set()),

    ("ok_return_value", """
int f() { return 42; }
int main() {
    int x = f();
    return x;
}
""", set()),

    ("ok_array_init", """
int main() {
    int arr[10];
    int i = 0;
    while (i < 10) {
        arr[i] = i;
        i = i + 1;
    }
    return arr[0];
}
""", set()),

    # ---- 应报错 ----
    ("err_undeclared_rhs", """
int main() {
    int a = 1;
    int b = a + zzz;
    return b;
}
""", {"E1"}),

    ("err_undeclared_call", """
int main() {
    foo();
    return 0;
}
""", {"E5"}),

    ("err_multi_redeclare", """
int main() {
    int a = 1;
    int a = 2;
    int a = 3;
    return a;
}
""", {"E3"}),

    ("err_uninit_cond", """
int main() {
    int flag;
    if (flag) { return 1; }
    return 0;
}
""", {"E2"}),

    ("err_uninit_in_call", """
int f(int x) { return x; }
int main() {
    int a;
    return f(a);
}
""", {"E2"}),

    ("err_argc_zero", """
int f(int a) { return a; }
int main() { return f(); }
""", {"E4"}),

    ("err_div_zero_var_init", """
int main() {
    int a = 10 / 0;
    return a;
}
""", {"E12"}),

    ("err_mod_zero", """
int main() {
    int a = 5 % 0;
    return a;
}
""", {"E12"}),

    ("err_oob_boundary", """
int main() {
    int arr[5];
    arr[5] = 1;
    return 0;
}
""", {"E13"}),

    ("err_oob_far", """
int main() {
    int arr[2];
    arr[100] = 1;
    return 0;
}
""", {"E13"}),

    ("err_break_in_if", """
int main() {
    if (1) { break; }
    return 0;
}
""", {"E11"}),

    ("err_continue_outside", """
int main() {
    continue;
    return 0;
}
""", {"E11"}),

    ("err_unknown_main", """
int just_a_func() { return 0; }
""", {"E10"}),

    ("err_syntax_missing_paren", """
int main() {
    if (1 { return 1; }
    return 0;
}
""", {"SYN"}),

    ("err_syntax_expr", """
int main() {
    int a = ;
    return a;
}
""", {"SYN"}),

    ("err_syntax_type", """
int main() {
    int a = 1;
    return a
}
""", {"SYN"}),

    ("err_lex_at", """
int main() {
    int a = 1;
    a = a $ 2;
    return a;
}
""", {"LEX"}),

    ("err_lex_backslash", """
int main() {
    int a = 1;
    a = a \\ 2;
    return a;
}
""", {"LEX"}),
]


# ---- 结构体成员检查（E15）----
# 这组用例的价值在于覆盖 typedef 指针的真实路径：
# typedef struct node {...} Node, *LinkList; 之后用 LinkList head;
# head->bad 必须能报出来。早期版本因为没展开 typedef 里的指针层，
# 这条路径完全失效，只在构造的简单例子里能过。

EXTRA_CASES += [
    ("err_struct_member_simple", """
struct Point { int x; int y; };
int main() {
    struct Point p;
    p.z = 1;
    return 0;
}
""", {"E15"}),

    ("err_struct_member_typedef_ptr", """
typedef struct node { int num; struct node *next; } Node, *LinkList;
int main() {
    LinkList head;
    head->nmae = 1;
    return 0;
}
""", {"E15"}),

    ("err_struct_member_typedef_value", """
typedef struct node { int num; } Node;
int main() {
    Node n;
    n.nim = 1;
    return 0;
}
""", {"E15"}),

    ("err_struct_member_arrow_chain", """
typedef struct inner { int v; } Inner;
typedef struct outer { Inner in; } Outer;
int main() {
    Outer o;
    o.in.w = 1;
    return 0;
}
""", {"E15"}),

    # 正确的成员访问不能被误报
    ("ok_struct_member", """
typedef struct node { int num; struct node *next; } Node, *LinkList;
int main() {
    LinkList p;
    p->num = 1;
    p->next->num = 2;
    return 0;
}
""", set()),
]
