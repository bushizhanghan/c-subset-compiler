#!/usr/bin/env python3
"""IR 解释执行器：把 src/ir.py 生成的三地址码真正"跑"一遍。

为什么需要它
------------
csub 的 IR 此前只被"看"过，没有被"执行"过。常量折叠、死代码消除、
break/continue/switch 的跳转到底对不对，光看文本是看不出来的——
看起来合理的 `goto` 完全可能跳到错误的位置。解释执行器是验证
IR 语义的最直接手段：按 IR 走一遍，结果与 gcc 编译真实运行的结果比对。

支持的指令（覆盖 IRGen 目前实际会生成的全部形式）
------------------------------------------------
  dst = a1                 赋值 / 常量
  dst = a1 op a2           + - * / % < > <= >= == != && || & | ^ << >>
  dst = -a1                neg
  dst = !a1                not
  dst = &a1                addr
  dst = *a1                deref
  dst = a1[a2]             load
  a1[i] = v                store
  label:                   label
  goto L                   goto
  ifFalse a goto L         if
  if a goto L              ifTrue
  return [a]               return
  dst = call f(a, b)       call（用户函数 + 有限内置函数）
  call f(a, b)             call（丢弃返回值）
  param a1                 param（保留兼容，当前 IRGen 不使用）

设计要点
--------
1. 纯 Python、零依赖、不读 src/：只吃 IR 文本，因此可以独立验证。
2. 步数上限：死循环必须终止。默认 10 万步，超限报错而不是挂死。
3. 调用深度上限：无限递归在 C 里是未定义行为，这里也要有硬边界，
   不能把 Python 的 RecursionError 抛给用户。
4. 语义按 C 来，不按 Python 来：
   - 除法向零截断（Python 的 // 是向下取整，-7/2 会变成 -4）
   - 取模符号跟被除数走（-7 % 2 在 C 里是 -1，Python 是 1）
   - 逻辑 && || 结果是 0/1；条件判断是"非零为真"
   - 读取未定义变量给 0 并记录 note（真实 C 是未定义行为，
     但 IR 里没有全局初始化，直接崩掉反而看不到结果）

命令行:
  python tools/ir_exec.py file.c              生成 IR 并执行 main，打印返回值
  python tools/ir_exec.py -e ir.txt           执行 IR 文本文件（取 main）
  python tools/ir_exec.py --entry f file.c    执行指定函数
  python tools/ir_exec.py --trace file.c      打印执行轨迹
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------- 异常

class IRExecError(Exception):
    """解释执行过程中的可预期错误（不是解释器 bug）。"""


class IRParseError(IRExecError):
    """IR 文本无法解析。"""


class StepLimitExceeded(IRExecError):
    """步数超限：几乎一定是死循环。"""


class CallDepthExceeded(IRExecError):
    """调用深度超限。"""


class UnsupportedCall(IRExecError):
    """调用了既不是用户函数、也没有内置实现的函数。"""


# ---------------------------------------------------------------- 数据模型

@dataclass
class Instr:
    """一条已解析的 IR 指令。raw 保留原始文本，报错时能指出是哪一行。"""
    op: str
    dst: Optional[str] = None
    a1: Optional[str] = None
    a2: Optional[str] = None
    extra: str = ""
    raw: str = ""
    line: int = 0


@dataclass
class Array:
    """稀疏数组：索引 -> 值，缺省 0。

    IR 里没有数组声明，只有 load/store，所以这里用"用到即存在"的
    稀疏结构，而不是开一块固定大小的内存。
    """
    cells: dict = field(default_factory=dict)

    def get(self, idx: Any) -> Any:
        return self.cells.get(_as_index(idx), 0)

    def set(self, idx: Any, value: Any) -> None:
        self.cells[_as_index(idx)] = value


@dataclass
class Ref:
    """&x 得到的引用；deref / store 通过它回到变量本身。"""
    name: str


def _as_index(idx: Any) -> Any:
    if isinstance(idx, bool):
        return int(idx)
    if isinstance(idx, float) and idx.is_integer():
        return int(idx)
    return idx


@dataclass
class Function:
    name: str
    params: list                      # [(name, default?)] 只取名字
    code: list                        # list[Instr]
    index: dict                       # 标签 -> 指令下标
    start: int = 0


@dataclass
class Program:
    functions: dict = field(default_factory=dict)   # 名字 -> Function

    def has(self, name: str) -> bool:
        return name in self.functions


@dataclass
class ExecResult:
    ok: bool
    value: Optional[int] = None
    steps: int = 0
    error: Optional[str] = None
    notes: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    exit_code: Optional[int] = None       # value & 0xFF，与进程退出码同口径

    def as_dict(self):
        return {
            "ok": self.ok,
            "value": self.value,
            "exit_code": self.exit_code,
            "steps": self.steps,
            "error": self.error,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------- 文本解析

_BINOP_ALT = (r"<<|>>|<=|>=|==|!=|&&|\|\||[+\-*/%<>&|^]")

# 说明：带 .+ / .* 的模式统一用 re.S。因为 ir.py 会把 C 源码里的 "a\nb"
# 渲染成真正的换行，字符串字面量可能跨行，不能让 . 在换行处停下。
# dst = ...
_RE_ASSIGN = re.compile(r"^([A-Za-z_]\w*)\s*=\s*(.+)$", re.S)
# a[i] = v
_RE_STORE = re.compile(r"^([A-Za-z_]\w*)\[([^\[\]]+)\]\s*=\s*(.+)$", re.S)
# t = a1 op a2（两侧都要求空格，避免把 -7 拆成二元减法）
_RE_BIN = re.compile(r"^(\S+)\s+(" + _BINOP_ALT + r")\s+(\S+)$", re.S)
# t = f(a, b)
_RE_CALL = re.compile(
    r"^(?:([A-Za-z_]\w*)\s*=\s*)?call\s+([A-Za-z_]\w*)\s*\((.*)\)$", re.S)
# t = a[i]
_RE_LOAD = re.compile(r"^([A-Za-z_]\w*)\[([^\[\]]+)\]$", re.S)
# t = -a / !a / &a / *a
_RE_UNARY = re.compile(r"^([-!&*])\s*(\S+)$", re.S)

_RE_LABEL = re.compile(r"^([A-Za-z_][\w .]*):$")
_RE_FUNC_START = re.compile(r"^func\s+([A-Za-z_]\w*):$")
_RE_FUNC_END = re.compile(r"^end\s+func\s+([A-Za-z_]\w*):$")
_RE_GOTO = re.compile(r"^goto\s+(\S+)$")
_RE_IFFALSE = re.compile(r"^ifFalse\s+(\S+)\s+goto\s+(\S+)$")
_RE_IFTRUE = re.compile(r"^if\s+(\S+)\s+goto\s+(\S+)$")
_RE_RETURN = re.compile(r"^return(?:\s+(.+))?$", re.S)
_RE_PARAM = re.compile(r"^param\s+(.+)$", re.S)


def _strip_comment(line: str) -> str:
    """去掉 ';' 之后的内容。引号里的 ';' 不能当注释。"""
    out, in_str, quote = [], False, ""
    for ch in line:
        if in_str:
            out.append(ch)
            if ch == quote:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str, quote = True, ch
            out.append(ch)
            continue
        if ch == ";":
            break
        out.append(ch)
    return "".join(out)


def _iter_ir_lines(text: str):
    """把展示用的 IR 文本（含 '; ---- 优化前 ----' 分隔行）拆成指令行。

    同时过滤 ir.py 输出里的统计行（'; 指令数 42 -> 40'）。

    特别处理字符串字面量里的换行：ir.py 把 C 源码里的 "\\n" 渲染成
    真正的换行，于是 `call printf("a\nb")` 在 IR 文本里会跨两行。
    这里把引号没有闭合的行并到下一条，还原成完整指令再解析。
    """
    buf, in_str, quote = "", False, ""

    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("#"):
            continue

        buf = f"{buf}{chr(10)}{line}" if buf else line

        # 用同样的规则扫一遍，确认引号是否在本行闭合
        in_str, quote = False, ""
        for ch in buf:
            if in_str:
                if ch == quote:
                    in_str = False
            elif ch in ("'", '"'):
                in_str, quote = True, ch

        if in_str:
            continue                 # 字符串还没结束，继续拼下一行

        yield buf, buf
        buf = ""

    if buf:                          # 收尾：引号始终没闭合也照常交出去
        yield buf, buf


def _split_args(s: str) -> list:
    """按逗号切参数，忽略引号和括号内部的逗号。"""
    parts, cur, depth, in_str, quote = [], [], 0, False, ""
    for ch in s:
        if in_str:
            cur.append(ch)
            if ch == quote:
                in_str = False
            continue
        if ch in ("'", '"'):
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
    if tail or parts:
        parts.append(tail)
    return [p for p in parts if p != ""] if parts else []


def parse_instr(text: str) -> Optional[Instr]:
    """把一行 IR 文本解析成 Instr；空行/纯标签返回 None 或 label 指令。"""
    t = text.strip()
    if not t:
        return None

    # 标签：func f: / end func f: / L1: / case_1_L2:
    m = _RE_LABEL.match(t)
    if m:
        return Instr("label", extra=m.group(1))

    m = _RE_GOTO.match(t)
    if m:
        return Instr("goto", extra=m.group(1))

    m = _RE_IFFALSE.match(t)
    if m:
        return Instr("if", a1=m.group(1), extra=m.group(2))

    m = _RE_IFTRUE.match(t)
    if m:
        return Instr("ifTrue", a1=m.group(1), extra=m.group(2))

    m = _RE_RETURN.match(t)
    if m:
        return Instr("return", a1=(m.group(1).strip() if m.group(1) else None))

    m = _RE_PARAM.match(t)
    if m:
        return Instr("param", a1=m.group(1).strip())

    m = _RE_CALL.match(t)
    if m:
        return Instr("call", dst=m.group(1), a1=m.group(2),
                     extra=m.group(3).strip())

    m = _RE_STORE.match(t)
    if m:
        return Instr("store", extra=m.group(1), a1=m.group(2).strip(),
                     a2=m.group(3).strip())

    m = _RE_ASSIGN.match(t)
    if m:
        dst, rhs = m.group(1), m.group(2).strip()

        mc = _RE_CALL.match(rhs)
        if mc:
            return Instr("call", dst=dst, a1=mc.group(2),
                         extra=mc.group(3).strip())

        mb = _RE_BIN.match(rhs)
        if mb:
            return Instr(mb.group(2), dst=dst, a1=mb.group(1), a2=mb.group(3))

        mu = _RE_UNARY.match(rhs)
        if mu:
            sym = mu.group(1)
            op = {"-": "neg", "!": "not", "&": "addr", "*": "deref"}[sym]
            return Instr(op, dst=dst, a1=mu.group(2))

        ml = _RE_LOAD.match(rhs)
        if ml:
            return Instr("load", dst=dst, a1=ml.group(1), a2=ml.group(2))

        return Instr("=", dst=dst, a1=rhs)

    raise IRParseError(f"无法解析的 IR 行：{text!r}")


def parse_program(lines, param_map: dict = None) -> Program:
    """把 IR 文本（str 或多行 list）解析成可执行 Program。

    param_map: 函数名 -> 形参名列表。IR 文本本身不记录形参名
    （`func add:` 后面直接就是函数体），而形参绑定是正确执行的必要条件，
    所以调用方要把 AST 里的形参名传进来。不传时形参按位置名 p0/p1...
    兜底，递归调用仍然能跑对。
    """
    if isinstance(lines, str):
        raw_lines = list(_iter_ir_lines(lines))
    else:
        raw_lines = []
        for item in lines:
            if "\n" in str(item):
                raw_lines.extend(_iter_ir_lines(str(item)))
            else:
                raw_lines.extend(_iter_ir_lines(str(item)))

    prog = Program()
    cur: Optional[Function] = None

    for raw, clean in raw_lines:
        m = _RE_FUNC_START.match(clean)
        if m:
            if cur is not None:
                raise IRParseError(f"函数 {cur.name} 里又出现 func 标记")
            name = m.group(1)
            params = list((param_map or {}).get(name) or [])
            cur = Function(name=name, params=params, code=[], index={})
            prog.functions[name] = cur
            continue
        m = _RE_FUNC_END.match(clean)
        if m:
            cur = None
            continue

        if cur is None:
            # 顶层散落的指令（例如只截取了循环体）挂到一个匿名函数上
            if not prog.has("__top__"):
                prog.functions["__top__"] = Function(
                    name="__top__", params=[], code=[], index={})
            cur = prog.functions["__top__"]
        ins = parse_instr(clean)
        if ins is None:
            continue
        ins.raw = clean
        ins.line = len(cur.code)
        if ins.op == "label":
            cur.index[ins.extra] = len(cur.code)
            # 标签本身不需要执行，但为了执行轨迹可读仍保留
            cur.code.append(ins)
        else:
            cur.code.append(ins)

    # 清理：__top__ 只在没有显式函数时保留
    if prog.has("__top__") and len(prog.functions) > 1:
        prog.functions.pop("__top__")
    # 形参名兜底推断：手写 IR 片段不带形参表，从函数体里"只读不写"的
    # 名字倒推。真实的验证路径会由 compile_source 直接给出 param_map。
    for fn in prog.functions.values():
        fn.start = 0
        if not fn.params:
            fn.params = infer_params(fn)
            fn.params_inferred = True
    return prog


_DEF_OPS = {"=", "+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=",
            "&&", "||", "&", "|", "^", "<<", ">>", "neg", "not", "addr",
            "deref", "load", "call"}


def infer_params(fn: Function) -> list:
    """从函数体倒推形参名：先读后写、或只读不写的名字。

    这只是手写 IR 的兜底。IR 文本本身不存形参表，所以真实链路
    （compile_source）一律传精确的 param_map，不走这里。
    """
    defined, read = set(), []
    for ins in fn.code:
        if ins.op in _DEF_OPS and ins.dst:
            defined.add(ins.dst)
        if ins.op == "store" and ins.extra:
            defined.add(ins.extra)
        for f in (ins.a1, ins.a2):
            if not f:
                continue
            for name in re.findall(r"[A-Za-z_]\w*", f):
                if name not in defined and name not in read:
                    read.append(name)
    # 排除临时变量和明显不是变量的东西
    return [n for n in read if not n.startswith("t")]


def load_ir_from_source(path: str, optimize: bool = True):
    """用 csub 前端把 .c 源码编译成 IR 文本 + 形参表（不执行）。

    返回 (ir_lines: list[str], param_map: dict)。
    """
    with open(path, encoding="utf-8") as f:
        return compile_source(f.read(), optimize=optimize)


def param_map_from_ast(cprog) -> dict:
    """从 C 的 AST 里取出 函数名 -> 形参名列表。

    IR 文本不记录形参名（`func add:` 后面直接就是函数体），
    而形参绑定是正确执行的必要条件，所以要单独带过来。
    """
    from parser import FuncDef                      # noqa: E402

    param_map = {}
    for d in cprog.decls:
        if isinstance(d, FuncDef):
            names = []
            for p in (d.params or []):
                if isinstance(p, tuple) and len(p) >= 2:
                    names.append(p[1])
                elif isinstance(p, str):
                    names.append(p)
                else:
                    names.append(getattr(p, "name", f"p{len(names)}"))
            param_map[d.name] = names
    return param_map


def compile_source(text: str, optimize: bool = True):
    """C 源码 -> (IR 行列表, 函数名->形参名列表)。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_dir = os.path.join(root, "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    from parser import parse as parse_c                # noqa: E402
    from ir import generate as gen_ir                  # noqa: E402

    cprog = parse_c(text)
    result = gen_ir(cprog, optimize=optimize)
    lines = result["optimized"] if optimize and result["optimized"] else result["raw"]
    return list(lines), param_map_from_ast(cprog)


# ---------------------------------------------------------------- 值运算

def truthy(v: Any) -> bool:
    """C 的真值：非零为真。None（void 调用结果）按假处理。"""
    if v is None:
        return False
    if isinstance(v, str):
        return len(v) > 0
    return v != 0


def _int(v: Any) -> int:
    """把操作数规整成 int；指针/字符串给出 0 而不是炸掉。"""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        return 0
    return 0


def c_div(a: int, b: int) -> int:
    """C 的整除：向零截断。Python 的 // 是向下取整，不能直接用。"""
    if b == 0:
        raise IRExecError("除零：IR 里出现了 a / 0")
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def c_mod(a: int, b: int) -> int:
    """C 的取模：结果的符号与被除数相同。"""
    return a - c_div(a, b) * b


def _signed_shift_left(a: int, b: int) -> int:
    return a << b if b >= 0 else a >> (-b)


def _signed_shift_right(a: int, b: int) -> int:
    return a >> b if b >= 0 else a << (-b)


BIN_OPS = {
    "+": lambda a, b: _int(a) + _int(b),
    "-": lambda a, b: _int(a) - _int(b),
    "*": lambda a, b: _int(a) * _int(b),
    "/": lambda a, b: c_div(_int(a), _int(b)),
    "%": lambda a, b: c_mod(_int(a), _int(b)),
    "<": lambda a, b: 1 if _int(a) < _int(b) else 0,
    ">": lambda a, b: 1 if _int(a) > _int(b) else 0,
    "<=": lambda a, b: 1 if _int(a) <= _int(b) else 0,
    ">=": lambda a, b: 1 if _int(a) >= _int(b) else 0,
    "==": lambda a, b: 1 if _int(a) == _int(b) else 0,
    "!=": lambda a, b: 1 if _int(a) != _int(b) else 0,
    "&&": lambda a, b: 1 if (truthy(a) and truthy(b)) else 0,
    "||": lambda a, b: 1 if (truthy(a) or truthy(b)) else 0,
    "&": lambda a, b: _int(a) & _int(b),
    "|": lambda a, b: _int(a) | _int(b),
    "^": lambda a, b: _int(a) ^ _int(b),
    "<<": lambda a, b: _signed_shift_left(_int(a), _int(b)),
    ">>": lambda a, b: _signed_shift_right(_int(a), _int(b)),
}


# ---------------------------------------------------------------- 内置函数

def _bi_printf(ex, args):
    """printf 只收集输出，返回 0；返回值没人用，但不能让程序崩。"""
    text = ""
    if args:
        fmt = args[0]
        if isinstance(fmt, str):
            rest = list(args[1:])
            out, i = [], 0
            while i < len(fmt):
                ch = fmt[i]
                if ch == "%" and i + 1 < len(fmt):
                    spec = fmt[i + 1]
                    if spec == "%":
                        out.append("%")
                        i += 2
                        continue
                    if spec in "diusc" and rest:
                        out.append(str(_int(rest.pop(0))))
                        i += 2
                        continue
                    if spec in "fge" and rest:
                        out.append(str(rest.pop(0)))
                        i += 2
                        continue
                    if spec == "s" and rest:
                        out.append(str(rest.pop(0)))
                        i += 2
                        continue
                out.append(ch)
                i += 1
            text = "".join(out)
        else:
            text = " ".join(str(a) for a in args)
    ex.output.append(text)
    return 0


def _bi_abs(ex, args):
    return abs(_int(args[0])) if args else 0


def _bi_ignore(ex, args):
    return 0


def _bi_max(ex, args):
    return max((_int(a) for a in args), default=0)


def _bi_min(ex, args):
    return min((_int(a) for a in args), default=0)


# 名字 -> (实现, 是否允许"只有名字没实现"的宽松模式)
BUILTINS = {
    "printf": _bi_printf,
    "fprintf": _bi_ignore,
    "sprintf": _bi_ignore,
    "puts": _bi_ignore,
    "putchar": _bi_ignore,
    "putc": _bi_ignore,
    "fflush": _bi_ignore,
    "abs": _bi_abs,
    "labs": _bi_abs,
    "llabs": _bi_abs,
    "max": _bi_max,
    "min": _bi_min,
    "exit": lambda ex, args: (_ for _ in ()).throw(
        UnsupportedCall("exit() 会终止程序，解释器不模拟")),
}


# ---------------------------------------------------------------- 执行器

class _Return(Exception):
    def __init__(self, value):
        self.value = value


class Executor:
    """按指令逐条解释执行 IR。

    参数:
      max_steps   全局步数上限（默认 10 万），防死循环
      max_depth   调用深度上限（默认 200），防无限递归
      trace       是否记录执行轨迹（默认只留最后 64 条）
      strict      严格模式：读未定义变量直接报错，而不是给 0
    """

    def __init__(self, program: Program, max_steps: int = 100_000,
                 max_depth: int = 200, trace: bool = False,
                 strict: bool = False, array_size: int = 16):
        self.prog = program
        self.max_steps = max_steps
        self.max_depth = max_depth
        self.want_trace = trace
        self.strict = strict
        self.array_size = array_size

        self.output: list = []
        self.notes: list = []
        self.trace: list = []
        self.steps = 0
        self.depth = 0
        self.frames: list = []
        self.formals: list = []

    # -- 环境 --
    def note(self, msg: str):
        if msg not in self.notes:
            self.notes.append(msg)

    def lookup(self, name: str, ins: Optional[Instr] = None):
        frame = self.frames[-1]
        if name in frame:
            return frame[name]
        # 形参名可能在上一帧（简化：只查当前帧，函数没有嵌套作用域）
        if self.strict:
            where = f"（IR 第 {ins.line} 条：{ins.raw}）" if ins else ""
            raise IRExecError(f"读取未定义变量 {name}{where}")
        self.note(f"读取未定义变量 {name}，按 0 处理")
        return 0

    def assign(self, name: str, value):
        self.frames[-1][name] = value

    def eval_operand(self, text: Optional[str], ins: Optional[Instr] = None):
        """把操作数文本取值：常量 / 变量 / 字符串字面量。"""
        if text is None:
            return None
        t = text.strip()
        if t == "":
            return None
        # 字符串字面量
        if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
            return t[1:-1]
        if len(t) >= 2 and t[0] == "'" and t[-1] == "'":
            return ord(t[1]) if len(t) > 2 else 0
        # 整数（允许负号、十六进制）
        try:
            low = t.lower()
            if low.startswith("-"):
                return -int(low[1:], 0)
            return int(low, 0)
        except ValueError:
            pass
        # 浮点常量
        try:
            return float(t)
        except ValueError:
            pass
        # 成员访问 base.member / 指针解引用等：查不到就给 0
        if "." in t or "->" in t:
            return 0
        # 内联复合表达式（手写 IR 里会出现 a + b，IRGen 自己不会生成）：
        # 递归求值，避免把它当成变量名去查而静默拿到 0。
        mb = _RE_BIN.match(t)
        if mb and mb.group(2) in BIN_OPS:
            lhs = self.eval_operand(mb.group(1), ins)
            rhs = self.eval_operand(mb.group(3), ins)
            return BIN_OPS[mb.group(2)](lhs, rhs)
        return self.lookup(t, ins)

    # -- 执行 --
    def run(self, entry: str = "main", args=()) -> ExecResult:
        if not self.prog.has(entry):
            # 手写 IR 片段常常没有 func 标记，整体落在 __top__ 里，
            # 这类片段就是"一个待执行的语句序列"，直接跑它。
            if entry == "main" and self.prog.has("__top__"):
                entry = "__top__"
            else:
                return ExecResult(False, error=f"IR 里没有函数 {entry}",
                                  notes=self.notes)
        try:
            value = self.call(entry, list(args))
        except StepLimitExceeded as e:
            return ExecResult(False, steps=self.steps, error=str(e),
                              notes=self.notes, trace=self.trace)
        except CallDepthExceeded as e:
            return ExecResult(False, steps=self.steps, error=str(e),
                              notes=self.notes, trace=self.trace)
        except IRExecError as e:
            return ExecResult(False, steps=self.steps, error=str(e),
                              notes=self.notes, trace=self.trace)
        except RecursionError:
            return ExecResult(False, steps=self.steps,
                              error="Python 递归深度耗尽（解释器自身限制）",
                              notes=self.notes, trace=self.trace)
        v = _int(value) if value is not None else 0
        return ExecResult(True, value=v, steps=self.steps,
                          exit_code=v & 0xFF, notes=self.notes,
                          trace=self.trace)

    def call(self, name: str, args: list):
        if self.depth >= self.max_depth:
            raise CallDepthExceeded(
                f"调用深度超过 {self.max_depth}：疑似无限递归（{name}）")
        fn = self.prog.functions.get(name)
        if fn is None:
            impl = BUILTINS.get(name)
            if impl is None:
                raise UnsupportedCall(f"未定义函数 {name}：IR 里没有它的函数体，"
                                      f"也没有内置实现")
            return impl(self, args)

        # 绑定形参。IR 文本不记录形参名，靠 parse_program(param_map=...)
        # 注入；没有名字时按位置兜底，至少不会污染调用者帧。
        binding = {}
        for i, pname in enumerate(fn.params):
            binding[pname] = args[i] if i < len(args) else 0
        if len(args) > len(fn.params):
            if fn.params:
                self.note(f"调用 {name} 传入 {len(args)} 个参数，"
                          f"函数只有 {len(fn.params)} 个形参")
            else:
                self.note(f"调用 {name} 传入 {len(args)} 个参数，但形参名未知"
                          f"（未提供 param_map），函数体里的形参按未定义处理")

        self.frames.append(binding)
        self.depth += 1
        try:
            self.exec_code(fn, 0, and_return=True)
            result = 0
        except _Return as r:
            result = r.value
        finally:
            self.depth -= 1
            self.frames.pop()
        return result

    def exec_code(self, fn: Function, pc: int, and_return: bool = True):
        """从 pc 开始执行函数体；遇到 return 用异常跳出（含嵌套调用）。"""
        code = fn.code
        n = len(code)
        while pc < n:
            ins = code[pc]
            self.steps += 1
            if self.steps > self.max_steps:
                raise StepLimitExceeded(
                    f"步数超过 {self.max_steps}（在 {fn.name} 第 {pc} 条："
                    f"{ins.raw}）——IR 很可能有死循环")
            if self.want_trace or len(self.trace) < 64:
                self.trace.append(f"{fn.name}:{pc}: {ins.raw}")

            op = ins.op
            pc += 1

            if op == "label":
                continue

            if op == "=":
                self.assign(ins.dst, self.eval_operand(ins.a1, ins))
                continue

            if op in BIN_OPS:
                a = self.eval_operand(ins.a1, ins)
                b = self.eval_operand(ins.a2, ins)
                self.assign(ins.dst, BIN_OPS[op](a, b))
                continue

            if op == "neg":
                self.assign(ins.dst, -_int(self.eval_operand(ins.a1, ins)))
                continue

            if op == "not":
                v = self.eval_operand(ins.a1, ins)
                self.assign(ins.dst, 0 if truthy(v) else 1)
                continue

            if op == "addr":
                self.assign(ins.dst, Ref(ins.a1))
                continue

            if op == "deref":
                v = self.eval_operand(ins.a1, ins)
                self.assign(ins.dst, self._deref(v, ins))
                continue

            if op == "load":
                base = self._array_of(ins.a1, ins, create=True)
                idx = self.eval_operand(ins.a2, ins)
                self.assign(ins.dst, base.get(idx) if base is not None else 0)
                continue

            if op == "store":
                base_name = ins.extra
                base = self._array_of(base_name, ins, create=True)
                idx = self.eval_operand(ins.a1, ins)
                val = self.eval_operand(ins.a2, ins)
                if base is not None:
                    base.set(idx, val)
                continue

            if op == "goto":
                target = ins.extra
                if target not in fn.index:
                    raise IRExecError(
                        f"goto 目标不存在：{target}（在 {fn.name} 里，"
                        f"第 {ins.line} 条：{ins.raw}）")
                pc = fn.index[target]
                continue

            if op == "if":
                if not truthy(self.eval_operand(ins.a1, ins)):
                    target = ins.extra
                    if target not in fn.index:
                        raise IRExecError(
                            f"ifFalse 目标不存在：{target}（在 {fn.name} 里，"
                            f"第 {ins.line} 条：{ins.raw}）")
                    pc = fn.index[target]
                continue

            if op == "ifTrue":
                if truthy(self.eval_operand(ins.a1, ins)):
                    target = ins.extra
                    if target not in fn.index:
                        raise IRExecError(
                            f"ifTrue 目标不存在：{target}（在 {fn.name} 里，"
                            f"第 {ins.line} 条：{ins.raw}）")
                    pc = fn.index[target]
                continue

            if op == "param":
                self.formals.append(self.eval_operand(ins.a1, ins))
                continue

            if op == "call":
                argv = [self.eval_operand(a, ins)
                        for a in _split_args(ins.extra)]
                if ins.op == "call" and not self.prog.has(ins.a1) \
                        and ins.a1 not in BUILTINS:
                    raise UnsupportedCall(
                        f"未定义函数 {ins.a1}（第 {ins.line} 条：{ins.raw}）")
                ret = self.call(ins.a1, argv)
                if ins.dst:
                    self.assign(ins.dst, ret)
                continue

            if op == "return":
                v = self.eval_operand(ins.a1, ins) if ins.a1 else None
                if and_return:
                    raise _Return(v)
                return v

            raise IRExecError(f"不支持的指令：{op}（{ins.raw}）")

        # 函数体走完没 return：C 里 main 隐式返回 0，其他函数属未定义行为
        if and_return:
            if fn.name == "main":
                raise _Return(0)
            raise _Return(0)
        return 0

    # -- 内存 --
    def _array_of(self, name: Optional[str], ins: Optional[Instr],
                  create: bool = False):
        if not name:
            return None
        frame = self.frames[-1]
        cur = frame.get(name)
        if isinstance(cur, Array):
            return cur
        if isinstance(cur, Ref):
            cur = frame.get(cur.name)
            if isinstance(cur, Array):
                return cur
        if create:
            arr = Array()
            # 静态声明的数组（若调用方通过 declare() 预置）沿用其容量
            frame[name] = arr
            return arr
        return None

    def _deref(self, v, ins):
        if isinstance(v, Ref):
            return self.lookup(v.name, ins)
        if isinstance(v, Array):
            return v.get(0)
        self.note("对非指针值做 * 解引用，按 0 处理")
        return 0

    def declare(self, name: str, size: int = None, init=None):
        """给调用方一个预置变量的入口（例如把全局变量塞进来）。"""
        if not self.frames:
            self.frames.append({})
        if size is not None:
            self.frames[-1][name] = Array()
        else:
            self.frames[-1][name] = 0 if init is None else init


# ---------------------------------------------------------------- 对外接口

def execute_ir(lines, entry: str = "main", args=(), max_steps: int = 100_000,
               max_depth: int = 200, trace: bool = False,
               strict: bool = False, globals_map: dict = None,
               param_map: dict = None) -> ExecResult:
    """一步到位：IR 文本 -> 执行结果。异常一律转成 ExecResult(ok=False)。"""
    try:
        prog = parse_program(lines, param_map=param_map)
    except IRParseError as e:
        return ExecResult(False, error=f"IR 解析失败：{e}")
    if not prog.functions:
        return ExecResult(False, error="IR 里没有任何函数")

    ex = Executor(prog, max_steps=max_steps, max_depth=max_depth,
                  trace=trace, strict=strict)
    if globals_map:
        ex.frames.append({})
        for k, v in globals_map.items():
            ex.frames[-1][k] = v
    return ex.run(entry, args)


def run_source(text: str, entry: str = "main", optimize: bool = True,
               max_steps: int = 100_000, trace: bool = False) -> ExecResult:
    """C 源码 -> IR -> 执行。便于其它工具复用。"""
    try:
        lines, param_map = compile_source(text, optimize=optimize)
    except Exception as e:                      # 前端自身的错误
        return ExecResult(False, error=f"前端失败：{type(e).__name__}: {e}")
    return execute_ir(lines, entry=entry, max_steps=max_steps, trace=trace,
                      param_map=param_map)


# ---------------------------------------------------------------- 命令行

def _setup_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv):
    _setup_console()
    args = list(argv[1:])
    entry, max_steps, trace, optimize, ir_file, src_file = "main", 100_000, \
        False, True, None, None

    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-e", "--ir"):
            i += 1
            ir_file = args[i]
        elif a in ("--entry", "-f"):
            i += 1
            entry = args[i]
        elif a in ("--max-steps",):
            i += 1
            max_steps = int(args[i])
        elif a in ("--raw",):
            optimize = False
        elif a in ("--trace", "-t"):
            trace = True
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            src_file = a
        i += 1

    if ir_file:
        with open(ir_file, encoding="utf-8") as f:
            ir_text = f.read()
        param_map = None
        title = ir_file
    elif src_file:
        ir_lines, param_map = load_ir_from_source(src_file, optimize=optimize)
        ir_text = "\n".join(ir_lines)
        title = src_file
        if optimize:
            print("# 说明：这里执行的是优化后的 IR（常量折叠 + 死代码消除）",
                  file=sys.stderr)
    else:
        print(__doc__)
        return 2

    print(f"# IR: {title}")
    print(ir_text)
    print("#" * 60)
    result = execute_ir(ir_text, entry=entry, max_steps=max_steps,
                        trace=trace, param_map=param_map)
    if trace:
        print("# 执行轨迹：")
        for t in result.trace:
            print("  " + t)
    if result.notes:
        print("# 备注：")
        for n in result.notes:
            print("  - " + n)
    if result.ok:
        print(f"执行成功：{entry} 返回 {result.value}"
              f"（退出码 {result.exit_code}），共 {result.steps} 步")
        return result.exit_code or 0
    print(f"执行失败：{result.error}（已走 {result.steps} 步）")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
