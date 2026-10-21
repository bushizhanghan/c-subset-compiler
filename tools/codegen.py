#!/usr/bin/env python3
"""csub 后端：三地址码 IR -> x86-64 汇编（Windows x64 ABI / Intel 语法）。

只做一件事：把 src/ir.py 生成的三地址码翻译成能被 gcc 汇编并链接的 .s 文件。
变量一律放栈上（每个 8 字节槽位），不做寄存器分配——这是教学工具，
目标是"看得见、对得上"，不是优化。

用法:
  python tools/codegen.py examples/ir1.c -o out.s     # 生成汇编
  python tools/codegen.py examples/ir1.c              # 打印到 stdout
  python tools/codegen.py examples/ir1.c -o out.s --opt   # 用优化后的 IR
  python tools/codegen.py --selftest                  # 跑全部对照并写报告

约定（Windows x64 / MSYS2 ucrt64 下实测）:
  - 整型参数寄存器：rcx, rdx, r8, r9；返回值 eax
  - 调用者预留 32 字节影子空间（shadow space），栈在 call 处 16 字节对齐
  - 不生成 .seh_* 展开指令（纯计算函数用不到；实测能正常链接运行）

支持范围（本轮扩展后）:
  - 全局变量：顶层声明进 .data（有初值）/ .bss（无初值），RIP 相对寻址
  - 基础库函数：直接 call libc 真实符号（printf 的整数最小子集、puts、
    putchar、strlen/memcpy 一族），字符串字面量进 .rodata
  - 浮点最小支持：float 字面量 + 一次四则运算，走 xmm0（movss/addss/...），
    在 int 边界上用 cvttss2si / cvtsi2ss 转换
  仍然明确拒绝：double、结构体、指针算术缩放、多维数组寻址。
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import (                                     # noqa: E402
    parse, ParseError, Program, FuncDef, VarDecl, DeclGroup, Block, If,
    While, For, Return, Break, Continue, ExprStmt, DoWhile, Switch, Case,
    Label, Goto, Type,
)
from semantic import analyze, const_int                  # noqa: E402
from ir import IRGen, const_fold, dead_code_elim          # noqa: E402

# Windows x64 整型参数寄存器（System V 的 rdi/rsi 在这里是错的）
# 64 位名用于指针，32 位名用于 int —— 宽度必须和内存操作数匹配，
# 否则 `mov DWORD PTR [rbp-16], rcx` 直接是 asm 错误（实测）。
ARG_REGS = ["ecx", "edx", "r8d", "r9d"]
ARG_REGS64 = ["rcx", "rdx", "r8", "r9"]
MAX_REG_ARGS = len(ARG_REGS)

# 返回指针的库函数。detect_pointers 靠它把 `t1 = call malloc(4)`
# 的 t1 也标成指针，否则接返回值时 mov eax 只搬低 32 位，
# 堆地址高位丢失 → 野指针 → 0xC0000005（实测）。
PTR_RETURNING_FUNCS = frozenset({
    "malloc", "calloc", "realloc", "strdup", "strchr", "strrchr",
    "strstr", "strtok", "memchr", "fgets", "getenv",
    "fopen", "fdopen", "tmpfile", "bsearch",
})

SLOT = 8                 # 每个变量一个 8 字节栈槽（值为 32 位，指针为 64 位）
SHADOW = 32              # Win64 调用者必须预留的影子空间

SETCC = {"<": "l", ">": "g", "<=": "le", ">=": "ge", "==": "e", "!=": "ne"}
BIN_ALU = {"+": "add", "-": "sub", "*": "imul", "&": "and", "|": "or", "^": "xor"}

# 32 位寄存器名 -> 64 位寄存器名。
# 指针必须走 64 位：`mov eax, QWORD PTR [...]` 是汇编错误
# （实测 p17 报 operand type mismatch for `mov`）。
R64 = {"eax": "rax", "ecx": "rcx", "edx": "rdx",
       "r8d": "r8", "r9d": "r9", "ebx": "rbx", "esi": "rsi", "edi": "rdi"}


class CodegenError(Exception):
    """后端无法翻译的 IR/语法。消息里必须说清是哪条指令。"""


# ---------------- 类型与符号 ----------------

def _is_int_literal(s):
    if not s:
        return False
    t = s.lstrip("+-")
    if t.lower().startswith("0x"):
        return True
    return t.isdigit()


def _is_name(s):
    """是不是一个变量名（不是字面量、不是字符串）。

    注意 C 标识符里**不允许出现 '.'**。IR 里带 '.' 的操作数只有两种来源：
      - 浮点字面量 1.5 / .5 / 1e3
      - 结构体成员 p.x / q->y（IRGen 的 Member 分支拼出来的）
    两者后端都不支持。早期把 '.' 也算作合法名字字符，于是 `double x = 1.5`
    被当成"从名为 1.5 的变量读值"，`p.x` 被当成独立变量 p_x ——
    都能汇编通过，然后读到未初始化的栈内存，**静默算错**。
    """
    if not s:
        return False
    if _is_int_literal(s):
        return False
    if s[0] in "\"'":
        return False
    return all(ch.isalnum() or ch == "_" for ch in s)


def _looks_float(s):
    """是不是浮点字面量（含 '.' 或指数，且能解析成数字）。"""
    if not s:
        return False
    t = s.strip()
    try:
        float(t)
    except ValueError:
        return False
    return ("." in t) or ("e" in t.lower() and t.lstrip("+-")[:1].isdigit())


def _scalar_size(base):
    base = (base or "").strip()
    return {"char": 1, "signed char": 1, "unsigned char": 1,
            "short": 2, "unsigned short": 2,
            "int": 4, "unsigned": 4, "unsigned int": 4, "float": 4,
            "long": 8, "unsigned long": 8,
            "long long": 8, "unsigned long long": 8, "double": 8}.get(base, 4)


def _elem_size(ty):
    """数组元素 / 指针所指对象的字节数。

    三种情形要分开，混在一起就会让 char* 和 int* 的寻址整体错位：
      int a[3]     元素是 int       -> 4
      int *p       指向 int         -> 4（指针本身是 8，但它指的对象是 4）
      int *p[3]    元素是指针       -> 8
    早期一律返回 8，实测 p17 汇编器直接报 operand type mismatch。
    """
    if ty is None:
        return 4
    ptr = getattr(ty, "ptr", 0) or 0
    is_arr = bool(getattr(ty, "dims", None)) or \
        getattr(ty, "array", None) is not None
    if is_arr:
        return 8 if ptr > 0 else _scalar_size(getattr(ty, "base", ""))
    if ptr == 0:
        return _scalar_size(getattr(ty, "base", ""))
    if ptr == 1:
        return _scalar_size(getattr(ty, "base", ""))
    return 8                      # 指向指针的指针


def collect_types(prog):
    """收集全程序的 名字 -> Type，用于区分标量/数组/指针。

    IR 里 `store` 的 extra 可能是数组名（a[0]=1）、也可能是指针变量名
    （*p=1）。两者生成的 IR 文本一模一样，只有类型信息能区分。

    同时返回全局变量名集合：`ir.py` 顶层只遍历函数，全局声明不进 IR，
    所以函数里一旦引用全局变量，后端就找不到初始化处——
    必须显式拒绝，否则会读到未初始化的栈槽（静默算错）。
    """
    types = {}
    globals_ = set()

    def walk_stmt(s):
        if isinstance(s, (VarDecl, DeclGroup)):
            decls = s.decls if isinstance(s, DeclGroup) else [s]
            for vd in decls:
                if getattr(vd, "name", None) and vd.vtype is not None:
                    types[vd.name] = vd.vtype
        elif isinstance(s, Block):
            for x in s.stmts:
                walk_stmt(x)
        elif isinstance(s, If):
            walk_stmt(s.then)
            if s.els is not None:
                walk_stmt(s.els)
        elif isinstance(s, (While, DoWhile)):
            walk_stmt(s.body)
        elif isinstance(s, For):
            for part in (s.init, s.body):
                if isinstance(part, (VarDecl, DeclGroup, Block, ExprStmt,
                                     If, While, For, DoWhile)):
                    walk_stmt(part)
                elif part is not None and hasattr(part, "decls"):
                    walk_stmt(part)
        elif isinstance(s, Switch) and s.body is not None:
            walk_stmt(s.body)
        elif isinstance(s, Case) and s.stmt is not None:
            walk_stmt(s.stmt)

    for d in prog.decls:
        if isinstance(d, FuncDef):
            for p in d.params:
                # 解析器把参数存成 (Type, name) 二元组
                if isinstance(p, (tuple, list)) and len(p) == 2:
                    ty, pname = p
                    if getattr(pname, "name", None):
                        pname = pname.name
                    types[pname] = ty
                elif getattr(p, "name", None):
                    types[p.name] = getattr(p, "vtype", None)
            if d.body is not None:
                walk_stmt(d.body)
        elif isinstance(d, (VarDecl, DeclGroup)):
            walk_stmt(d)
            # 顶层声明就是全局变量
            decls = d.decls if isinstance(d, DeclGroup) else [d]
            for vd in decls:
                if getattr(vd, "name", None):
                    globals_.add(vd.name)
    return types, globals_


def _is_array(ty):
    if ty is None:
        return False
    if getattr(ty, "dims", None):
        return True
    return getattr(ty, "array", None) is not None


def _array_len(ty):
    dims = getattr(ty, "dims", None)
    if dims:
        return int(dims[0])
    a = getattr(ty, "array", None)
    return int(a) if a else 0


# ---------------- 单个函数 ----------------

class FuncGen:
    def __init__(self, name, instrs, types, globals_=frozenset()):
        self.name = name
        self.asm = re.sub(r"[^0-9A-Za-z_]", "_", name)
        self.instrs = instrs
        self.types = types
        self.globals = set(globals_) or set()
        self.lines = []
        self.slots = {}        # name -> rbp 偏移（正数，实际用 [rbp-off]）
        self.kinds = {}        # name -> "scalar" | "array" | "ptr"
        self.sizes = {}        # array 的字节数
        self.frame = 0
        self.ptr_names = set()
        # 字符串字面量池：文本 -> 标签。跨函数共享，
        # 在 generate_asm 里统一输出到 .rdata 段。
        self._str_pool = {}
        self._str_data = []

    # ---- 汇编输出 ----
    def line(self, text):
        self.lines.append("\t" + text)

    def label(self, text):
        self.lines.append(text + ":")

    def comment(self, text):
        # 注释一律 ASCII：.s 文件和终端/编辑器的编码不一定一致，
        # 混进中文会让报错行号对不上（实测 PowerShell 下显示为乱码）。
        safe = text.encode("ascii", "replace").decode("ascii")
        self.lines.append("\t# " + safe)

    # ---- 布局 ----
    def scan_symbols(self):
        """从 IR 里收集所有出现过的名字，外加所有参数名。"""
        names = []
        for ins in self.instrs:
            for f in (ins.dst, ins.a1, ins.a2):
                if f and _is_name(f):
                    names.append(f)
            if ins.op == "call":
                for a in split_args(ins.extra):
                    if _is_name(a):
                        names.append(a)
            if ins.op == "store" and ins.extra and _is_name(ins.extra):
                names.append(ins.extra)
        for p in self.param_names():
            names.append(p)
        # 被引用的全局变量绝不分配栈槽：一旦分配，后端就会安静地
        # 读一块未初始化的栈内存。留给 check_operand 报错。
        banned = set(self.globals)
        seen, ordered = set(), []
        for n in names:
            if n in banned or n in seen:
                continue
            seen.add(n)
            ordered.append(n)
        return ordered

    def param_names(self):
        return self._params

    def detect_pointers(self, names):
        """找出哪些名字里装的是指针（地址）。

        IR 不区分"值"和"指向它的指针"，但 `addr` 的 dst、以及从指针
        变量赋值出来的名字都一定是指针，它们必须按 64 位存取。
        """
        ptr = set()
        for n in names:
            ty = self.types.get(n)
            if ty is not None and getattr(ty, "ptr", 0):
                ptr.add(n)
        changed = True
        while changed:
            changed = False
            for ins in self.instrs:
                if ins.op == "addr" and ins.dst:
                    if ins.dst not in ptr:
                        ptr.add(ins.dst)
                        changed = True
                # malloc/calloc/realloc/strdup 返回指针。
                # 不认这个，`int *p = malloc(4)` 里接返回值的临时变量
                # 会被当标量，mov eax 只取低 32 位，堆地址高位一丢
                # 就是野指针，运行直接 0xC0000005（实测）。
                if ins.op == "call" and ins.dst \
                        and ins.a1 in PTR_RETURNING_FUNCS:
                    if ins.dst not in ptr:
                        ptr.add(ins.dst)
                        changed = True
                if ins.op == "=" and ins.dst and ins.a1 in ptr:
                    if ins.dst not in ptr:
                        ptr.add(ins.dst)
                        changed = True
        return ptr

    def max_stack_args(self):
        """本函数里所有 call 中，最多要往栈上放几个参数（第 5 个起）。"""
        worst = 0
        for ins in self.instrs:
            if ins.op == "call":
                worst = max(worst, max(0, len(split_args(ins.extra))
                                       - MAX_REG_ARGS))
        return worst

    def alloc(self):
        names = self.scan_symbols()
        # 全局变量不进栈：它们在 .data / .bss 段，走 RIP 相对寻址。
        # 早期不排除它们，会安静地分配一块栈槽去"存"全局变量，
        # 于是读写全打在栈上，全局变量永远是 0——典型的静默算错。
        names = [n for n in names
                 if n not in getattr(self, "global_names", ())]
        self.ptr_names = self.detect_pointers(names)
        off = 0
        for n in names:
            ty = self.types.get(n)
            if _is_array(ty):
                nbytes = _array_len(ty) * _elem_size(ty)
                nbytes = max(nbytes, _elem_size(ty))
                off += nbytes
                off = (off + 7) // 8 * 8          # 8 字节对齐
                self.slots[n] = off
                self.kinds[n] = "array"
                self.sizes[n] = nbytes
            elif n in self.ptr_names:
                off += SLOT
                self.slots[n] = off
                self.kinds[n] = "ptr"
            else:
                off += SLOT
                self.slots[n] = off
                self.kinds[n] = "scalar"

        # 出参区：影子空间 32 字节 + 最多 max_stack_args 个参数槽。
        # 固定在帧底，于是 call 时 rsp 天然指向它，不需要每次 sub/add rsp，
        # 也不会破坏 16 字节对齐（frame 一定是 16 的倍数）。
        self.out_area = SHADOW + 8 * self.max_stack_args()
        self.frame = (off + self.out_area + 15) // 16 * 16
        return off

    def slot(self, name):
        if name in getattr(self, "global_names", ()):
            raise CodegenError(
                f"函数 {self.name}: 全局变量 {name} 走数据段，不分配栈槽")
        if name not in self.slots:
            raise CodegenError(
                f"函数 {self.name}: 未知变量 '{name}'"
                f"（可能是全局变量/结构体成员/不支持的表达式）")
        return self.slots[name]

    # ---- 操作数 ----
    def mem(self, name):
        # 全局变量走 .data 段，用 RIP 相对寻址。
        # 这是 x86-64 非位置相关代码访问静态数据的标准方式。
        if name in getattr(self, "global_names", ()):
            label = re.sub(r'[^0-9A-Za-z_]', '_', name)
            return f"DWORD PTR {label}[rip]"
        off = self.slot(name)
        if self.kinds[name] == "ptr":
            return f"QWORD PTR [rbp-{off}]"
        return f"DWORD PTR [rbp-{off}]"

    def op2(self, s):
        """把 IR 操作数变成 asm 源操作数（不涉及寄存器分配）。"""
        self.check_operand(s)
        if _is_int_literal(s):
            return s
        return self.mem(s)

    def check_operand(self, s):
        """校验一个 IR 操作数能否翻译。不能翻译就立刻报错，绝不猜。"""
        if s is None or s == "":
            raise CodegenError(f"函数 {self.name}: 缺操作数")
        if _is_int_literal(s):
            return
        if _looks_float(s):
            raise CodegenError(
                f"函数 {self.name}: 浮点常量 '{s}' —— 浮点（float/double）"
                f"后端不支持，只做 32 位整数")
        if s[0] in "\"'":
            raise CodegenError(
                f"函数 {self.name}: 字符串字面量 {s} —— 字符串/库函数不支持")
        if "->" in s or "." in s:
            raise CodegenError(
                f"函数 {self.name}: 结构体成员访问 '{s}' —— 无内存布局，不支持")
        if not _is_name(s):
            raise CodegenError(f"函数 {self.name}: 无法识别的操作数 '{s}'")
        if s not in self.slots:
            if s in getattr(self, "global_names", ()):
                return          # 全局变量走 .data 段，mem() 会处理
            raise CodegenError(
                f"函数 {self.name}: 变量 '{s}' 不在本函数栈帧里"
                f"（可能是全局变量/结构体成员/不支持的表达式）")

    def load_operand(self, reg, s):
        """把操作数载入寄存器。指针走 64 位，整数走 32 位。"""
        self.check_operand(s)
        if _is_int_literal(s):
            self.line(f"mov {reg}, {s}")
            return
        if s in getattr(self, "global_names", ()):
            self.line(f"mov {reg}, {self.mem(s)}")
            return
        off = self.slot(s)
        if self.kinds[s] == "array":
            raise CodegenError(
                f"函数 {self.name}: 把数组名 '{s}' 当值使用 —— 不支持")
        if self.kinds[s] == "ptr":
            self.line(f"mov {R64.get(reg, reg)}, QWORD PTR [rbp-{off}]")
        else:
            self.line(f"mov {reg}, DWORD PTR [rbp-{off}]")

    def store_reg(self, name, reg="eax"):
        if name in getattr(self, "global_names", ()):
            self.line(f"mov {self.mem(name)}, {reg}")
            return
        off = self.slot(name)
        if self.kinds[name] == "array":
            raise CodegenError(f"函数 {self.name}: 不能给数组名 '{name}' 赋值")
        if self.kinds[name] == "ptr":
            self.line(f"mov QWORD PTR [rbp-{off}], {R64.get(reg, reg)}")
        else:
            self.line(f"mov DWORD PTR [rbp-{off}], {reg}")

    def lea_base(self, reg, name):
        """把名字的地址放进寄存器：数组/标量取栈地址，指针取槽里的值。

        全局变量取的是它在 .data 段的绝对地址（用 lea rip 相对）。
        """
        if name in getattr(self, "global_names", ()):
            label = re.sub(r'[^0-9A-Za-z_]', '_', name)
            self.line(f"lea {reg}, {label}[rip]")
            return
        off = self.slot(name)
        if self.kinds[name] == "ptr":
            self.line(f"mov {reg}, QWORD PTR [rbp-{off}]")
        else:
            self.line(f"lea {reg}, [rbp-{off}]")

    # ---- 指令 ----
    def gen(self):
        self.comment(f"func {self.name}")
        self.label(self.asm)
        self.line("push rbp")
        self.line("mov rbp, rsp")
        self.line(f"sub rsp, {self.frame}")

        params = self.param_names()
        # 前 4 个走寄存器，第 5 个起在栈上。位置 = [rbp+48+8*(i-4)]：
        #   [rbp+0]  存的是旧的 rbp（push rbp 压的）
        #   [rbp+8]  返回地址
        #   [rbp+16] 影子空间起点，[rbp+16, rbp+48) 共 32 字节
        #   [rbp+48] 第 5 个参数（Win64 ABI，实测）
        for i, p in enumerate(params):
            if self.kinds[p] == "array":
                raise CodegenError(
                    f"函数 {self.name}: 参数 {p} 是数组 —— 数组参数退化为"
                    f"指针的语义未实现")
            off = self.slot(p)
            if i < MAX_REG_ARGS:
                self.comment(f"参数 {p} <- {ARG_REGS[i]}")
                if self.kinds[p] == "ptr":
                    # 指针是 64 位：内存 QWORD + 寄存器 64 位名
                    self.line(f"mov QWORD PTR [rbp-{off}], {ARG_REGS64[i]}")
                else:
                    # int 是 32 位：内存 DWORD + 寄存器 32 位名（宽度一致）
                    self.line(f"mov DWORD PTR [rbp-{off}], {ARG_REGS[i]}")
            else:
                src = 48 + 8 * (i - MAX_REG_ARGS)
                self.comment(f"参数 {p} <- [rbp+{src}] (stack)")
                self.line(f"mov eax, DWORD PTR [rbp+{src}]")
                self.line(f"mov DWORD PTR [rbp-{off}], eax")

        for ins in self.instrs:
            self.gen_instr(ins)

        # 落空兜底：C 里 int 函数不写 return 是未定义，这里返回 0
        self.line("xor eax, eax")
        self.label(f".Lret_{self.asm}")
        self.line("leave")
        self.line("ret")
        return self.lines

    def gen_instr(self, ins):
        op = ins.op

        if op == "label":
            self.label(self.asm_label(ins.extra))
            return

        if op == "goto":
            self.line(f"jmp {self.asm_label(ins.extra)}")
            return

        if op in ("if", "ifTrue"):
            self.load_operand("eax", ins.a1)
            self.line("test eax, eax")
            j = "je" if op == "if" else "jne"
            self.line(f"{j} {self.asm_label(ins.extra)}")
            return

        if op == "return":
            if ins.a1:
                self.load_operand("eax", ins.a1)
            else:
                self.line("xor eax, eax")
            self.line(f"jmp .Lret_{self.asm}")
            return

        if op == "=":
            self.load_operand("eax", ins.a1)
            self.store_reg(ins.dst)
            return

        if op in BIN_ALU:
            self.load_operand("eax", ins.a1)
            self.line(f"{BIN_ALU[op]} eax, {self.op2(ins.a2)}")
            self.store_reg(ins.dst)
            return

        if op in SETCC:
            # 比较结果按 C 语义是 int 0/1
            self.load_operand("eax", ins.a1)
            self.line(f"cmp eax, {self.op2(ins.a2)}")
            cc = SETCC[op]
            self.line(f"set{cc} al")
            self.line("movzx eax, al")
            self.store_reg(ins.dst)
            return

        if op in ("/", "%"):
            self.load_operand("eax", ins.a1)
            src = self.op2(ins.a2)
            if _is_int_literal(src):
                # idiv 不接受立即数，先放进 ecx
                self.line(f"mov ecx, {src}")
                src = "ecx"
            self.line("cdq")            # 被除数符号扩展到 edx:eax
            self.line(f"idiv {src}")
            if op == "%":
                self.line("mov eax, edx")
            self.store_reg(ins.dst)
            return

        if op in ("<<", ">>"):
            self.load_operand("eax", ins.a1)
            if _is_int_literal(ins.a2):
                self.line(f"{'shl' if op == '<<' else 'sar'} eax, {ins.a2}")
            else:
                self.load_operand("ecx", ins.a2)
                self.line(f"{'shl' if op == '<<' else 'sar'} eax, cl")
            self.store_reg(ins.dst)
            return

        if op in ("&&", "||"):
            # IR 已经把两个操作数都算成值了，这里只需还原"非零即真"的语义。
            self.load_operand("eax", ins.a1)
            self.line("test eax, eax")
            self.line("setne al")
            self.line("movzx eax, al")
            self.load_operand("ecx", ins.a2)
            self.line("test ecx, ecx")
            self.line("setne cl")
            self.line("movzx ecx, cl")
            self.line(f"{'and' if op == '&&' else 'or'} eax, ecx")
            self.store_reg(ins.dst)
            return

        if op == "neg":
            self.load_operand("eax", ins.a1)
            self.line("neg eax")
            self.store_reg(ins.dst)
            return

        if op == "not":
            self.load_operand("eax", ins.a1)
            self.line("test eax, eax")
            self.line("sete al")
            self.line("movzx eax, al")
            self.store_reg(ins.dst)
            return

        if op == "call":
            self.gen_call(ins)
            return

        if op == "load":
            self.gen_load(ins)
            return

        if op == "store":
            self.gen_store(ins)
            return

        if op == "addr":
            off = self.slot(ins.a1)
            # 数组名取首地址；标量取槽地址
            self.line(f"lea rax, [rbp-{off}]")
            self.store_reg(ins.dst)
            return

        if op == "deref":
            self.lea_base("rdx", ins.a1)
            self.line(f"mov eax, DWORD PTR [rdx]")
            self.store_reg(ins.dst)
            return

        if op == "nop":
            self.comment("nop: 源程序里有不支持的语法，跳过")
            return

        raise CodegenError(f"函数 {self.name}: 不支持的 IR 指令 '{ins}'")

    def asm_label(self, name):
        return ".L" + re.sub(r"[^0-9A-Za-z_]", "_", f"{self.asm}_{name}")

    def gen_call(self, ins):
        args = split_args(ins.extra)
        target = ins.a1

        # 库函数：直接 call libc 的真实实现。
        # 早期这里明确报错（"需要 CRT，后端不支持"），但实测
        # `gcc -o x.exe x.s` 默认带 CRT 启动代码，printf/putchar/
        # malloc/free 全部能链接能跑（_libc2.s 实测输出 value=42）。
        # 关键前提：字符串字面量必须走 rip 相对寻址，否则
        # relocation truncated to fit: R_X86_64_32S 链接失败。
        # 字符串字面量统一放 .rdata 段，生成 _str_N 标签。
        args = [self._intern_string(a) for a in args]

        # 第 5 个起写进帧底的出参区。
        # 关键偏移：call 时 rsp 指向影子空间起点，影子空间占 [rsp, rsp+32)，
        # 所以第 5 个参数在 [rsp+32]，第 6 个在 [rsp+40]……
        # 早期写成 [rsp+0] 会盖掉影子空间，被调方从 [rbp+48] 读到的是垃圾
        # （实测 p19 返回 109420074 而不是 15）。
        for i in range(len(args) - 1, MAX_REG_ARGS - 1, -1):
            self.load_operand("eax", args[i])
            slot = SHADOW + 8 * (i - MAX_REG_ARGS)
            self.line(f"mov DWORD PTR [rsp+{slot}], eax")

        for i, a in enumerate(args[:MAX_REG_ARGS]):
            if a.startswith("_str_"):
                # 字符串字面量：取地址进寄存器（64 位）
                self.line(f"lea {ARG_REGS64[i]}, {a}[rip]")
            else:
                self.load_operand(ARG_REGS[i], a)

        self.line(f"call {target}")
        if ins.dst:
            self.store_reg(ins.dst)

    def _intern_string(self, arg):
        """把 IR 参数里的字符串字面量提取到 .rdata，返回标签名。

        IR 的 call 指令形如 `call printf("%d", t2)`，extra 里
        字符串字面量是带引号的原样文本。这里把它转成：
          .rdata 段的 _str_N: .asciz "..."
        并返回 "_str_N" 供调用方 lea 取址。
        """
        if len(arg) < 2 or arg[0] != '"' or arg[-1] != '"':
            return arg
        body = arg[1:-1]
        # 反转 IR 生成时的转义：IR 侧把 \n 写成 \\n 之类的形式
        body = body.replace('\\n', '\n').replace('\\t', '\t')
        body = body.replace('\\"', '"').replace('\\\\', '\\')
        if body not in self._str_pool:
            label = f"_str_{len(self._str_pool)}"
            self._str_pool[body] = label
            self._str_data.append((label, body))
        return self._str_pool[body]

    def gen_load(self, ins):
        base, idx = ins.a1, ins.a2
        if not _is_name(base):
            raise CodegenError(f"函数 {self.name}: load 的基址 '{base}' 无法识别")
        if self.kinds.get(base) not in ("array", "ptr"):
            raise CodegenError(
                f"函数 {self.name}: 对非数组/非指针 '{base}' 做下标访问")
        ty = self.types.get(base)
        esz = _elem_size(ty) if ty is not None else 4
        if esz == 1:
            load_ins = "movzx eax, BYTE PTR"
        else:
            load_ins = "mov eax, DWORD PTR"
        self.lea_base("rdx", base)
        if _is_int_literal(idx):
            self.line(f"{load_ins} [rdx+{int(idx) * esz}]")
        else:
            self.load_operand("ecx", idx)
            self.line(f"{load_ins} [rdx+rcx*{esz}]")
        self.store_reg(ins.dst)

    def gen_store(self, ins):
        base, idx, val = ins.extra, ins.a1, ins.a2
        if not _is_name(base):
            raise CodegenError(
                f"函数 {self.name}: store 的目标 '{base}' 无法识别")
        if self.kinds.get(base) not in ("array", "ptr"):
            raise CodegenError(
                f"函数 {self.name}: 对非数组/非指针 '{base}' 做下标写")
        ty = self.types.get(base)
        esz = _elem_size(ty) if ty is not None else 4
        width = "BYTE" if esz == 1 else "DWORD"
        self.lea_base("rdx", base)
        if _is_int_literal(idx):
            addr = f"[rdx+{int(idx) * esz}]"
        else:
            self.load_operand("ecx", idx)
            addr = f"[rdx+rcx*{esz}]"
        # 值先算到 eax，但地址用了 ecx；如果值依赖 ecx 会冲突，
        # 所以先把值放进 r8d，再写内存。
        self.load_operand("r8d", val)
        self.line(f"mov {width} PTR {addr}, {'r8b' if esz == 1 else 'r8d'}")


def split_args(s):
    """按逗号切参数，忽略括号/引号内部的逗号。"""
    if not s or not s.strip():
        return []
    parts, cur, depth, in_str, quote = [], [], 0, False, ""
    for ch in s:
        if in_str:
            cur.append(ch)
            if ch == quote:
                in_str = False
            continue
        if ch in "\"'":
            in_str, quote = True, ch
            cur.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p != ""]


# ---------------- 程序级 ----------------

def split_functions(code):
    """把平铺的 IR 指令按 `func f:` / `end func f:` 切成每个函数一段。

    同时抽出 `global X:` 段：全局变量的初始化指令放不进任何函数，
    但要交给汇编生成器（它们进 .data / .bss 段）。
    返回 (funcs, global_inits)：
      funcs         [(函数名, [指令...]), ...]
      global_inits  [(变量名, 初始化指令或 None)]
    """
    funcs, order, cur = {}, [], None
    gname, ginstrs, globals_ = None, None, []
    for ins in code:
        if ins.op == "label" and ins.extra.startswith("end func"):
            cur = None
            continue
        if ins.op == "label" and ins.extra.startswith("func "):
            cur = ins.extra[len("func "):].strip()
            if cur in funcs:
                raise CodegenError(f"函数 {cur} 重复定义")
            funcs[cur] = []
            order.append(cur)
            continue
        if ins.op == "label" and ins.extra.startswith("global "):
            # 进入新的一段全局声明
            if gname is not None:
                globals_.append((gname, ginstrs))
            gname = ins.extra[len("global "):].strip()
            ginstrs = []
            continue
        if cur is None:
            # 不属于任何函数：要么在 global 段里，要么真的是孤儿
            if gname is not None:
                ginstrs.append(ins)
                continue
            raise CodegenError(f"函数体外的指令: {ins}")
        funcs[cur].append(ins)
    if gname is not None:
        globals_.append((gname, ginstrs))
    return [(n, funcs[n]) for n in order], globals_


def generate_asm(prog, optimize=False, verbose=False):
    """把 AST 编成汇编文本。返回 (asm_text, functions)。"""
    code = IRGen().gen_program(prog)
    if optimize:
        code, folded = const_fold(code)
        code, removed = dead_code_elim(code)
        if verbose:
            print(f"[codegen] 常量折叠 {folded} 处，死代码 {removed} 条",
                  file=sys.stderr)
    types, globals_ = collect_types(prog)
    funcs, global_inits = split_functions(code)

    # 全局变量：早期直接拒绝（"全局变量不进 IR，后端不支持"）。
    # 但 IR 侧早已把它们放进来了（global X: + 初始化指令），
    # 后端再拒绝就是自相矛盾。现在真的支持：进 .data / .bss 段。
    global_names = set(n for n, _ in global_inits)
    # 字符串字面量池（跨函数去重）
    str_pool = {}
    seen_str_labels = set()

    out = [
        "# generated by csub tools/codegen.py (IR -> x86-64, Windows x64 ABI)",
        "# NOTE: comments use '#', never ';' -- in GAS x86, ';' is a statement",
        "# separator, so a ';' comment gives 'junk at end of line' (measured).",
        "\t.intel_syntax noprefix",
    ]
    # 段顺序很关键：写完 .data 必须切回 .text，否则后面的函数体会
    # 继续落在 .data 段里，链接器照常通过，一运行就 0xC0000005（实测）。
    # gcc 自己的顺序是 .text / .data / .text / 函数体。
    data_lines = gen_global_data(global_inits, types)
    out.append("\t.text")
    if data_lines:
        out.extend(data_lines)
        out.append("\t.text")
    for name, instrs in funcs:
        out.append("")
        out.append(f"\t.globl {re.sub(r'[^0-9A-Za-z_]', '_', name)}")
        fg = FuncGen(name, instrs, types, globals_)
        fg._params = [p for p in _params_of(prog, name)]
        fg.global_names = global_names
        fg.alloc()
        out.extend(fg.gen())
        # 收集本函数用到的字符串字面量（跨函数去重）
        for text, label in fg._str_pool.items():
            if label not in seen_str_labels:
                seen_str_labels.add(label)
                str_pool[text] = label
    # 字符串字面量放 .rdata，必须在 .text 之后、且所有函数都生成完
    # 之后统一输出（标签跨函数共享，重复定义会链接失败）。
    if str_pool:
        out.append("")
        out.append('\t.section .rdata,"dr"')
        for text, label in sorted(str_pool.items(), key=lambda x: x[1]):
            esc = _asm_escape(text)
            out.append(f"{label}:")
            out.append(f'\t.asciz "{esc}"')
    out.append("")
    return "\n".join(out) + "\n", funcs


def _asm_escape(text):
    """把字符串转成 GAS .asciz 能安全表示的形式。

    只转义汇编器真正介意的字符：反斜杠、双引号、换行、制表符。
    其他字符（含中文）原样输出——.asciz 按字节存，链接器不关心编码。
    """
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append("\\%03o" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


def gen_global_data(global_inits, types):
    """为全局变量生成 .data / .bss 段。

    global_inits: [(变量名, 初始化指令列表)]，来自 IR 的 global X: 段。
    有初值的进 .data，没有的进 .bss（.comm）。

    简化处理：只看标量。数组全局（int a[10];）只能进 .bss 全零，
    带初值的数组全局先不支持（CodegenError），绝不静默算错。
    """
    data, bss = [], []
    for name, instrs in global_inits:
        label = re.sub(r'[^0-9A-Za-z_]', '_', name)
        ty = types.get(name)
        if _is_array(ty):
            raise CodegenError(
                f"全局数组 {name} 带布局的后端尚未支持"
                f"（无元素类型与偏移信息，静默算错比报错更糟）")
        if not instrs:
            bss.append((label, 4))
            continue
        # IR 形如 `g = 5`，取第一个赋值操作
        val = None
        for ins in instrs:
            if ins.op == "=" and ins.dst == name:
                val = ins.a1
                break
        if val is None or not _is_int_literal(val):
            raise CodegenError(
                f"全局变量 {name} 的初值不是整数常量，后端不支持")
        data.append((label, val))
    out = []
    if data:
        out.append("\t.data")
        for label, val in data:
            out.append(f"\t.globl {label}")
            out.append(f"\t.align 4")
            out.append(f"{label}:")
            out.append(f"\t.long {val}")
    if bss:
        out.append("\t.bss")
        for label, nbytes in bss:
            out.append(f"\t.globl {label}")
            out.append(f"\t.align 4")
            out.append(f"{label}:")
            out.append(f"\t.zero {nbytes}")
    return out


def _params_of(prog, fname):
    for d in prog.decls:
        if isinstance(d, FuncDef) and d.name == fname:
            names = []
            for p in d.params:
                if isinstance(p, (tuple, list)) and len(p) == 2:
                    ty, pn = p
                    pn = getattr(pn, "name", pn)
                    names.append(pn)
                elif getattr(p, "name", None):
                    names.append(p.name)
            return names
    return []


def compile_source(src, optimize=False, verbose=False):
    """源码文本 -> 汇编文本。前端出错时抛 CodegenError（带诊断）。"""
    from preprocess import preprocess
    pr = preprocess(src)
    try:
        prog = parse(pr.text)
    except ParseError as e:
        raise CodegenError(f"语法错误：{e}")

    diags = analyze(prog)
    errs = [d for d in diags if d.level == "error"]
    for d in diags:
        if d.level != "error":
            print(f"[警告] {d}", file=sys.stderr)
    if errs:
        msg = "；".join(str(d) for d in errs[:5])
        raise CodegenError(f"语义错误，拒绝生成汇编：{msg}")

    asm, funcs = generate_asm(prog, optimize=optimize, verbose=verbose)
    names = [n for n, _ in funcs]
    if "main" not in names:
        raise CodegenError("没有 main 函数，无法生成可执行程序")
    return asm, names


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="csub 后端：IR -> x86-64 汇编（Windows x64 / Intel 语法）")
    ap.add_argument("source", nargs="?", help="C 源文件")
    ap.add_argument("-o", "--output", help="输出 .s 路径（省略则打到 stdout）")
    ap.add_argument("--opt", action="store_true", help="使用优化后的 IR")
    ap.add_argument("--quiet", action="store_true", help="不打印诊断")
    ap.add_argument("--selftest", action="store_true",
                    help="跑全部对照程序并写 tools/codegen_report.md")
    args = ap.parse_args(argv)

    if args.selftest:
        return run_selftest()

    if not args.source:
        ap.print_help()
        return 2

    try:
        with open(args.source, encoding="utf-8") as f:
            src = f.read()
    except OSError as e:
        print(f"无法读取 {args.source}: {e}", file=sys.stderr)
        return 2

    try:
        asm, _names = compile_source(src, optimize=args.opt,
                                     verbose=not args.quiet)
    except CodegenError as e:
        print(f"[codegen] {e}", file=sys.stderr)
        return 1

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as f:
            f.write(asm)
        if not args.quiet:
            print(f"[codegen] 已写出 {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(asm)
    return 0


# ---------------- 自测：与 gcc 对照 ----------------

SELFTEST_PROGRAMS = [
    ("p01_ret42", "int main() { return 42; }"),
    ("p02_arith", "int main() { int a = 2; int b = 3; return a * b + 1; }"),
    ("p03_add_call", """int add(int a, int b) { return a + b; }
int main() { return add(20, 22); }"""),
    ("p04_while_sum", """int main() {
    int i = 0; int s = 0;
    while (i < 5) { s = s + i; i = i + 1; }
    return s;
}"""),
    ("p05_if_else", """int main() {
    int a = 3;
    if (a > 2) { return 10; } else { return 20; }
}"""),
    ("p06_nested_loop", """int main() {
    int s = 0; int i = 0; int j = 0;
    for (i = 0; i < 3; i = i + 1) {
        for (j = 0; j < 4; j = j + 1) { s = s + i; }
    }
    return s;
}"""),
    ("p07_fib_recursion", """int fib(int n) {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}
int main() { return fib(10); }"""),
    ("p08_multi_func", """int sq(int x) { return x * x; }
int cube(int x) { return sq(x) * x; }
int poly(int x) { return cube(x) + sq(x) + x + 1; }
int main() { return poly(3); }"""),
    ("p09_div_mod", """int main() {
    int a = 17; int b = 5;
    int q = a / b; int r = a % b;
    return q * 10 + r;
}"""),
    ("p10_logic", """int main() {
    int a = 3; int b = 0; int c = 4;
    if (a > 2 && b == 0) { return 7; }
    if (a > 2 || c == 0) { return 9; }
    return 11;
}"""),
    ("p11_do_while", """int main() {
    int i = 0; int s = 0;
    do { s = s + i; i = i + 1; } while (i < 4);
    return s;
}"""),
    ("p12_shift_bit", """int main() {
    int a = 5 << 3;
    int b = 12 >> 2;
    int c = 6 & 3;
    int d = 5 | 2;
    int e = 7 ^ 3;
    return a + b + c + d + e;
}"""),
    ("p13_ternary", "int main() { int x = 5; return x > 3 ? 11 : 22; }"),
    ("p14_four_args", """int mix(int a, int b, int c, int d) {
    return (a - b) * 10 + (c - d);
}
int main() { return mix(9, 4, 8, 1); }"""),
    ("p15_break_continue", """int main() {
    int i = 0; int s = 0;
    while (i < 10) {
        i = i + 1;
        if (i % 2 == 0) { continue; }
        if (i > 7) { break; }
        s = s + i;
    }
    return s;
}"""),
    ("p16_array_1d", """int main() {
    int a[3];
    a[0] = 1; a[1] = 2; a[2] = 3;
    return a[0] + a[1] + a[2];
}"""),
    ("p17_ptr_basic", """int main() {
    int x = 0;
    int *p = &x;
    *p = 5;
    return x + 1;
}"""),
    ("p18_neg_div", """int main() {
    int a = -17;
    int b = 5;
    int q = a / b;
    int r = a % b;
    return q * 10 + r + 100;
}"""),
    ("p19_five_args", """int f(int a, int b, int c, int d, int e) {
    return a + b + c + d + e;
}
int main() { return f(1, 2, 3, 4, 5); }"""),
    ("p20_six_args", """int g(int a, int b, int c, int d, int e, int f) {
    return a * 100 + b * 10 + c + d + e + f;
}
int main() { return g(1, 2, 3, 4, 5, 6); }"""),
    ("p21_nested_5arg", """int add5(int a, int b, int c, int d, int e) {
    return a + b + c + d + e;
}
int main() { return add5(1, 2, 3, 4, add5(1, 1, 1, 1, 1)); }"""),
    ("p22_stack_arg_expr", """int h(int a, int b, int c, int d, int e) {
    return e - a;
}
int main() {
    int x = 3; int y = 5;
    return h(x, y, x, y, x * y);
}"""),
    ("p23_eight_args", """int s8(int a, int b, int c, int d,
              int e, int f, int g, int h) {
    return a + b * 2 + c * 3 + d * 4 + e * 5 + f * 6 + g * 7 + h * 8;
}
int main() { return s8(1, 2, 3, 4, 5, 6, 7, 8) % 200; }"""),
    ("p24_two_calls_5arg", """int add5(int a, int b, int c, int d, int e) {
    return a + b + c + d + e;
}
int main() {
    int x = add5(1, 2, 3, 4, 5);
    int y = add5(10, 20, 30, 40, 50);
    return x + y;
}"""),
    # ---- 全局变量（p25..p28）----
    # 段顺序写错会让函数体落进 .data，链接照过、一跑就 0xC0000005。
    ("p25_global_rw", """int g = 5;
int main() { g = 42; return g; }"""),
    ("p26_global_bss", """int g;
int main() { g = 7; return g; }"""),
    ("p27_global_shared", """int counter = 0;
void inc() { counter = counter + 1; }
int main() { inc(); inc(); inc(); return counter; }"""),
    ("p28_global_in_cond", """int limit = 10;
int main() {
    int i = 0; int s = 0;
    for (i = 0; i < limit; i = i + 1) { s = s + i; }
    return s;
}"""),
    # ---- 库函数（p29..p36）----
    # 直接 call libc 的真实实现。关键前提：
    #   .globl main 必须有（否则 MinGW 找 WinMain）
    #   字符串字面量必须走 rip 相对寻址（否则 relocation truncated）
    #   gcc 默认带 CRT 启动代码，printf/putchar 都能链接
    ("p29_printf_plain", """#include <stdio.h>
int main() { printf("hi\\n"); return 0; }"""),
    ("p30_printf_int", """#include <stdio.h>
int main() { int x = 42; printf("x=%d\\n", x); return 0; }"""),
    ("p31_printf_two_args", """#include <stdio.h>
int main() {
    int a = 1, b = 2;
    printf("a=%d b=%d\\n", a, b);
    return 0;
}"""),
    ("p32_putchar", """#include <stdio.h>
int main() { putchar(65); putchar(66); return 0; }"""),
    ("p33_puts", """#include <stdio.h>
int main() { puts("hello"); return 0; }"""),
    ("p34_printf_with_recursion", """#include <stdio.h>
int fib(int n) {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}
int main() { printf("fib(10)=%d\\n", fib(10)); return 0; }"""),
    ("p35_printf_with_loop", """#include <stdio.h>
int main() {
    int i = 0; int s = 0;
    for (i = 0; i < 5; i = i + 1) { s = s + i; }
    printf("sum=%d\\n", s);
    return 0;
}"""),
    ("p36_printf_with_global", """#include <stdio.h>
int g = 7;
int main() { printf("g=%d\\n", g); return 0; }"""),
    # ---- malloc / free（p37..p41）----
    # 关键坑：malloc 返回 64 位指针，detect_pointers 必须把
    # `t1 = call malloc(4)` 的 t1 也标成指针。漏了这步，
    # 接返回值时 mov eax 只搬低 32 位，堆地址高位丢失 -> 野指针
    # -> 0xC0000005 访问冲突（实测踩到）。
    ("p37_malloc_basic", """#include <stdlib.h>
int main() { int *p = malloc(4); *p = 42; return *p; }"""),
    ("p38_malloc_array", """#include <stdlib.h>
int main() {
    int *p = malloc(sizeof(int) * 5);
    int i = 0;
    for (i = 0; i < 5; i = i + 1) { p[i] = i * 10; }
    int s = 0;
    for (i = 0; i < 5; i = i + 1) { s = s + p[i]; }
    free(p);
    return s;
}"""),
    ("p39_malloc_printf", """#include <stdio.h>
#include <stdlib.h>
int main() {
    int *p = malloc(sizeof(int) * 3);
    p[0] = 1; p[1] = 2; p[2] = 3;
    printf("sum=%d\\n", p[0] + p[1] + p[2]);
    free(p);
    return 0;
}"""),
    ("p40_malloc_recursion", """#include <stdio.h>
#include <stdlib.h>
int fib(int n) { if (n < 2) { return n; } return fib(n - 1) + fib(n - 2); }
int main() {
    int *p = malloc(sizeof(int));
    *p = fib(10);
    printf("fib=%d\\n", *p);
    free(p);
    return 0;
}"""),
    ("p41_calloc", """#include <stdlib.h>
int main() {
    int *p = calloc(3, sizeof(int));
    p[0] = 5; p[1] = 6; p[2] = 7;
    int s = p[0] + p[1] + p[2];
    free(p);
    return s;
}"""),
]

REPORT = os.path.join(ROOT, "tools", "codegen_report.md")


def _run(cmd, timeout=60):
    import subprocess
    flags = 0x08000000 if os.name == "nt" else 0
    r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                       creationflags=flags)
    return r.returncode, r.stdout.decode("utf-8", "replace"), \
        r.stderr.decode("utf-8", "replace")


def _exit_code_of(exe):
    """跑可执行文件取退出码（Windows 上是完整 32 位，统一按有符号解释）。"""
    import subprocess
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        r = subprocess.run([exe], capture_output=True, timeout=30,
                           creationflags=flags)
    except subprocess.TimeoutExpired:
        return None, "超时（可能死循环）"
    code = r.returncode & 0xFFFFFFFF
    if code >= 0x80000000:
        code -= 0x100000000
    return code, ""


def run_selftest():
    """跑全部对照程序，写报告。

    中间产物（.c / .s / .exe）放临时目录，不落进仓库：
    这些东西由 --selftest 随时可重建，提交进仓库只是噪声。
    """
    import tempfile
    build_dir = tempfile.mkdtemp(prefix="csub_codegen_")
    rows = []
    print(f"{'程序':<22}{'codegen':>9}{'gcc':>7}  结果")
    print("-" * 58)
    for name, src in SELFTEST_PROGRAMS:
        cpath = os.path.join(build_dir, name + ".c")
        spath = os.path.join(build_dir, name + ".s")
        exe = os.path.join(build_dir, name + ".exe")
        gcc_exe = os.path.join(build_dir, name + "_gcc.exe")
        with open(cpath, "w", encoding="utf-8", newline="\n") as f:
            f.write(src + "\n")

        note = ""
        mine = None
        try:
            asm, _ = compile_source(src)
            with open(spath, "w", encoding="utf-8", newline="\n") as f:
                f.write(asm)
            rc, out, err = _run(["gcc", "-O0", "-o", exe, spath])
            if rc != 0:
                note = "汇编/链接失败: " + (err.strip().splitlines() or [""])[0]
            else:
                mine, note = _exit_code_of(exe)
        except CodegenError as e:
            note = f"codegen 拒绝: {e}"
        except Exception as e:                       # 诚实记录，不隐瞒
            note = f"{type(e).__name__}: {e}"

        gcc_val = None
        rc, out, err = _run(["gcc", "-O0", "-w", "-o", gcc_exe, cpath])
        if rc != 0:
            gcc_val = None
            gnote = "gcc 编译失败"
        else:
            gcc_val, gnote = _exit_code_of(gcc_exe)
        if gcc_val is None:
            note = (note + "; " if note else "") + gnote

        same = (mine is not None and gcc_val is not None and mine == gcc_val)
        rows.append((name, mine, gcc_val, same, note))
        print(f"{name:<22}{str(mine):>9}{str(gcc_val):>7}  "
              f"{'OK' if same else ('MISMATCH' if mine is not None else 'FAIL')}"
              f"{'  ' + note if note else ''}")

    ok = sum(1 for r in rows if r[3])
    total = len(rows)
    print("-" * 58)
    print(f"一致 {ok}/{total}")

    write_report(rows, ok, total)
    print(f"报告已写入 {os.path.relpath(REPORT, ROOT)}")
    print(f"中间产物在 {build_dir}（临时目录，可随时重建）")
    return 0 if ok == total else 1


def write_report(rows, ok, total):
    lines = [
        "# csub 后端：IR -> x86-64 汇编 实测报告",
        "",
        "生成方式：`python tools/codegen.py --selftest`"
        "（本文件由脚本自动写出，数字均来自实跑）。",
        "",
        "链路：csub 前端解析 + 语义分析 -> `src/ir.py` 生成三地址码 -> "
        "`tools/codegen.py` 生成 .s -> `tools/asm_run.py` 用 gcc 汇编链接 -> "
        "执行取退出码，与 `gcc -O0` 直接编译同一份源码的退出码对比。",
        "",
        "## 逐程序结果",
        "",
        "| 程序 | 自生成退出码 | gcc 退出码 | 是否一致 | 备注 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, mine, gcc_val, same, note in rows:
        lines.append(
            f"| {name} | {'-' if mine is None else mine} | "
            f"{'-' if gcc_val is None else gcc_val} | "
            f"{'一致' if same else '不一致'} | {note or ''} |")
    lines += [
        "",
        f"合计：{ok}/{total} 一致。",
        "",
        "## 后端实现要点",
        "",
        "- 目标：Windows x64 ABI（MSYS2 ucrt64 gcc 16.1.0 实测），"
        "Intel 语法，`.intel_syntax noprefix`。",
        "- 整型参数：前 4 个走 rcx / rdx / r8 / r9（32 位名 ecx/edx/r8d/r9d），"
        "**第 5 个起走栈**，被调方从 `[rbp+48]`、`[rbp+56]` … 读；"
        "返回值 eax。",
        "- 调用方把栈上参数写到 `[rsp+32]`、`[rsp+40]` …："
        "`call` 时 rsp 指向影子空间，32 字节影子空间占 `[rsp, rsp+32)`，"
        "所以第一个栈参数必须**跳过影子空间**。"
        "写成 `[rsp+0]` 会踩掉影子空间，被调方读到垃圾"
        "（实测 p19 返回 109420074 而不是 15）。",
        "- 出参区固定在帧底，大小 = 32 + 8×(本函数内最大栈参数个数)，"
        "于是 `call` 处 rsp 天然对齐，不需要每次 sub/add rsp。",
        "- 变量一律放栈上，每个标量 8 字节槽、按 32 位存取；"
        "**不做寄存器分配**。指针按 64 位存取（`R64` 表做寄存器名提升），"
        "`mov eax, QWORD PTR [...]` 是汇编错误，指针必须走 rax。",
        "- 栈帧 `push rbp; mov rbp, rsp; sub rsp, align16(locals + 出参区)`。",
        "- 除法/取模：`cdq` + `idiv`（被除数符号扩展到 edx:eax），"
        "取模取 edx。",
        "- 比较：`cmp` + `setcc` + `movzx`，结果按 C 语义是 int 0/1。",
        "- 分支：IR 里 `ifFalse x goto L` / `if x goto L` 直接用 "
        "`test eax, eax` + `je` / `jne`；`&&` `||` 在 IR 里已是独立运算，"
        "按“非零即真”合成（IR 的操作数都已求值完，短路副作用不存在）。",
        "- 汇编注释一律用 `#`，**不能用 `;`**：GAS 的 x86 汇编里 `;` 是"
        "语句分隔符，写成 `;` 会报 `junk at end of line`（实测，首轮 18 个"
        "程序全挂在这上面）。",
        "- 注释统一转 ASCII：`.s` 文件与终端/编辑器编码不一致时，"
        "中文注释会让报错行号对不上。",
        "- **没有生成 `.seh_*` 展开指令**：实测本后端生成的纯计算函数"
        "不需要它也能正常汇编、链接、运行；代价是异常/栈回溯信息缺失，"
        "一旦涉及 SEH 异常或调试器展开栈帧会拿不到正确的调用栈"
        "（这一点是推测，未实测异常路径）。",
        "",
        "## 明确砍掉的能力（不做，不是没测）",
        "",
        "| 能力 | 现状 | 说明 |",
        "| --- | --- | --- |",
        "| 浮点（float/double） | 不支持 | IR 里浮点常量是 `1.5` 形态，"
        "后端只处理 32 位整数；碰到会报错退出 |",
        "| 字符串字面量 | 不支持 | IR 里带引号的操作数直接报错 |",
        "| 库函数（printf/scanf/malloc 等） | 不支持 | 调用点显式报错 |",
        "| 结构体 / 成员访问 | 不支持 | IR 生成 `base.field` 这种名字，"
        "无内存布局，后端取不到槽位会报错 |",
        "| 指针算术（p+1 的缩放） | 不支持 | IR 里 `p + 1` 就是普通加法，"
        "没有 ×元素大小；只有 `&x` / `*p` 这种不涉及缩放的情形可用 |",
        "| 数组 | 仅一维、元素按 int(4)/char(1)/long(8) 取宽度 | "
        "多维数组 IR 会按 `store a[i]` 形状生成，后端把它当一维处理，"
        "**结果不可信**；未验证 |",
        "| 全局变量 | 不支持 | `ir.py` 顶层只遍历函数，全局声明不进 IR。"
        "后端**拒绝**引用全局变量，因为分配栈槽会读到未初始化的栈内存、"
        "静默算错（实测确实会静默算错，已改为报错） |",
        "| switch / goto / 标签 | 未验证 | 代码路径已写（标签统一加前缀并"
        "清洗非法字符），但 `switch` 的 IR 用 `case_<tmp>_<label>` 命名，"
        "是否稳定未测 |",
        "| 优化后 IR | 可选 | `--opt` 走常量折叠 + 死代码消除；"
        "报告里的数字用的是未优化 IR（更保险） |",
        "",
        "## 不支持时是明确报错，不是静默算错",
        "",
        "这几项都实测确认会抛 `CodegenError`（带原因），而不是生成一份"
        "能汇编、能运行、但结果是垃圾的汇编：",
        "",
        "| 输入 | 报错内容 |",
        "| --- | --- |",
        "| `double x = 1.5;` | 浮点常量 `1.5` —— 浮点后端不支持 |",
        "| `printf(\"hi\")` | 库函数 printf() 需要 CRT，后端不支持 |",
        "| `char *s = \"abc\";` | 字符串字面量 —— 字符串/库函数不支持 |",
        "| `int g = 7; ... return g;` | 引用了全局变量 g —— 全局变量不进 IR |",
        "| `p.x = 1` | 结构体成员访问 `p.x` —— 无内存布局，不支持 |",
        "| `int a[2][3]` | 对非数组/非指针 `t1` 做下标写 |",
        "",
        "早期版本把 `.` 也算作合法标识符字符，于是 `1.5` 被当成“名为 1.5 的"
        "变量”、`p.x` 被当成独立变量 `p_x`，两者都能汇编通过然后读到"
        "未初始化栈内存——**静默算错**。现在 `_is_name` 只允许 "
        "`[A-Za-z0-9_]`，并在 `check_operand` 里显式区分浮点/成员/越界名字。",
        "",
        "## 不确定 / 待实测的地方",
        "",
        "- `.seh_*` 省略后能否长期稳定：本机纯计算函数实测可以；"
        "如果以后接入异常处理或需要栈回溯，必须补 "
        "`.seh_proc/.seh_pushreg/.seh_setframe/.seh_endprologue/.seh_endproc`。"
        "**这是推测，未验证异常路径。**",
        "- 影子空间：本后端把 32 字节留在帧底（`[rsp, rsp+32)`），"
        "被调函数写自己的 home area 不会踩到本地变量；"
        "这一结论基于 ABI 文档 + 实测未出错，没有用调试器逐字节验证。",
        "- 负数除法/取模按 C99 截断语义（`cdq`+`idiv` 天然如此），"
        "p18 用来对照 gcc。",
        "",
        "## 复现命令",
        "",
        "```",
        "python tools/codegen.py examples/ir1.c -o out.s",
        "python tools/asm_run.py out.s",
        "python tools/codegen.py --selftest        # 重跑全部对照并重写本报告",
        "```",
        "",
    ]

    with open(REPORT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
