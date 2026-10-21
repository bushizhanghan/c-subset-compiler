#!/usr/bin/env python3
"""变异测试：拿真实作业代码做基准，注入错误看诊断器能不能抓出来。

这比手写用例集有说服力得多，原因有三条：
1. 基准是真实代码，不是我编的例子；
2. 注入的错误是"学生真会犯的"，且能验证误报——基准本身必须零误报，
   否则说明工具在真实代码上不可用；
3. 除了"查得出"，还量"指得准"——诊断行号必须落在注入处附近，
   否则学生拿着行号去翻源码会翻到无关的地方。

流程：
  基准文件 --(零误报检查)--> 通过
  基准文件 --(注入 1 个错误)--> 必须报出对应的码，且行号落在注入行 ±2 行内

基准与用例分布在两个文件里（都在 examples/ 下，都不是我临时编的玩具）：
  examples/hw_josephus.c   数据结构作业：循环链表 / typedef 指针 / 结构体
  examples/mutation_bank.c 算法作业：递归 / 数组 / 浮点 / switch
两个文件覆盖的语法面不同，单靠一个文件覆盖不到全部错误类型
（比如 switch 的合法 break、float 返回值、结构体值传递只在 bank 里有）。

指标分三个层次，不要只看第一个：
  1. 主用例表检出率：故意只放"确认能查到"的用例，用来守住已有能力；
  2. 行号准确率：检出的诊断是不是指到了注入行附近；
  3. 全量口径：把 KNOWN_GAPS 里的已知漏报也算进去，这才是工具的真实检出率。
     （只报 1 而不报 3，等于把缺陷藏起来。）

用法:
  python tools/mutation_test.py                     跑全部基准（默认）
  python tools/mutation_test.py examples/hw_josephus.c  只跑指定基准
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from preprocess import preprocess
from lexer import tokenize, LexError
from parser import parse, ParseError
from semantic import analyze

# 诊断行号允许的偏差（行）。超过这个值算"指向了错误的地方"。
LINE_TOLERANCE = 2

# 词法/语法阶段是"一报即停"，拿到的是异常而不是 Diag 列表，
# 这里用统一的伪码表示，好让变异用例的期望值写法保持一致。
LEX_CODE = "LEX"
SYN_CODE = "SYN"


def check_text(src: str):
    """对一段源码跑完整前端，返回 (全部诊断码集合, 全诊断列表)。

    诊断统一成 (level, code, line, col, msg) 四元组，这样词法/语法
    阶段的致命错误和语义阶段的 Diag 能用同一套逻辑比对——
    早期版本对前两者返回空列表，导致 LEX/SYN 变异永远算不出行号。

    注意要包含 warning：像"条件里写了赋值"是 warning 级别，
    只比对 error 会把它当成漏报。

    行号一定要过 preprocess 的 to_orig_line 映射：基准文件里有
    #include / #define，预处理会把这些行删掉，不映射的话行号整体前移，
    "行号准确性"这个指标就全是假的。
    """
    pr = preprocess(src)
    try:
        tokenize(pr.text)
    except LexError as e:
        return {LEX_CODE}, [("error", LEX_CODE,
                             pr.to_orig_line(e.line), e.col, e.msg)]
    try:
        prog = parse(pr.text)
    except ParseError as e:
        return {SYN_CODE}, [("error", SYN_CODE,
                             pr.to_orig_line(e.line), e.col, str(e))]
    # parser 现在带错误恢复：语法错误不抛异常，收集在 prog.errors 里。
    # 不检查这个，所有 SYN 类变异都会变成"漏报"（实测 6 条）。
    if getattr(prog, "errors", None):
        e = prog.errors[0]
        return {SYN_CODE}, [("error", SYN_CODE,
                             pr.to_orig_line(e.line), e.col, str(e))]
    diags = analyze(prog)
    return ({d.code for d in diags},
            [(d.level, d.code, pr.to_orig_line(d.line), d.col, d.msg)
             for d in diags])


def check_errors(src: str):
    """只要 error 级别（用于基准零误报检查）。

    基准必须零 error 才算合格：warning 是"提示性建议"，真实代码里
    整数除法、float 截断这类警告是合理的（README 里 lab_optime.c
    就是 0 error + 2 条有效 warning），不能因为基准带 warning 就判它不合格。
    """
    codes, diags = check_text(src)
    if codes & {LEX_CODE, SYN_CODE}:
        return codes, diags
    return {c for _lvl, c, _ln, _col, _msg in diags if _lvl == "error"}, diags


# ---------------------------------------------------------------------------
# 变异定义
# ---------------------------------------------------------------------------
# 每个变异是 6 元组：
#   (分类, 名称, 查找的原始片段, 替换成, 期望报出的码集合, 行号锚点)
#
# 约定：
#   * 片段必须在基准文件里**唯一出现**。出现次数不为 1 就报 SKIP，
#     绝不静默改到别的地方——那是变异测试最容易自欺的地方。
#   * 行号锚点是被注入那段代码里"应该被指到"的特征串；None 表示
#     就用被替换片段自身的起始行。锚点存在的理由：有些变异把错误
#     放在了新增的多行里（如先声明数组再越界访问），片段起点不是
#     错误真正所在的位置。
#
# 覆盖的错误类型（对应 README 的检测规则表）：
#   E1 未声明变量 / E2 未初始化 / E3 重复声明 / E4 用户函数参数不符
#   E5 未声明函数 / E11 break|continue 位置错 / E12 除零
#   E13 数组常量越界 / E14 标准库函数参数不符 / E15 结构体成员拼错
#   W3 整数除法精度 / W4 float 赋 int 截断 / W5 条件里写赋值
#   LEX 词法错误 / SYN 语法错误

MUTATIONS_JOSEPHUS = [
    # ---- E1 使用未声明变量 ----
    ("E1", "删掉变量声明 int v",
     "    int v;\n",
     "", {"E1"}, None),
    ("E1", "变量名拼错 prompt->promt",
     'printf("%s", prompt);',
     'printf("%s", promt);', {"E1"}, None),
    ("E1", "printf 里用拼错的名字 ou",
     '    printf("\\n（共 %d 人出列，剩余 %d 人）\\n", out, X);',
     '    printf("\\n（共 %d 人出列，剩余 %d 人）\\n", ou, X);', {"E1"}, None),
    ("E1", "赋值目标拼错 head->hdead",
     "        head = initList();",
     "        hdead = initList();", {"E1"}, None),
    ("E1", "结构体指针 del 拼错 dl",
     "        cur = del->next;",
     "        cur = dl->next;", {"E1"}, None),

    # ---- E3 重复声明 ----
    ("E3", "重复声明局部变量 i",
     "    int i, n;\n",
     "    int i, n;\n    int i;\n", {"E3"}, "    int i;\n"),
    ("E3", "重复声明变量 v",
     "    int v;\n",
     "    int v;\n    int v;\n", {"E3"}, "    int v;\n    int v;\n"),

    # ---- E2 使用未初始化变量（含指针） ----
    ("E2", "count 去掉初始化",
     "    int i, count = 0;\n",
     "    int i, count;\n", {"E2"}, "        count++;"),
    ("E2", "指针 rear 未初始化就 -> 访问",
     "    Node *rear = head;  // rear 始终指向最后一个结点\n",
     "    Node *rear;  // 忘了指向头结点\n", {"E2"}, "        p->next = rear->next;"),
    ("E2", "指针 p 未初始化就参与比较",
     "    Node *p = head->next, *q;\n",
     "    Node *p, *q;\n", {"E2"}, "    while (p != head) {\n        q = p->next;"),
    ("E2", "int v 未赋值就参加 printf",
     "    int v;\n    printf(\"%s\", prompt);",
     "    int v;\n    printf(\"%s %d\", prompt, v);",
     {"E2"}, "    printf(\"%s %d\", prompt, v);"),

    # ---- E4 用户函数参数个数不符 ----
    ("E4", "josephus 少传一个参数",
     "josephus(head, S, M, X);",
     "josephus(head, S, M);", {"E4"}, None),
    ("E4", "printList 多传一个参数",
     "printList(head);",
     "printList(head, n);", {"E4"}, None),
    ("E4", "inputInt 少传两个参数",
     '    n = inputInt("请输入人数 N（0 < N < 50）：", 1, MAX_N - 1);',
     '    n = inputInt("请输入人数 N（0 < N < 50）：", 1);', {"E4"}, None),

    # ---- E5 调用未声明的函数 ----
    ("E5", "printList 拼错成 printListt",
     "printList(head);",
     "printListt(head);", {"E5"}, None),
    ("E5", "initList 拼错成 initlist",
     "        head = initList();",
     "        head = initlist();", {"E5"}, None),

    # ---- E14 标准库函数参数个数不符 ----
    ("E14", "fclose 少传参数",
     "    free(del);",
     "    free(del);\n    fclose();", {"E14"}, "    fclose();"),

    # ---- E12 除零 ----
    ("E12", "return v / 0 常量除零",
     "    return v;\n}",
     "    return v / 0;\n}", {"E12"}, None),
    ("E12", "count % 0 常量模零",
     "        count--;",
     "        count = count % 0;", {"E12"}, None),

    # ---- E13 数组常量下标越界 ----
    ("E13", "局部数组 tiny[2] 读下标 5",
     "    int i, n;\n",
     "    int i, n;\n    int tiny[2];\n    int bad = tiny[5];\n",
     {"E13"}, "tiny[5]"),
    ("E13", "局部数组 buf[4] 用负下标 -1",
     "    int out = 0;  // 已出列人数\n",
     "    int out = 0;  // 已出列人数\n    int buf[4];\n    int neg = buf[-1];\n",
     {"E13"}, "buf[-1]"),
    ("E13", "局部数组 slots[3] 写下标 3",
     "    return n;\n}",
     "    int slots[3];\n    slots[3] = n;\n    return n;\n}",
     {"E13"}, "slots[3]"),

    # ---- E11 break / continue 位置错误 ----
    ("E11", "break 写在 printList 的循环外",
     '    printf("==================================\\n");\n}',
     '    printf("==================================\\n");\n    break;\n}',
     {"E11"}, "    break;\n}"),
    ("E11", "continue 写在 main 的循环外",
     '    printf("程序结束，谢谢使用！\\n");',
     '    continue;\n    printf("程序结束，谢谢使用！\\n");',
     {"E11"}, "continue;"),
    ("E11", "break 写在 findNode 的循环后",
     "    return NULL;\n}",
     "    break;\n    return NULL;\n}", {"E11"}, None),

    # ---- E15 结构体成员拼错（走 typedef 指针 LinkList/Node 的真实路径）----
    ("E15", "cur->sex 拼错成 cur->sx",
     "cur->sex",
     "cur->sx", {"E15"}, None),
    ("E15", "del->num 拼错成 del->nu",
     "del->num",
     "del->nu", {"E15"}, None),
    ("E15", "typedef 指针 LinkList 上 head->next 拼错成 nxt",
     "    head->next = head;  // 空表，头结点的 next 指向自己\n",
     "    head->nxt = head;  // 空表，头结点的 next 指向自己\n",
     {"E15"}, "head->nxt"),
    ("E15", "printRest 里 p->age 拼错成 agge",
     'p->num, p->name, p->sex, p->age);\n        p = p->next;\n'
     '    }\n    printf("===================================\\n");',
     'p->num, p->name, p->sex, p->agge);\n        p = p->next;\n'
     '    }\n    printf("===================================\\n");',
     {"E15"}, None),

    # ---- W5 条件表达式恒真恒假 / 条件里写赋值 ----
    ("W5", "while 条件 != 误写成 =",
     "    while (p != head) {\n        q = p->next;",
     "    while (p = head) {\n        q = p->next;", {"W5"}, None),
    ("W5", "if 条件 < 误写成 =",
     "    if (M < 1) {",
     "    if (M = 1) {", {"W5"}, None),
    ("W5", "if 条件退化成常量 1",
     "    if (X < 1 || X >= n) {",
     "    if (1) {", {"W5"}, None),

    # ---- W3 整数除法丢精度 ----
    # 注意：必须用字面量除以字面量（5 / 2）。
    # 早期这里写的是 `return n / 2`，但 n/2 在 stu_02 的真实作业里
    # 是有意的整数除法（递归除以 2），W3 已明确豁免——
    # 学生照"改成浮点"的提示做会把递归毁掉。
    # 所以 W3 只抓"分子也是字面量"的笔误场景。
    ("W3", "return 5 / 2 整数除",
     "    return n;\n}",
     "    return 5 / 2;\n}", {"W3"}, "5 / 2"),
    ("W3", "printf 里写 7 / 2",
     '    printf("\\n是否再运行一组数据？(y/n)：");',
     '    printf("\\n是否再运行一组数据？(y/n)：%d", 7 / 2);', {"W3"}, "7 / 2"),

    # ---- W4 float 赋给 int 截断 ----
    ("W4", "int half = 1.5 字面量截断",
     "    char again = 'n';",
     "    char again = 'n';\n    int half = 1.5;", {"W4"}, "int half = 1.5"),
    ("W4", "count = count * 1.5 浮点赋 int",
     "        count--;",
     "        count = count * 1.5;", {"W4"}, "count * 1.5"),

    # ---- LEX 词法错误 ----
    ("LEX", "非法字符 @",
     "    int i, count = 0;\n",
     "    int i, count = 0 @ 1;\n", {LEX_CODE}, None),
    ("LEX", "字符串未闭合",
     '    printf("程序结束，谢谢使用！\\n");',
     '    printf("程序结束，谢谢使用！\\n);', {LEX_CODE}, None),
    ("LEX", "块注释未闭合",
     "    return 0;\n}",
     "    return 0;\n}\n/* 忘记闭合的注释", {LEX_CODE}, "/* 忘记闭合的注释"),

    # ---- SYN 语法错误 ----
    ("SYN", "语句缺分号",
     '    printf("程序结束，谢谢使用！\\n");',
     '    printf("程序结束，谢谢使用！\\n")', {SYN_CODE}, None),
    ("SYN", "if 缺右括号",
     "    if (M < 1) {",
     "    if (M < 1 {", {SYN_CODE}, None),
    ("SYN", "for 缺右括号",
     "    for (i = 1; i <= n; i++) {",
     "    for (i = 1; i <= n; i++ {", {SYN_CODE}, None),
    ("SYN", "函数定义缺返回类型",
     "LinkList initList(void)\n{",
     "initList(void)\n{", {SYN_CODE}, None),
]

MUTATIONS_BANK = [
    # ---- E1 使用未声明变量 ----
    ("E1", "sum 拼错成 sm",
     "        sum = sum + data[i];",
     "        sm = sum + data[i];", {"E1"}, "        sm = sum"),
    ("E1", "total 拼错成 tot",
     "            total = total + t->cell[i * t->cols + j];",
     "            tot = total + t->cell[i * t->cols + j];", {"E1"}, "            tot ="),

    # ---- E2 使用未初始化变量 ----
    ("E2", "int r 未赋值就读入 a",
     "    int r;\n",
     "    int r;\n    a = r;\n", {"E2"}, "    a = r;"),
    ("E2", "float mean 未赋值就转 int",
     "    mean = avgOf(scores, n);",
     "    i = (int)mean;\n    mean = avgOf(scores, n);", {"E2"}, "    i = (int)mean;"),
    ("E2", "StudentPtr 未初始化就传参",
     "    StudentPtr sp = &stu;",
     "    StudentPtr sp;", {"E2"}, "    showStudent(sp);"),

    # ---- E3 重复声明 ----
    ("E3", "重复声明 i",
     "    int i, n = 4, total;\n",
     "    int i, n = 4, total;\n    int i;\n", {"E3"}, "    int i;\n"),

    # ---- E4 用户函数参数个数不符 ----
    ("E4", "gcd 少传一个参数",
     '    printf("gcd(84,36) = %d\\n", gcd(84, 36));',
     '    printf("gcd(84,36) = %d\\n", gcd(84));', {"E4"}, None),
    ("E4", "fact 多传一个参数",
     '    printf("5! = %d\\n", fact(5));',
     '    printf("5! = %d\\n", fact(5, 1));', {"E4"}, None),
    ("E4", "avgOf 少传一个参数",
     "    mean = avgOf(scores, n);",
     "    mean = avgOf(scores);", {"E4"}, "avgOf(scores)"),

    # ---- E5 调用未声明的函数 ----
    ("E5", "rowSum 拼错成 rowsum",
     '    printf("第 2 行合计 %d\\n", rowSum(&tab, 1));',
     '    printf("第 2 行合计 %d\\n", rowsum(&tab, 1));', {"E5"}, "rowsum(&tab, 1)"),

    # ---- E14 标准库函数参数个数不符 ----
    ("E14", "strcpy 少传参数",
     '    strcpy(stu.name, "Zhang San");',
     "    strcpy(stu.name);", {"E14"}, "strcpy(stu.name)"),
    ("E14", "printf 传 0 个参数",
     '    printf("程序结束。\\n");',
     "    printf();",
     {"E14"}, "    printf();"),

    # ---- E12 除零 ----
    ("E12", "表达式模零 (a+b) % 0",
     "        t = a + b;",
     "        t = (a + b) % 0;", {"E12"}, "        t = (a + b) % 0;"),

    # ---- E13 数组常量下标越界 ----
    ("E13", "scores[MAX_STU] 越界写（宏展开成 8）",
     "        scores[i] = PASS_LINE + i * 7;",
     "        scores[i] = PASS_LINE + i * 7;\n        scores[MAX_STU] = 0;",
     {"E13"}, "scores[MAX_STU]"),
    ("E13", "scores[-1] 负下标",
     "    mean = avgOf(scores, n);",
     "    scores[-1] = 0;\n    mean = avgOf(scores, n);", {"E13"}, "scores[-1]"),
    # 下面三条原本是 KNOWN_GAPS，captain 修好 array_len_of()/const_int() 后
    # 已能检出（行号偏差均为 0），按约定移进主表做回归守护。
    ("E13", "结构体成员数组的常量下标越界写",
     "    total = tableTotal(&tab);",
     "    tab.cell[24] = 1;\n    total = tableTotal(&tab);",
     {"E13"}, "tab.cell[24]"),
    ("E13", "结构体成员数组的常量下标越界读",
     "    total = tableTotal(&tab);",
     "    total = tab.cell[24];", {"E13"}, "tab.cell[24]"),
    ("E13", "下标写成常量表达式 2*4（不是字面量）",
     "    mean = avgOf(scores, n);",
     "    scores[2*4] = 0;\n    mean = avgOf(scores, n);",
     {"E13"}, "scores[2*4]"),

    # ---- E11 break / continue 位置错误 ----
    ("E11", "break 写在 main 的循环外",
     "    stu.id = 1;",
     "    break;\n    stu.id = 1;", {"E11"}, "break;"),
    ("E11", "continue 写在 fact 的循环外",
     "    if (n <= 1) {\n        return 1;\n    }",
     "    if (n <= 1) {\n        continue;\n    }", {"E11"}, "continue;"),
    ("E11", "break 写在 fib 的循环外",
     "    int t;\n",
     "    int t;\n    break;\n", {"E11"}, "    break;"),

    # ---- E15 结构体成员拼错 ----
    ("E15", "sp->name 拼错成 sp->nmae",
     "sp->id, sp->name, sp->score",
     "sp->id, sp->nmae, sp->score", {"E15"}, "sp->nmae"),
    ("E15", "sp->score 拼错成 sp->scor",
     "sp->id, sp->name, sp->score",
     "sp->id, sp->name, sp->scor", {"E15"}, "sp->scor"),
    ("E15", "t->rows 拼错成 t->rowz",
     "    for (i = 0; i < t->rows; i++) {",
     "    for (i = 0; i < t->rowz; i++) {", {"E15"}, "t->rowz"),

    # ---- W5 条件表达式恒真恒假 / 条件里写赋值 ----
    ("W5", "while 条件 != 误写成 =",
     "    while (b != 0) {\n        r = a % b;",
     "    while (b = 0) {\n        r = a % b;", {"W5"}, "    while (b = 0) {"),
    ("W5", "if 条件 < 误写成 =",
     "    if (n <= 1) {\n        return 1;\n    }",
     "    if (n = 1) {\n        return 1;\n    }", {"W5"}, "    if (n = 1) {"),
    ("W5", "if 条件退化成常量 0",
     "    if (n <= 0) {\n        return 0.0;\n    }",
     "    if (0) {\n        return 0.0;\n    }", {"W5"}, "    if (0) {"),

    # ---- W3 整数除法丢精度 ----
    ("W3", "avgOf 里 sum / n 整数除",
     "    return sum * 1.0 / n;",
     "    return sum / n;", {"W3"}, "sum / n"),
    ("W3", "fact 里掺入 7 / 2 整数除",
     "    return n * fact(n - 1);",
     "    return 7 / 2 * fact(n - 1);", {"W3"}, "7 / 2"),

    # ---- W4 float 赋给 int 截断 ----
    ("W4", "int t = 1.5 字面量截断",
     "    int t;\n",
     "    int t = 1.5;\n", {"W4"}, "int t = 1.5"),
    ("W4", "float 函数返回值赋给 int",
     "    stu.score = 88.5;",
     "    stu.score = 88.5;\n    i = avgOf(scores, n);", {"W4"}, "i = avgOf(scores, n)"),

    # ---- LEX 词法错误 ----
    ("LEX", "非法字符 @",
     "    int g;\n",
     "    int g @ 1;\n", {LEX_CODE}, "int g @ 1"),
    ("LEX", "字符串未闭合",
     '    printf("平均分 %.2f\\n", mean);',
     '    printf("平均分 %.2f\\n, mean);', {LEX_CODE}, "平均分"),
    ("LEX", "字符常量未闭合",
     "    char again = 'n';",
     "    char again = 'n;", {LEX_CODE}, "char again"),

    # ---- SYN 语法错误 ----
    ("SYN", "语句缺分号",
     "    total = tableTotal(&tab);",
     "    total = tableTotal(&tab)", {SYN_CODE}, "tableTotal(&tab)"),
    ("SYN", "if 缺左括号",
     "    if (n <= 1) {\n        return 1;\n    }",
     "    if n <= 1) {\n        return 1;\n    }", {SYN_CODE}, "n <= 1)"),
]

# 基准文件 -> 该基准上的变异清单
BASELINES = [
    ("examples/hw_josephus.c", MUTATIONS_JOSEPHUS,
     "数据结构作业：循环链表 / typedef 指针 / 结构体"),
    ("examples/mutation_bank.c", MUTATIONS_BANK,
     "算法作业：递归 / 数组 / 浮点 / switch"),
]

# ---------------------------------------------------------------------------
# 已知能力缺口（探针）
# ---------------------------------------------------------------------------
# 这几条是**真实存在的漏报**，不是为了凑数而编的。
# 单独列出来的理由：把它们混进 MUTATIONS 会让检出率掉下来，
# 看不出"是整体退化了还是只是这几条已知边界"；而直接删掉它们，
# 又等于把工具的缺陷藏起来——那比漏报本身更糟。
#
# 所以这里分开测、单独报数字，并在最终输出里点名每一跳的原因。
#
# 字段：(基准文件, 分类, 名称, 查找片段, 替换成, 期望码, 行号锚点, 原因类型, 说明)
# 基准文件直接写在元组里，不用"按名字猜归属"——那种写法改个名字就静默
# 不跑了，而静默不跑比漏报更难发现。
#
# 原因类型只有两种，**请勿混淆**：
#   "有意取舍"  —— 放宽检查换误报率，是产品决策。修掉反而会让真实代码
#                  出现大量假错误。不是待修 bug，不要"顺手修好"。
#   "精度限制"  —— 实现精度的边界，理论上可以做得更准，但代价（数据流
#                  分析）远超当前定位。也不是待修 bug。
#
# 历史：原本这里还有 4 条 E13/E14（结构体成员数组越界 ×2、常量表达式
# 下标、printf 零参数）。captain 修了 array_len_of() / const_int() /
# STD_FUNCS 之后已能检出，按约定移进主用例表做回归守护。剩下这 3 条
# 是**刻意保留**的取舍，不是修不掉。
KNOWN_GAPS = [
    ("examples/hw_josephus.c", "E1", "全大写的未声明变量名",
     '    printf("\\n（共 %d 人出列，剩余 %d 人）\\n", out, X);',
     '    printf("\\n（共 %d 人出列，剩余 %d 人）\\n", out, XX);',
     {"E1"}, None, "有意取舍",
     "_is_likely_external_macro() 把全大写标识符一律当成「来自被忽略"
     "头文件的宏」而容忍。依据：real_fortranobject.c（1400 行 numpy C 扩展）"
     "里 NPY_INTP_FMT 这类宏出现十几次，不放宽就是十几条假错误。"
     "代价是全大写拼写错误查不出来——这个取舍值得，不是 bug。"),
    ("examples/hw_josephus.c", "E5", "全大写的未声明函数名",
     "printList(head);",
     "PRINTLIST(head);",
     {"E5"}, None, "有意取舍",
     "_looks_like_external_fn() 对首字母大写的名字直接判为外部函数"
     "（Windows API：SetErrorMode/ReadFile；CPython C-API：PyErr_Occurred）。"
     "同理是误报抑制的代价，不是 bug。"),
    ("examples/hw_josephus.c", "E2", "读未初始化数组的元素",
     "    int i, count = 0;\n",
     "    int i, count = 0;\n    int arr[4];\n    count = arr[0];\n",
     {"E2"}, "count = arr[0];", "精度限制",
     "数组声明时被直接标记 initialized=True（为了让「整块被函数写入」"
     "的场景不误报，如 GetFullPathName(szPath, n, szCwd, NULL)），"
     "元素级的初始化状态没有跟踪。要做到元素级精确需要数据流分析，"
     "超出当前定位；不是漏写检查。"),
]


def gaps_for(rel_path):
    """取某个基准上的缺口探针。"""
    return [g[1:] for g in KNOWN_GAPS if g[0] == rel_path]


# 分类的展示顺序，同时也是"应该覆盖哪些类型"的自检清单
CATEGORY_ORDER = [
    "E1", "E2", "E3", "E4", "E5", "E11", "E12", "E13", "E14", "E15",
    "W3", "W4", "W5", "LEX", "SYN",
]

CATEGORY_LABEL = {
    "E1": "使用未声明变量",
    "E2": "使用未初始化变量/指针",
    "E3": "重复声明",
    "E4": "用户函数参数个数不符",
    "E5": "调用未声明函数",
    "E11": "break/continue 位置错误",
    "E12": "除零/模零",
    "E13": "数组常量下标越界",
    "E14": "标准库函数参数不符",
    "E15": "结构体成员拼错",
    "W3": "整数除法丢精度",
    "W4": "float 赋 int 截断",
    "W5": "条件里写赋值/恒真恒假",
    "LEX": "词法错误",
    "SYN": "语法错误",
}


def target_line(mutated: str, start: int, anchor):
    """算出"被注入的错误应该在哪一行"（预处理前的原始行号，1-based）。

    start 是替换发生处的字符下标（在 mutated 里，替换不改变起点偏移）。

    anchor 为什么必需：像"先声明 tiny[2] 再 tiny[5]"这种变异，片段起点
    是声明行，真正越界的是后面那行；锚点直接指到注入的那个 token 上。

    anchor 为什么必须**从 start 往后**找：`break;`、`int i;`、`scores[MAX_STU]`
    这类串在基准文件里前面的函数里早就出现过，从文件头开始找会命中
    毫不相干的那一处，于是"注入行"本身就是错的，行号准确率全是假的。

    返回 None 表示锚点没找到，此时行号准确性按"无法判定"处理，
    不能悄悄算成"准确"。
    """
    if anchor is None:
        return mutated.count("\n", 0, start) + 1
    pos = mutated.find(anchor, start)
    if pos < 0:
        return None
    return mutated.count("\n", 0, pos) + 1


def pick_diag(diags, expect, want):
    """在命中期望码的诊断里挑一条离注入处最近的。

    为什么挑"最近"而不是"第一条"：一个变异可能同时引出多条诊断
    （注入越界顺带报未使用变量），拿第一条比行号会误判成不准确。
    这里按"是否指到注入点"取最优，对工具是公平的。
    """
    hits = [d for d in diags if d[1] in expect]
    if not hits:
        return None
    if want is None:
        return hits[0]
    return min(hits, key=lambda d: abs(d[2] - want))


def probe_gaps(base, gaps):
    """在基准上跑"已知缺口探针"，返回每一跳的实测结果。

    这些用例**期望是检出**，跑出来如果是 STILL_GAP 就坐实了缺口。
    它们不计入主检出率（否则 78/78 会变成 78/81，看不出退化来源），
    但必须在输出里点名，并且带上实测到的实际诊断码——
    原因写错了（比如某个缺口其实已经被修掉）这里会立刻暴露。

    每条探针都带"原因类型"（有意取舍 / 精度限制），
    目的是让后来的人一眼分清"该不该修"，而不是当成待修 bug 列表。
    """
    rows = []
    for cat, name, old, new, expect, anchor, kind, why in gaps:
        count = base.count(old)
        if count != 1:
            rows.append({"cat": cat, "name": name, "status": "SKIP",
                         "note": f"片段在基准里出现 {count} 次", "why": why,
                         "kind": kind, "actual": None})
            continue
        start = base.find(old)
        mutated = base.replace(old, new, 1)
        want = target_line(mutated, start, anchor)
        codes, diags = check_text(mutated)
        hit = bool(codes & expect)
        best = pick_diag(diags, expect, want) if hit else None
        rows.append({
            "cat": cat, "name": name,
            "status": "STILL_GAP" if not hit else "NOW_FIXED",
            "note": f"期望 {sorted(expect)}，实际 {sorted(codes) or '无'}",
            "actual": sorted(codes), "want": want, "why": why, "kind": kind,
            "line": best[2] if best else None,
        })

    if not rows:
        return rows

    print(f"\n[已知缺口探针]  {len(rows)} 条（不计入上面的检出率）")
    for r in rows:
        print(f"  {r['status']:10s} [{r['cat']}] {r['name']}  "
              f"<{r['kind']}>")
        print(f"     实测: {r['note']}")
        print(f"     说明: {r['why']}")
    n_gap = sum(1 for r in rows if r["status"] == "STILL_GAP")
    n_fix = sum(1 for r in rows if r["status"] == "NOW_FIXED")
    n_keep = sum(1 for r in rows if r["kind"] == "有意取舍")
    n_prec = sum(1 for r in rows if r["kind"] == "精度限制")
    print(f"  小结: 仍有 {n_gap} 条未检出，其中有意取舍 {n_keep} 条、"
          f"精度限制 {n_prec} 条——两类都**不是待修 bug**")
    if n_fix:
        print(f"  注意: {n_fix} 条现在能检出了，应移进主用例表做回归守护")
    return rows


def run_baseline(rel_path, mutations, note):
    """在单个基准上跑全部变异，返回统计字典。"""
    path = os.path.join(ROOT, rel_path)
    with open(path, encoding="utf-8") as f:
        base = f.read()

    print(f"\n{'=' * 74}")
    print(f"基准文件: {rel_path}    ({note})")
    print(f"{'=' * 74}")

    # ---- 第 0 步：基准必须零 error ----
    codes, diags = check_errors(base)
    print(f"[基准检查] error 码: {sorted(codes) or '无'}")
    if codes:
        print("  基准代码有错误，无法用作变异测试的起点。")
        for level, code, line, col, msg in diags:
            if level == "error":
                print(f"   [{code}] {line}:{col}: {msg}")
        return None
    n_warn = sum(1 for d in diags if d[0] == "warning")
    print(f"  通过：真实代码零误报（0 个 error，{n_warn} 条 warning）")

    rows = []
    for cat, name, old, new, expect, anchor in mutations:
        count = base.count(old)
        if count != 1:
            # 关键防线：片段不唯一就跳过。静默替换第一处会改到
            # 别的函数里去，测的就不是这条用例了。
            rows.append({"cat": cat, "name": name, "path": rel_path,
                         "status": "SKIP",
                         "note": f"片段在基准里出现 {count} 次（要求恰好 1 次）",
                         "line_diff": None, "line_ok": None, "want": None})
            continue

        start = base.find(old)
        mutated = base.replace(old, new, 1)
        want = target_line(mutated, start, anchor)
        codes, diags = check_text(mutated)
        hit = bool(codes & expect)
        best = pick_diag(diags, expect, want) if hit else None

        if hit:
            diff = abs(best[2] - want) if (best and want is not None) else None
            line_ok = diff is not None and diff <= LINE_TOLERANCE
            rows.append({"cat": cat, "name": name, "path": rel_path,
                         "status": "CAUGHT",
                         "note": f"报出 {sorted(codes)}",
                         "line": best[2] if best else None,
                         "want": want, "line_diff": diff, "line_ok": line_ok,
                         "diag_code": best[1] if best else None})
        else:
            rows.append({"cat": cat, "name": name, "path": rel_path,
                         "status": "MISSED",
                         "note": f"期望 {sorted(expect)}，"
                                 f"实际 {sorted(codes) or '无'}",
                         "line": None, "want": want,
                         "line_diff": None, "line_ok": None})

    # ---- 明细 ----
    print(f"\n[变异明细]  行号容差 ±{LINE_TOLERANCE} 行")
    print(f"  {'结果':8s} {'类型':4s} {'变异':34s} {'注入行':>6s} "
          f"{'诊断行':>6s} {'偏差':>4s}  说明")
    for r in rows:
        want = "-" if r.get("want") is None else str(r["want"])
        got = "-" if r.get("line") is None else str(r["line"])
        diff = "-" if r.get("line_diff") is None else str(r["line_diff"])
        if r["status"] == "CAUGHT":
            extra = (f"偏差 {diff} 行 -> OK" if r["line_ok"]
                     else f"偏差 {diff} 行 -> 行号不准")
        else:
            extra = r["note"]
        print(f"  {r['status']:8s} {r['cat']:4s} {r['name']:34s} "
              f"{want:>6s} {got:>6s} {diff:>4s}  {extra}")

    # ---- 已知缺口探针（单独测、单独报）----
    gap_rows = probe_gaps(base, gaps_for(rel_path))

    return {"path": rel_path, "rows": rows, "gaps": gap_rows}


def summarize(results):
    """汇总所有基准的结果：分类明细 + 检出率 + 行号准确率 + 已知缺口。"""
    results = [r for r in results if r]
    all_rows = [row for res in results for row in res["rows"]]
    all_gaps = [g for res in results for g in res["gaps"]]
    if not all_rows:
        print("\n没有可统计的变异。")
        return 1

    caught = [r for r in all_rows if r["status"] == "CAUGHT"]
    missed = [r for r in all_rows if r["status"] == "MISSED"]
    skipped = [r for r in all_rows if r["status"] == "SKIP"]
    valid = len(caught) + len(missed)

    # ---- 分类明细 ----
    print(f"\n{'=' * 74}")
    print("分类明细（主用例表）")
    print(f"{'=' * 74}")
    print(f"  {'类型':4s} {'含义':24s} {'用例':>4s} {'检出':>4s} "
          f"{'漏报':>4s} {'跳过':>4s} {'行号准':>8s}")
    for cat in CATEGORY_ORDER:
        rows = [r for r in all_rows if r["cat"] == cat]
        if not rows:
            continue
        c = sum(1 for r in rows if r["status"] == "CAUGHT")
        m = sum(1 for r in rows if r["status"] == "MISSED")
        s = sum(1 for r in rows if r["status"] == "SKIP")
        acc = sum(1 for r in rows if r["line_ok"])
        denom = c + m
        acc_s = f"{acc}/{denom}" if denom else "-"
        print(f"  {cat:4s} {CATEGORY_LABEL.get(cat, ''):24s} "
              f"{len(rows):>4d} {c:>4d} {m:>4d} {s:>4d} {acc_s:>8s}")

    # ---- 覆盖率自检：任务要求的类型是不是都覆盖到了 ----
    covered = {r["cat"] for r in all_rows}
    missing_cats = [c for c in CATEGORY_ORDER if c not in covered]

    # ---- 总量 ----
    print(f"\n{'=' * 74}")
    print("总计")
    print(f"{'=' * 74}")
    print(f"变异用例总数: {len(all_rows)}（有效 {valid}，跳过 {len(skipped)}）")
    det = len(caught) / valid if valid else 0.0
    print(f"检出 {len(caught)}/{valid}，漏报 {len(missed)}，"
          f"无效变异 {len(skipped)}")
    print(f"变异检出率: {det:.1%}")

    # ---- 行号准确率（只在检出的用例上统计）----
    with_line = [r for r in caught if r["line_ok"] is not None]
    acc_ok = sum(1 for r in with_line if r["line_ok"])
    line_acc = acc_ok / len(with_line) if with_line else 0.0
    print(f"行号准确 {acc_ok}/{len(with_line)}"
          f"（检出的用例里诊断行落在注入行 ±{LINE_TOLERANCE} 行内）")
    print(f"行号准确率: {line_acc:.1%}")

    # ---- 全量口径：把已知缺口一起算进去，这是对工具真实的评价 ----
    gap_still = [g for g in all_gaps if g["status"] == "STILL_GAP"]
    gap_fixed = [g for g in all_gaps if g["status"] == "NOW_FIXED"]
    gap_skip = [g for g in all_gaps if g["status"] == "SKIP"]
    if all_gaps:
        denom_all = valid + len(gap_still) + len(gap_fixed)
        num_all = len(caught) + len(gap_fixed)
        print(f"\n[全量口径] 主用例表 + 已知缺口探针一起算")
        print(f"  检出 {num_all}/{denom_all}，真实检出率 "
              f"{(num_all / denom_all if denom_all else 0):.1%}")
        print(f"  其中已知缺口造成的漏报 {len(gap_still)} 条"
              + (f"，已修复 {len(gap_fixed)} 条" if gap_fixed else "")
              + (f"，探针无效 {len(gap_skip)} 条" if gap_skip else ""))

    if missing_cats:
        print(f"\n[覆盖缺口] 以下错误类型没有用例: "
              f"{', '.join(missing_cats)}")

    # ---- 主用例表里的漏报（正常情况下应为空）----
    if missed:
        print(f"\n[漏报明细] {len(missed)} 个变异没有被检出")
        for r in missed:
            print(f"  - [{r['cat']}] {r['name']}  ({r['path']})")
            print(f"    期望 {r['note']}")
        print("  说明：漏报即诊断器的能力缺口，记录在此供后续修复，"
              "本次不改 src/。")

    # ---- 已知缺口清单：点名哪个没检出、属于哪种原因 ----
    if gap_still:
        print(f"\n[已知缺口明细] {len(gap_still)} 条未检出"
              f"（均为已确认的取舍，不改 src/，只记录）")
        for g in gap_still:
            print(f"  - [{g['cat']}] {g['name']}  <{g['kind']}>")
            print(f"    期望检出 {g['cat']}，实际 "
                  f"{', '.join(g['actual']) if g['actual'] else '无任何诊断'}")
            print(f"    说明: {g['why']}")
        print("  提醒：清单一律写明了原因类型。'有意取舍'修掉会让真实代码"
              "出现大量假错误；'精度限制'需要数据流分析才做得准。")
        print("  两者都**不是待修 bug**，不要按「漏报」简单处理。")
    if gap_fixed:
        print(f"\n[缺口已修复] 以下探针现在能检出了，应移进主用例表：")
        for g in gap_fixed:
            print(f"  - [{g['cat']}] {g['name']}，实际 {g['actual']}")

    # ---- 行号偏大的清单 ----
    bad_line = [r for r in with_line if not r["line_ok"]]
    if bad_line:
        print(f"\n[行号不准] {len(bad_line)} 个变异检出了但指错位置")
        for r in bad_line:
            print(f"  - [{r['cat']}] {r['name']}: 注入在第 {r['want']} 行，"
                  f"诊断指到第 {r['line']} 行（偏差 {r['line_diff']} 行）")

    # 退出码只看主用例表：已知缺口是"已记录在案的边界"，
    # 让它把整个测试判失败，等于每次跑都红，起不到守门作用。
    ok = not missed and not skipped and not missing_cats
    return 0 if ok else 1


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    if args:
        targets = []
        for a in args:
            rel = os.path.relpath(os.path.abspath(a), ROOT).replace(os.sep, "/")
            hit = [(p, m, n) for p, m, n in BASELINES if p == rel]
            if not hit:
                print(f"未知基准文件: {a}")
                print("可用的基准：")
                for p, m, n in BASELINES:
                    print(f"  {p}  ({len(m)} 个变异)  {n}")
                return 2
            targets.extend(hit)
    else:
        targets = BASELINES

    print(f"变异测试：{len(targets)} 个基准文件")
    results = [run_baseline(p, m, n) for p, m, n in targets]
    if any(r is None for r in results):
        return 1
    return summarize(results)


if __name__ == "__main__":
    sys.exit(main())
