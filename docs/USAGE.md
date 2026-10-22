# csub 使用手册

> 两种读者：**你自己用**（第 1-3 节）、**给别人用**（第 4-6 节）。
>
> 最后更新：2026-10-22（v1.0）

---

# 第一部分：你自己用

## 1. 最快上手（3 条命令）

```bash
cd E:\dsh\csub

# 1) 查一个 C 文件有没有问题
python csub.py check 你的作业.c

# 2) 带源码上下文看（推荐，能直接看到问题在哪一行）
python csub.py report 你的作业.c

# 3) 确认工具本身是好的
python tests/run_tests.py
```

**`report` 的输出长这样：**

```
[WARNING W11] 第 16 行: 把整数（int）赋给指针 'b[...]'：这不是一个有效的地址
   14 |     for(int i=0;i<n;i++){
   15 |         b[i] = i;
>> 16 |         ...
      ^
   提示: 要表示空指针请写 0 或 NULL；其他地址必须来自 取地址 &x / 数组名 / malloc

[ERROR E2] 第 17 行: 使用了未初始化的变量 'a'
   提示: 它声明在 9:10，但没有赋初值

共 2 条：1 个错误，1 个警告。
```

## 2. 五个子命令

| 命令 | 用途 | 什么时候用 |
|---|---|---|
| `check` | 只列诊断 | 快速过一遍 |
| `report` | 诊断 + 源码上下文 + 光标 | **日常最常用** |
| `tokens` | 词法切分 | 教编译原理 / 排查"为什么这个字串解析不了" |
| `ast` | 抽象语法树 | 同上 |
| `ir` | 三地址码 + 优化前后对比 | 教学演示 / 排查后端问题 |

```bash
python csub.py tokens 程序.c
python csub.py ast    程序.c
python csub.py ir     程序.c        # 还能看到 CFG 和活跃变量
```

## 3. 真正编译运行

```bash
# 生成 x86-64 汇编
python tools/codegen.py 程序.c -o out.s

# 汇编 + 链接 + 运行（打印退出码）
python tools/asm_run.py out.s

# 保留可执行文件
python tools/asm_run.py out.s --keep

# 用优化后的 IR 生成（默认用未优化的）
python tools/codegen.py 程序.c -o out.s --opt
```

**能跑什么、不能跑什么**：见
[docs/CAPABILITY_BOUNDARY.md](CAPABILITY_BOUNDARY.md) 第三节。

> 后端不支持数组传参、全局数组、多维数组、函数返回结构体。
> 但**查错（前端）完全不受影响**——两套能力是分开的。

## 3.1 六套验证怎么跑

```bash
python tests/run_tests.py           # 规则正确性    176 条
python tools/err_bench.py           # 错误检出      42 用例
python tools/student_eval.py        # 真实样本      42 份
python tools/mutation_test.py       # 变异测试      79 个变异
python tools/ir_verify.py           # IR vs gcc     23 程序
python tools/codegen.py --selftest  # 后端自测      60 程序
python tools/probe_limits.py        # 能力边界探针
```

**改任何代码之后，六套全绿才算改对。**
任何一项退化都不应该合并。

## 3.2 加一个新诊断规则的标准流程

1. 从真实代码里**找到一个具体案例**（不要凭想象）
2. 写正向用例（该报）+ **至少两个反向用例**（不该报）
3. 实现规则，确认反向用例不报
4. 跑 `probe_limits.py` 检查有没有把语法支持弄坏
5. 六套验证全绿
6. 提交信息里写清楚**是哪个样本撞出来的**

> 反例：E16 早期只有正向用例，误报了
> `scanf("%20s", a)` 这个正确写法，一周后在真实代码撞出来。
> **反向用例比正向重要。**

## 3.3 加一个新样本的标准流程

```bash
# 1) 放到 samples/student/，命名 stu_NN_主题.c
# 2) 文件头写清楚来源、场景、已知问题（老师的批注逐条抄进去）
# 3) 跑一次看诊断
python csub.py check samples/student/stu_NN_xxx.c

# 4) 判断每条诊断是真的还是误报
#    误报 → 修规则（这是最有价值的）
#    漏报 → 对照老师的批注看能不能做
# 5) 评估全库
python tools/student_eval.py
```

---

# 第二部分：给别人用

## 4. 交付什么

**只要给这一个文件夹就行**：`E:\dsh\csub`

```
csub/
├── README.md              ← 对方先看这个
├── csub.py                ← 入口
├── src/                   ← 编译器源码
├── tools/                 ← 工具 + 六套验证
├── tests/                 ← 测试
├── samples/               ← 145 个 C 样本
├── docs/                  ← 文档（三份必读）
└── scripts/
```

**大小约 1.4 MB**（不含 `.git`），U 盘 / 微信 / 邮件都能发。

## 5. 对方需要什么环境

| 要求 | 说明 |
|---|---|
| **Python 3.8+** | 必需 |
| **gcc（MinGW）** | 只有跑验证/汇编链接时需要 |
| **Windows** | 后端目标是 x86-64 Windows ABI |

**纯查错（`check` / `report` / `ir`）只需要 Python，不需要 gcc。**

## 6. 给对方的说明（可以直接复制这段话发过去）

> 这是一个 C 语言查错工具，专门抓「能编译但会出事」的代码。
>
> **用法**：
> ```
> cd csub
> python csub.py report 你的作业.c
> ```
> 它会在问题行下面打光标，用中文告诉你哪里可能不对。
>
> **能查**：类型混淆、未初始化、指针误用、格式串写错、
> 漏 include、死循环、栈溢出风险、malloc 大小不足等 39 条规则。
>
> **查不到**：算法思路错、内存泄漏、运行时越界（长度来自输入）。
> 老师批改的错误里它大概能查出一半左右。
>
> **重要**：它的诊断是**建议**，不是判决。
> 如果它报了一个你觉得对的代码，那是它的 bug，告诉作者。
>
> 详细能力边界见 `docs/CAPABILITY_BOUNDARY.md`。

## 7. 交付前你要检查什么

```bash
cd E:\dsh\csub

# 1) 六套验证全绿
python tests/run_tests.py && python tools/err_bench.py && python tools/student_eval.py

# 2) 清掉临时文件
git status --short          # 应该为空

# 3) 确认没有敏感信息
findstr /S /I "ghp_ token" *.py *.md

# 4) 确认文档是最新的
type docs\CAPABILITY_BOUNDARY.md | more
```

## 8. 别人拿到后可能问的问题

### Q: 为什么我的代码它什么都不报？

可能原因：
1. 你的代码确实是对的（42 份样本里 9 份是零诊断且正确）
2. 错误属于「查不到的五类」（算法逻辑、内存泄漏等）
3. 用了不支持的语法（宽字符 `L"..."`）

**先看 `docs/CAPABILITY_BOUNDARY.md` 第五、六节。**

### Q: 它报的那条我觉得不对，怎么办？

**那很可能是工具的 bug。** 把代码片段发回来，
作者会加进 `samples/student/` 做回归测试。

### Q: 能替代 gcc 编译吗？

不能。它的后端只支持 C 的一个子集，
不支持数组传参、全局数组、多维数组、函数返回结构体。
**它是查错工具，不是编译器替代品。**

### Q: 能查算法错吗？

不能。例如：
```c
q = q % j;      // 应该是 q = q / j
```
每个字符都对，只有「这道题想干什么」是错的。
**任何静态分析工具都做不到。**

### Q: 为什么只查出一半的老师标注错误？

因为另一半是「算法意图」类错误。
21% 是这条技术路线的真实上限，不是实现偷懒。

---

# 第三部分：常见场景

## 9. 教学演示（编译原理课）

```bash
# 展示一个简单程序的完整编译过程
python csub.py tokens demo.c    # 词法：切成 token
python csub.py ast    demo.c    # 语法：语法树
python csub.py ir     demo.c    # 中间代码：三地址码 + 优化
python tools/codegen.py demo.c -o demo.s   # 后端：x86-64 汇编
```

`ir` 的输出包含：
- 优化前后对比（常量折叠、死代码消除）
- 基本块划分（CFG）
- 每个块的 live_in / live_out（活跃变量分析）

## 10. 批量检查一堆作业

```powershell
# PowerShell：检查目录下所有 .c
Get-ChildItem *.c | ForEach-Object {
    $r = python csub.py check $_.Name 2>&1
    if ($r -match "\[ERROR") { "$($_.Name): 有错误" }
}
```

## 11. 在 CI 里用

```yaml
# .github/workflows/check.yml 示例
- name: csub check
  run: |
    python csub.py check src/*.c
    python tests/run_tests.py
```

> 注意：`csub.py check` 有 ERROR 时退出码非 0，
> 可以直接作为 CI 的门禁。警告不会导致非 0。

---

# 附录：目录速查

| 想找什么 | 去哪 |
|---|---|
| 诊断规则实现 | `src/semantic.py` |
| 语法解析 | `src/parser.py` |
| 词法 | `src/lexer.py` |
| 预处理 | `src/preprocess.py` |
| 中间代码 | `src/ir.py` |
| 代码生成 | `tools/codegen.py` |
| 测试用例 | `tests/cases/cases_extra.py` |
| 真实样本 | `samples/student/` |
| 能力边界 | `docs/CAPABILITY_BOUNDARY.md` |
| 已知限制 | `docs/KNOWN_LIMITATIONS.md` |
| 后端实现细节 | `docs/CODEGEN_PLAN.md` |
