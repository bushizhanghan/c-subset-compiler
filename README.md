# csub —— C 子集编译器与初学者错误诊断器

面向《计算机系统基础》等课程的教学工具。它做两件 gcc 不做的事：

1. **把"能编译但会出事"的代码拦下来**（未初始化、常量越界、`if (a = 1)` 之类）
2. **把编译过程可视化**（三地址码 IR 的生成与优化前后对比）

当前状态：预处理 + 前端 + IR + x86-64 后端全链路可用。
**用 11 份真实学生代码验证过**（不是自造用例）：
零崩溃、零误报、真检出 6/11，修出 7 个缺陷、新增 2 条规则。

能查什么、不能查什么、哪里可能误报，全部写在
**[docs/CAPABILITY_BOUNDARY.md](docs/CAPABILITY_BOUNDARY.md)** 里。
那份文档的依据是实测，不是自评。

已知问题也都写在本文档里，没有藏：
见 [已知边界](#已知边界)。

---

## 为什么需要它

gcc 为运行效率而设计，对"未定义行为"一律放行。对学生来说，这意味着：

```c
int a;
int b = a;        // gcc 不报错，b 的值是随机的
int arr[2];
arr[2] = 1;       // gcc 不报错，越界写坏了别的内存
if (a = 1) {}     // gcc 只在 -Wall 下警告，很多人不开
```

这些不是语法错误，程序能编译通过、能跑起来，然后结果莫名其妙。
**这正是初学者最挫败的地方。**

csub 的立场是：把这些拦下来，并用中文说清楚为什么错、怎么改。

## 真实代码验证（这是最重要的部分）

不靠自造用例说话，直接拿真实代码跑。当前验证了 **9 份真实 C 源码**
（另有 12 份小样例用于语法与 IR 演示，合计 `examples/` 下 21 个文件）：

| 文件 | 规模 | 结果 |
|---|---|---|
| `hw_josephus.c`（数据结构作业，约瑟夫环） | 238 行 | 零误报、零警告 |
| `real_nmakehlp.c`（Tcl 构建工具，纯 Windows API） | 815 行 | **0 error / 0 warning** |
| `real_fortranobject.c`（numpy f2py） | 1436 行 | **0 error / 0 warning** |
| `mutation_bank.c`（算法作业：递归/数组/浮点/switch） | — | 零误报、零警告 |
| `lab_optbench.c`（矩阵拷贝基准，static + 二维数组） | 101 行 | 零误报 |
| `lab_optime.c`（浮点循环基准） | 61 行 | 零误报，2 条有效警告 |
| `lab_optimize.c`（编译优化对比） | 48 行 | 零误报，2 条有效警告 |
| `lab_madd.c` / `lab_optbench2.c` | 67 / 88 行 | 零误报 |

**21 个示例文件全部无崩溃。** 两份最大的第三方源码（815 行、1436 行）
都做到了 **0 error / 0 warning**。

最关键的两个数字：

- **injection 测试**：在 `hw_josephus.c` 里注入 8 个典型错误，**8/8 全部检出**
- **nmakehlp**：一份 815 行纯 Windows API 代码，从最初 113 个误报收敛到 **0 error / 0 warning**

### 走到"零警告"这段经历值得记

这两份文件此前各剩 1 条警告，看起来像"检查终于生效了"，实测**两条都是误报**：

| 位置 | 检查 | 实际是 |
|---|---|---|
| `real_nmakehlp.c:798` | W3 整数除法丢精度 | 误报。命中的是 `sizeof(paths)/sizeof(paths[0])` 取数组长度惯用法，两个操作数都是 `sizeof`，相除不可能丢小数 |
| `real_fortranobject.c:531` | W6 变量声明后未使用 | 误报。`int i = 0;` 下一行就用在 `fp->defs[i].rank` 里，是检查漏了成员链中的下标 |

修这两条的过程又牵出两个更根本的缺陷（行号口径、成员链下标漏检），
详见 [`docs/PROGRESS.md`](docs/PROGRESS.md) 的第五轮与第八、九节。

留下作对照的**有效警告**仍然有：`lab_optime.c:41` 与 `lab_optimize.c:40`，
`counter += (5 - 1) / 4` 确实是整数除法得 1，浮点循环变量与常量比较
确实有精度风险。**"零警告"只在这两份第三方文件上成立，不是全局状态。**

变异测试做的事：拿真实代码当基准，逐个注入学生会犯的错，看能不能抓出来。
这比手写用例集有说服力，因为基准不是我编的例子。

## 与 gcc 的对照

跑 `python tools/crosscheck.py -v`，17 个程序：

| 项 | 结果 |
|---|---|
| 判定一致 | 14 |
| csub 有意更严格 | 3（未初始化、常量越界、缺 main） |
| 真不一致 | **0** |
| 含设计意图符合率 | **100%** |
| L3 常量折叠 vs gcc 实际运行 | 3 / 3 吻合 |

那 3 处「更严格」不是缺陷，是产品定位。报告里要这么写，不能藏。

## IR 语义验证

IR 是"生成出来给人看"的，光看文本判断不了对错：一个看起来合理的 `goto`
完全可能跳到错误的位置。`tools/ir_verify.py` 把这件事变成可测的：

- **A** = 用 `tools/ir_exec.py` 解释执行 csub 生成的 IR
- **B** = 用 gcc 编译同一份 C 真实运行，取退出码
- 比对 A 与 B，未优化 IR 和优化后 IR **分别统计**

| 项 | 结果 |
|---|---|
| 程序总数 | 23 |
| 未优化 IR 一致 | 23 / 23（100%） |
| 优化后 IR 一致 | 23 / 23（100%） |
| 两者都一致 | **23 / 23（100%）** |

解释执行器本身也要先自证：`ir_verify.py` 用一组**手写 IR 片段**做自测，
这些片段与 `src/ir.py` 无关、期望值按 C 语义手工推导，避免"执行器错"
被误记成"IR 生成错"。

## 用法

```bash
python csub.py check  file.c           # 输出诊断
python csub.py report file.c           # 带源码上下文与 ^ 定位（行号对应原文件）
python csub.py ir     file.c           # 三地址码 IR + 基本块活跃变量
python csub.py tokens file.c           # 词法 token
python csub.py ast    file.c           # AST
python render.py      file.c -o r.html # 生成可视化报告
```

跑测试：

```bash
python tests/run_tests.py -v            # 诊断准确性（自建用例）
python tools/crosscheck.py -v           # 与 gcc 交叉验证
python tools/mutation_test.py           # 真实代码变异测试（两个基准文件）
python tools/ir_verify.py               # IR 语义：解释执行 vs gcc 真实运行
python tools/fuzz_test.py --quick       # 模糊测试：任意输入不崩溃、不死循环
```

## 指标总表

| 指标 | 数值 | 来源 |
|---|---|---|
| 自建用例通过 | 56 / 56 = 100% | `tests/run_tests.py` |
| 有错代码召回率 | 37 / 37 = 100% | 同上 |
| 合法代码误报率 | 0 / 19 = 0% | 同上 |
| gcc 判定真不一致 | 0 / 17 | `tools/crosscheck.py` |
| IR 语义与真实运行一致 | 23 / 23 = 100%（raw 与 opt 各 100%） | `tools/ir_verify.py` |
| 变异检出（主用例表） | 79 / 79 = 100% | `tools/mutation_test.py` |
| 变异诊断行号准确 | 79 / 79 = 100%（±2 行内） | 同上 |
| 变异检出（全量口径） | 79 / 82 = 96.3% | 同上（含 3 条已知缺口） |
| 真实第三方源码 error 数 | 0（815 行 / 1436 行各 0） | `csub.py check` |
| 示例文件崩溃数 | 0 / 21 | 手工全量扫 |

**怎么解读这些数字**：自建用例的 100% 参考价值有限，因为用例是按实现写的。
真正有分量的是四行：**gcc 真不一致 0**、**IR 与真实运行 23/23**、
**真实第三方源码 0 error**、**示例文件 0 崩溃**。前者靠独立实现做裁判，
后三者靠非自造代码和非静态检查做裁判。

变异测试的全量口径（96.3%）低于主用例表（100%），这个差值不是缺陷，
是**主动把已知漏报算进去**的结果；只报 100% 等于把缺口藏起来。
3 条缺口逐条写在 README 末尾的「已知边界」里。

## 支持的语法（经真实代码验证）

> 覆盖 C89/C99 的常用子集，足以解析 Tcl、numpy 这类真实项目源码。
> 下面每一项都在 `src/parser.py` 里有对应的解析分支，并且至少在
> `examples/` 的一个文件里被实际用到过。

- **预处理**：`#include`（丢弃并记录）、对象宏与函数宏（展开）、
  多行宏续行 `\`、`#ifndef/#define/#endif` 头文件保护
- **类型**：`int/float/char/double/void`、`signed/unsigned/short/long` 组合、
  指针、多维数组、`struct`、`typedef`、函数指针 typedef、调用约定修饰符
- **语句**：`if/else`、`while`、`for`、`do-while`、`switch/case/default`、
  `return`、`break`、`continue`、`goto` 与标签 `again:`
- **表达式**：算术、比较、逻辑、位运算、赋值（含复合）、调用、下标、成员、
  三目 `?:`、逗号表达式、`sizeof`、类型转换、字符常量、科学计数法、
  初始化列表 `{1, 2}` 与 C99 指定初始化器 `{.x = 1, .y = 2}`
- **声明**：多变量声明 `int a = 1, b = 2;`、数组形参 `char *argv[]`、
  函数指针形参 `void (*dtor)(void *)`、可变参数 `int f(int, ...)`
- **标准库**：预置 84 个常用函数 + 15 个常量宏 + 33 个全局对象
  （`NULL`、`EOF`、`stdin`、`stderr`、`printf`、`scanf`、`malloc`、`strstr`、
  `clock_gettime`、`ReadFile` 等），用于抑制"不处理头文件"带来的误报

**不支持**（已知边界，不要当成 bug）：

| 项 | 现状 |
|---|---|
| `enum` / `union` | 不支持，直接 ParseError |
| 位域 `int a : 3;` | 不支持 |
| 局部函数指针变量 `int (*f)(int);` | 不支持（typedef 形式 `fn_t f;` 可以，形参 `void (*dtor)(void*)` 也可以） |
| 函数宏展开 | 不展开，但会记录并提示"未展开" |
| 复杂条件编译 | `#if defined(...)` 等整体跳过并提示该分支未处理 |
| 完整的结构体成员类型检查 | 只登记成员名与成员类型，不做整体赋值兼容检查 |
| 完整转义序列 | 只处理 `\n \t \r \0 \" \\` 等常用项 |

## 诊断规则

错误（error）：

| 码 | 含义 |
|---|---|
| E1 | 使用未声明的变量 |
| E2 | 使用未初始化的变量 |
| E3 | 重复声明 |
| E4 | 函数参数个数不符 |
| E5 | 调用未声明的函数 |
| E10 | 找不到 main |
| E11 | break/continue 在循环外 |
| E12 | 除数为常量 0 |
| E13 | 数组下标越界（常量下标） |
| E14 | 标准库函数参数个数不符 |
| E15 | 结构体没有该成员（拼错字段名） |
| LEX / SYN | 词法 / 语法错误 |

`E15` 附带"是不是想写 `data`？"这类编辑距离建议，因为它主要抓的就是
`n.dat = 1` 这种笔误。例：

```
[ERROR E15] 第 4 行: 结构体 Node 没有成员 'dat'
>>    4 |     n.dat = 1;
               ^
   提示: 是不是想写 'data'？
```

警告（warning，提示性）：

| 码 | 含义 |
|---|---|
| W3 | 整数除法丢精度 |
| W4 | float 赋给 int 会截断 |
| W5 | 条件里用了赋值 / 条件恒定 / 浮点比较 |
| W6 | 变量声明后未使用 |
| W8 | main 可能缺 return |

另外还有 `E9`（无法识别的顶层声明）与 `E0`（文件读不出来），
属边界情况，正常代码碰不到。

## 目录

```
csub.py                命令行入口（含行号回映）
render.py              生成 HTML 诊断报告
src/preprocess.py      预处理：include/define/条件编译
src/lexer.py           词法分析
src/parser.py          语法分析（递归下降 + 优先级爬升）
src/semantic.py        符号表、类型检查、易错点检测  ← 核心价值
src/ir.py              三地址码 IR、常量折叠、死代码消除、CFG 与活跃变量
tests/run_tests.py     诊断用例集与指标
tests/cases_extra.py   扩充用例
tools/crosscheck.py    与 gcc 交叉验证（L1 语法 / L3 执行）
tools/mutation_test.py 真实代码变异测试（两个基准文件）
tools/ir_exec.py       IR 解释执行器（独立于 src/，只吃 IR 文本）
tools/ir_verify.py     IR 语义验证：解释执行 vs gcc 真实运行
tools/fuzz_test.py     模糊测试：变异 / token 汤 / 边界输入
examples/              21 个示例源码（含真实作业与两份第三方源码）
docs/PROGRESS.md       项目进展报告：每轮被什么代码逼出了什么缺陷
```

## 第二轮精进：被真实代码逼出来的 20 个缺陷

第二轮拿更大的第三方源码（Tcl 构建工具、numpy f2py 模块）做压力测试，
每一个都是从"真实文件跑不过"倒逼出来的：

| # | 缺陷 | 暴露它的代码 |
|---|---|---|
| 15 | 浮点后缀 `0.0f` / `1979.0f` 不识别 | lab_optime.c |
| 16 | 调用约定 `WINAPI` / `__cdecl` 挡住类型解析 | real_nmakehlp.c |
| 17 | 多行宏 `\` 续行导致后续代码全歪 | real_fortranobject.c |
| 18 | `do-while` 不支持 | 两边都有 |
| 19 | 初始化列表 `{a, b}` 与 C99 指定初始化 `.field = x` | 两边都有 |
| 20 | 相邻字符串字面量拼接 `"a" "b"` | real_fortranobject.c |
| 21 | 数组形参 `char *argv[]` | real_nmakehlp.c |
| 22 | 函数指针 typedef `T *(*name)(...)` | real_fortranobject.c |
| 23 | `switch` / `case` / `default` 完全不支持 | real_nmakehlp.c |
| 24 | goto 标签 `again:`（且踩了 `peek()` 与 `peek(1)` 同值的坑） | real_nmakehlp.c |
| 25 | `(*(init))()` 函数指针调用被误判为类型转换 | real_fortranobject.c |
| 26 | 三目运算符 `a ? b : c` | real_fortranobject.c |
| 27 | 逗号表达式 `for (p=p->next, ++n)` | real_nmakehlp.c |
| 28 | 数组长度是常量表达式 `[MAX_PATH + 1]` | real_nmakehlp.c |
| 29 | 可变参数 `f(int, ...)` | real_fortranobject.c |
| 30 | 函数指针形参 `void (*dtor)(void *)` | real_fortranobject.c |
| 31 | 类型名无穷无尽（`size_t`/`npy_intp`/`ternaryfunc`…） | 两边都有 |
| 32 | 字符串中间嵌未定义宏 `"fmt" MACRO` | real_fortranobject.c |
| 33 | 全局变量被误判"未初始化"（C 里默认零初始化） | real_nmakehlp.c |
| 34 | 输出参数索引写错，把 `NULL` 当左值 | real_nmakehlp.c |

**其中两个最值得记录**：

**类型名识别**（第 25 / 31 条）。`(*(init))()` 和 `(ternaryfunc)fn` 在语法上
无法区分，因为都形如 `(标识符)` + 表达式。最后的解法是让 parser 记录
**已声明的变量名**：括号里的名字如果是见过的变量，就绝不是类型转换。
这个判据是唯一可靠的。

**输出参数索引**（第 34 条）。`WriteFile(..., &dw, NULL)` 里只有第 4 个参数是
输出。早期把"第 3 个起都算输出"，结果 `NULL` 被当成左值报 E1。改成精确 0 基索引。

## 第一轮：真实作业暴露的 14 个缺陷

这些全部是拿真实作业测试时暴露的，不是凭空设想的：

1. `#include` 直接词法报错 → 整个工具在真实代码上不可用（**最大的一条**）
2. 行号错位：预处理删行后，报的位置指向原文件的错误行
3. `typedef struct` 不支持 → 数据结构作业全崩
4. `f(void)` 表示无参，被当成一个 void 参数
5. `(LinkList)malloc(...)` 类型转换不支持
6. `int a = 1, b = 2;` 逗号多变量声明报语法错
7. `do-while` 不支持
8. 字符常量 `'n'` 不支持
9. `NULL` 未识别为常量宏
10. `scanf("%d",&v)` 里的 `v` 被当未初始化读取
11. `sizeof(Node)` 里的 typedef 名被当变量
12. 多维数组 `int a[N][N]` 不支持
13. 科学计数法 `1e9` 不支持
14. `static` / `unsigned long` 等存储类与多词类型不支持

## 第三轮：被"真跑一遍"逼出来的缺陷

前两轮的缺陷都是"真实文件跑不过"逼出来的。第三轮换了裁判方式：
不只看它能不能跑通，而是**把 IR 真正执行一遍，和 gcc 跑出来的结果比**。
结果是 13 个程序里 IR 语义是错的，而静态检查一个都发现不了。

| 缺陷 | 症状 | 修复前 |
|---|---|---|
| `const_fold` 无视控制流 | 循环里的 `i = i + 1` 被固化成 `i = 1`，循环不前进 | 13 / 23 不一致，其中 5 例死循环 |
| do-while 回边极性反 | 该"条件为真跳回循环头"，写成了"条件为假跳回" | 语义完全相反 |
| switch 负常量 case 悬空标签 | 分发与展开各调一次 `gen_expr`，生成两个不同临时变量 | `goto` 指向不存在的标签 |

三条的共同点：**都是自己写进 `src/ir.py` 的翻译/优化逻辑，此前只做静态检查，
从没运行验证过**。补上 `tools/ir_exec.py` + `tools/ir_verify.py` 之后才暴露。

另外这一轮还修了几个一跑真实文件就现形的：

- 成员访问的基表达式不是裸标识符时（`(*p).a`、`mk().a`）无限递归，直接 `RecursionError` 崩溃
- `&x` 被当成"读取 x"，导致 `struct S *p = &s;` 之后再用 `s` 误报未初始化
- `check_lvalue` 侧漏了外部符号容忍，`&PyArray_Type` 走左值路径时仍报 E1
- `va_arg(argp, int)` 的类型参数无法解析，parser 在 `int` 上直接崩（整份 1436 行文件卡住的真正原因）
- `va_list argp` 由 `va_start` 初始化的语义没实现
- 函数指针调用 `(*(getter))(&flag)` 的参数没视为可能写出

详见 [`docs/PROGRESS.md`](docs/PROGRESS.md)。

## 已知边界

这些是**当前确实做不到的**，都经过实测确认，不是猜测。
写在这里是为了让"漏报"和"bug"能区分开。

**有意取舍**（修掉会让真实代码出现大量假错误）：

| 漏掉什么 | 原因 |
|---|---|
| 全大写标识符的拼写错误 | `_is_likely_external_macro()` 把全大写名字当成"来自被忽略头文件的宏"。不放宽的话，`real_fortranobject.c` 里 `NPY_INTP_FMT` 这类宏就是十几条假错误 |
| 首字母大写函数名的拼写错误 | 同上，`_looks_like_external_fn()` 覆盖 Windows API 与 CPython C-API |

**精度限制**：

| 漏掉什么 | 原因 |
|---|---|
| 读未初始化数组的元素 | 数组声明时直接标记 `initialized=True`，避免"整块被函数写入"的场景误报；元素级状态没跟踪 |
| 结构体整体赋值后再按值传参 | `struct S s; s.a = 1; f(s);` 仍报 E2 未初始化——只跟踪成员级写入，不跟踪"整体已完整初始化" |
| 成员链里 Index 之后的数组越界 | `p->defs[0].rank[99]` 不报 E13，而 `p->cell[24]`、`t.sub.cell[24]` 都正常报。缺口只在"成员链走过一次下标之后"这一段 |

**鲁棒性**（`tools/fuzz_test.py` 发现，正常 C 文件触发不到）：

解析器没有递归深度上限。超深嵌套（150 层括号、400 层花括号、深三目、
深 if）会耗尽 Python 递归栈，抛 `RecursionError` 而不是给出语法错误。
另外 `0x` 后面没数字、超长整数字面量（>4300 位）会抛 `ValueError`。
764 个模糊测试用例里 8 类去重崩溃、18 次命中、0 次死循环；
其中 5 类在对当前代码重跑时仍复现（`tools/fuzz_artifacts/crash_*.c` 可直接复跑）。

**其他**：

- 函数宏不展开，只记录并提示
- 复杂条件编译（`#if defined(...)`）整体跳过并提示
- 没有机器码生成，当前止于 IR

### 已经修掉的（留在这里备查）

这几条曾经是缺陷，现在已修，写出修复方式供核对：

| 曾经的问题 | 修法 |
|---|---|
| 行号口径偏差：`str.splitlines()` 把换页符 `\x0c` 也当换行，`real_nmakehlp.c` 里 csub 报第 808 行、gcc 报第 798 行 | `preprocess.py` 导出 `split_lines()` 只按 `\n` 切，`csub.py` / `render.py` 统一调用。实测注入同一点位，csub 与 gcc 现在都报第 798 行 |
| 成员链下标漏检：`fp->defs[i].rank` 的 `i` 误报 W6，同位置漏报 E1 | `check_member()` 增加 `check_member_chain_exprs()` 遍历链上各表达式。实测 `o.defs[zz].rank` 现在报 E1 |
| W3 在 `sizeof(a)/sizeof(b)` 上误报 | W3 对两侧都是 `sizeof` 的除法豁免 |
| 成员访问基表达式非裸标识符时无限递归崩溃（`(*p).a`、`mk().a`） | `check_member_base` 只对剥完 Member 后的内层表达式做检查，不再把 Member 节点丢回 `check_expr` |
| `const_fold` 无视控制流，循环里的 `i = i + 1` 被固化成 `i = 1` | 常量传播只在基本块内做，遇 `label`/`goto`/`if`/`ifTrue`/`return`/`call` 清空常量表 |
| do-while 回边极性反（该 ifTrue 写成 ifFalse） | 改用 `ifTrue` |
| switch 负常量 case 生成悬空标签 | 预先求值一次、缓存标签名，分发与展开严格复用 |

## 下一步

1. **成员链里 Index 之后的越界检查**：`p->defs[0].rank[99]` 目前不报。
   缺口范围窄但要定位清楚，避免修一处崩另一处。
2. **结构体整体初始化跟踪**：`s.a = 1; f(s);` 仍误报 E2。
3. **元素级的数组初始化跟踪**：需要数据流分析，工作量最大。
4. **解析器递归深度上限**：4 类深嵌套仍抛 `RecursionError`，
   应改成"嵌套太深"的明确诊断。
5. **再找 2–3 份真实作业**，尤其是有编译错误的。当前真实代码都是能正常编译的，
   "能不能检出错误"仍主要靠注入验证，真实错误分布可能完全不同。
6. 没有机器码生成。当前止于 IR。

第 5 条价值最高：前几轮的裁判虽然越来越狠，但都还是"我说了算"的代码
——我选的文件、我注入的错误、我写的 IR 片段。真正的下一个里程碑是
拿别人写的、本来就是错的代码来测。
