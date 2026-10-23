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
    Label, Goto, Type, StructDef,
)
from semantic import analyze, const_int                  # noqa: E402
from ir import IRGen, const_fold, dead_code_elim          # noqa: E402

# Windows x64 整型参数寄存器（System V 的 rdi/rsi 在这里是错的）
# 64 位名用于指针，32 位名用于 int —— 宽度必须和内存操作数匹配，
# 否则 `mov DWORD PTR [rbp-16], rcx` 直接是 asm 错误（实测）。
ARG_REGS = ["ecx", "edx", "r8d", "r9d"]
ARG_REGS64 = ["rcx", "rdx", "r8", "r9"]
MAX_REG_ARGS = len(ARG_REGS)

# 变参函数：Windows x64 ABI 要求调用前 al 记录"用了几个 xmm 寄存器"。
# 我们把 double 实参走整数寄存器（gcc -O0 就是这么干的），
# 所以 al 一律置 0。不置的话 printf 读到的浮点参数是垃圾。
VARARG_FUNCS = frozenset({
    "printf", "fprintf", "sprintf", "snprintf",
    "scanf", "fscanf", "sscanf",
    "vprintf", "vfprintf", "vsprintf", "vsnprintf",
})

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
    dims = list(getattr(ty, "dims", None) or [])
    is_arr = bool(dims) or getattr(ty, "array", None) is not None
    if is_arr:
        if ptr > 0 and dims:
            # 指向数组的指针 int(*)[3]：元素（一行）大小 = 3×4=12。
            # 注意区分两种用法：
            #   &a[i]      （addrof）要行宽 12 —— a[i] 是一整行
            #   a[flat]    （load）  要元素宽 4 —— flat 已是扁平索引
            # 这里返回行宽；load/store 走 _elem_stride 拿元素宽。
            n = 1
            for d in dims:
                n *= int(d)
            return n * _scalar_size(getattr(ty, "base", ""))
        return 8 if ptr > 1 else _scalar_size(getattr(ty, "base", ""))
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
            # 先收集体内的局部变量类型，再登记形参——形参优先。
            # 反过来的话，`int dist2(struct P *p)` 的形参 p 会被
            # main 里的 `struct P p`（同名）覆盖成非指针，
            # 于是 p->x 走"取栈地址"而不是"解引用"（实测得垃圾值）。
            locals_before = dict(types)
            if d.body is not None:
                walk_stmt(d.body)
            for p in d.params:
                # 解析器把参数存成 (Type, name) 二元组
                if isinstance(p, (tuple, list)) and len(p) == 2:
                    ty, pname = p
                    if getattr(pname, "name", None):
                        pname = pname.name
                    types[pname] = ty
                elif getattr(p, "name", None):
                    types[p.name] = getattr(p, "vtype", None)
            # 形参没出现的名字保持局部类型
            for k, v in locals_before.items():
                if k not in types:
                    types[k] = v
        elif isinstance(d, (VarDecl, DeclGroup)):
            walk_stmt(d)
            # 顶层声明就是全局变量
            decls = d.decls if isinstance(d, DeclGroup) else [d]
            for vd in decls:
                if getattr(vd, "name", None):
                    globals_.add(vd.name)
    return types, globals_


def collect_structs(prog):
    """收集结构体布局：类型名 -> {成员名: (偏移, 字节数, 是否指针)}。

    教学后端用**简单布局**：按声明顺序紧密排列，不插入对齐填充。
    与真实 ABI 不完全一致（gcc 会对齐到成员宽度），但：
      - 对"全是 int 的结构体"完全一致
      - 对混合宽度（char + int）会偏小，但同一次编译里读写都用
        同一套偏移，程序自身是自洽的
    这一点在报告里明确标注为"教学简化，不是完整 ABI"。
    """
    structs = {}

    def layout(sd):
        members = getattr(sd, "members", None) or []
        off = 0
        table = {}
        for mty, mname in members:
            size = _type_size(mty)
            table[mname] = (off, size, getattr(mty, "ptr", 0) > 0)
            off += size
        return table, off

    def walk_decls(items):
        for d in items:
            if isinstance(d, StructDef):
                tag = getattr(d, "tag", "") or ""
                if tag:
                    table, total = layout(d)
                    structs[tag] = {"members": table, "size": total}
                # 匿名结构体的 typedef：typedef struct { int r; } Pos;
                # 没有 tag，成员布局只能挂在别名上。
                # 早期只登记 tag，于是 Pos p; p.r=1 报
                # "找不到结构体布局"（实测 typedef 结构体全挂）。
                for alias, ty in (getattr(d, "typedefs", None) or []):
                    if getattr(ty, "ptr", 0):
                        continue            # 指针别名不是结构体本身
                    table, total = layout(d)
                    structs[alias] = {"members": table, "size": total}
                    if tag:
                        structs.setdefault("struct " + tag,
                                           {"members": table,
                                            "size": total})
            elif isinstance(d, DeclGroup):
                walk_decls(d.decls)

    walk_decls(prog.decls)
    return structs


def _type_size(ty):
    """一个类型占多少字节（教学简化，不做对齐）。"""
    if ty is None:
        return 4
    if getattr(ty, "ptr", 0):
        return 8
    base = (ty.base or "int").replace("struct ", "")
    if getattr(ty, "dims", None) or getattr(ty, "array", None):
        n = 1
        dims = list(getattr(ty, "dims", None) or [])
        if getattr(ty, "array", None):
            dims.insert(0, ty.array)
        for d in dims:
            if isinstance(d, int) and d > 0:
                n *= d
        return n * _scalar_size(base)
    return _scalar_size(base)


def _elem_type_of(ty):
    """数组/指针去掉一层后的元素类型。"""
    if ty is None:
        return None
    ptr = getattr(ty, "ptr", 0)
    if ptr > 0:
        return Type(base=ty.base, ptr=ptr - 1)
    dims = list(getattr(ty, "dims", None) or [])
    if dims:
        rest = dims[1:]
        return Type(base=ty.base, ptr=0,
                    dims=rest, array=(rest[0] if rest else None))
    if getattr(ty, "array", None) is not None:
        return Type(base=ty.base, ptr=0)
    return Type(base=ty.base, ptr=0)


def _is_array(ty):
    if ty is None:
        return False
    if getattr(ty, "dims", None):
        return True
    return getattr(ty, "array", None) is not None


def _array_len(ty):
    """数组元素总数 = 所有维度的乘积。

    早期只返回 dims[0]，int a[2][3] 被当成只有 2 个元素，
    栈槽只分配 8 字节（2×4），但扁平索引会写到 a[0..5] 共 24 字节
    —— 后 4 个元素直接踩进相邻临时量的槽位（实测矩阵转置输出
    0 3 1 3 5 5，a[4] 的值被 t5 覆盖、a[2] 被 t6 覆盖）。
    """
    dims = getattr(ty, "dims", None)
    if dims:
        n = 1
        for d in dims:
            n *= int(d)
        return n
    a = getattr(ty, "array", None)
    return int(a) if a else 0


# ---------------- 单个函数 ----------------

class FuncGen:
    def __init__(self, name, instrs, types, globals_=frozenset()):
        self.name = name
        self.asm = re.sub(r"[^0-9A-Za-z_]", "_", name)
        self.instrs = instrs
        self.types = types
        # 本函数的形参类型覆盖表（不写共享的 self.types，
        # 否则会污染其他函数的同名变量）
        self._type_override = {}
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
        # 浮点：已知是 float 的临时量/变量名，以及浮点常量池
        self.float_tmps = set()
        self._fconst_pool = {}
        self._fconst_data = []
        # 结构体数组元素取址得到的临时量 -> 它指向的布局。
        # a[0].x 里 gen_load 产出 t1 存 a[0] 的地址，
        # 后续 setmem/getmem 要靠这张表知道 t1 是指向哪个结构体的。
        self.struct_ptr_of = {}

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
            # getmem / setmem 的结构体基址在 extra 里，
            # 不扫的话结构体变量根本不会分配栈槽，
            # 后面 lea_base 查不到就报"未知变量"（实测踩到）。
            if ins.op in ("getmem", "setmem") and ins.extra \
                    and _is_name(ins.extra):
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

    def ty_of(self, name):
        """查名字的类型：先看本函数形参覆盖表，再看全局表。"""
        if name in self._type_override:
            return self._type_override[name]
        return self.types.get(name)

    def param_names(self):
        return self._params

    def detect_pointers(self, names):
        """找出哪些名字里装的是指针（地址）。

        IR 不区分"值"和"指向它的指针"，但 `addr` 的 dst、以及从指针
        变量赋值出来的名字都一定是指针，它们必须按 64 位存取。
        """
        ptr = set()
        for n in names:
            ty = self.ty_of(n)
            if ty is None:
                continue
            # 数组优先：int *p[3] 的 ptr 属性也是 1（元素是指针），
            # 但它本身是数组不是指针变量。误判成指针会让 lea_base
            # 取槽里的"值"当地址（实测 *p[0] 访问违例）。
            if _is_array(ty):
                continue
            if getattr(ty, "ptr", 0):
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
                # 自定义的"返回指针的函数"：int *at(int i){ return &g[i]; }
                # 不认这个，*at(1)=99 里接返回值的临时量会被当标量，
                # store 时按 4 字节写，指针高位丢失，写进错误地址
                # （实测 *at(1)=99 报"对非数组做下标写"）。
                if ins.op == "call" and ins.dst \
                        and ins.a1 in getattr(self, "ptr_returning_funcs",
                                              ()):
                    if ins.dst not in ptr:
                        ptr.add(ins.dst)
                        changed = True
                if ins.op == "=" and ins.dst and ins.a1 in ptr:
                    if ins.dst not in ptr:
                        ptr.add(ins.dst)
                        changed = True
                # 指针算术的结果也是指针（t1 = p + 1）。
                # 不提前标记的话，alloc 会给它按 scalar 分配，
                # generate 时 store_reg/load_operand 只搬 32 位。
                if ins.op in ("+", "-") and ins.dst:
                    a1p = (ins.a1 in ptr) or _is_array(self.ty_of(ins.a1))
                    a2p = (ins.a2 in ptr) or _is_array(self.ty_of(ins.a2))
                    if a1p != a2p and ins.dst not in ptr:
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
        # ── 形参类型覆盖 ──
        # collect_types 是全局共享的，同名变量在不同函数里类型可能不同：
        # `int dist2(struct P *p)` 的形参 p 会被 main 里的 `struct P p`
        # 覆盖成非指针，`int f(int a[])` 的 a 会被 main 的 `int a[4]`
        # 覆盖成数组。必须用每个函数自己的形参类型表覆盖回来，
        # 否则指针形参按 32 位存取、数组形参按数组分配
        # （实测 dist2(&p) 得垃圾值、f(a+1) 算错）。
        # 数组形参一律退化成指针。
        # 形参在本函数内一律按退化后的类型看：数组形参 = 指针。
        # 注意：绝不写 self.types（全局共享）——dist2 的形参覆盖会把
        # main 的同名局部变量类型也改掉（实测 dist2(&p) 后 main 的
        # p.x=3 被当成指针解引用，读栈垃圾）。写进局部覆盖表。
        ptypes = getattr(self, "_param_types", None) or {}
        for p in self.param_names():
            ty = ptypes.get(p) or self.ty_of(p)
            if ty is None:
                continue
            if _is_array(ty):
                # 数组形参退化成指针。int a[][3] 的 dims=[3] 是行宽，
                # 保留在类型上（ptr 归一成 1）——gen_addrof 的步长
                # 靠它算一行几个元素（实测丢掉后 sum2 得 18 而非 21）。
                # row_ptr 标记区分两种同形类型：
                #   int a[][3] 形参  -> row_ptr=True，指向数组（行宽 12）
                #   int *p[3]  局部  -> row_ptr=False，指针数组（元素 8）
                pd = list(getattr(ty, "dims", None) or [])
                t = Type(base=(ty.base or "int").replace("struct ", ""),
                         ptr=1, dims=pd)
                t.row_ptr = True
                self._type_override[p] = t
            else:
                self._type_override[p] = ty
        # 全局变量不进栈：它们在 .data / .bss 段，走 RIP 相对寻址。
        # 早期不排除它们，会安静地分配一块栈槽去"存"全局变量，
        # 于是读写全打在栈上，全局变量永远是 0——典型的静默算错。
        # 但 kinds/sizes 仍要登记：gen_load/store 靠 kinds 判"是不是数组"，
        # 不登记的话全局数组 g[3] 访问会报"对非数组做下标访问"
        # （实测全局数组 g[3]={7,8,9} 编译直接被拒）。
        # scan_symbols 只扫函数体，全局段的名字要在这里补。
        for n in getattr(self, "global_names", ()):
            if n in self.kinds:
                continue
            ty = self.ty_of(n)
            if _is_array(ty):
                self.kinds[n] = "array"
                self.sizes[n] = max(_array_len(ty)
                                    * (_elem_size(ty) if ty else 4), 4)
            elif ty is not None and (ty.ptr or 0) > 0:
                self.kinds[n] = "ptr"
            else:
                self.kinds[n] = "scalar"
        names = [n for n in names
                 if n not in getattr(self, "global_names", ())]
        self.ptr_names = self.detect_pointers(names)
        off = 0
        for n in names:
            ty = self.ty_of(n)
            # 数组优先于结构体：`struct P a[3]` 是"结构体数组"，
            # 要按数组分配（size = 元素数 × 元素大小），
            # 不是按单个结构体分配。早期先判断结构体，导致
            # 结构体数组只分到一个元素的槽位，下标访问被拒。
            if _is_array(ty):
                nbytes = _array_len(ty) * self._elem_stride(ty)
                nbytes = max(nbytes, self._elem_stride(ty))
                off += nbytes
                off = (off + 7) // 8 * 8          # 8 字节对齐
                self.slots[n] = off
                self.kinds[n] = "array"
                self.sizes[n] = nbytes
                continue
            # 结构体变量要按布局大小分配，不能只给 8 字节
            _lay, _deref = self._struct_layout_of_type(ty)
            if _lay is not None and not _deref:
                nbytes = _lay["size"]
                off += nbytes
                off = (off + 7) // 8 * 8
                self.slots[n] = off
                self.kinds[n] = "struct"
                self.sizes[n] = nbytes
                continue
            if n in self.ptr_names:
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
            # 浮点常量已支持（xmm 寄存器 + .rdata 常量池）。
            # 早期这里直接报错，整个浮点子系统被一句话排除。
            return
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
            if s in getattr(self, "all_func_names", ()):
                return          # 函数名：取它的地址（函数指针赋值）
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
        if s in getattr(self, "all_func_names", ()):
            # 函数名作为值：取函数地址（lea rip 相对）。
            # int (*f)(int) = sq; 的初始化就靠这个。
            # 标签用函数名本身（.globl sq 就是 sq:）——asm_label
            # 会加函数前缀，链接报 undefined reference（实测踩到）。
            self.line(f"lea {R64.get(reg, reg)}, {s}[rip]")
            return
        off = self.slot(s)
        if self.kinds[s] == "array":
            # 数组名作为值使用时退化成首元素地址（C 标准规则）。
            # `strlen(s)` / `strcpy(a, b)` / `f(s)` 都是这个形态。
            # 早期直接报错，导致所有字符串函数都用不了。
            self.line(f"lea {R64.get(reg, reg)}, [rbp-{off}]")
            return
        # 指针判据要同时看 kinds 和 ptr_names：
        # 指针算术的临时量（t1 = p + 1）槽位在 alloc 时按 scalar 分配，
        # 但运行期它装的是 64 位地址。只看 kinds 会用 mov eax 截断高位
        # （实测 0xC0000005）。
        if self.kinds[s] == "ptr" or s in self.ptr_names:
            self.line(f"mov {R64.get(reg, reg)}, QWORD PTR [rbp-{off}]")
        else:
            self.line(f"mov {reg}, DWORD PTR [rbp-{off}]")

    def store_reg(self, name, reg="eax"):
        if name in getattr(self, "global_names", ()):
            self.line(f"mov {self.mem(name)}, {reg}")
            return
        off = self.slot(name)
        if self.kinds.get(name) == "array":
            raise CodegenError(f"函数 {self.name}: 不能给数组名 '{name}' 赋值")
        # 判据要同时看 kinds 和 ptr_names：addr/addrof 的产物是临时量，
        # kinds 是 scalar 但装的是 64 位地址。只看 kinds 会用
        # mov DWORD 截断高位，栈地址一丢就是野指针
        # （实测 int *p[3]; p[0]=&a; *p[0] 读到垃圾）。
        if self.kinds.get(name) == "ptr" or name in getattr(self, "ptr_names", ()):
            self.line(f"mov QWORD PTR [rbp-{off}], {R64.get(reg, reg)}")
        else:
            self.line(f"mov DWORD PTR [rbp-{off}], {reg}")

    def lea_base(self, reg, name):
        """把名字的地址放进寄存器：数组/标量取栈地址，指针取槽里的值。

        全局变量取的是它在 .data 段的绝对地址（用 lea rip 相对）。
        指针临时量（如 *at(1)=99 里的 t2、int *p[3] 取出的 p[0]）：
        槽里存的就是地址，按指针取。判据必须同时看 kinds 和
        ptr_names——kinds 里它们是 scalar（alloc 时按 4 字节分的槽），
        但运行期装 64 位地址；只看 kinds 会走 lea 分支取到槽地址本身
        （实测 *p[0] 把栈地址当 int 打出来）。
        """
        if name in getattr(self, "global_names", ()):
            label = re.sub(r'[^0-9A-Za-z_]', '_', name)
            self.line(f"lea {reg}, {label}[rip]")
            return
        is_ptr = (self.kinds.get(name) == "ptr"
                  or name in getattr(self, "ptr_names", ()))
        off = self.slot(name)
        if is_ptr:
            self.line(f"mov {reg}, QWORD PTR [rbp-{off}]")
        else:
            # array / scalar / struct 都是取栈上地址。
            # 结构体：slots[n] 存的是它占用的末地址，
            # 基址就是 [rbp-off]（成员按正偏移往上加）。
            self.line(f"lea {reg}, [rbp-{off}]")

    # ---- 指令 ----
    def gen(self):
        self.comment(f"func {self.name}")
        self.label(self.asm)
        self.line("push rbp")
        self.line("mov rbp, rsp")
        self.line(f"sub rsp, {self.frame}")
        # 注意：局部帧清零必须放在参数保存之后——
        # 清零用的 eax/ecx/rdi 正是 Win64 参数寄存器，
        # 先清零会把进参全部冲掉（实测所有带参函数直接算错）。

        params = self.param_names()
        # 前 4 个走寄存器，第 5 个起在栈上。位置 = [rbp+48+8*(i-4)]：
        #   [rbp+0]  存的是旧的 rbp（push rbp 压的）
        #   [rbp+8]  返回地址
        #   [rbp+16] 影子空间起点，[rbp+16, rbp+48) 共 32 字节
        #   [rbp+48] 第 5 个参数（Win64 ABI，实测）
        for i, p in enumerate(params):
            # C 的数组形参本来就会退化成指针：int sum(int a[], int n)
            # 里 a 就是 int*。早期这里直接报"数组参数未实现"，
            # 于是最基本的数组传参函数全部没法编译（实测
            # 冒泡排序/求和这类作业一律失败）。
            # 处理办法：当指针看，槽位存 64 位地址。
            is_arr_param = self.kinds[p] == "array"
            if is_arr_param:
                self.kinds[p] = "ptr"
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

        # 注意：不做全帧清零。清零用的 eax/ecx/rdi 是参数寄存器，
        # 且清零范围覆盖参数槽位本身，会把刚保存的进参冲掉
        # （实测所有带参函数返回 0）。char 槽位的垃圾字节问题
        # 在 addr 指令里按需清零解决。

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
                if self._operand_is_float(ins.a1):
                    # float 函数返回值：main 的返回值是 int，
                    # 要 cvttss2si 截断转整数（C 的 float->int 是截断不是四舍五入）
                    self.load_float("xmm0", ins.a1)
                    self.line("cvttss2si eax, xmm0")
                else:
                    self.load_operand("eax", ins.a1)
            else:
                self.line("xor eax, eax")
            self.line(f"jmp .Lret_{self.asm}")
            return

        # ---- 浮点路径（必须放在 "=" 之前）----
        # 判据：任一操作数是 float 字面量或 float 变量，这条指令就是浮点。
        # 早期把这个判断放在 "=" 之后，导致 `float x = 1.5` 先走了
        # 整数路径，1.5 被当成变量名去查槽位，报"未知变量 '1.5'"。
        if self._is_float_expr(ins):
            # 内存/取址操作即使涉及 float 变量也不是"浮点运算"：
            # &f 只是算地址，不走 xmm。早期一律送进 gen_float_instr，
            # 那里不认识 addr，直接报"浮点指令 'addr' 后端不支持"
            # （实测 scanf("%f",&f) 全挂）。
            if op not in ("addr", "load", "store", "deref", "call",
                          "getmem", "setmem", "addrof", "if", "ifTrue"):
                # 特例：把 float 赋给 int 变量，必须先转成整数再存。
                # 直接走浮点路径会把 32 位浮点位模式原样写进 int 槽
                # （实测 `int r = (int)(1.5f+2.5f)` 输出 1082130432，
                # 那正是 4.0f 的 IEEE 位模式）。
                if op == "=" and ins.dst and self._dst_is_int(ins.dst) \
                        and self._operand_is_float(ins.a1):
                    self.load_float("xmm0", ins.a1)
                    self.line("cvttss2si eax, xmm0")   # 截断，和 C 一致
                    self.store_reg(ins.dst)
                    return
                # ftoi/itof 自己知道怎么转，不能被浮点路径吞掉
                if op in ("ftoi", "itof"):
                    pass                    # 落到下面按普通指令处理
                else:
                    self.gen_float_instr(ins)
                    return

        if op == "=":
            self.load_operand("eax", ins.a1)
            self.store_reg(ins.dst)
            return

        if op in BIN_ALU:
            # 指针算术要先缩放：C 里 `p + 1` 前进的是 sizeof(元素) 字节，
            # 不是 1 字节。IR 里 p+1 就是普通加法（没有类型信息），
            # 所以必须在后端补这一步，否则取到错位的元素（实测：
            # `int *p = a; p = p + 1; return *p;` 直接 0xC0000005）。
            if op in ("+", "-") and self._is_ptr_arith(ins):
                self.gen_ptr_arith(ins)
                return
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

        if op == "bnot":
            # 按位取反：~x 等价于 x XOR -1
            self.load_operand("eax", ins.a1)
            self.line("not eax")
            self.store_reg(ins.dst)
            return

        if op == "itof":
            # 整数 -> float：(float)i。必须 cvtsi2ss，
            # 直接把整数位当浮点存会得到 0.0（实测）。
            self.load_operand("eax", ins.a1)
            self.line("cvtsi2ss xmm0, eax")
            self.store_float(ins.dst, "xmm0")
            if ins.dst:
                self.float_tmps.add(ins.dst)
            return

        if op == "ftoi":
            # float -> int：(int)f。C 用截断，不是四舍五入。
            self.load_float("xmm0", ins.a1)
            self.line("cvttss2si eax, xmm0")
            self.store_reg(ins.dst)
            if ins.dst:
                self.float_tmps.discard(ins.dst)
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

        if op == "getmem":
            self.gen_getmem(ins)
            return
        if op == "setmem":
            self.gen_setmem(ins)
            return

        if op == "addr":
            off = self.slot(ins.a1)
            # char 标量取地址前先清掉槽位：scanf("%c",&c) 只写
            # 1 字节，槽位其余 3 字节是栈残留，后续按 int 读/比较
            # 全是垃圾（实测 scanf("%c",&op) 后 op==43 判 false）。
            # 只清这一格，不动别的变量——全帧清零会冲掉已保存的
            # 参数（实测所有带参函数算错）。
            ty = self.ty_of(ins.a1)
            if ty is not None and (ty.base or "") == "char" \
                    and getattr(ty, "ptr", 0) == 0 \
                    and not getattr(ty, "dims", None):
                self.line(f"mov DWORD PTR [rbp-{off}], 0")
            # 数组名取首地址；标量取槽地址
            self.line(f"lea rax, [rbp-{off}]")
            self.store_reg(ins.dst)
            return

        if op == "addrof":
            self.gen_addrof(ins)
            return

        if op == "deref":
            self.lea_base("rdx", ins.a1)
            # 解引用宽度要按 pointee 的类型：char* 读 1 字节，
            # int* 读 4 字节。早期一律 DWORD，char 指针会读到
            # 相邻 3 个字节（实测 char* 解引用返回 -150060222）。
            esz = self._elem_size_of_ptr(ins.a1)
            if esz == 1:
                self.line("movzx eax, BYTE PTR [rdx]")
            elif esz == 8:
                self.line("mov rax, QWORD PTR [rdx]")
                off = self.slot(ins.dst)
                self.line(f"mov QWORD PTR [rbp-{off}], rax")
                self.ptr_names.add(ins.dst)
                return
            else:
                self.line("mov eax, DWORD PTR [rdx]")
            self.store_reg(ins.dst)
            return

        if op == "nop":
            self.comment("nop: 源程序里有不支持的语法，跳过")
            return

        raise CodegenError(f"函数 {self.name}: 不支持的 IR 指令 '{ins}'")

    def _is_ptr_arith(self, ins):
        """这条 ± 是不是指针算术（一侧是指针/数组，另一侧是整数）。"""
        if self._operand_is_ptr(ins.a1) and not self._operand_is_ptr(ins.a2):
            return True
        if self._operand_is_ptr(ins.a2) and not self._operand_is_ptr(ins.a1):
            return True
        return False

    def _elem_size_of_ptr(self, name):
        """指针指向的元素有多少字节。"""
        ty = self.ty_of(name)
        if ty is None:
            return 4
        base = (ty.base or "int").replace("struct ", "")
        if getattr(ty, "ptr", 0) > 1:
            return 8                 # 指针的指针
        if base.startswith("struct") or base in self.structs:
            lay, _ = self._struct_layout_of_type(
                type("T", (), {"base": ty.base, "ptr": 0})())
            return lay["size"] if lay else 4
        return _scalar_size(base)

    def gen_ptr_arith(self, ins):
        """指针加减整数：结果 = 指针 ± 整数 * sizeof(元素)。

        `p + 1` 前进 sizeof(元素) 字节，不是 1 字节。
        `1 + p` 交换操作数处理。
        `p - q` 这种两个都是指针的情形不在此路径（返回的是元素个数差，
        语义不同），调用方会走普通整数减法。
        """
        op = ins.op
        if self._operand_is_ptr(ins.a1):
            ptr_op, int_op = ins.a1, ins.a2
        else:
            ptr_op, int_op = ins.a2, ins.a1
        esz = self._elem_size_of_ptr(ptr_op)
        # 指针装 64 位
        self.load_operand("rax", ptr_op)
        if _is_int_literal(int_op):
            n = int(int_op)
        else:
            self.load_operand("ecx", int_op)
            n = None
        if n is not None:
            # 立即数直接乘
            self.line(f"imul eax, {n * esz}" if False else
                      f"mov ecx, {n * esz}")
            self.line(f"{'add' if op == '+' else 'sub'} rax, rcx")
        else:
            self.line(f"imul ecx, {esz}")
            # ecx 是有符号的，要符号扩展到 rcx 再参与 64 位运算
            self.line("movsxd rcx, ecx")
            self.line(f"{'add' if op == '+' else 'sub'} rax, rcx")
        # 结果是指针（64 位），必须用 QWORD 存。
        # 早期用 store_reg 走 scalar 路径，mov DWORD [rbp-x], eax
        # 只存低 32 位，取回来就是野指针（实测 0xC0000005）。
        self.ptr_names.add(ins.dst)
        if ins.dst in getattr(self, "global_names", ()):
            label = re.sub(r'[^0-9A-Za-z_]', '_', ins.dst)
            self.line(f"mov QWORD PTR {label}[rip], rax")
        else:
            off = self.slot(ins.dst)
            self.line(f"mov QWORD PTR [rbp-{off}], rax")

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
            elif self._operand_is_ptr(a):
                # 数组名退化的首地址 / 指针变量：都是 64 位值，
                # 必须用 64 位寄存器。用 ecx 装地址会截断高位
                # （实测堆/栈地址高位非零，一截就是野指针）。
                self.load_operand(ARG_REGS64[i], a)
            elif self._operand_is_float(a):
                # 浮点实参：C 的默认实参提升把 float 变 double，
                # 然后按 64 位值走整数寄存器（gcc -O0 实测就是
                # cvtss2sd + movq xmm→rdx，不是走 xmm 寄存器）。
                # 早期直接 load_operand("edx", a) 把 32 位浮点
                # 位模式当 int 传过去 —— printf("%.1f") 打出 0.0。
                self.load_float("xmm0", a)
                self.line("cvtss2sd xmm0, xmm0")     # float -> double
                self.line(f"movq {ARG_REGS64[i]}, xmm0")
            else:
                self.load_operand(ARG_REGS[i], a)

        # 变参调用（printf 家族）必须把 al 置成"用了几个 xmm 寄存器"。
        # 我们走的是整数寄存器传 double，所以是 0。
        if target in VARARG_FUNCS:
            self.line("xor eax, eax")

        # 函数指针调用：target 是本函数里的变量（int (*f)(int) = sq;
        # f(7)），不是函数名。必须间接调用 call rax——直接 call f
        # 会被汇编器当成调用一个叫 f 的函数，链接报未定义
        # （实测函数指针调用全挂）。
        is_func_name = (target in self.funcs
                        if hasattr(self, "funcs") else False)
        if not is_func_name and _is_name(target) \
                and target not in self.globals \
                and (self.kinds.get(target) == "ptr"
                     or target in getattr(self, "ptr_names", ())):
            self.load_operand("rax", target)
            self.line("call rax")
        else:
            self.line(f"call {target}")
        if ins.dst:
            self.store_reg(ins.dst)

    def _operand_is_ptr(self, s):
        """这个 IR 操作数是不是 64 位值（指针或数组名）。"""
        if s in getattr(self, "ptr_names", ()):
            return True
        if self.kinds.get(s) == "array":
            return True                 # 数组名退化成首地址
        return False

    # ---- 浮点 ----
    def _operand_is_float(self, s):
        """这个 IR 操作数是不是 float 值。"""
        if s is None:
            return False
        if _looks_float(s):
            return True                     # 字面量 1.5
        ty = self.ty_of(s)
        if ty is not None and (ty.base or "") in ("float", "double"):
            return True
        return s in self.float_tmps

    def _dst_is_int(self, name):
        """赋值目标是不是整数类型变量。

        用来判断 `int r = float_expr` 这种需要 cvttss2si 的场景。
        临时量（t1/t2）不查——它们的类型由产生它的指令决定。
        """
        ty = self.ty_of(name)
        if ty is None:
            return False                    # 临时量/未知：不插手
        base = (ty.base or "").replace("struct ", "")
        if base in ("float", "double"):
            return False
        if ty.ptr > 0:
            return False                    # 指针另算
        return True

    def _is_float_expr(self, ins):
        """这条指令是不是浮点运算（按 C 的算术提升规则）。"""
        if ins.dst and ins.dst in self.float_tmps:
            return True
        if self._operand_is_float(ins.a1) or self._operand_is_float(ins.a2):
            return True
        return False

    def gen_float_instr(self, ins):
        """生成浮点指令。用 xmm0/xmm1，scalar 单精度。

        只支持 float（32 位）。double 需要 sd 后缀和不同寻址宽度，
        本教学后端先不做，遇到明确报错。
        """
        op = ins.op
        if op == "=":
            self.load_float("xmm0", ins.a1)
            self.store_float(ins.dst, "xmm0")
            if ins.dst:
                self.float_tmps.add(ins.dst)
            return
        if op in ("+", "-", "*", "/"):
            self.load_float("xmm0", ins.a1)
            fop = {"+": "addss", "-": "subss",
                   "*": "mulss", "/": "divss"}[op]
            if self._operand_is_float(ins.a2):
                self.load_float("xmm1", ins.a2)
                self.line(f"{fop} xmm0, xmm1")
            elif _is_int_literal(ins.a2):
                self.line(f"mov eax, {ins.a2}")
                self.line("cvtsi2ss xmm1, eax")
                self.line(f"{fop} xmm0, xmm1")
            else:
                raise CodegenError(
                    f"函数 {self.name}: 浮点运算 '{op}' 的右操作数 "
                    f"'{ins.a2}' 无法识别")
            self.store_float(ins.dst, "xmm0")
            if ins.dst:
                self.float_tmps.add(ins.dst)
            return
        if op in ("<", ">", "<=", ">=", "==", "!="):
            self.load_float("xmm0", ins.a1)
            self.load_float("xmm1", ins.a2)
            self.line("comiss xmm0, xmm1")
            # ucomiss/comiss 设置的是 CF/ZF/PF，不是 SF/OF。
            # 浮点比较必须用无符号的条件码：
            #   xmm0 <  xmm1  ->  CF=1        -> setb
            #   xmm0 >  xmm1  ->  CF=0 且 ZF=0 -> seta
            #   xmm0 <= xmm1  ->  CF=1 或 ZF=1 -> setbe
            #   xmm0 >= xmm1  ->  CF=0        -> setae
            #   ==  -> set  e ；!= -> setne
            # 早期直接复用整数的 SETCC（setl/setg），那是看 SF≠OF 的，
            # 浮点里 SF/OF 恒为 0，于是 a<b 永远算出 0
            # （实测 1.5f < 2.5f 判成 false，printf 不输出）。
            FSETCC = {"<": "b", ">": "a", "<=": "be", ">=": "ae",
                      "==": "e", "!=": "ne"}
            cc = FSETCC.get(op)
            if cc is None:
                raise CodegenError(
                    f"函数 {self.name}: 浮点比较 '{op}' 不支持")
            self.line(f"set{cc} al")
            self.line("movzx eax, al")
            # 比较结果是 int（0/1），不是 float
            self.store_reg(ins.dst)
            if ins.dst:
                self.float_tmps.discard(ins.dst)
            return
        if op == "neg":
            self.load_float("xmm0", ins.a1)
            self.line("mov eax, 0x80000000")
            self.line("movd xmm1, eax")
            self.line("xorps xmm0, xmm1")
            self.store_float(ins.dst, "xmm0")
            if ins.dst:
                self.float_tmps.add(ins.dst)
            return
        raise CodegenError(
            f"函数 {self.name}: 浮点指令 '{op}' 后端不支持")

    def load_float(self, xmm, s):
        """把 float 操作数装进 xmm 寄存器。

        三种来源：字面量（.rdata 常量）、float 变量（槽位）、
        整数（cvtsi2ss 提升）。
        """
        if s is None:
            raise CodegenError(f"函数 {self.name}: 浮点操作数缺失")
        if _looks_float(s):
            label = self._intern_float_const(s)
            self.line(f"movss {xmm}, DWORD PTR {label}[rip]")
            return
        if s in self.float_tmps or self._operand_is_float(s):
            off = self.slot(s)
            self.line(f"movss {xmm}, DWORD PTR [rbp-{off}]")
            return
        if _is_int_literal(s):
            self.line(f"mov eax, {s}")
            self.line(f"cvtsi2ss {xmm}, eax")
            return
        if s in self.slots:
            off = self.slot(s)
            self.line(f"cvtsi2ss {xmm}, DWORD PTR [rbp-{off}]")
            return
        raise CodegenError(
            f"函数 {self.name}: 无法识别的浮点操作数 '{s}'")

    def store_float(self, name, xmm):
        """把 xmm 里的 float 存进变量槽。"""
        if name in getattr(self, "global_names", ()):
            label = re.sub(r'[^0-9A-Za-z_]', '_', name)
            self.line(f"movss DWORD PTR {label}[rip], {xmm}")
            return
        off = self.slot(name)
        self.line(f"movss DWORD PTR [rbp-{off}], {xmm}")

    def _intern_float_const(self, text):
        """把浮点字面量放 .rdata，返回标签（.float 单精度）。"""
        if text not in self._fconst_pool:
            label = f"_fc_{len(self._fconst_pool)}"
            self._fconst_pool[text] = label
            self._fconst_data.append((label, float(text)))
        return self._fconst_pool[text]

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

    def _struct_of(self, name):
        """查一个变量/指针对应的结构体布局表。

        可能是指针（q->x），要剥一层；返回 (布局表, 是否需解引用)。
        """
        # 结构体数组元素取址得到的临时量：它本身就是地址
        if name in self.struct_ptr_of:
            return self.struct_ptr_of[name], True
        return self._struct_layout_of_type(self.ty_of(name))

    def _struct_layout_of_type(self, ty):
        """按类型查结构体布局，返回 (lay, deref) 或 (None, False)。"""
        if ty is None:
            return None, False
        base = (ty.base or "")
        if base.startswith("struct "):
            tag = base[len("struct "):]
        elif base in self.structs:
            tag = base
        else:
            return None, False
        lay = self.structs.get(tag)
        if lay is None:
            return None, False
        return lay, getattr(ty, "ptr", 0) > 0

    def gen_getmem(self, ins):
        """结构体成员读取：t = p.x / t = q->x"""
        memname = ins.a1
        basename = ins.extra
        lay, deref = self._struct_of(basename)
        if lay is None:
            raise CodegenError(
                f"函数 {self.name}: 读成员 {basename}.{memname} 时"
                f"找不到结构体布局（类型未知）")
        info = lay["members"].get(memname)
        if info is None:
            raise CodegenError(
                f"函数 {self.name}: 结构体没有成员 {memname!r}")
        off, size, is_ptr = info
        # 取基址
        if deref:
            # q->x：q 里存的是地址
            self.load_operand("rdx", basename)
        else:
            # p.x：p 本身在栈上，取它的地址
            self.lea_base("rdx", basename)
        if size == 1:
            self.line(f"movzx eax, BYTE PTR [rdx+{off}]")
        elif size == 8 and is_ptr:
            self.line(f"mov rax, QWORD PTR [rdx+{off}]")
        else:
            self.line(f"mov eax, DWORD PTR [rdx+{off}]")
        self.store_reg(ins.dst)

    def gen_setmem(self, ins):
        """结构体成员写入：p.x = v / q->x = v"""
        value = ins.a1
        memname = ins.a2
        basename = ins.extra
        lay, deref = self._struct_of(basename)
        if lay is None:
            raise CodegenError(
                f"函数 {self.name}: 写成员 {basename}.{memname} 时"
                f"找不到结构体布局（类型未知）")
        info = lay["members"].get(memname)
        if info is None:
            raise CodegenError(
                f"函数 {self.name}: 结构体没有成员 {memname!r}")
        off, size, is_ptr = info
        if deref:
            self.load_operand("rdx", basename)
        else:
            self.lea_base("rdx", basename)
        if size == 8 and is_ptr:
            self.load_operand("rax", value)
            self.line(f"mov QWORD PTR [rdx+{off}], rax")
        else:
            self.load_operand("eax", value)
            self.line(f"mov DWORD PTR [rdx+{off}], eax")

    def gen_load(self, ins):
        base, idx = ins.a1, ins.a2
        if not _is_name(base):
            raise CodegenError(f"函数 {self.name}: load 的基址 '{base}' 无法识别")
        # 指针临时量（函数返回的指针）也算合法基址
        if self.kinds.get(base) not in ("array", "ptr") \
                and base not in getattr(self, "ptr_names", ()):
            raise CodegenError(
                f"函数 {self.name}: 对非数组/非指针 '{base}' 做下标访问")
        ty = self.ty_of(base)
        esz = self._elem_stride(ty) if ty is not None else 4
        # 元素是结构体时，load 得到的是"元素地址"而不是值：
        # a[0].x 需要 a[0] 的地址再按成员偏移访问。
        _lay, _ = self._struct_layout_of_type(
            _elem_type_of(ty) if ty is not None else None)
        if _lay is not None:
            self.lea_base("rdx", base)
            if _is_int_literal(idx):
                self.line(f"lea rax, [rdx+{int(idx) * esz}]")
            else:
                self.load_operand("ecx", idx)
                self.line(f"lea rax, [rdx+rcx*{esz}]")
            off = self.slot(ins.dst)
            self.line(f"mov QWORD PTR [rbp-{off}], rax")
            self.ptr_names.add(ins.dst)
            # 记住这个临时量指向哪个结构体，供 getmem/setmem 查布局
            self.struct_ptr_of[ins.dst] = _lay
            return
        # 宽度必须和步长一致：int *p[3] 元素是指针（8 字节），
        # 早期一律 mov eax（4 字节），读出来高 32 位是垃圾
        # （实测 *p[0] 得到随机数而非变量地址）。
        if esz == 1:
            load_ins = "movzx eax, BYTE PTR"
        elif esz == 8:
            load_ins = "mov rax, QWORD PTR"
        else:
            load_ins = "mov eax, DWORD PTR"
        self.lea_base("rdx", base)
        if _is_int_literal(idx):
            self.line(f"{load_ins} [rdx+{int(idx) * esz}]")
        elif esz in (1, 2, 4, 8):
            self.load_operand("ecx", idx)
            self.line(f"{load_ins} [rdx+rcx*{esz}]")
        else:
            # 非 2 幂步长（int a[][3] 的行宽 12）：先乘再加
            self.load_operand("ecx", idx)
            self.line(f"imul rcx, {esz}")
            self.line(f"add rdx, rcx")
            self.line(f"{load_ins} [rdx]")
        if esz == 8:
            # 指针元素：整个 64 位都是有效值
            off = self.slot(ins.dst)
            self.line(f"mov QWORD PTR [rbp-{off}], rax")
            self.ptr_names.add(ins.dst)
        else:
            self.store_reg(ins.dst)

    def gen_addrof(self, ins):
        """&base[idx]：算元素地址，不读值。

        和 gen_load 的区别只有最后一步——不 load，直接把
        lea 算出来的地址当结果。早期 &a[i] 走的是"先 load 再
        取临时槽地址"，于是 scanf("%d",&a[i]) 写进临时槽，
        数组根本没变（实测数组输入全读成垃圾值）。
        """
        base, idx = ins.a1, ins.a2
        if not _is_name(base):
            raise CodegenError(
                f"函数 {self.name}: addrof 的基址 '{base}' 无法识别")
        if self.kinds.get(base) not in ("array", "ptr") \
                and base not in getattr(self, "ptr_names", ()):
            raise CodegenError(
                f"函数 {self.name}: 对非数组/非指针 '{base}' 取元素地址")
        ty = self.ty_of(base)
        # addrof 算的是"元素地址"，步长用 _elem_size（含行宽语义）：
        # 形参 int a[][3] 的 &a[i] 要 12（一行），load 的扁平索引要 4。
        # 混用会把 a[i] 的地址算错（实测 sum2 得 18）。
        esz = _elem_size(ty) if ty is not None else 4

        self.lea_base("rdx", base)
        if _is_int_literal(idx):
            self.line(f"lea rax, [rdx+{int(idx) * esz}]")
        elif esz in (1, 2, 4, 8):
            self.load_operand("ecx", idx)
            self.line(f"lea rax, [rdx+rcx*{esz}]")
        else:
            # 非 2 幂步长：先乘再加
            self.load_operand("ecx", idx)
            self.line(f"imul rcx, {esz}")
            self.line(f"add rdx, rcx")
            self.line("mov rax, rdx")

        off = self.slot(ins.dst)
        self.line(f"mov QWORD PTR [rbp-{off}], rax")
        self.ptr_names.add(ins.dst)
        # 记住它指向什么类型，后续 deref 才知道读几个字节
        elem = _elem_type_of(ty) if ty is not None else None
        lay, _ = self._struct_layout_of_type(elem)
        if lay is not None:
            self.struct_ptr_of[ins.dst] = lay

    def _elem_stride(self, ty):
        """load/store 的步长 = 单个元素大小。

        和 _elem_size 的区别：int(*)[3]（退化形参 int a[][3]）在
        _elem_size 里返回行宽 12（给 &a[i] 用），但 IR 的扁平索引
        a[i*3+j] 已经除过行宽，步长必须是元素大小 4。
        早期直接复用 _elem_size，imul 乘了两次（实测 sum2 得 65530）。
        row_ptr 区分同形类型：int a[][3] 形参（行宽）vs int *p[3]
        局部指针数组（元素 8 字节）——两者都是 ptr=1 dims=[3]。
        """
        if ty is None:
            return 4
        if getattr(ty, "row_ptr", False):
            # 形参 int a[][3]：扁平索引场景，步长 = 标量大小
            return _scalar_size(getattr(ty, "base", "") or "int")
        if getattr(ty, "ptr", 0) and (getattr(ty, "dims", None)
                                      or getattr(ty, "array", None)):
            # 局部 int *p[3]：元素是指针，8 字节
            return 8
        elem = _elem_type_of(ty)
        lay, _ = self._struct_layout_of_type(elem)
        if lay is not None:
            return lay["size"]
        return _elem_size(ty)

    def _scaled_addr(self, rdx_base, idx, esz):
        """算 [base + idx*esz] 的地址表达式。

        x86 的 scale 只支持 1/2/4/8。esz=12（int[3] 一行）这种
        非 2 幂步长必须先 imul 到寄存器再加——直接写 rcx*12
        汇编器报 "not a valid base/index expression"（实测
        sum2(int a[][3]) 链接失败）。
        返回 (addr_expr, 是否需要先算寄存器)。
        """
        if esz in (1, 2, 4, 8):
            if _is_int_literal(idx):
                return f"[{rdx_base}+{int(idx) * esz}]", False
            self.load_operand("ecx", idx)
            return f"[{rdx_base}+rcx*{esz}]", False
        # 非 2 幂：先算 idx*esz 到 eax
        if _is_int_literal(idx):
            off = int(idx) * esz
            return f"[{rdx_base}+{off}]", False
        self.load_operand("eax", idx)
        self.line(f"imul eax, {esz}")
        self.line(f"add rax, {rdx_base}")
        return "[rax]", True

    def gen_store(self, ins):
        base, idx, val = ins.extra, ins.a1, ins.a2
        if not _is_name(base):
            raise CodegenError(
                f"函数 {self.name}: store 的目标 '{base}' 无法识别")
        # 指针写：base 是数组名、指针变量，或"返回指针的函数"的
        # 返回值临时量（*at(1)=99 里的 t2）。后者不在 kinds 里
        # （临时量不分配槽位类型），但一定在 ptr_names 里。
        # 早期只查 kinds，*at(1)=99 直接报"对非数组做下标写"（实测）。
        ok_base = (self.kinds.get(base) in ("array", "ptr")
                   or base in getattr(self, "ptr_names", ()))
        if not ok_base:
            raise CodegenError(
                f"函数 {self.name}: 对非数组/非指针 '{base}' 做下标写")
        ty = self.ty_of(base)
        esz = self._elem_stride(ty) if ty is not None else 4
        # 宽度必须和步长一致：int *p[3] 元素是指针（8 字节），
        # 早期 width 一律 DWORD，地址按 8 步进但只写 4 字节，
        # 高 4 字节留栈垃圾，读出来是随机地址（实测 *p[0] 得 559937664）。
        if esz == 1:
            width, valreg = "BYTE", "r8b"
        elif esz == 8:
            width, valreg = "QWORD", "r8"
        else:
            width, valreg = "DWORD", "r8d"
        self.lea_base("rdx", base)
        if _is_int_literal(idx):
            addr = f"[rdx+{int(idx) * esz}]"
        else:
            self.load_operand("ecx", idx)
            addr = f"[rdx+rcx*{esz}]" if esz in (1, 2, 4, 8) else None
            if addr is None:
                # 非 2 幂步长：先算 idx*esz 再加基址
                self.line(f"imul rcx, {esz}")
                self.line(f"add rdx, rcx")
                addr = "[rdx]"
        # 值先算到 eax，但地址用了 ecx；如果值依赖 ecx 会冲突，
        # 所以先把值放进 r8，再写内存。
        # 注意宽度匹配：char 数组存 1 字节，但 load_operand 加载
        # 临时量时按 kinds 出 32/64 位。直接给 r8b 会生成
        # "mov r8b, DWORD PTR" 这种宽度不一致的指令（汇编器报
        # operand type mismatch，实测 char 数组动态写入全挂）。
        # 解决：统一加载到 32 位 r8d，存储时按 esz 取对应宽度。
        self.load_operand("r8d", val)
        if esz == 1:
            self.line(f"mov BYTE PTR {addr}, r8b")
        elif esz == 8:
            self.line(f"mov QWORD PTR {addr}, r8")
        else:
            self.line(f"mov DWORD PTR {addr}, r8d")


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
    structs = collect_structs(prog)
    funcs, global_inits = split_functions(code)

    # 找出"返回指针的函数"：函数体 return 的名字是 addr/addrof 的
    # 产物（&x / &a[i]），或类型就是指针。
    # 自定义的 int *at(int i){ return &g[i]; } 就要靠这个识别，
    # 否则 *at(1)=99 会按 4 字节写指针（实测报"对非数组做下标写"）。
    ptr_returning = set()
    for fname, instrs in funcs:
        addr_dsts = set()
        for ins in instrs:
            if ins.op in ("addr", "addrof") and ins.dst:
                addr_dsts.add(ins.dst)
        for ins in instrs:
            if ins.op == "return" and ins.a1 and _is_name(ins.a1):
                ty = types.get(ins.a1)
                if (ty is not None and getattr(ty, "ptr", 0)) \
                        or ins.a1 in addr_dsts:
                    ptr_returning.add(fname)
                    break

    # 全局变量：早期直接拒绝（"全局变量不进 IR，后端不支持"）。
    # 但 IR 侧早已把它们放进来了（global X: + 初始化指令），
    # 后端再拒绝就是自相矛盾。现在真的支持：进 .data / .bss 段。
    global_names = set(n for n, _ in global_inits)
    # 字符串字面量池（跨函数去重）
    str_pool = {}
    seen_str_labels = set()
    # 浮点常量池（跨函数去重）
    fc_pool = {}
    seen_fc_labels = set()

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
        fg.structs = structs
        fg.ptr_returning_funcs = ptr_returning
        # 本函数自己的形参类型表（避免同名局部变量污染）
        fg._param_types = _param_types_of(prog, name)
        # 全部函数名：函数名当值用时要取它的地址（函数指针）
        fg.all_func_names = frozenset(n for n, _ in funcs)
        fg.funcs = frozenset(n for n, _ in funcs)
        fg.alloc()
        out.extend(fg.gen())
        # 收集本函数用到的字符串字面量（跨函数去重）
        for text, label in fg._str_pool.items():
            if label not in seen_str_labels:
                seen_str_labels.add(label)
                str_pool[text] = label
        # 浮点常量同样跨函数去重
        for text, label in fg._fconst_pool.items():
            if label not in seen_fc_labels:
                seen_fc_labels.add(label)
                fc_pool[text] = label
    # 字符串字面量放 .rdata，必须在 .text 之后、且所有函数都生成完
    # 之后统一输出（标签跨函数共享，重复定义会链接失败）。
    if str_pool or fc_pool:
        out.append("")
        out.append('\t.section .rdata,"dr"')
        for text, label in sorted(str_pool.items(), key=lambda x: x[1]):
            esc = _asm_escape(text)
            out.append(f"{label}:")
            out.append(f'\t.asciz "{esc}"')
        for text, label in sorted(fc_pool.items(), key=lambda x: x[1]):
            out.append(f"{label}:")
            out.append(f"\t.float {text}")
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
    有初值的进 .data，没有的进 .bss（全零）。

    数组全局：
    - 无初值 int a[10]; -> .bss 全零，大小 = 元素数 × 元素字节
    - 带初值 int a[3]={7,8,9}; -> .data 逐元素 .long
    早期直接报"不支持"，但全局数组是学生作业高频写法
    （int g[100]; 这种统计表），对照测试实测必须支持。
    """
    data, bss = [], []
    for name, instrs in global_inits:
        label = re.sub(r'[^0-9A-Za-z_]', '_', name)
        ty = types.get(name)
        if _is_array(ty):
            total = _array_len(ty)
            # 模块级函数，没有 self——直接用 _elem_size
            esz = _elem_size(ty) if ty is not None else 4
            nbytes = max(total * esz, esz)
            if not instrs:
                bss.append((label, nbytes))
                continue
            # 逐元素收集：IR 里是 store extra=name a1=idx a2=val
            vals = {}
            for ins in instrs:
                if ins.op == "store" and ins.extra == name:
                    idx = ins.a1
                    v = ins.a2
                    if _is_int_literal(idx) and _is_int_literal(v):
                        vals[int(idx)] = int(v)
                    else:
                        raise CodegenError(
                            f"全局数组 {name} 的初值不是常量，后端不支持")
            # 补零到 total
            elems = [vals.get(i, 0) for i in range(total)]
            data.append((label, elems, esz))
            continue
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
        data.append((label, val, 4))
    out = []
    if data:
        out.append("\t.data")
        for label, val, esz in data:
            out.append(f"\t.globl {label}")
            out.append(f"\t.align 4")
            out.append(f"{label}:")
            if isinstance(val, list):
                # 数组：逐元素输出。char 数组用 .byte
                if esz == 1:
                    out.append("\t.byte " + ", ".join(str(v) for v in val))
                else:
                    out.append("\t.long " + ", ".join(str(v) for v in val))
            else:
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


def _param_types_of(prog, fname):
    """某个函数的 形参名 -> Type 表。

    collect_types 的全局表会被同名局部变量污染
    （int dist2(struct P *p) 的 p 被 main 的 struct P p 覆盖），
    所以每个函数要单独拿自己的形参类型。
    """
    for d in prog.decls:
        if isinstance(d, FuncDef) and d.name == fname:
            out = {}
            for p in d.params:
                if isinstance(p, (tuple, list)) and len(p) == 2:
                    ty, pn = p
                    pn = getattr(pn, "name", pn)
                    if pn:
                        out[pn] = ty
                elif getattr(p, "name", None):
                    out[p.name] = getattr(p, "vtype", None)
            return out
    return {}


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
    # ---- 浮点（p42..p47）----
    # 用 xmm 寄存器 + .rdata 里的 .float 常量。
    # 关键：浮点判断必须放在 "=" 分支之前。放之后的话
    # `float x = 1.5` 会先走整数路径，1.5 被当成变量名查槽位，
    # 报"未知变量 '1.5'"（实测踩到）。
    ("p42_float_const", """int main() {
    float x = 1.5f;
    return (int)x;
}"""),
    ("p43_float_add", """int main() {
    float x = 1.5f;
    float y = x + 2.5f;
    return (int)y;
}"""),
    ("p44_float_mul", """int main() {
    float x = 2.5f;
    float y = x * 4.0f;
    return (int)y;
}"""),
    ("p45_float_div", """int main() {
    float x = 7.0f;
    float y = x / 2.0f;
    return (int)y;
}"""),
    ("p46_float_compare", """int main() {
    float x = 1.5f;
    if (x > 1.0f) { return 10; }
    return 20;
}"""),
    ("p47_float_in_loop", """int main() {
    float s = 0.0f;
    int i = 0;
    for (i = 0; i < 4; i = i + 1) { s = s + 1.5f; }
    return (int)s;
}"""),
    # ---- 结构体（p48..p52）----
    # IR 新增 getmem / setmem 两条指令：把"结构体基址 + 成员名"
    # 分开传，偏移由后端按布局算。
    # 踩到的坑：
    #   1. Assign 早期只处理 Ident/Index/*p 三种目标，Member 落到
    #      return v，p.x = 1 这类赋值被整个丢弃（IR 里根本没有指令）
    #   2. scan_symbols 不扫 getmem/setmem 的 extra，结构体变量
    #      分不到栈槽，后面报"未知变量"
    # 布局用紧密排列（不做对齐填充），教学简化，报告里标注了。
    ("p48_struct_basic", """struct P { int x; int y; };
int main() {
    struct P p;
    p.x = 1; p.y = 2;
    return p.x + p.y;
}"""),
    ("p49_struct_read", """struct P { int x; };
int main() {
    struct P p;
    p.x = 42;
    return p.x;
}"""),
    ("p50_struct_ptr", """struct P { int x; };
int main() {
    struct P p;
    struct P *q = &p;
    q->x = 7;
    return q->x;
}"""),
    ("p51_struct_three", """struct P { int a; int b; int c; };
int main() {
    struct P p;
    p.a = 1; p.b = 2; p.c = 3;
    return p.a * 100 + p.b * 10 + p.c;
}"""),
    ("p52_struct_printf", """#include <stdio.h>
struct P { int x; int y; };
int main() {
    struct P p;
    p.x = 3; p.y = 4;
    printf("sum=%d\\n", p.x + p.y);
    return 0;
}"""),
    # ---- 指针算术缩放（p53..p57）----
    # C 里 p+1 前进 sizeof(元素) 字节，IR 里 p+1 是普通加法，
    # 所以必须在后端补缩放。踩到两个坑：
    #   1. 不缩放：p+1 只前进 1 字节，取到错位数据 -> 0xC0000005
    #   2. 缩放对了但用 mov DWORD 存结果，64 位地址被截断
    #      -> 还是 0xC0000005。必须看 kinds 和 ptr_names 两个判据。
    ("p53_ptr_add1", """int main() {
    int a[3];
    a[0] = 10; a[1] = 20; a[2] = 30;
    int *p = a;
    p = p + 1;
    return *p;
}"""),
    ("p54_ptr_sub1", """int main() {
    int a[3];
    a[0] = 10; a[1] = 20; a[2] = 30;
    int *p = a + 2;
    p = p - 1;
    return *p;
}"""),
    ("p55_ptr_add2", """int main() {
    int a[3];
    a[0] = 10; a[1] = 20; a[2] = 30;
    int *p = a;
    p = p + 2;
    return *p;
}"""),
    ("p56_ptr_walk_sum", """int main() {
    int a[4];
    int i = 0;
    int s = 0;
    for (i = 0; i < 4; i = i + 1) { a[i] = i * 10; }
    int *p = a;
    for (i = 0; i < 4; i = i + 1) {
        s = s + *p;
        p = p + 1;
    }
    return s;
}"""),
    ("p57_char_ptr_stride", """int main() {
    char a[3];
    a[0] = 65; a[1] = 66; a[2] = 67;
    char *p = a;
    p = p + 1;
    return *p;
}"""),
    # ---- 结构体数组（p58..p60）----
    # 两个坑：数组判断要优先于结构体判断（否则按单个结构体分配
    # 一个槽位）；gen_load 对结构体元素要产出"地址"而不是值。
    ("p58_struct_array_one", """struct P { int x; };
int main() {
    struct P a[3];
    a[0].x = 5;
    return a[0].x;
}"""),
    ("p59_struct_array_many", """struct P { int x; };
int main() {
    struct P a[3];
    a[0].x = 1; a[1].x = 2; a[2].x = 3;
    return a[0].x * 100 + a[1].x * 10 + a[2].x;
}"""),
    ("p60_struct_array_two_members", """struct P { int x; int y; };
int main() {
    struct P a[2];
    a[1].x = 7; a[1].y = 8;
    return a[1].x + a[1].y;
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
