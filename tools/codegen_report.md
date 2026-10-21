# csub 后端：IR -> x86-64 汇编 实测报告

生成方式：`python tools/codegen.py --selftest`（本文件由脚本自动写出，数字均来自实跑）。

链路：csub 前端解析 + 语义分析 -> `src/ir.py` 生成三地址码 -> `tools/codegen.py` 生成 .s -> `tools/asm_run.py` 用 gcc 汇编链接 -> 执行取退出码，与 `gcc -O0` 直接编译同一份源码的退出码对比。

## 逐程序结果

| 程序 | 自生成退出码 | gcc 退出码 | 是否一致 | 备注 |
| --- | --- | --- | --- | --- |
| p01_ret42 | 42 | 42 | 一致 |  |
| p02_arith | 7 | 7 | 一致 |  |
| p03_add_call | 42 | 42 | 一致 |  |
| p04_while_sum | 10 | 10 | 一致 |  |
| p05_if_else | 10 | 10 | 一致 |  |
| p06_nested_loop | 12 | 12 | 一致 |  |
| p07_fib_recursion | 55 | 55 | 一致 |  |
| p08_multi_func | 40 | 40 | 一致 |  |
| p09_div_mod | 32 | 32 | 一致 |  |
| p10_logic | 7 | 7 | 一致 |  |
| p11_do_while | 6 | 6 | 一致 |  |
| p12_shift_bit | 56 | 56 | 一致 |  |
| p13_ternary | 11 | 11 | 一致 |  |
| p14_four_args | 57 | 57 | 一致 |  |
| p15_break_continue | 16 | 16 | 一致 |  |
| p16_array_1d | 6 | 6 | 一致 |  |
| p17_ptr_basic | 6 | 6 | 一致 |  |
| p18_neg_div | 68 | 68 | 一致 |  |
| p19_five_args | 15 | 15 | 一致 |  |
| p20_six_args | 138 | 138 | 一致 |  |
| p21_nested_5arg | 15 | 15 | 一致 |  |
| p22_stack_arg_expr | 12 | 12 | 一致 |  |
| p23_eight_args | 4 | 4 | 一致 |  |
| p24_two_calls_5arg | 165 | 165 | 一致 |  |
| p25_global_rw | 42 | 42 | 一致 |  |
| p26_global_bss | 7 | 7 | 一致 |  |
| p27_global_shared | 3 | 3 | 一致 |  |
| p28_global_in_cond | 45 | 45 | 一致 |  |
| p29_printf_plain | 0 | 0 | 一致 |  |
| p30_printf_int | 0 | 0 | 一致 |  |
| p31_printf_two_args | 0 | 0 | 一致 |  |
| p32_putchar | 0 | 0 | 一致 |  |
| p33_puts | 0 | 0 | 一致 |  |
| p34_printf_with_recursion | 0 | 0 | 一致 |  |
| p35_printf_with_loop | 0 | 0 | 一致 |  |
| p36_printf_with_global | 0 | 0 | 一致 |  |

合计：36/36 一致。

## 后端实现要点

- 目标：Windows x64 ABI（MSYS2 ucrt64 gcc 16.1.0 实测），Intel 语法，`.intel_syntax noprefix`。
- 整型参数：前 4 个走 rcx / rdx / r8 / r9（32 位名 ecx/edx/r8d/r9d），**第 5 个起走栈**，被调方从 `[rbp+48]`、`[rbp+56]` … 读；返回值 eax。
- 调用方把栈上参数写到 `[rsp+32]`、`[rsp+40]` …：`call` 时 rsp 指向影子空间，32 字节影子空间占 `[rsp, rsp+32)`，所以第一个栈参数必须**跳过影子空间**。写成 `[rsp+0]` 会踩掉影子空间，被调方读到垃圾（实测 p19 返回 109420074 而不是 15）。
- 出参区固定在帧底，大小 = 32 + 8×(本函数内最大栈参数个数)，于是 `call` 处 rsp 天然对齐，不需要每次 sub/add rsp。
- 变量一律放栈上，每个标量 8 字节槽、按 32 位存取；**不做寄存器分配**。指针按 64 位存取（`R64` 表做寄存器名提升），`mov eax, QWORD PTR [...]` 是汇编错误，指针必须走 rax。
- 栈帧 `push rbp; mov rbp, rsp; sub rsp, align16(locals + 出参区)`。
- 除法/取模：`cdq` + `idiv`（被除数符号扩展到 edx:eax），取模取 edx。
- 比较：`cmp` + `setcc` + `movzx`，结果按 C 语义是 int 0/1。
- 分支：IR 里 `ifFalse x goto L` / `if x goto L` 直接用 `test eax, eax` + `je` / `jne`；`&&` `||` 在 IR 里已是独立运算，按“非零即真”合成（IR 的操作数都已求值完，短路副作用不存在）。
- 汇编注释一律用 `#`，**不能用 `;`**：GAS 的 x86 汇编里 `;` 是语句分隔符，写成 `;` 会报 `junk at end of line`（实测，首轮 18 个程序全挂在这上面）。
- 注释统一转 ASCII：`.s` 文件与终端/编辑器编码不一致时，中文注释会让报错行号对不上。
- **没有生成 `.seh_*` 展开指令**：实测本后端生成的纯计算函数不需要它也能正常汇编、链接、运行；代价是异常/栈回溯信息缺失，一旦涉及 SEH 异常或调试器展开栈帧会拿不到正确的调用栈（这一点是推测，未实测异常路径）。

## 明确砍掉的能力（不做，不是没测）

| 能力 | 现状 | 说明 |
| --- | --- | --- |
| 浮点（float/double） | 不支持 | IR 里浮点常量是 `1.5` 形态，后端只处理 32 位整数；碰到会报错退出 |
| 字符串字面量 | 不支持 | IR 里带引号的操作数直接报错 |
| 库函数（printf/scanf/malloc 等） | 不支持 | 调用点显式报错 |
| 结构体 / 成员访问 | 不支持 | IR 生成 `base.field` 这种名字，无内存布局，后端取不到槽位会报错 |
| 指针算术（p+1 的缩放） | 不支持 | IR 里 `p + 1` 就是普通加法，没有 ×元素大小；只有 `&x` / `*p` 这种不涉及缩放的情形可用 |
| 数组 | 仅一维、元素按 int(4)/char(1)/long(8) 取宽度 | 多维数组 IR 会按 `store a[i]` 形状生成，后端把它当一维处理，**结果不可信**；未验证 |
| 全局变量 | 不支持 | `ir.py` 顶层只遍历函数，全局声明不进 IR。后端**拒绝**引用全局变量，因为分配栈槽会读到未初始化的栈内存、静默算错（实测确实会静默算错，已改为报错） |
| switch / goto / 标签 | 未验证 | 代码路径已写（标签统一加前缀并清洗非法字符），但 `switch` 的 IR 用 `case_<tmp>_<label>` 命名，是否稳定未测 |
| 优化后 IR | 可选 | `--opt` 走常量折叠 + 死代码消除；报告里的数字用的是未优化 IR（更保险） |

## 不支持时是明确报错，不是静默算错

这几项都实测确认会抛 `CodegenError`（带原因），而不是生成一份能汇编、能运行、但结果是垃圾的汇编：

| 输入 | 报错内容 |
| --- | --- |
| `double x = 1.5;` | 浮点常量 `1.5` —— 浮点后端不支持 |
| `printf("hi")` | 库函数 printf() 需要 CRT，后端不支持 |
| `char *s = "abc";` | 字符串字面量 —— 字符串/库函数不支持 |
| `int g = 7; ... return g;` | 引用了全局变量 g —— 全局变量不进 IR |
| `p.x = 1` | 结构体成员访问 `p.x` —— 无内存布局，不支持 |
| `int a[2][3]` | 对非数组/非指针 `t1` 做下标写 |

早期版本把 `.` 也算作合法标识符字符，于是 `1.5` 被当成“名为 1.5 的变量”、`p.x` 被当成独立变量 `p_x`，两者都能汇编通过然后读到未初始化栈内存——**静默算错**。现在 `_is_name` 只允许 `[A-Za-z0-9_]`，并在 `check_operand` 里显式区分浮点/成员/越界名字。

## 不确定 / 待实测的地方

- `.seh_*` 省略后能否长期稳定：本机纯计算函数实测可以；如果以后接入异常处理或需要栈回溯，必须补 `.seh_proc/.seh_pushreg/.seh_setframe/.seh_endprologue/.seh_endproc`。**这是推测，未验证异常路径。**
- 影子空间：本后端把 32 字节留在帧底（`[rsp, rsp+32)`），被调函数写自己的 home area 不会踩到本地变量；这一结论基于 ABI 文档 + 实测未出错，没有用调试器逐字节验证。
- 负数除法/取模按 C99 截断语义（`cdq`+`idiv` 天然如此），p18 用来对照 gcc。

## 复现命令

```
python tools/codegen.py examples/ir1.c -o out.s
python tools/asm_run.py out.s
python tools/codegen.py --selftest        # 重跑全部对照并重写本报告
```
