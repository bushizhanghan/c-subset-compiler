# IR 到 x86-64 汇编：可行路径调研

> 本文只做调研，不改任何 `src/` 代码。所有结论都基于当前代码实际能生成的 IR，
> 每条都标注了证据位置（`src/ir.py` 行号 / `examples/` 文件名 / 实测命令）。
> 读代码得出的结论与"推测"分开写，推测明确标 **[推测]**。

调研对象：`src/ir.py` 生成的三地址码 IR。
工作目录：`E:\dsh\work\csub`。gcc 版本：16.1.0（MSYS2 ucrt64）。

---

## 一、结论先说

**可以开始做，但要先把范围砍到很小。**

现在就能做的是一个**只支持整数、单文件、无全局变量、无库函数**的最小后端：
约 10 条 IR 指令 → 约 20 条 x86-64 指令，能跑通
`int add(int a,int b){return a+b;} int main(){return add(2,3);}`，
并且能和 gcc 的汇编逐条对比。这件事不依赖任何新能力，是纯增量。

但下面这些东西**现在做不了**，因为它们缺的不是汇编器，是汇编之前的语义信息：

| 想做 | 缺什么 | 缺在哪 |
|---|---|---|
| 数组 `arr[i]` 寻址 | 数组的**元素宽度** | `src/ir.py:322` 的 `load` 只给 `(base, idx)`，没有元素类型 |
| 指针解引用 `*p` | 同上 | `src/ir.py:291` 的 `deref` 只有变量名 |
| 结构体成员 `s.a` | 成员的**偏移量** | `src/ir.py:329` 直接返回字符串 `f"{base}.{name}"`，没有偏移计算 |
| 浮点 | 一条浮点指令都没有 | IR 里 `+` 同时服务 int 和 float，后端无法区分 |
| `printf` | 调用约定 + 字符串常量池 | IR 的 `call` 把实参拼成字符串文本（`src/ir.py:318`），没有参数类型 |
| `sizeof` 的真实值 | 结构体/数组的布局 | `src/ir.py:277` 直接返回常量字符串 `"4"` |

核心问题可以一句话说完：**当前 IR 是无类型的（untyped）**。
它把 `a + b` 记成 `dst = a1 + a2`，不记录 a1/a2/dst 的类型和宽度。
整数后端能凑合（全按 32 位 `int` 处理），但只要碰到指针、数组、结构体、浮点，
就必须知道宽度和偏移——而这些信息现在在 IR 里被丢掉了。

所以本报告的迭代顺序（第七节）是：**先做不依赖类型的那一半，把"能跑通"建立起来；
类型信息作为第二阶段单独补，不要试图一次性做完整后端。**

---

## 二、当前 IR 指令集（全量枚举）

枚举方法：读 `src/ir.py` 的 `Instr` 定义与全部 `self.emit(...)` 调用点
（`src/ir.py` 共 44 处 `emit`），并用 `python csub.py ir <file>` 实测各语法构造的产出。

### 2.1 `Instr` 的字段（`src/ir.py:26-34`）

```python
op: str                 # = | + | - | * | / | % | call | return | ...
dst: Optional[str] = None
a1: Optional[str] = None
a2: Optional[str] = None
extra: str = ""
line: int = 0
```

**注意：没有任何类型字段、宽度字段、符号表引用。** 这是后文所有困难的根源。

### 2.2 指令全表

`src/ir.py` 实际会 emit 的操作共 **27 种**：

| op | 形式 | 文本渲染（`__str__`） | emit 位置 |
|---|---|---|---|
| `=` | `dst = a1` | `a = t1` | 256, 301, 295 |
| `+` `-` `*` `/` `%` | `dst = a1 op a2` | `t = b + t0` | 273 |
| `<` `>` `<=` `>=` | `dst = a1 op a2` | `t = i < 5` | 273 |
| `==` `!=` | `dst = a1 op a2` | `t = i == 3` | 273, 241 |
| `&&` `\|\|` | `dst = a1 op a2` | `t = a && b` | 273 |
| `&` `\|` `^` | `dst = a1 op a2` | `t = n & 1` | 273 |
| `<<` `>>` | `dst = a1 op a2` | `t = n << 2` | 273 |
| `neg` | `dst = -a1` | `t = -n` | 282 |
| `not` | `dst = !a1` | `t = !n` | 289 |
| `return` | `return [a1]` | `return c` | 180, 182 |
| `call` | `dst = call f(a, b)` / `call f(a,b)` | `t = call add(2, 3)` | 318 |
| `if` | `ifFalse a1 goto extra` | `ifFalse t1 goto L2` | 120, 135, 168 |
| `ifTrue` | `if a1 goto extra` | `if t0 goto L1` | 155, 242 |
| `goto` | `goto extra` | `goto L1` | 124, 139, 175, 190, 193, 243, 204 |
| `label` | `extra:` | `L1:` / `func main:` / `case_1_L2:` / `skip:` | 100, 102, 125, 129, 133, 140, 145, 149, 156, 165, 172, 176, 198, 248, 252, 202 |
| `load` | `dst = a1[a2]` | `t1 = arr[0]` | 326 |
| `store` | `extra[a1] = a2` | `arr[0] = 7` / `p[0] = 9` | 306, 310 |
| `addr` | `dst = &a1` | `t1 = &a` | 293 |
| `deref` | `dst = *a1` | `t3 = *p` | 291 |
| `param` | `param a1` | `param x` | **`IRGen` 从不产生**（`tools/ir_exec.py:879` 保留兼容） |
| `,`（逗号表达式） | `dst = a1 , a2` | 见下 | 273（`gen_expr` 透传 `BinOp`） |

最后一项要单独说：`src/ir.py:269-274` 对**所有** `BinOp` 一律 `emit(e.op, ...)`，
而 parser 会把逗号表达式也做成 `BinOp(op=",")`（`src/parser.py:952-962`）。
所以 `,` 会以二元运算的形式进入 IR。实测确认（`docs/.probe_incr.c`，已删除）：

```
d = a, e = c          ->    d = a          # 注意：这里其实是 Assign 的返回值
                            e = c
```

`ir_exec.py` 的 `BIN_OPS` 里没有 `,`，所以逗号表达式在 IR 层就是不可执行的
（解释器会报"不支持的指令"）。**这是 IR 现有缺陷，与汇编无关，但会影响后端测试集的选择。**

### 2.3 三种"做了一半"的语法：文本可读、语义不可执行

这三类不是崩溃，是**安静地产出不可执行 IR**。它们现在不影响 `ir_verify.py` 的 23/23，
因为那 23 个程序不含这些构造；但一旦要做汇编后端，它们会直接变成后端的错。

| 源码构造 | 实际 IR | 问题 | 位置 |
|---|---|---|---|
| 三目 `a ? b : c` | `c = ?` | `gen_expr` 没有 `Ternary` 分支，返回字面量 `"?"` | `src/ir.py:259-333` 无 Ternary 处理；`ir.py:333` 的兜底 `return "?"` |
| 初始化列表 `int a[] = {1,2}` | `a = ?` | `InitList` 无分支 | `src/ir.py:259-333` 无 InitList 处理 |
| 结构体成员 `s.a + s.b` | `t1 = s.a + s.b` | 成员被拼成**点号变量名**，不是地址计算 | `src/ir.py:329-331` |
| 复合赋值 `a += 2` | `a = 2` | 丢掉左值，退化成常量赋值 | `src/ir.py:298-312`（`Assign` 只认 `op == "="` 之外的都走 `gen_expr(value)`） |
| 自增 `a++` / `--a` | `t1 = a` / `t2 = a` | 只读值，不写回 | `src/parser.py:1064-1066` 产生 `UnOp("post++"/"--")`；`src/ir.py:294-295` 的 `else` 分支只做 `=` |

实测证据（用 `python csub.py ir` 跑临时文件，已删除）：

```c
int main(){ int a=3; a += 2; int b = a++; int c = --a; ... }
```
```
a = 3
a = 2          <- a += 2 变成 a = 2（丢掉了 a 本身）
t1 = a
b = t1         <- a++ 只读不写
t2 = a
c = t2         <- --a 只读不写
```

**对汇编后端的含义**：`a += 2` 这种最常见的一行代码，IR 层就是错的。
如果直接基于当前 IR 写后端，后端会把"IR 已经错了"误判成"后端写错了"。
所以**第一步必须先修 IR 生成（这些是 IR bug，不是后端工作），再写后端**。
这一点必须在计划里讲清楚，否则调试时会归因错误。

---

## 三、哪些已经能一对一映射到 x86-64

"一对一"的判据：IR 指令的左值/右值都是**已知道宽度的整数**，且不涉及内存地址运算。
当前 IR 只有整数路径满足这个条件（因为所有变量都按 32 位 `int` 处理）。

### 3.1 可以直接映射的（15 条 IR 指令 → 16 条 x86-64 指令）

| IR | x86-64（AT&T） | 说明 |
|---|---|---|
| `t = a + b` | `movl a, %eax` / `addl b, %eax` / `movl %eax, t` | 32 位即可 |
| `t = a - b` | `movl a,%eax` / `subl b,%eax` | |
| `t = a * b` | `imull b, %eax`（有符号） | 注意 `mul` 是无符号 |
| `t = a / b` | `movl a,%eax` / `cltd` / `idivl b` | **需要 `cdq`/`cltd` 做符号扩展**，不是单纯一条 |
| `t = a % b` | `movl a,%eax` / `cltd` / `idivl b` / `movl %edx, t` | 余数在 `%edx` |
| `t = a < b` 等 6 种 | `cmpl b, %eax` / `setl %al` / `movzbl %al, %eax` | C 的比较结果是 0/1，必须 `set` + `movzx` |
| `t = a == b` | 同上，`sete` | |
| `t = a && b` | 需要短路分支（约 8 条指令） | **不是一对一**，C 的 `&&` 有短路语义 |
| `t = a \|\| b` | 同上 | 同上 |
| `t = a & b` / `\|` / `^` | `andl` / `orl` / `xorl` | 一对一 |
| `t = a << b` | `movl b,%ecx` / `sall %cl, %eax` | 移位量必须在 `%cl` |
| `t = a >> b` | `sarl %cl, %eax` | 算术右移（IR 语义是有符号，见 `ir_exec.py:549-551`） |
| `t = -a` | `negl %eax` | 一对一 |
| `t = !a` | 约 5 条：`testl %eax,%eax` / `sete %al` / `movzbl` | 近似一对一 |
| `t = a`（拷贝） | `movl a, t` | 一对一 |
| `t = 5`（常量） | `movl $5, t` | 一对一 |
| `ifFalse t goto L` | `cmpl $0, t` / `je L` | 一对一 |
| `if t goto L` | `cmpl $0, t` / `jne L` | 一对一 |
| `goto L` | `jmp L` | 一对一 |
| `L:` | `L:` | 一对一 |
| `return t` | `movl t, %eax` / `leave`/`pop` / `ret` | 返回值约定在 `%eax` |
| `call f(args)` | `movl $3,%edx` / `movl $2,%ecx` / `call f` | 见第四节：**这是唯一有约定要处理的** |

### 3.2 不能直接映射的（5 条）

| IR | 为什么不能 | 缺什么 |
|---|---|---|
| `load` `dst = base[idx]` | 需要 `base + idx*elemsize`，IR 没有元素宽度 | 元素类型/宽度 |
| `store` `base[idx] = v` | 同上 | 元素类型/宽度 |
| `deref` `dst = *p` | 需要知道解引用出几字节 | 指针指向的类型 |
| `addr` `dst = &x` | 需要知道 x 在栈帧哪个偏移 | **符号到栈偏移的映射**（后端自己算，见 5.2） |
| `call`（带实参） | 实参在 IR 里是**字符串文本** `"2, 3"`，不是结构化列表 | 调用约定（第五节） |

---

## 四、函数调用约定：现在 IR 给的信息不够

这是**最小可行目标里唯一真正需要设计的东西**，单独一节讲。

### 4.1 IR 现在给了什么

```python
# src/ir.py:314-320
if isinstance(e, Call):
    args = [self.gen_expr(a) for a in e.args]
    name = e.func.name if isinstance(e.func, Ident) else "?"
    t = self.newtmp()
    self.emit("call", dst=t, a1=name, extra=", ".join(args), line=e.line)
```

产出：`t2 = call add(2, 3)`。
`extra` 是 `"2, 3"` —— 一个**逗号连接的字符串**。
被调函数的形参名、个数、类型，IR 里一概没有。

实测（`docs/.probe_add.c`）：

```
func add:
t1 = a + b
return t1
end func add:
func main:
t2 = call add(2, 3)
return t2
end func main:
```

形参 `a`、`b` 在 `add` 的函数体里以**裸名字**出现，没有任何"我是第 0 个形参"的标记。
这正是 `tools/ir_exec.py` 必须额外传 `param_map` 的原因（`tools/ir_exec.py:347-354, 450-470`）：

> IR 文本本身不记录形参名（`func add:` 后面直接就是函数体），而形参绑定是
> 正确执行的必要条件，所以调用方要把 AST 里的形参名传进来。

**这句话对汇编后端同样成立，而且更严重**：解释执行器可以用 `param_map` 兜底，
汇编器必须知道"第 0 个实参进 `%ecx`，第 1 个进 `%edx`"，否则连 `movl` 的目标寄存器都选不了。

### 4.2 结论

写后端时，**形参表不能从 IR 文本解析，必须从 AST 带过来**。
可复用的现成实现：`tools/ir_exec.py:450` 的 `param_map_from_ast()`。
（注意它是 tools/ 下的，按任务约束不能改；但可以参照它写一个后端的等价物，
或者让后端直接吃 AST + IR 两侧。）

### 4.3 x86-64 System V 与 Windows x64 的差异（本机是 Windows）

本机 gcc 16.1.0 是 MSYS2 ucrt64 构建，目标平台是 **Windows x64 ABI**（实测 `.seh_proc` 等指令）。
两套约定的整数参数寄存器完全不同：

| ABI | 参数寄存器（按序） | 返回值 | 栈对齐 |
|---|---|---|---|
| System V AMD64（Linux/macOS） | `%rdi %rsi %rdx %rcx %r8 %r9` | `%rax` | 16 字节 |
| **Windows x64**（本机） | `%rcx %rdx %r8 %r9` | `%rax` | 16 字节 |

本机 gcc 对 `add(2,3)` 的实际产出（`gcc -S -O0 docs/.probe_add.c`）：

```asm
add:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	%ecx, 16(%rbp)      # 第 0 个形参 <- %ecx
	movl	%edx, 24(%rbp)      # 第 1 个形参 <- %edx
	movl	16(%rbp), %edx
	movl	24(%rbp), %eax
	addl	%edx, %eax
	popq	%rbp
	ret
main:
	pushq	%rbp
	movq	%rsp, %rbp
	subq	$32, %rsp
	call	__main             # MinGW 运行时辅助，生成端不需要
	movl	$3, %edx           # 第 1 个实参
	movl	$2, %ecx           # 第 0 个实参
	call	add
	addq	$32, %rsp
	popq	%rbp
	ret
```

**这意味着最小后端必须选一个 ABI**。选 Windows x64 的理由：本机 gcc 就是这个，
汇编后能直接和它对比。代价：换到 Linux 上要改寄存器表。
（如果选 System V，在本机反而无法用 gcc 做对照。）

还有一个细节：`call __main` 是 MinGW 运行时的，自己生成的汇编**不应该有它**
（不链接 libgcc 就不需要），但它会出现在 gcc 的对照输出里，对比时要排除。

---

## 五、缺的能力（按缺口大小排序）

### 5.1 栈帧管理 —— 后端自己算，不依赖 IR 改动

IR 是**无栈帧**的：所有变量都是裸名字（`a`、`b`、`t1`），
没有"这个变量在栈上哪个偏移"的概念。

好消息：这件事后端可以独立完成。做法是扫一遍函数体：

- 收集所有被**赋值**的名字（`=`、`+`、`load`、`call` 的 dst、`store` 的 base…）
- 排除临时变量 `tN`（可以放到寄存器，或干脆也放栈上，第一版建议全放栈）
- 每个变量分配一个 4 字节槽位，帧大小 = 槽位数 × 4，16 字节对齐

参考实现依据：`src/ir.py:554-571` 的 `_uses_defs()` 已经会算每个指令的 def 集合，
后端可以直接借用同样的逻辑（这是读代码得到的可行路径，不是推测）。

**要小心的两件事**：

1. **全局变量**。`int g = 7;` 在 IR 里和局部变量长得一模一样
   （实测 `docs/.probe_glob.c`：`t1 = g + h`，没有任何 global/local 区分）。
   所以最小后端必须**显式只支持单文件无全局变量**，或者自己区分顶层声明。
   前者简单，建议第一版这样做，并在错误信息里说清楚。

2. **`load`/`store` 的 base 是变量名**，需要 base 变量也有栈槽位——它可能有，
   因为 base 本身也是个被赋值的名字。这一点在第一版里是自洽的。

### 5.2 符号到栈偏移 —— 后端自己算

`addr dst = &x`（`src/ir.py:293`）要求知道 `x` 的栈偏移。
这需要 5.1 的槽位分配先完成。所以 `&` 是**栈帧之后**才能做的，不是第一版内容。

### 5.3 数组寻址 —— 缺元素宽度，IR 层信息不足

`src/ir.py:322-327`：

```python
if isinstance(e, Index):
    base = self.gen_expr(e.base)
    idx = self.gen_expr(e.idx)
    t = self.newtmp()
    self.emit("load", dst=t, a1=base, a2=idx, line=e.line)
```

产出 `t1 = arr[0]`。要变成汇编，需要 `arr + 0*4`（`int`）还是 `arr + 0*1`（`char`）？
IR 不知道。

而且实测发现**二维数组的 IR 是错的**（`docs/.probe_arr2.c`）：

```c
int a[3][4];  a[1][2] = 7;  int x = a[1][2];
```
```
t1 = a[1]        # 第一维：返回临时变量，不是地址
t1[2] = 7        # 第二维：把 t1 当成 base 名字
```

`a[1]` 在 C 里是 `int[4]`（退化成 `int*`），应该得到**地址** `a + 4`；
而 IR 把它当成一个"读出来的值"存进 `t1`，下一行又把 `t1` 当数组名用。
这是 IR 生成缺陷，不是后端问题。

**结论**：数组寻址必须**先补 IR 的类型/宽度信息，再写后端**。
顺序不能反。可行的最小补法：给 `load`/`store` 加一个元素宽度字段
（或在 `Instr` 上加 `ty` 字段），值从 `semantic.py` 已有的类型推导拿。
`semantic.py:775-852` 的 `type_of_chain()` 已经能沿 `a.b / p->b / arr[i]` 推出类型，
`type_of_chain` 返回的 `Type` 有 `base`（`"int"` / `"char"` / `"float"`…），
宽度可以从 `base` 查表得到。**这是现成的，不需要从零做类型推导。**

### 5.4 指针解引用 —— 同 5.3

`deref dst = *p`（`src/ir.py:291`）。需要知道 `*p` 是几字节。
同样缺宽度。`semantic.py:611-624` 已经有 `*` 的类型推导（返回 `sym.vtype.base`），
信息是有的，只是没传到 IR。

另外实测 `p = &arr[2]; *p = 9;`（`docs/.probe_ptr.c`）：

```
t1 = arr[2]      # 这是"读 arr[2]"，不是"取 arr[2] 的地址"
t2 = &t1         # 于是 & 作用在临时变量上
p = t2
p[0] = 9
```

`&arr[2]` 在 C 里是 `&(arr[2])`，IR 的 `addr` 作用域搞错了层级。
又一处 IR 缺陷。

### 5.5 结构体成员偏移 —— 缺得最多

`src/ir.py:329-331`：

```python
if isinstance(e, Member):
    base = self.gen_expr(e.base)
    return f"{base}.{e.name}"
```

直接返回一个**带点号的字符串**。实测：

```
t1 = s.a + s.b
```

这不是"偏移没算"，这是**成员访问完全没有被翻译成任何计算**。
`s.a` 在 IR 里是一个形如变量的名字。

要支持它，需要：结构体成员布局（偏移 + 对齐 + 总大小）。
`src/parser.py:446-485` 的 `_parse_struct_body()` 只记录 `[(Type, name)]` 列表，
**明确不做内存布局**（`src/parser.py:47-55` 的注释：「只记录成员名与类型，不做内存布局，
够语义分析用」）。

所以结构体需要**新写一个布局计算模块**。这是四类缺口里最大的一块：
偏移、对齐、嵌套结构体、数组成员、指针成员（8 字节宽，与 int 不同宽）、
按值传参（结构体大于 16 字节要走内存，Windows x64 ABI 的规则）。

**建议**：结构体放在最后，且第一版只支持"成员全是 int/指针、无嵌套"的扁平结构体。

### 5.6 库函数调用（printf 等）—— 缺调用约定 + 字符串常量池

IR 产出 `t1 = call printf("hello %d\n", 42)`（实测）。
两个问题：

1. **调用约定**：`printf` 是可变参数，Windows x64 ABI 要求可变参数函数有影子空间
   （`subq $32, %rsp` + 前 4 个寄存器各存一份）。这是**必须做的**，否则会崩。
2. **字符串常量池**：IR 把字符串字面量当操作数文本内联（`src/ir.py:263-264`）。
   汇编里必须放进 `.rodata` 然后 `leaq .LC0(%rip), %rcx`。
   IR 层连"这是第几个字符串常量"都没有，后端要自己收集去重。
3. **格式串与实参的类型对应**：`%d` 要 32 位、`%f` 要 double。
   IR 里 `42` 就是 `"42"`，不知道 printf 要它当几个字节传。

**结论**：库函数调用属于"能做但要单独一步"，且第一版建议只支持 `printf("%d", x)`
这种最窄形式，直接声明"格式串只认 `%d %s %c`，其余不支持"。

### 5.7 浮点 —— IR 里**有**浮点常量，但**没有**浮点指令

这个问题要精确回答，因为任务明确问了"现在 IR 里有浮点吗"。

**有浮点字面量，没有浮点运算指令。**

证据一：`src/ir.py:260-261`：

```python
if isinstance(e, Num):
    return repr(e.value) if e.is_float else str(e.value)
```

`is_float` 为真时走 `repr()`，产出 `1.5`、`2.0` 这样的浮点文本。

证据二：`src/parser.py:1105-1107` 会产出 `Num(is_float=True)`。

证据三：实测 `float a = 1.5; double b = 2.0 + a;`：

```
a = 1.5
t1 = 2.0 + a        # 运算符还是 "+"，与整数完全同形
b = t1
```

证据四（反证）：`src/ir.py:38-39` 的 `__str__` 分支里，
二元运算集合是 `("+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "&&", "||", "&", "|", "^", "<<", ">>")`——
**没有任何带 `f` 后缀的运算**（如 `addsd`、`mulsd`）。整数和浮点共用同一个 `+`。

证据五（后果）：`src/ir.py:407` 的 `const_fold` 只折叠 `("+", "-", "*", "/", "%")`，
且 `is_int()`（`src/ir.py:355-359`）用 `t.isdigit()` 判断，`"1.5"` 不匹配
→ **浮点常量折叠完全不做**。

所以浮点后端的缺口是：**需要区分 int 和 float 两套运算，需要 SSE 寄存器
（`%xmm0`-`%xmm7` 传参、`%xmm0` 返回），需要 `movsd`/`addsd`/`cvtsi2sd` 等指令族，
需要 int↔float 转换。** 这是和整数后端**平行的一套**，工作量约等于整数后端本身。

**结论**：浮点不进最小可行目标。明确写进"不做"清单。

---

## 六、最小可行目标

### 6.1 目标程序

```c
int add(int a, int b) { return a + b; }
int main() { return add(2, 3); }
```

### 6.2 csub 现在为它生成的 IR（实测）

```
func add:
t1 = a + b
return t1
end func add:
func main:
t2 = call add(2, 3)
return t2
end func main:
```

（这条 IR 是**正确的**——上面列出的 IR 缺陷都不在这个程序里，
所以它是合理的第一步目标。）

### 6.3 IR → asm 映射表（最小可行子集，10 条 IR 指令）

只支持整数、单文件、无全局变量、无数组、无指针、无结构体、无浮点、无库函数。

| # | IR 指令 | x86-64（Windows x64 ABI，AT&T） |
|---|---|---|
| 1 | `t = 5`（常量） | `movl $5, -N(%rbp)` |
| 2 | `t = a`（拷贝） | `movl -M(%rbp), %eax` / `movl %eax, -N(%rbp)` |
| 3 | `t = a + b` | `movl -M(%rbp), %eax` / `addl -K(%rbp), %eax` / `movl %eax, -N(%rbp)` |
| 4 | `t = a - b` | 同上，`subl` |
| 5 | `t = a * b` | 同上，`imull` |
| 6 | `t = a < b` / `==` / `<=` / `>` / `>=` / `!=` | `movl -M(%rbp),%eax` / `cmpl -K(%rbp),%eax` / `setl %al` / `movzbl %al,%eax` / `movl %eax,-N(%rbp)` |
| 7 | `ifFalse t goto L` | `cmpl $0, -N(%rbp)` / `je L` |
| 8 | `if t goto L` | `cmpl $0, -N(%rbp)` / `jne L` |
| 9 | `goto L` / `L:` | `jmp L` / `L:` |
| 10 | `t = call f(x, y)` | `movl y_slot, %edx` / `movl x_slot, %ecx` / `call f` / `movl %eax, t_slot` |
| 11 | `return t` | `movl t_slot, %eax` / `movq %rbp,%rsp` / `popq %rbp` / `ret` |
| 12 | 函数序言/尾声 | `pushq %rbp` / `movq %rsp,%rbp` / `subq $FSZ,%rsp` |

（11、12 不是 IR 指令，是每个函数必需的框架。所以**IR 侧 10 条，asm 侧 12 类模式**。）

### 6.4 目标程序的完整预期汇编（手写，作为后端的对照基准）

```asm
	.text
	.globl	add
add:
	pushq	%rbp
	movq	%rsp, %rbp
	subq	$16, %rsp
	movl	%ecx, -8(%rbp)        # a
	movl	%edx, -12(%rbp)       # b
	movl	-8(%rbp), %eax
	addl	-12(%rbp), %eax
	movl	%eax, -4(%rbp)        # t1
	movl	-4(%rbp), %eax
	jmp	.Lend_add
.Lend_add:
	movq	%rbp, %rsp
	popq	%rbp
	ret

	.globl	main
main:
	pushq	%rbp
	movq	%rsp, %rbp
	subq	$16, %rsp
	movl	$3, %edx
	movl	$2, %ecx
	call	add
	movl	%eax, -4(%rbp)        # t2
	movl	-4(%rbp), %eax
	movq	%rbp, %rsp
	popq	%rbp
	ret
```

（这段是**伪代码性质的预期产出**，用于说明形态；具体偏移取决于槽位分配策略。
gcc 的实际产出见第四节 4.3，两者语义等价、指令数不同。）

### 6.5 为什么这个子集是自洽的

- 不碰 `load`/`store`/`deref`/`addr` → 不需要元素宽度
- 不碰 `Member` → 不需要结构体布局
- 没有浮点字面量 → 不需要 SSE
- 没有 `printf` → 不需要影子空间、不需要 `.rodata`
- 没有全局变量 → 不需要 `.data`/`.bss` 段与符号表
- 参数个数 ≤ 4 → 不需要栈上传参（Windows x64 前 4 个整型参数走寄存器）

**每一条"不做"都对应第五节的一个缺口。** 这不是偷懒，是把缺口显式化。

### 6.6 可验证产出

```
gcc -S -O0 docs/expected_add.s          # 手写的预期汇编（6.4）
csub asm docs/probe_add.c -o out.s      # 待实现的后端（本任务不实现）
gcc out.s -o out && ./out ; echo $?     # 应为 5
diff <(gcc -S -O0 docs/probe_add.c) out.s   # 语义级对比，不要求逐字相同
```

---

## 七、建议的迭代顺序

每一步都给出**可验证产出**。顺序的原则：**先修 IR 的已知缺陷，再写后端**，
否则后端会把 IR 的错当自己的错。

### 阶段 0：修 IR 生成缺陷（前置，不是后端工作）

这五条是第二节列出的"安静出错"，**不修就无法写后端**：

| 要修 | 现状 | 验证 |
|---|---|---|
| 复合赋值 `a += 2` | IR 是 `a = 2`（丢左值） | `python csub.py ir` 应产出 `t = a + 2; a = t` |
| 自增/自减 `a++` | IR 只读不写 | 同上 |
| 三目 `a?b:c` | IR 是 `c = ?` | 应产出 `if/else` 跳转结构 |
| 初始化列表 `{1,2}` | IR 是 `a = ?` | 应产出逐元素 `store` |
| 逗号表达式 | IR 指令 `,` 不可执行 | `ir_exec.py` 的 `BIN_OPS` 应能执行 |

**这一步的产出**：`tools/ir_verify.py` 的用例集扩充到覆盖这 5 类，
且 23/23 的基础上新增用例全过。**先有裁判，再改代码。**

### 阶段 1：整数后端骨架（最小可行目标）

只做 6.3 那张表的 12 类模式。

- 输入：IR 文本 + AST 的 `param_map`（复用 `tools/ir_exec.py:450` 的思路）
- 输出：AT&T 语法 `.s` 文件
- 产出验证：
  1. `int add(int a,int b){return a+b;} int main(){return add(2,3);}` 汇编后 `gcc out.s -o out; ./out` 返回 5
  2. 与 gcc 的 `.s` 语义对比（指令序列等价，不逐字相同）
  3. 用 `tools/ir_verify.py` 的 23 个程序中**不含数组/指针/浮点/库函数**的那些做回归

**这一步做完，"csub 能生成可执行汇编"就是事实，不再是路线图。**

### 阶段 2：控制流与循环

`if/else` 的完整形态、`while`/`for`/`do-while` 的回边、`break`/`continue`。
IR 侧这些都已经是对的（`ir_cfg.c` 实测输出正确），所以这一步**只写后端，不改 IR**。

- 产出验证：`examples/ir_cfg.c` 的汇编版本运行结果与 gcc 一致

### 阶段 3：给 IR 加类型信息（为数组/指针铺路）

这是整个计划里**唯一需要改 `src/ir.py` 数据结构的步骤**：

- 给 `Instr` 加一个可选字段（如 `ty: str = ""`，存 `"int"`/`"char"`/`"ptr"`）
- `load`/`store` 记元素宽度，`deref` 记解引用宽度，`addr` 记目标宽度
- 数据来源：`semantic.py:775` 的 `type_of_chain()` 已经能推出类型，宽度查表即可
- **约束**：新字段必须默认空，不影响现有 23/23（否则说明改坏了）

- 产出验证：`examples/ir_arr.c` 的 `load` 带 `ty="int"`；`ir_verify.py` 仍 23/23

### 阶段 4：数组与指针寻址

- `load dst = base[idx]` → `movl (base, idx, 4), %eax`
- `store base[idx] = v` → `movl v, (base, idx, 4)`
- `deref dst = *p` → `movl (%rax), %eax`
- `addr dst = &x` → `leaq -N(%rbp), %rax`（依赖阶段 1 的槽位分配）
- 同时要修 5.3 / 5.4 发现的二维数组与 `&arr[2]` 的 IR 缺陷

- 产出验证：`examples/ir_arr.c`、`examples/t_ptr.c` 汇编后运行结果与 gcc 一致

### 阶段 5：结构体

- 新写一个布局模块：偏移、对齐、`sizeof`（替换 `src/ir.py:277` 的假 `"4"`）
- 第一版只支持"成员全为 int/指针"的扁平结构体
- `Member` 从"点号变量名"改成 `load(base + offset)`

- 产出验证：`examples/mutation_bank.c` 里的 `Table`（3 个 int + 24 个 int）能正确布局并运行

### 阶段 6：库函数与浮点（并列，都很大）

- 库函数：影子空间 + `.rodata` 字符串池 + 窄格式串支持
- 浮点：SSE 寄存器分配、`addsd`/`movsd`/`cvtsi2sd`、int↔float 转换

**这两个都不该仓促开始。** 浮点约等于把阶段 1-4 用另一套寄存器重做一遍；
库函数则需要先决定"支持到哪些函数"，否则范围无限膨胀。

---

## 八、现在就能做的 vs 需要先补能力的

### 8.1 现在就能做（零依赖）

| 项 | 依据 |
|---|---|
| 整数二元运算 17 种（`+ - * / % < > <= >= == != && \|\| & \| ^ << >>`） | 第三节 3.1 |
| `neg` / `not` / `=` / 常量 | 第三节 3.1 |
| `ifFalse` / `ifTrue` / `goto` / `label` | 第三节 3.1 |
| `return` | 第三节 3.1 |
| 函数序言/尾声、栈帧分配、符号到栈偏移 | 5.1 / 5.2，后端自己算 |
| `call`（整型参数 ≤ 4 个） | 4.3 选 Windows x64 ABI |
| 汇编文本输出、与 gcc 汇编的对比脚本 | 纯工程 |

### 8.2 需要先补能力（按依赖顺序）

| 项 | 前置依赖 |
|---|---|
| `load` / `store`（数组） | **阶段 0**（修 IR 缺陷）+ **阶段 3**（IR 加类型）+ 修二维数组 IR 缺陷 |
| `deref` / `addr`（指针） | 同上 + 阶段 1 的栈帧槽位 |
| 结构体成员 | **新写布局模块**（`src/parser.py` 明确不做布局）+ 阶段 3 |
| `sizeof` 真实值 | 结构体/数组布局 |
| 浮点 | 独立一套 SSE 后端 + IR 区分 int/float 运算 |
| `printf` 等库函数 | 调用约定 + 影子空间 + `.rodata` 字符串池 + 格式串类型对应 |
| 全局变量 | 段划分 + 符号表（IR 现在不区分 global/local） |
| 超过 4 个参数的函数 | 栈上传参规则 |
| `param` 指令 | 当前 `IRGen` 从不产生（`src/ir.py` 无 emit 点），无需处理 |

### 8.3 明确不做（本任务范围外）

- 优化：寄存器分配、指令选择、窥孔优化。第一版全放栈上，性能不重要，
  **能跑通 + 能和 gcc 对比**才重要。
- 完整的 Windows x64 ABI（浮点参数、结构体按值传参、异常展开 `.seh_*`）。
  gcc 会生成 `.seh_proc` 等指令，最小后端不需要——不链接 libgcc 就不需要展开信息。
- 链接与可执行文件生成。产出到 `.s` 为止，用 gcc 做汇编+链接（`gcc out.s -o out`）。
  这条要写进文档：**csub 不自己做汇编器和链接器，那是另一个大阶段。**

---

## 九、风险与不确定项

诚实记录本次调研**没能验证**的部分：

1. **`gcc out.s -o out` 能否链接我们自己生成的汇编**——未验证。
   预期可以（gcc 接受 AT&T `.s`），但如果有符号重定位问题（比如 label 命名），
   要到阶段 1 才会暴露。**[推测]**

2. **不生成 `.seh_*` 指令能否在 Windows 上正常运行**——未验证。
   gcc 生成的汇编带 `.seh_proc`/`.seh_endproc`，这是为了栈展开（异常/调试）。
   简单的 `ret` 路径不需要它，但如果调用的函数触发异常可能会有问题。**[推测]**
   阶段 1 的第一个可验证产出就是它。

3. **`call __main` 是否可以省略**——未验证。
   MinGW 的 gcc 给 `main` 生成 `call __main`，这是 libgcc 的运行时辅助。
   自己写的汇编不调用它，链接时**可能**会报未定义符号，也可能不会
   （取决于是否真的需要初始化）。**[推测]** 阶段 1 实测。

4. **阶段 3 给 `Instr` 加字段会不会破坏 `tools/ir_exec.py`**——未验证。
   `ir_exec.py` 自己定义了同名的 `Instr` dataclass（`tools/ir_exec.py:82-89`），
   与 `src/ir.py` 的 `Instr` 是**两个不同类**，文本层解耦。
   所以理论上改 `src/ir.py` 的字段不影响 `ir_exec.py`，
   但 `__str__` 的输出格式如果变了，`ir_exec.py` 的正则可能解析不了。**[推测]**
   这是阶段 3 的主要风险点，动手前要先跑 `tools/ir_verify.py` 建立基线。

5. **`semantic.py:type_of_chain()` 的类型推导精度是否够做数组元素宽度**——未验证。
   它能处理 `a.b / p->b / arr[i]` 链，但对 `(char*)x`、函数返回值等可能推不出。
   阶段 3 需要实测它的覆盖率。**[推测]**

---

## 十、一句话总结

**能开始，从"整数 + 单文件 + 无全局 + 无数组指针结构体浮点库函数"切进去，
先修 5 处 IR 生成缺陷建立裁判，再写 12 类 asm 模式跑通 `add(2,3)`。
之后每一步都以前一步的可执行产物为凭据，不要一次性冲完整后端。**

---

## 附：本报告的验证方式

所有结论可用以下命令复核：

```bash
cd E:\dsh\work\csub

# 1. IR 指令集：各语法构造的实际产出
python csub.py ir examples/ir1.c        # 算术 + 常量折叠
python csub.py ir examples/ir_arr.c     # load/store
python csub.py ir examples/ir_cfg.c     # if/while/break/goto/label
python csub.py ir examples/t_ptr.c      # addr
python csub.py ir examples/lab_madd.c   # 浮点 + 数组 + 循环

# 2. gcc 对本机 ABI 的实际产出（第四节 4.3）
printf 'int add(int a,int b){return a+b;}\nint main(){return add(2,3);}\n' > /tmp/add.c
gcc -S -O0 /tmp/add.c && cat /tmp/add.s

# 3. IR 语义基线（改动前后都必须跑）
python tools/ir_verify.py

# 4. IR 解析对形参表的依赖（第四节 4.1）
python tools/ir_exec.py examples/ir1.c
```

调研过程中创建的临时文件（`docs/.probe_*.c`、`docs/.probe_add.s`）已全部删除，
`docs/` 目录现在只有 `PROGRESS.md` 与本文件。
