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
""", set()),

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


# ---- parser 边界回归（防旧坑复发）----
EXTRA_CASES += [
    # peek() 与 peek(1) 返回同一个 token，这是踩过的坑：
    # 曾用 self.peek(1) 判断"标识符后是冒号"，结果和 peek() 一样
    # 永远指向下一个 token，导致 goto 标签识别失败。
    ("ok_goto_label", """
int main() {
    int x = 1;
  L: x = 2;
    goto L;
    return x;
}
""", set()),

    # 三目里的冒号不能被误判成 goto 标签
    ("ok_ternary_not_label", """
int main() {
    int a = 1;
    int b = 2;
    return a > b ? a : b;
}
""", set()),

    # 嵌套三目（右结合）
    ("ok_nested_ternary", """
int main() {
    int x = 2;
    int r = x == 1 ? 10 : x == 2 ? 20 : 30;
    return r;
}
""", set()),

    # 函数指针参数（曾因缺少支持而整份 numpy 文件卡住）
    ("ok_funcptr_param", """
void reg(void (*cb)(void *), void *arg) { cb(arg); }
int main() { return 0; }
""", set()),

    # 多维数组维度是常量表达式
    ("ok_array_expr_dim", """
int main() {
    int a[2 * 3];
    a[5] = 1;
    return a[5];
}
""", set()),

    ("err_array_expr_dim_oob", """
int main() {
    int a[2 * 3];
    a[6] = 1;
    return 0;
}
""", {"E13"}),

    # 相邻字符串字面量拼接（跨行）
    ("ok_string_concat", """
int main() {
    printf("first part "
           "second part\\n");
    return 0;
}
""", set()),
]


# ---- 健壮性回归（防崩溃复发）----
# 这些输入正常 C 文件不会出现，但一旦出现，早期版本是直接
# traceback 崩掉而不是给诊断。工具崩溃比报错更糟：
# 学生看到一堆 Python 栈就没法继续了。
EXTRA_CASES += [
    # 深嵌套括号：超过 200 层必须报可读的 ParseError，不能 RecursionError
    ("err_deep_paren", """
int main() {
    return ((((((((((((((((((((((((((((((((((((((((((((((
    (((((((((((((((((((((((((((((((((((((((((((((((((((
    1
    )))))))))))))))))))))))))))))))))))))))))))))))))))
    ))))))))))))))))))))))))))))))))))))))))))))))))))));
}
""", {"SYN"}),

    # 超大整数字面量：超 4300 位不能抛 ValueError
    # 注意必须写成单行——C 的数字字面量不能跨行（这点和字符串不同）
    ("ok_huge_int_literal",
     "int main() { return " + "9" * 5000 + "; }",
     set()),

    # 大整数旁边仍要能正常查错
    ("err_oob_with_huge_int", """
int main() {
    int a[3];
    a[5] = 1;
    return 999999999999999999999999999999999999999999999999;
}
""", {"E13"}),

    # scanf 漏 &：真实高频错误，必须报 E16
    ("err_scanf_missing_addr", """
int main() {
    int v;
    scanf("%d", v);
    return 0;
}
""", {"E16"}),

    # scanf 正确写法不报
    ("ok_scanf_with_addr", """
int main() {
    int v;
    scanf("%d", &v);
    return v;
}
""", set()),

    # scanf 传数组名不报 —— 数组名本身就退化成首地址。
    # 这条是 stu_11 暴露的误报：E16 早期只要看到裸标识符就报，
    # 把 `char a[21]; scanf("%20s", a);` 这种标准写法判成 ERROR，
    # 而 gcc 零诊断。误报会让学生去改正确的代码，比漏报伤害大。
    ("ok_scanf_array_arg", """
int main() {
    char a[21];
    scanf("%20s", a);
    return a[0];
}
""", set()),

    # scanf 传指针变量也不报 —— 指针的值就是地址
    ("ok_scanf_ptr_arg", """
int main() {
    char buf[10];
    char *p = buf;
    scanf("%9s", p);
    return buf[0];
}
""", set()),

    # scanf 传数组元素地址不报
    ("ok_scanf_elem_addr", """
int main() {
    int a[5];
    scanf("%d", &a[0]);
    return a[0];
}
""", set()),

    # 非 void 函数 if 链缺兜底 return
    ("warn_missing_return", """
int sign(int x) {
    if (x > 0) { return 1; }
    else if (x < 0) { return -1; }
}
int main() { return sign(0); }
""", set()),
]


# ---- sizeof 语义（真实类型尺寸，不再是硬编码 4）----
# 这些用例的正确性由 tools/ir_verify.py 的执行对比兜底，
# 这里只保证不报错、不误报。
EXTRA_CASES += [
    ("ok_sizeof_type", """
int main() {
    int a = sizeof(int);
    int b = sizeof(char);
    int c = sizeof(double);
    return a + b + c;
}
""", set()),

    ("ok_sizeof_array_idiom", """
int main() {
    int arr[10];
    /* sizeof(arr)/sizeof(arr[0]) 是取数组长度的经典写法，
       必须算成 10，且不能因为两个 sizeof 相除就误报 W3 */
    int n = sizeof(arr) / sizeof(arr[0]);
    return n;
}
""", set()),

    ("ok_sizeof_ptr", """
int main() {
    int *p;
    /* 指针尺寸固定 8 字节（64 位模型） */
    return sizeof(p);
}
""", set()),

    ("ok_sizeof_struct", """
struct Point { int x; int y; };
int main() {
    /* 结构体尺寸 = 成员之和，不考虑对齐填充（教学简化） */
    return sizeof(struct Point);
}
""", set()),
]


# ---- W20 块作用域逃逸 / W21 读未初始化数组 ----
# 每条规则都必须配反向用例：只测"该报的能报"等于没测，
# E16 那次误报（scanf 传数组名被判成漏写 &）就是只有正向用例
# 才一路溜过全绿的。
EXTRA_CASES += [
    # W20 正向：内层变量的地址被带到块外
    ("warn_block_scope_escape", """
int main() {
    int *p = 0;
    {
        int inner = 7;
        p = &inner;
    }
    printf("%d", *p);
    return 0;
}
""", {"W20"}),

    # W20 反向：同作用域取地址，指针和对象同生命周期
    ("ok_addr_same_scope", """
int main() {
    int x = 1;
    int *p = &x;
    return *p;
}
""", set()),

    # W20 反向：外层变量的地址传给内层指针，安全
    ("ok_outer_addr_to_inner", """
int main() {
    int x = 1;
    {
        int *p = &x;
        printf("%d", *p);
    }
    return 0;
}
""", set()),

    # W21 正向：数组一个元素都没写过就读
    ("warn_uninit_array_read", """
int main() {
    int a[4];
    printf("%d", a[2]);
    return 0;
}
""", {"W21"}),

    # W21 反向：循环赋值后读，安全
    ("ok_array_written_in_loop", """
int main() {
    int a[4];
    int i;
    for (i = 0; i < 4; i = i + 1) { a[i] = i; }
    return a[2];
}
""", set()),

    # W21 反向：初始化列表后读，安全
    ("ok_array_init_list", """
int main() {
    int a[3] = {1, 2, 3};
    return a[1];
}
""", set()),

    # W21 反向：全局数组零初始化，安全
    ("ok_global_array_read", """
int a[4];
int main() { return a[2]; }
""", set()),

    # W21 反向：scanf 写入数组后读，安全（stu_11 撞出的误报）
    ("ok_scanf_fills_array", """
int main() {
    char s[10];
    scanf("%9s", s);
    return s[0];
}
""", set()),
]


# ---- W14 常量变量参与的溢出 ----
# W14 早期只查纯字面量表达式（2000000000 + 2000000000），
# 变量参与的一律放过。err_38 就是变量形态：
#     int big = 2147483647;
#     int over = big + 1;      // 溢出
# 补一个受限的常量表接住它。注意常量表的失效条件必须测全：
# 函数调用、重新赋值成非常量——这两处漏一个就会假阳性。
EXTRA_CASES += [
    ("warn_overflow_const_var", """
int main() {
    int big = 2147483647;
    int over = big + 1;
    return over;
}
""", {"W14"}),

    ("warn_overflow_mul_chain", """
int main() {
    int a = 46341;
    int b = a * 46341;
    return b;
}
""", {"W14"}),

    # 反向：long 变量不按 int 范围判断
    ("ok_overflow_long_var", """
int main() {
    long a = 2147483647;
    long b = a + 1;
    return (int)b;
}
""", set()),

    # 反向：unsigned 不适用（回绕是定义好的行为）
    ("ok_overflow_unsigned_var", """
int main() {
    unsigned a = 2147483647u;
    unsigned b = a + 1u;
    return (int)b;
}
""", set()),

    # 反向：值来自输入，不是常量
    ("ok_overflow_from_input", """
int main() {
    int a;
    scanf("%d", &a);
    int b = a + 1;
    return b;
}
""", set()),

    # 反向：函数调用后常量表作废（f 可能改 a）
    ("ok_overflow_after_call", """
int modify(int *p) { *p = 0; return 0; }
int main() {
    int a = 2147483647;
    modify(&a);
    int b = a + 1;
    return b;
}
""", set()),

    # 反向：重新赋值成非常量后，旧常量值必须失效
    ("ok_overflow_reassigned", """
int main() {
    int a = 2147483647;
    int t;
    scanf("%d", &t);
    a = t;
    int b = a + 1;
    return b;
}
""", set()),
]


# ---- W22 复合赋值里又写同一个变量 ----
# stu_02 是 11 份样本里最早发现却一直没修的漏报：
#     rev += rev + t * d;      // 学生本意 rev = rev + t*d
# `x += expr` 展开是 x = x + expr，expr 里再出现 x 就变成 2x + ...，
# 结果翻倍。等了两轮样本确认不是高频模式（只有 stu_02 撞到），
# 但它是唯一"确定根因却没修"的一条。
EXTRA_CASES += [
    ("warn_compound_self_ref", """
int main() {
    int rev = 0, t = 1, d = 1;
    rev += rev + t * d;
    return rev;
}
""", {"W22"}),

    ("warn_compound_self_x", """
int main() { int x = 1; x += x; return x; }
""", {"W22"}),

    # 反向：正常复合赋值
    ("ok_compound_normal", """
int main() { int x = 1; x += 1; x += 2; return x; }
""", set()),

    # 反向：右侧是别的变量
    ("ok_compound_other_var", """
int main() { int x = 1, y = 2; x += y; return x; }
""", set()),

    # 反向：循环里累加（s = s + i 不是 s += s）
    ("ok_compound_in_loop", """
int main() {
    int i = 0, s = 0;
    for (i = 0; i < 5; i = i + 1) { s = s + i; }
    return s;
}
""", set()),

    # 反向：位运算和位移的自引用是合法的
    ("ok_compound_bitwise", """
int main() { int x = 3; x |= x; x <<= x; return x; }
""", set()),

    # 反向：指针运算
    ("ok_compound_pointer", """
int main() {
    int a[3];
    int *p = a;
    int *q = a;
    p += q - p;
    return *p;
}
""", set()),

    # 反向：x += f(x) 语义明确，不是笔误
    ("ok_compound_with_call", """
int inc(int v) { return v + 1; }
int main() { int x = 1; x += inc(x); return x; }
""", set()),
]


# ---- W23 格式串与参数类型不匹配 ----
# 来源：老师在 stu_12（质因数分解）里标了两次这类错误。
# gcc 的 -Wformat 默认不开，所以两个编译器都放行。
#
# 关键实现细节：long/long long 的 base 被 parser 归一化成 int，
# 必须靠 Type/VT 的 long_flag 还原。不传这个信息的话，
# `long long n; scanf("%d",&n)` 会被判成"类型匹配"而漏报。
EXTRA_CASES += [
    # 应报
    ("warn_fmt_d_longlong", """
int main() {
    long long n;
    scanf("%d", &n);
    return 0;
}
""", {"W23"}),

    ("warn_fmt_printf_d_longlong", """
int main() {
    long long n = 1;
    printf("%d", n);
    return 0;
}
""", {"W23"}),

    ("warn_fmt_lld_int", """
int main() {
    int n;
    scanf("%lld", &n);
    return 0;
}
""", {"W23"}),

    ("warn_fmt_f_int", """
int main() {
    int x = 1;
    printf("%f", x);
    return 0;
}
""", {"W23"}),

    ("warn_fmt_d_double", """
int main() {
    double d = 1.5;
    printf("%d", d);
    return 0;
}
""", {"W23"}),

    # 反向（不该报）
    ("ok_fmt_d_int", """
int main() {
    int n;
    scanf("%d", &n);
    return n;
}
""", set()),

    ("ok_fmt_lld_longlong", """
int main() {
    long long n;
    scanf("%lld", &n);
    return (int)n;
}
""", set()),

    ("ok_fmt_s_array", """
int main() {
    char s[10];
    scanf("%9s", s);
    return s[0];
}
""", set()),

    ("ok_fmt_f_float", """
int main() {
    float f = 1.5f;
    printf("%f", f);
    return 0;
}
""", set()),

    ("ok_fmt_d_char", """
int main() {
    char c = 65;
    printf("%d", c);
    return 0;
}
""", set()),

    ("ok_fmt_multi", """
int main() {
    int a = 1;
    float b = 2.0f;
    printf("%d %f", a, b);
    return 0;
}
""", set()),

    ("ok_fmt_percent_escape", """
int main() {
    printf("100%%\\n");
    return 0;
}
""", set()),

    ("ok_fmt_width", """
int main() {
    int a = 1;
    printf("%5d", a);
    return 0;
}
""", set()),

    # 未知 typedef 不报（npy_int / DWORD 这类真实宽度看不到）
    ("ok_fmt_unknown_typedef", """
typedef int npy_int;
int main() {
    npy_int v = 1;
    printf("%d", v);
    return 0;
}
""", set()),
]


# ---- W24 无效果的表达式语句 ----
# 来源：老师在 stu_13 里指出 `for(q; q<=n/2; q++)` 的初始化
# "写成单独的 q 是无效果的表达式"。gcc 用 -Wunused-value 报，
# 默认不开，所以两个编译器都放行。
EXTRA_CASES += [
    # 应报
    ("warn_no_effect_for_init", """
int main() {
    int n = 10;
    int q = 1;
    int k = 1;
    for (q; q <= n / 2; q++) { k++; }
    return k;
}
""", {"W24"}),

    ("warn_no_effect_ident", """
int main() {
    int a = 1, b = 2;
    a;
    return b;
}
""", {"W24"}),

    ("warn_no_effect_const", """
int main() { 42; return 0; }
""", {"W24"}),

    ("warn_no_effect_arith", """
int main() {
    int a = 1, b = 2;
    a + b;
    return 0;
}
""", {"W24"}),

    # 反向（不该报）
    ("ok_effect_assign", """
int main() { int a = 1; a = 2; return a; }
""", set()),

    ("ok_effect_call", """
int main() { printf("hi"); return 0; }
""", set()),

    ("ok_effect_incr", """
int main() { int a = 1; a++; return a; }
""", set()),

    ("ok_effect_deref", """
int main() {
    int a = 1;
    int *p = &a;
    *p;
    return 0;
}
""", set()),

    ("ok_effect_normal_for", """
int main() {
    int i;
    int s = 0;
    for (i = 0; i < 5; i = i + 1) { s = s + i; }
    return s;
}
""", set()),

    # 空定义宏展开成空语句，不该报
    ("ok_empty_macro", """
#define EMPTY_MACRO
int main() { EMPTY_MACRO; return 0; }
""", set()),
]


# ---- W25 调标准库函数但漏 include ----
# 来源：老师在 stu_16 里标了这条（abs 需要 stdlib.h）。
# gcc 报 "implicit declaration of function 'abs'"，是 error，
# 但 csub 早期把 abs 当"已知标准库函数"直接放行，从没检查头文件。
#
# 判据保守：只看 FUNC_HEADER 里明确归属的函数；
# 有非标准头（可能是预编译头/自建总头）就不报。
EXTRA_CASES += [
    # 应报
    ("warn_missing_include_abs", """
bool f(int *a, int n, int k) {
    int i, j;
    for (i = 0; i < n; i = i + 1) {
        for (j = i + 1; j < n; j = j + 1) {
            if (a[i] == a[j] && abs(i - j) <= k) { return true; }
        }
    }
    return false;
}
""", {"W25"}),

    ("warn_missing_include_with_other", """
#include <stdio.h>
int main() {
    printf("hi");
    return abs(-1);
}
""", {"W25"}),

    ("warn_missing_string_h", """
#include <stdio.h>
int main() {
    printf("%d", strlen("ab"));
    return 0;
}
""", {"W25"}),

    # 反向（不该报）
    ("ok_include_stdlib", """
#include <stdlib.h>
int main() { return abs(-1); }
""", set()),

    ("ok_include_stdio", """
#include <stdio.h>
int main() { printf("hi"); return 0; }
""", set()),

    ("ok_include_string", """
#include <string.h>
int main() { return strlen("ab"); }
""", set()),

    ("ok_include_multi", """
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main() {
    printf("%d", strlen("ab") + abs(-1));
    return 0;
}
""", set()),

    # 有自建头，可能是预编译头，不报
    ("ok_include_custom_header", """
#include "mylib.h"
int main() { return abs(-1); }
""", set()),
]


# ---- W26 标准库函数的指针参数收到非指针值 ----
# 来源：老师在 stu_01 里把 `strcmp(*b[j], *b[j+1])` 标为★致命。
# `*b[j]` 是 char（首字符），strcmp 要 char*。
# gcc 报 "passing argument 1 of 'strcmp' makes pointer from integer"。
#
# 和 W23 是同一类逻辑（函数要地址、给了值），但 W23 只覆盖
# printf/scanf 家族的格式串，覆盖不到 strcmp/strlen/free 这些。
EXTRA_CASES += [
    # 应报
    ("warn_ptr_arg_strcmp", """
#include <string.h>
int main() {
    char *b[3];
    strcmp(*b[0], *b[1]);
    return 0;
}
""", {"W26"}),

    ("warn_ptr_arg_strlen", """
#include <string.h>
int main() {
    char *s;
    return strlen(*s);
}
""", {"W26"}),

    ("warn_ptr_arg_free", """
#include <stdlib.h>
int main() {
    char *p;
    free(*p);
    return 0;
}
""", {"W26"}),

    # 反向（不该报）
    ("ok_ptr_arg_strcmp", """
#include <string.h>
int main() {
    char *a = "x";
    char *b = "y";
    strcmp(a, b);
    return 0;
}
""", set()),

    ("ok_ptr_arg_strlen_array", """
#include <string.h>
int main() {
    char s[10];
    return strlen(s);
}
""", set()),

    ("ok_ptr_arg_strlen_literal", """
#include <string.h>
int main() { return strlen("abc"); }
""", set()),

    ("ok_ptr_arg_free_null", """
#include <stdlib.h>
int main() { free(0); return 0; }
""", set()),

    ("ok_ptr_arg_strlen_addr", """
#include <string.h>
int main() {
    char c = 65;
    return strlen(&c);
}
""", set()),
]


# ---- W27 return 类型与函数声明不匹配 ----
# 来源：老师在 stu_17（十六进制转换）里标为★致命的三重错误之一：
#     char* toHex(int num) { ... return *bin; }
# *bin 是 char（首字符），函数要 char*。
# gcc 对这条在部分配置下报 error，默认不开。
EXTRA_CASES += [
    # 应报
    ("warn_return_ptr_mismatch", """
char* f() {
    char bin[64];
    return *bin;
}
""", {"W27"}),

    ("warn_return_value_from_ptr_func", """
int f(char *p) {
    return p;
}
""", {"W27"}),

    # 反向（不该报）
    ("ok_return_ptr", """
char* f(char *p) { return p; }
""", set()),

    ("ok_return_int", """
int f() { return 1; }
""", set()),

    ("ok_return_void", """
void f() { return; }
""", set()),

    ("ok_return_array_from_ptr_func", """
char* f() {
    static char bin[64];
    return bin;
}
""", set()),

    ("ok_return_addr_from_ptr_func", """
int* f(int *p) { return &p[0]; }
""", set()),
]


# ---- W28 参数声明了但从未使用 ----
# 对应 gcc 的 -Wunused-parameter（需 -Wextra 才开）。
# 真实来源：stu_20 的 LeetCode 签名带 returnSize / returnColumnSizes
# 两个参数，学生完全没用。gcc 报两条 warning，csub 之前没有这条。
EXTRA_CASES += [
    # 应报
    ("warn_unused_param", """
int f(int a, int b) { return a; }
int main() { return f(1, 2); }
""", {"W28"}),

    ("warn_unused_param_two", """
int g(int x, int y, int z) { return x + y; }
int main() { return g(1, 2, 3); }
""", {"W28"}),

    # 反向（不该报）
    ("ok_param_all_used", """
int f(int a, int b) { return a + b; }
int main() { return f(1, 2); }
""", set()),

    # 单参数不用：可能是回调签名要求的
    ("ok_param_single_unused", """
int cb(int x) { return 0; }
int main() { return cb(1); }
""", set()),

    # main 的 argv/argc 签名固定
    ("ok_param_main_args", """
int main(int argc, char **argv) { return 0; }
""", set()),

    # _ 前缀是约定俗成的"我知道不用"
    ("ok_param_underscore", """
int f(int a, int _b) { return a; }
int main() { return f(1, 2); }
""", set()),

    # 名字里含 unused 也是约定
    ("ok_param_unused_name", """
int f(int a, int unused_x) { return a; }
int main() { return f(1, 2); }
""", set()),

    # 可变参数的 ... 不是参数名
    ("ok_param_varargs", """
int f(int a, ...) { return a; }
int main() { return f(1); }
""", set()),
]


# ---- W29 malloc 分配不足却用变量下标 ----
# 来源：老师在 stu_22 里标为★致命：
#     int *ans = (int*)malloc(sizeof(int));   // 只分了 4 字节
#     for (i = 0; i < numsSize; i++) { ans[i] = count; }
# gcc 不报这类（它只查类型不查大小）。
#
# 判据很窄：必须是 malloc/calloc 来的、字节数是常量、
# 算出来的元素个数 <= 1、且用变量下标访问。
EXTRA_CASES += [
    # 应报
    ("warn_malloc_one_elem", """
#include <stdlib.h>
int main() {
    int n = 5;
    int *p = (int *)malloc(sizeof(int));
    int i;
    for (i = 0; i < n; i = i + 1) { p[i] = 0; }
    return 0;
}
""", {"W29"}),

    ("warn_malloc_one_elem_assign", """
#include <stdlib.h>
int main() {
    int n = 3;
    int *p;
    p = (int *)malloc(sizeof(int));
    int i;
    for (i = 0; i < n; i = i + 1) { p[i] = 1; }
    return 0;
}
""", {"W29"}),

    # 反向（不该报）
    ("ok_malloc_sizeof_n", """
#include <stdlib.h>
int main() {
    int n = 5;
    int *p = (int *)malloc(sizeof(int) * n);
    int i;
    for (i = 0; i < n; i = i + 1) { p[i] = 0; }
    return 0;
}
""", set()),

    ("ok_malloc_sizeof_100", """
#include <stdlib.h>
int main() {
    int *p = (int *)malloc(sizeof(int) * 100);
    int i;
    for (i = 0; i < 100; i = i + 1) { p[i] = 0; }
    return 0;
}
""", set()),

    ("ok_malloc_const_index", """
#include <stdlib.h>
int main() {
    int *p = (int *)malloc(4);
    p[0] = 1;
    return p[0];
}
""", set()),

    ("ok_plain_array", """
int main() {
    int a[10];
    int i;
    for (i = 0; i < 10; i = i + 1) { a[i] = 0; }
    return 0;
}
""", set()),
]


# ---- W30 外部依赖检测（未知函数过多时收敛 E5）----
# 来源：stu_24（400 行 EasyX 贪吃蛇）。graphics.h / conio.h
# 本机没有，预处理忽略后 EasyX 函数全部变成"未声明"，
# 一次冒出 97 条 E5 —— 全是噪音，学生会直接扔掉工具。
#
# 规则：未声明函数（去重）>= 5 个时，E5 收敛成一条 W30 提示。
EXTRA_CASES += [
    # 应报 W30（未知函数 >= 5 个）
    ("warn_ext_deps_graphics", """
int main() {
    initgraph(640, 480);
    setfillcolor(RGB(255, 0, 0));
    setlinecolor(RGB(255, 0, 0));
    fillcircle(100, 100, 50);
    cleardevice();
    return 0;
}
""", {"W30"}),

    # 不该报：1-2 个未知函数是真错误（拼错/忘声明）
    ("ok_few_unknown_funcs", """
int main() {
    int x = getuserinput();
    return x;
}
""", {"E5"}),

    # 不该报：已声明的函数
    ("ok_declared_funcs", """
int add(int a, int b) { return a + b; }
int main() { return add(1, 2); }
""", set()),
]


# ---- 结构体里的函数指针成员 ----
# 来源：GitHub c_ds_algo 的 array_list（"面向对象 C"写法）。
# 修复过程踩了四个坑：off-by-one 两次、回退后掉进旧分支、
# depth 起步错误 —— 每个都是静默失效，靠最小化矩阵测试揪出。
EXTRA_CASES += [
    # 函数指针成员（int 返回）
    ("ok_fnptr_member_int", """
typedef struct list {
    int (*f)(int a);
} list;
int main() { return 0; }
""", set()),

    # 函数指针成员（void* 返回）
    ("ok_fnptr_member_voidptr", """
typedef struct list {
    void* (*g)(int a);
} list;
int main() { return 0; }
""", set()),

    # 函数指针成员（void** 返回）+ 普通成员混合
    ("ok_fnptr_member_mixed", """
typedef struct list {
    void** a;
    int (*f)(int);
    int n;
} list;
int main() { return 0; }
""", set()),

    # 反向：函数指针成员不该触发 E18/W11 等
    ("ok_fnptr_no_false_positives", """
typedef struct obj {
    int value;
    int (*get)(struct obj* self);
    void (*set)(struct obj* self, int v);
} obj;
int my_get(struct obj* self) { return self->value; }
int main() {
    obj o;
    o.value = 5;
    o.get = my_get;
    return o.get(&o);
}
""", set()),
]


# ---- 位字段 + 匿名结构体/联合体 + sizeof 惯用法 ----
# 来源：stu_37（构造样本，覆盖三类语法）。
# 位字段、匿名嵌套、sizeof 计数组长度都是真实代码常见写法，
# 早期全部报 SYN 或误报。
EXTRA_CASES += [
    # 位字段
    ("ok_bitfield", """
struct Flags {
    unsigned int visible : 1;
    unsigned int priority : 3;
};
int main(void) {
    struct Flags f;
    f.visible = 1;
    f.priority = 5;
    return f.priority;
}
""", set()),

    # 匿名 union + 匿名 struct（C11 成员提升）
    ("ok_anon_union", """
struct Shape {
    int kind;
    union {
        struct { int x, y, r; };
        struct { int x1, y1, x2, y2; };
    };
};
int main(void) {
    struct Shape s;
    s.kind = 0;
    s.x = 1;
    s.y = 2;
    s.r = 3;
    return s.r;
}
""", set()),

    # 结构体内 enum 判别式
    ("ok_enum_in_struct", """
struct Node {
    enum { LEAF, BRANCH } kind;
    int value;
};
int main(void) {
    struct Node n;
    n.kind = LEAF;
    n.value = 42;
    return n.value;
}
""", set()),

    # sizeof 算数组长度（ARRAY_SIZE 惯用法）
    ("ok_sizeof_array_len", """
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
int main(void) {
    int arr[10];
    return (int)ARRAY_SIZE(arr);
}
""", set()),

    # 反向：自引用结构体指针成员不能被当成 int
    ("ok_selfref_struct", """
typedef struct listint_s {
    int n;
    struct listint_s *next;
} listint_t;
int main(void) {
    listint_t a, b;
    a.next = &b;
    b.next = 0;
    return a.next->n;
}
""", set()),
]


# ---- 头文件保护 + static inline + 输出参数 ----
# 来源：stu_39（多文件项目风格合成）。
# #ifndef X / #define X / #endif 是头文件保护三件套，
# 早期被当成"复杂条件编译"整块丢弃 —— 所有带头文件保护的代码消失。
EXTRA_CASES += [
    # 头文件保护里的内容必须保留
    ("ok_header_guard", """
#ifndef MYLIB_H
#define MYLIB_H
static inline int dbl(int x) { return x * 2; }
#endif
int main(void) { return dbl(21); }
""", set()),

    # ifdef 已定义的分支保留
    ("ok_ifdef_defined", """
#define FEATURE_A
#ifdef FEATURE_A
static int val = 10;
#endif
int main(void) { return val; }
""", set()),

    # 输出参数：&v 传给本文件函数后 v 算已初始化
    ("ok_out_param_defined_fn", """
static int read_it(int *out) { *out = 42; return 0; }
int main(void) {
    int v;
    read_it(&v);
    return v;
}
""", set()),

    # restrict + _Bool 组合
    ("ok_restrict_bool", """
void copy_n(char *restrict d, const char *restrict s, int n) {
    int i;
    for (i = 0; i < n; i = i + 1) { d[i] = s[i]; }
}
int main(void) {
    char buf[8];
    _Bool ok = 1;
    copy_n(buf, "abc", 4);
    return ok ? buf[0] : 0;
}
""", set()),
]
