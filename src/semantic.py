"""语义分析：符号表、类型检查、以及初学者易错点检测。

这个模块是整个项目的价值核心。它不只是"能不能编译"，
而是"能指出初学者最常犯的错，并说清为什么错"。

检测规则（对应教材里学生实际会踩的坑）：
  E1  使用未声明变量
  E2  使用未初始化变量
  E3  重复声明
  E4  函数调用参数个数不符
  E5  函数未声明就调用
  W1  数组下标可能越界（常量越界直接报错）
  W2  有符号/无符号比较（本项目未实现 unsigned，故检查负数比较）
  W3  整数除法可能丢精度
  W4  赋值截断（float 赋给 int）
  W5  条件表达式恒真/恒假（如 if (a = 1) 误写成赋值）
  W6  变量声明后从未使用
  W7  除数为常量 0
  W8  main 缺少 return

每条诊断都带 level / code / 位置 / 原因 / 修复建议，
这样才能在界面上直接给学生看，而不是丢一个编译器内部错误。
"""
from dataclasses import dataclass, field
from typing import Optional

from parser import (
    DoWhile,
    Program, FuncDef, VarDecl, Block, If, While, For, Return, Break,
    Continue, ExprStmt, Num, Str, Ident, BinOp, UnOp, Assign, Call,
    Index, Member, Type, DeclGroup, StructDef, InitList, Switch, Case, Ternary,
)


def _is_type_name(s: str) -> bool:
    """parser 在 sizeof(类型) 时用 repr(Type) 生成形如 "int" / "int*" 的名字。"""
    if not isinstance(s, str):
        return False
    base = s.rstrip("*")
    if base.startswith("struct "):
        return True
    return base in ("int", "float", "void", "char", "double")


# 来自标准头文件 / 框架头文件的常见全局对象。
# 这类名字在真实代码里到处都是，而我们不处理头文件，
# 不预置就会造成大面积 E1 误报。
STD_SYMBOLS = {
    # C 标准库的全局对象
    "stdin", "stdout", "stderr", "environ", "errno",
    # CPython C-API 的常用全局对象（numpy / 扩展模块里极高频）
    "PyExc_TypeError", "PyExc_ValueError", "PyExc_RuntimeError",
    "PyExc_MemoryError", "PyExc_IndexError", "PyExc_KeyError",
    "PyExc_AttributeError", "PyExc_ImportError", "PyExc_SystemError",
    "PyExc_OverflowError", "PyExc_ZeroDivisionError",
    "PyExc_NotImplementedError", "PyExc_BufferError",
    "Py_None", "Py_True", "Py_False", "PyExc_Exception",
    "PyArray_Type", "PyFloat_Type", "PyLong_Type", "PyList_Type",
    "PyDict_Type", "PyTuple_Type", "PyUnicode_Type", "PyBytes_Type",
    "PyModule_Type", "PyType_Type", "PyBaseObject_Type",
}


def _is_likely_external_macro(name: str) -> bool:
    """判断标识符是否"来自看不到的头文件"，从而不该报未声明。

    三类都会命中：
      1) 全大写宏      —— NPY_INTP_FMT、MAX_PATH
      2) 标准全局对象  —— stderr、PyExc_ValueError、PyArray_Type
      3) 框架前缀名    —— Py* / NPY_* / PyArray_* 等

    为什么必须容忍：我们不处理 #include，这些符号的声明在头文件里。
    误报比漏报更伤害工具可信度——学生看到一堆假错误就不用了。
    """
    if not isinstance(name, str) or len(name) < 2:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    if name in STD_SYMBOLS:
        return True
    if name.isupper():
        return True
    # Py*_Type / PyExc_* 这类框架全局对象（不是函数调用，走 Ident 路径）
    if name.startswith("Py") and ("_Type" in name or "_Exc_" in name
                                  or name.startswith("Py_RETURN")):
        return True
    # 第三方框架的命名前缀（CPython / numpy）
    for prefix in ("Py", "NPY_", "npy_", "PyArray_", "_Py", "F2PY_"):
        if name.startswith(prefix) and len(name) > len(prefix):
            # 排除学生自己的变量名恰好以 Py 开头的极小概率情况：
            # 要求后面紧跟大写字母（PyFooBar 风格）
            rest = name[len(prefix):]
            if rest[:1].isupper():
                return True
    return False


# 标准库常量宏：真实代码里 NULL 到处都是，不认就会误报
STD_MACROS = {"NULL": "0", "EOF": "-1", "EXIT_SUCCESS": "0",
              "EXIT_FAILURE": "1", "TRUE": "1", "FALSE": "0",
              "CLOCK_MONOTONIC": "1", "CLOCK_REALTIME": "0",
              "SEEK_SET": "0", "SEEK_CUR": "1", "SEEK_END": "2",
              "RAND_MAX": "32767", "INT_MAX": "2147483647",
              "INT_MIN": "-2147483648", "M_PI": "3.141592653589793"}

# 常见库函数：名字 -> (最少参数, 最多参数, 第几个参数是"写出"位置)
# 写成"写出"是为了不把 scanf("%d", &v) 里的 v 当成未初始化读取。
STD_FUNCS = {
    "printf": (1, 99, set()), "puts": (1, 1, set()),
    "putchar": (1, 1, set()), "getchar": (0, 0, set()),
    "puts": (1, 1, set()), "fputs": (2, 2, set()),
    "malloc": (1, 1, set()), "calloc": (2, 2, set()),
    "free": (1, 1, set()), "realloc": (2, 2, set()),
    "strlen": (1, 1, set()), "strcpy": (2, 2, set()),
    "strncpy": (3, 3, set()), "strcmp": (2, 2, set()),
    "strcat": (2, 2, set()), "memset": (3, 3, set()),
    "memcpy": (3, 3, set()), "scanf": (1, 99, "needs_addr_out"),
    "sscanf": (2, 99, "needs_addr_out"),
    "fscanf": (2, 99, "needs_addr_out"),
    "sscanf": (2, 99, "needs_addr_out"), "exit": (1, 1, set()),
    "abs": (1, 1, set()), "labs": (1, 1, set()),
    "sqrt": (1, 1, set()), "pow": (2, 2, set()),
    "fabs": (1, 1, set()), "floor": (1, 1, set()),
    "ceil": (1, 1, set()), "log": (1, 1, set()),
    "exp": (1, 1, set()), "sin": (1, 1, set()), "cos": (1, 1, set()),
    "fopen": (2, 2, set()), "fclose": (1, 1, set()),
    "fprintf": (2, 99, set()), "fscanf": (2, 99, "needs_addr_out"),
    "fgets": (3, 3, set()), "sprintf": (2, 99, set()),
    "snprintf": (3, 99, set()), "atoi": (1, 1, set()),
    "atof": (1, 1, set()), "atol": (1, 1, set()),
    "rand": (0, 0, set()), "srand": (1, 1, set()),
    "time": (1, 1, set()), "clock": (0, 0, set()),
    "clock_gettime": (2, 2, "second_out"),
    "qsort": (4, 4, set()), "bsearch": (5, 5, set()),
    "isalpha": (1, 1, set()), "isdigit": (1, 1, set()),
    "isspace": (1, 1, set()), "toupper": (1, 1, set()),
    "tolower": (1, 1, set()), "assert": (1, 1, set()),
    "perror": (1, 1, set()), "fflush": (1, 1, set()),
    # 变长参数宏（本质是宏，但在语义上按函数处理最省事）
    "va_start": (2, 2, 0), "va_arg": (2, 2, ""), "va_end": (1, 1, 0),
    "va_copy": (2, 2, ""),
    # 字符串函数：真实代码高频
    "strstr": (2, 2, set()), "strchr": (2, 2, set()),
    "strrchr": (2, 2, set()), "strtok": (2, 2, set()),
    "strspn": (2, 2, set()), "strcspn": (2, 2, set()),
    "strdup": (1, 1, set()), "strncat": (3, 3, set()),
    "memmove": (3, 3, set()), "memcmp": (3, 3, set()),
    # Windows 常用（lstr* 系列）
    "lstrcpy": (2, 2, set()), "lstrcat": (2, 2, set()),
    "lstrlen": (1, 1, set()), "lstrcmp": (2, 2, set()),
    "wsprintf": (2, 99, set()), "GetLastError": (0, 0, set()),
    "CloseHandle": (1, 1, set()), "CreateFile": (7, 7, set()),
    # 输出参数用 0 基索引：WriteFile(h,m,c,&dw,ov) 输出的是第 4 个（索引 3）
    "ReadFile": (5, 5, 3), "WriteFile": (5, 5, 3),
    "GetFullPathName": (4, 4, 1),
    "SetErrorMode": (1, 1, set()), "GetCurrentDirectory": (2, 2, "second_out"),
    "atoi_ex": (1, 1, set()), "isdigit_ch": (1, 1, set()),
}


def const_int(node, _depth=0):
    """把常量表达式折成整数；不是纯常量就返回 None。

    支持：字面量、正负号、以及二元算术（2*4、8/2、1+2）。
    递归有深度限制，避免畸形表达式把栈打爆。

    踩过的坑：
      - -1 在 AST 里是 UnOp('-', Num(1))，只认 Num 会让 arr[-1] 漏检；
      - 只折 Num 不折 BinOp，会让 scores[2*4] 漏检（结构体成员数组
        和常量表达式的越界是真实高频错误，不能放过）。
    """
    if _depth > 20:
        return None
    if isinstance(node, Num) and not node.is_float:
        return int(node.value)
    if isinstance(node, UnOp) and node.op in ("-", "+"):
        v = const_int(node.operand, _depth + 1)
        if v is None:
            return None
        return -v if node.op == "-" else v
    if isinstance(node, BinOp) and node.op in ("+", "-", "*", "/", "%",
                                               "<<", ">>", "&", "|", "^"):
        a = const_int(node.left, _depth + 1)
        b = const_int(node.right, _depth + 1)
        if a is None or b is None:
            return None
        try:
            if node.op == "+":
                return a + b
            if node.op == "-":
                return a - b
            if node.op == "*":
                return a * b
            if node.op == "/":
                return int(a / b) if b else None
            if node.op == "%":
                return a % b if b else None
            if node.op == "<<":
                return a << b if 0 <= b < 64 else None
            if node.op == ">>":
                return a >> b if 0 <= b < 64 else None
            if node.op == "&":
                return a & b
            if node.op == "|":
                return a | b
            if node.op == "^":
                return a ^ b
        except (ZeroDivisionError, ValueError, OverflowError):
            return None
    return None


@dataclass
class Diag:
    level: str          # "error" | "warning" | "note"
    code: str
    line: int
    col: int
    msg: str
    hint: str = ""

    def __str__(self):
        loc = f"{self.line}:{self.col}"
        s = f"[{self.level.upper()} {self.code}] {loc}: {self.msg}"
        if self.hint:
            s += f"\n    提示: {self.hint}"
        return s


@dataclass
class Symbol:
    name: str
    vtype: Type
    kind: str = "var"       # var | func | param
    initialized: bool = False
    used: bool = False
    line: int = 0
    col: int = 0
    # 数组常量下标的历史访问记录，用于越界判断
    const_indices: list = field(default_factory=list)


class Scope:
    def __init__(self, parent=None):
        self.parent = parent
        self.syms: dict[str, Symbol] = {}

    def declare(self, s: Symbol) -> Optional[Symbol]:
        """返回被遮蔽的旧符号（若有）。"""
        old = self.syms.get(s.name)
        self.syms[s.name] = s
        return old

    def lookup(self, name) -> Optional[Symbol]:
        sc = self
        while sc:
            if name in sc.syms:
                return sc.syms[name]
            sc = sc.parent
        return None

    def lookup_local(self, name) -> Optional[Symbol]:
        return self.syms.get(name)


class Analyzer:
    def __init__(self):
        self.diags = []
        self.globals = Scope()
        self.scope = self.globals
        self.funcs: dict[str, FuncDef] = {}
        self.cur_func: Optional[FuncDef] = None
        self.loop_depth = 0
        self.structs: dict[str, StructDef] = {}
        self.typedefs: dict[str, Type] = {}
        # 表达式递归深度。t[0][0][0]... 这种超长下标链在 AST 里
        # 是深度嵌套的 Index，check_expr 每层递归一次，
        # 几百层就把 Python 栈打爆（实测 crash_04 崩在
        # semantic.py 的 check_expr 自递归上）。
        self._expr_depth = 0
        self.MAX_EXPR_DEPTH = 150

    # ---- 工具 ----
    def err(self, code, line, col, msg, hint=""):
        self.diags.append(Diag("error", code, line, col, msg, hint))

    def warn(self, code, line, col, msg, hint=""):
        self.diags.append(Diag("warning", code, line, col, msg, hint))

    def push(self):
        self.scope = Scope(self.scope)

    def pop(self):
        # 离开作用域前，检查本层未使用的变量。
        # 跳过被后续同名声明遮蔽的符号：重复声明已经报过 E3，
        # 再补一条"未使用"只会制造噪音。
        shadowed = set()
        for s in self.scope.syms.values():
            shadowed.add(s.name)
        seen = set()
        for name, s in reversed(list(self.scope.syms.items())):
            if s.kind == "var" and not s.used:
                # 同名变量只对最后一次声明报未使用
                if name in seen:
                    continue
                seen.add(name)
                self.warn("W6", s.line, s.col,
                          f"变量 {s.name!r} 声明后从未使用",
                          "删掉它，或确认是否忘了使用")
        self.scope = self.scope.parent

    # ---- 入口 ----
    def analyze(self, prog: Program) -> list:
        # 第零遍：登记 typedef 出来的类型名，否则 Node / LinkList 会被当变量
        for d in prog.decls:
            if isinstance(d, StructDef):
                if d.tag:
                    self.structs[d.tag] = d
                for alias, ty in d.typedefs:
                    self.typedefs[alias] = ty
                    # 匿名结构体 typedef：typedef struct { int a; } T;
                    # 没有 tag，成员定义只能挂在别名上。
                    # 不这么登记，self.structs 里就查不到 T 的成员，
                    # 成员检查和数组越界检查对它全部失效
                    # （实测 tab.cell[24] 越界一声不响）。
                    if not d.tag and d.members and not ty.ptr:
                        self.structs[alias] = d

        # 第一遍：收集函数签名，允许前向调用
        for d in prog.decls:
            if isinstance(d, FuncDef):
                self.funcs[d.name] = d
                self.globals.declare(Symbol(
                    name=d.name, vtype=d.ret_type, kind="func",
                    initialized=True, used=True, line=d.line, col=d.col))

        # 第二遍：检查每个声明
        for d in prog.decls:
            if isinstance(d, FuncDef):
                self.check_func(d)
            elif isinstance(d, VarDecl):
                self.check_vardecl(d)
            elif isinstance(d, DeclGroup):
                for vd in d.decls:
                    self.check_vardecl(vd)
            elif isinstance(d, StructDef):
                pass        # 已登记
            else:
                self.diags.append(Diag(
                    "error", "E9", d.line, d.col,
                    "无法识别的顶层声明", "只支持变量与函数定义"))

        self.check_main_exists(prog)
        return self.diags

    def check_main_exists(self, prog):
        if "main" in self.funcs:
            return
        # 库文件（.c 里全是给别的程序调用的函数）本来就没有 main。
        # 判断依据：文件里有非 static 的函数定义且没有一个叫 main，
        # 就当作库文件，不报缺 main。这避免了对 CPython/numpy 这类
        # 第三方源码的整体误判。
        pub_funcs = [f for f in self.funcs.values()
                     if f.body is not None and not f.name.startswith("_")]
        if len(pub_funcs) >= 2:
            return
        self.err("E10", 1, 1, "找不到 main 函数",
                 "程序需要一个 int main() 作为入口")

    # ---- 函数 ----
    def check_func(self, fn: FuncDef):
        if fn.body is None:
            return                      # 仅声明
        self.cur_func = fn
        self.push()
        for pty, pname in fn.params:
            if not pname:
                continue
            self.scope.declare(Symbol(
                name=pname, vtype=pty, kind="param",
                initialized=True, used=True,
                line=fn.line, col=fn.col))
        self.check_block(fn.body, new_scope=False)

        # 非 void 函数必须有返回值。
        # 早期版本只查 main，其他函数（如 int max(...) 忘了 return）
        # 完全不报——而那是真实作业里错误率很高的一类。
        # 注意 void 函数不要求 return，别误报。
        if (fn.ret_type is None or fn.ret_type.base != "void") \
                and fn.body is not None \
                and not self.block_returns(fn.body):
            who = fn.name if fn.name != "main" else "main"
            hint = ("在末尾补 return 0;（C99 起 main 隐式返回 0，"
                    "但显式写更清楚）" if fn.name == "main"
                    else f"{fn.name} 声明为 {fn.ret_type.base if fn.ret_type else '?'}，"
                         "所有控制流路径都必须有 return")
            self.warn("W8", fn.line, fn.col,
                      f"{who} 函数可能没有 return 语句", hint)
        self.pop()
        self.cur_func = None

    def block_returns(self, blk) -> bool:
        """判断这个块是否"末尾能保证有返回值"。

        判定规则（保守但能接住真实高频错误）：
          1) 块里最后一条有效语句是 return  -> 有返回
          2) 最后一条是 if/else 且两支都返回  -> 有返回
          3) 最后一条是 if 链但没有 else 兜底 -> **无返回**（真实高频错误）
          4) 其他情况（中途有 return、循环等）-> 当作有返回，不报

        为什么不做得更精确：真正的路径可达性分析需要控制流图。
        而 W8 只是 warning，误报会直接摧毁信任
        （实测 real_fortranobject.c 有个 90 行函数、中途大量 return，
         早期粗判把它误报了一次）。所以规则 4 主动放行。
        但规则 3 必须报：`if/else if` 忘了 else 兜底、
        或者函数体只有 if 没有 else，是真实作业里最常见的缺 return 写法。
        """
        stmts = [s for s in blk.stmts
                 if not (isinstance(s, Block) and not s.stmts)]
        if not stmts:
            return False
        last = stmts[-1]
        if isinstance(last, Return):
            return True
        if isinstance(last, If):
            # if 有 else 且两支都返回 -> 安全
            if last.els is not None:
                return (self.block_returns_of_stmt(last.then)
                        and self.block_returns_of_stmt(last.els))
            # if 无 else -> 存在不走 if 的路径
            return False
        # 中途有 return 的情况：放行，避免误报长函数
        return self._has_any_return(blk)

    def block_returns_of_stmt(self, s) -> bool:
        """单条语句是否一定返回。"""
        if isinstance(s, Return):
            return True
        if isinstance(s, Block):
            return self.block_returns(s)
        if isinstance(s, If):
            if s.els is None:
                return False
            return (self.block_returns_of_stmt(s.then)
                    and self.block_returns_of_stmt(s.els))
        return False

    def _has_any_return(self, node, _depth=0) -> bool:
        """递归查找任意位置的 Return 节点。"""
        if _depth > 60:
            return True          # 太深就当作有，避免误报
        if isinstance(node, Return):
            return True
        if isinstance(node, Block):
            return any(self._has_any_return(s, _depth + 1)
                       for s in node.stmts)
        if isinstance(node, If):
            return (self._has_any_return(node.then, _depth + 1)
                    or self._has_any_return(node.els, _depth + 1))
        if isinstance(node, (While, For, DoWhile)):
            return self._has_any_return(node.body, _depth + 1)
        if isinstance(node, Switch):
            return any(self._has_any_return(s, _depth + 1)
                       for s in (node.body.stmts if node.body else []))
        return False

    # ---- 声明 ----
    def check_vardecl(self, vd: VarDecl):
        # 重复声明
        if self.scope.lookup_local(vd.name):
            old = self.scope.lookup_local(vd.name)
            self.err("E3", vd.line, vd.col,
                     f"变量 {vd.name!r} 重复声明",
                     f"上一次声明在 {old.line}:{old.col}")
            return

        # 全局变量在 C 里默认零初始化，不存在"未初始化"问题。
        # 早期版本一视同仁，导致 Windows 代码里大量全局缓冲区
        # 被误报 E2（如 ReadFile 写入的 dwWritten）。
        is_global = self.scope is self.globals
        sym = Symbol(name=vd.name, vtype=vd.vtype, kind="var",
                     line=vd.line, col=vd.col,
                     initialized=is_global)
        if vd.init is not None:
            t = self.check_expr(vd.init)
            sym.initialized = True
            self.check_assign_compat(vd.vtype, t, vd.init, vd.name)
        elif vd.vtype.array is not None or vd.vtype.dims:
            # 数组不需要整体初始化：元素可以逐个赋值，也可能整块被
            # 函数写入（GetFullPathName(szPath, n, szCwd, NULL)）。
            # 注意要同时看 dims：维度写成常量表达式（MAX_PATH + 1）时
            # array 是 None，但 dims 有值。只看 array 会漏掉这种。
            sym.initialized = True
        self.scope.declare(sym)

    def check_assign_compat(self, target: Type, src_type: Optional[str],
                            node, name: str):
        """赋值截断检查（W4）。"""
        if target.ptr > 0 or src_type is None:
            return
        if target.base in ("int", "char") and src_type == "float":
            self.warn("W4", node.line, node.col,
                      f"把 float 赋给 {name!r}（{target.base}），小数部分会被截断",
                      "确认是否需要 float 类型，或显式写成 (int) 转换")

    # ---- 语句 ----
    def check_block(self, blk: Block, new_scope=True):
        if new_scope:
            self.push()
        for s in blk.stmts:
            self.check_stmt(s)
        if new_scope:
            self.pop()

    def check_stmt(self, s):
        if isinstance(s, DeclGroup):
            # int a = 1, b = 2; 逐个按普通声明处理，
            # 这样重复声明、未使用等检查都自动生效。
            for vd in s.decls:
                self.check_vardecl(vd)
        elif isinstance(s, Block):
            self.check_block(s)
        elif isinstance(s, VarDecl):
            self.check_vardecl(s)
        elif isinstance(s, If):
            self.check_condition(s.cond, "if")
            self.check_stmt(s.then)
            if s.els is not None:
                self.check_stmt(s.els)
        elif isinstance(s, While):
            self.check_condition(s.cond, "while")
            self.loop_depth += 1
            self.check_stmt(s.body)
            self.loop_depth -= 1
        elif isinstance(s, DoWhile):
            self.loop_depth += 1
            self.check_stmt(s.body)
            self.loop_depth -= 1
            self.check_condition(s.cond, "while")
        elif isinstance(s, Switch):
            self.check_expr(s.expr)
            # switch 里 break 合法，所以按"循环"处理 break 的深度计数
            self.loop_depth += 1
            self.check_stmt(s.body)
            self.loop_depth -= 1
        elif isinstance(s, Case):
            if s.value is not None:
                self.check_expr(s.value)
            if s.stmt is not None:
                self.check_stmt(s.stmt)
        elif isinstance(s, For):
            self.push()
            # for 的 init 可能是 VarDecl 也可能是 ExprStmt，
            # 后者走 check_stmt 才会把 i=0 记为"已初始化"。
            # 早期版本对 ExprStmt 只调 check_expr，导致 i 一直算未初始化。
            if s.init is not None:
                self.check_stmt(s.init)
            if s.cond is not None:
                self.check_condition(s.cond, "for")
            if s.step is not None:
                self.check_expr(s.step)
            self.loop_depth += 1
            self.check_stmt(s.body)
            self.loop_depth -= 1
            self.pop()
        elif isinstance(s, Return):
            if s.value is not None:
                self.check_expr(s.value)
                self.check_return_local_addr(s)
        elif isinstance(s, (Break, Continue)):
            if self.loop_depth == 0:
                self.err("E11", s.line, s.col,
                         f"{type(s).__name__.lower()} 只能用在循环内部",
                         "检查是否写在了循环外")
        elif isinstance(s, ExprStmt):
            self.check_expr(s.expr)

    def check_condition(self, cond, ctx):
        """条件表达式检查：重点抓 if (a = 1) 这类误写（W5）。"""
        t = self.check_expr(cond)
        if isinstance(cond, Assign) and cond.op == "=":
            self.warn("W5", cond.line, cond.col,
                      f"{ctx} 的条件里用了赋值 =，通常是想写比较 ==",
                      f"如果确实要赋值，请改写成 ({cond.target.name} = ...) != 0")
        # 恒真恒假常量
        if isinstance(cond, Num):
            truth = bool(cond.value)
            self.warn("W5", cond.line, cond.col,
                      f"条件恒为{'真' if truth else '假'}（常量 {cond.value}）",
                      "检查是否漏写了变量或比较")
        if t == "float":
            # 只有"浮点表达式与 0 直接比较"才有精度风险。
            # 早期版本不区分条件形态，只要条件整体是浮点就报，
            # 于是 `if (a + b == c)` 也被说成"与 0 直接比较"——
            # 结论方向没错但文案是错的，学生会以为代码里有个 0。
            is_zero_cmp = (
                isinstance(cond, BinOp)
                and cond.op in ("==", "!=", "<", ">", "<=", ">=")
                and ((isinstance(cond.right, Num) and cond.right.value == 0)
                     or (isinstance(cond.left, Num) and cond.left.value == 0))
            )
            if is_zero_cmp:
                self.warn("W5", cond.line, cond.col,
                          "浮点值与 0 直接比较，可能因精度出错",
                          "改用 fabs(x) < 1e-6 之类的写法")
            else:
                # 浮点之间用 == 比较同样有风险，但文案不同
                if isinstance(cond, BinOp) and cond.op in ("==", "!="):
                    self.warn("W5", cond.line, cond.col,
                              "浮点值用 == 或 != 比较，可能因精度出错",
                              "浮点运算有误差，建议比较差值是否小于阈值")

    # ---- 表达式 ----
    def check_expr(self, e) -> Optional[str]:
        """返回表达式的推导类型名，未知返回 None。

        带递归深度保护：超长下标链/成员链会让 AST 深达数百层，
        不做限制就是 RecursionError（stack overflow），
        而学生需要的是诊断不是 traceback。
        """
        self._expr_depth += 1
        try:
            if self._expr_depth > self.MAX_EXPR_DEPTH:
                self.err("E17", getattr(e, "line", 0), getattr(e, "col", 0),
                         "表达式嵌套过深，无法完成类型检查",
                         "把长表达式拆成多条语句")
                return None
            return self._check_expr_inner(e)
        finally:
            self._expr_depth -= 1

    def _check_expr_inner(self, e) -> Optional[str]:
        if e is None:
            return None

        if isinstance(e, Num):
            return "float" if e.is_float else "int"

        if isinstance(e, Str):
            return "char*"

        if isinstance(e, Ident):
            # sizeof(int) 里的 int 是类型名，不是变量。
            # parser 用 repr(Type) 把它塞进 Ident，这里要认出来。
            sym = self.scope.lookup(e.name)
            # NULL 这类标准库常量
            if sym is None and e.name in STD_MACROS:
                return "int"
            # sizeof(Node) 里的 typedef 名
            if sym is None and e.name in self.typedefs:
                return "int"
            if sym is None and _is_type_name(e.name):
                return "int"
            # 框架里的小写类型名（npy_intp 等），当类型处理
            if sym is None and (e.name.startswith("npy_")
                                or e.name.startswith("NPY_")):
                return "int"
            # 来自被忽略头文件的宏（全大写），容忍掉。
            # 这类名字在我们看不到的 .h 里有定义，报错是误报。
            if sym is None and _is_likely_external_macro(e.name):
                return None
            if sym is None:
                self.err("E1", e.line, e.col,
                         f"使用了未声明的变量 {e.name!r}",
                         "检查拼写，或先声明它")
                return None
            if sym.kind == "func":
                return "func"
            if not sym.initialized:
                self.err("E2", e.line, e.col,
                         f"使用了未初始化的变量 {e.name!r}",
                         f"它声明在 {sym.line}:{sym.col}，但没有赋初值")
            sym.used = True
            return sym.vtype.base

        if isinstance(e, BinOp):
            lt = self.check_expr(e.left)
            rt = self.check_expr(e.right)
            # 除零 / 模零
            if e.op in ("/", "%") and isinstance(e.right, Num) and e.right.value == 0:
                self.err("E12", e.line, e.col,
                         f"除数是常量 0（{e.op} 运算）",
                         "运行时会崩溃或触发未定义行为")
            # 整数除法丢精度（除零已报错时不再重复提示）
            #
            # 例外：sizeof(a)/sizeof(b) 是取数组长度的标准惯用法，
            # 两个操作数都是 sizeof，结果本来就是元素个数，不存在"丢小数"。
            # 早期无脑报警，把 `sizeof(paths)/sizeof(paths[0])` 这种
            # 教科书写法也报成 W3（实测 real_nmakehlp.c:798 就是这样）。
            _is_sizeof = lambda n: (isinstance(n, UnOp) and n.op == "sizeof")
            if (e.op == "/" and lt == "int" and rt == "int"
                    and not (isinstance(e.right, Num) and e.right.value == 0)
                    and not (_is_sizeof(e.left) and _is_sizeof(e.right))):
                self.warn("W3", e.line, e.col,
                          "整数相除会丢弃小数部分",
                          "若要精确结果，把任一操作数写成浮点，如 5.0 / 2")
            if lt == "float" or rt == "float":
                return "float"
            return lt or rt

        if isinstance(e, UnOp):
            # 类型转换 (Node *)malloc(...) 里的 Node 是类型名，不是变量
            if e.op == "cast":
                return self.check_expr(e.operand)
            # 取地址 &x 只是取地址，不读 x 的值，所以不该检查 x 是否初始化。
            # 不这么处理的话，struct S *p = &s; 之后再用 s 会被误报未初始化
            # （实测 (*p).a 就因这条链路报了 s 未初始化）。
            if e.op == "&":
                return self.check_lvalue(e.operand)
            # 解引用 *p 会读 p 指向的内容，p 本身必须已初始化
            if e.op == "*":
                base = e.operand
                if isinstance(base, Ident):
                    sym = self.scope.lookup(base.name)
                    if sym is not None:
                        sym.used = True
                        if not sym.initialized:
                            self.err("E2", base.line, base.col,
                                     f"对未初始化的指针 {base.name!r} 做解引用",
                                     "先让它指向有效的内存")
                        return sym.vtype.base
                return self.check_expr(base)
            # sizeof 在算类型尺寸之前不能调 check_expr(e.operand)：
            # 操作数可能是类型名（sizeof(int)），当成变量检查会误报 E1。
            if e.op == "sizeof":
                sz = self.sizeof_node(e.operand)
                # 把算出的尺寸挂在节点上，供 ir.py 生成真实常量
                try:
                    e.size_value = sz
                except Exception:
                    pass
                return "int"
            t = self.check_expr(e.operand)
            if e.op == "!":
                return "int"
            return t

        if isinstance(e, Ternary):
            self.check_expr(e.cond)
            t1 = self.check_expr(e.then)
            t2 = self.check_expr(e.els)
            if t1 == "float" or t2 == "float":
                return "float"
            return t1 or t2

        if isinstance(e, Assign):
            # 左值是"被写入"，不能按"读取未初始化变量"检查。
            # 早期版本对 p = &a 误报 E2，就是因为检查了左值。
            tt = self.check_lvalue(e.target)
            vt = self.check_expr(e.value)
            if isinstance(e.target, Ident):
                sym = self.scope.lookup(e.target.name)
                if sym:
                    sym.initialized = True
                    self.check_assign_compat(sym.vtype, vt, e, e.target.name)
            return tt

        if isinstance(e, Call):
            return self.check_call(e)

        if isinstance(e, Index):
            self.check_expr(e.base)
            self.check_expr(e.idx)
            self.check_oob(e, "read")
            return "int"

        if isinstance(e, InitList):
            for it in e.items:
                self.check_expr(it)
            return None

        if isinstance(e, Member):
            return self.check_member(e)

        return None

    def resolve_struct(self, ty) -> Optional[StructDef]:
        """把变量的类型解析到 struct 定义上。

        链路：变量类型可能是 typedef 名（Node），也可能直接写成
        struct Node。两种都要能追到定义，否则成员检查无从谈起。
        """
        if ty is None:
            return None
        base = ty.base or ""
        if base.startswith("struct "):
            return self.structs.get(base[len("struct "):])
        if base == "struct":
            return None
        # 先直接按别名查（覆盖匿名结构体 typedef 的情况），
        # 查不到再顺着 typedef 链往下展开。
        # 顺序反了的话，匿名结构体的成员定义会被漏掉。
        sd = self.structs.get(base)
        if sd is not None:
            return sd
        if base in self.typedefs:
            inner = self.typedefs[base]
            if inner is not ty:
                return self.resolve_struct(inner)
        return None

    def expand_typedef(self, ty) -> "Type":
        """把 typedef 别名展开成它真正的底层类型，并合并指针层。

        为什么必须做这一步：
            typedef struct node { ... } Node, *LinkList;
            LinkList head;      // LinkList 本身是 struct node*
        变量声明时记录的是 Type(base="LinkList", ptr=0)，
        指针层藏在 typedef 里。不合并的话，head->next 会被当成
        "在非指针上取成员"而直接跳过检查，
        导致结构体成员拼错在真实代码上完全不报（实测确实如此）。
        """
        if ty is None:
            return None
        base = ty.base or ""
        # 匿名结构体的 typedef（typedef struct {...} Tab;）：
        # 别名的底层类型记成 "struct"，但成员信息挂在别名 Tab 上。
        # 这种情况不能展开——一展开 base 变成 "struct"，
        # 结构体信息就丢了，成员检查和数组越界检查会全部失效。
        if base in self.structs:
            return ty
        if base in self.typedefs and base not in self.structs:
            inner = self.typedefs[base]
            merged = Type(base=inner.base,
                          ptr=inner.ptr + ty.ptr,
                          array=ty.array if ty.array is not None else inner.array,
                          dims=list(ty.dims) or list(inner.dims))
            # 防止 typedef 自引用导致死循环
            if inner.base == base:
                return ty
            return self.expand_typedef(merged)
        return ty

    def member_base_var(self, node):
        """剥掉一层层 . 和 ->，拿到最底下的变量节点。"""
        depth = 0
        while isinstance(node, Member) and depth < 16:
            node = node.base
            depth += 1
        return node

    def check_member_base(self, node) -> Optional[str]:
        """成员访问的基表达式：只验证存在性，不判整体初始化。

        a->next 读的是指针 a 的值，所以指针本身未初始化仍要报；
        而 struct 变量某个字段的访问不该报"结构体未初始化"——
        访问成员只碰到这一个字段，不代表整个结构体被读取。
        早期版本在这里对 p.x = 1 误报 E2。
        """
        base_var = self.member_base_var(node)
        if isinstance(base_var, Ident):
            sym = self.scope.lookup(base_var.name)
            if sym is None:
                if (base_var.name in STD_MACROS
                        or _is_type_name(base_var.name)
                        or _is_likely_external_macro(base_var.name)):
                    return None
                self.err("E1", base_var.line, base_var.col,
                         f"使用了未声明的变量 {base_var.name!r}",
                         "检查拼写，或先声明它")
                return None
            sym.used = True
            # 指针经 -> 访问时要读指针值，必须已初始化
            if sym.vtype.ptr > 0 and not sym.initialized:
                self.err("E2", base_var.line, base_var.col,
                         f"使用了未初始化的指针 {base_var.name!r}",
                         f"它声明在 {sym.line}:{sym.col}，但没有赋初值")
            return sym.vtype.base
        # 基表达式不是裸标识符（如 (*p).a、mk().a、arr[i].x）：
        # 只检查它的"基"，绝不能再把整个 Member 节点丢回 check_expr，
        # 否则 check_expr -> check_member -> check_member_base -> check_expr
        # 无限递归，直接 RecursionError 崩溃（实测 (*p).a 必崩）。
        inner = node
        while isinstance(inner, Member):
            inner = inner.base
        if inner is node:
            return None
        return self.check_member_base(inner)


    def type_of_chain(self, base_node):
        """沿 a.b / p->b / arr[i] 这类链把类型逐级推导出来。

        返回 (最终类型, 是否成功)。这是成员检查、数组越界检查共用的核心。

        关键：链上可能混着 Index 节点（p->defs[0].rank[99]）。
        早期版本只收集 Member，碰到 Index 就停，于是它之后的类型推导
        整段断掉——rank[99] 的越界因此静默漏报。
        现在 Index 也参与：对数组/指针取一次元素类型即可。
        """
        node = base_node
        guard = 0
        while guard < 64:
            guard += 1
            if isinstance(node, (Member, Index)):
                node = node.base
                continue
            break
        # node 现在应当是链最底部的标识符
        if not isinstance(node, Ident):
            return None
        sym = self.scope.lookup(node.name)
        if sym is None:
            return None

        ty = self.expand_typedef(sym.vtype)

        # 自底向上重放整条链
        chain = []
        cur = base_node
        guard = 0
        while guard < 64:
            guard += 1
            if isinstance(cur, (Member, Index)):
                chain.append(cur)
                cur = cur.base
                continue
            break
        chain.reverse()

        for step in chain:
            if ty is None:
                return None
            ty = self.expand_typedef(ty)

            if isinstance(step, Index):
                # 取一次元素类型：数组降一维，指针解一层
                if ty.dims:
                    rest = list(ty.dims)[1:]
                    ty = Type(base=ty.base,
                              ptr=ty.ptr,
                              array=rest[0] if rest else None,
                              dims=rest)
                elif ty.ptr > 0:
                    ty = Type(base=ty.base, ptr=ty.ptr - 1,
                              array=None, dims=[])
                elif ty.array is not None:
                    ty = Type(base=ty.base, ptr=ty.ptr,
                              array=None, dims=[])
                else:
                    return None
                continue

            # Member：按 -> 或 . 解到结构体上，再取成员类型
            if step.arrow or ty.ptr > 0:
                if ty.ptr == 0:
                    return None
                ty = Type(base=ty.base, ptr=ty.ptr - 1,
                          array=None, dims=[])
            sd = self.resolve_struct(ty)
            if sd is None or not sd.members:
                return None
            names = {mem[1]: mem[0] for mem in sd.members}
            if step.name not in names:
                return None
            ty = self.expand_typedef(names[step.name])

        return ty

    def array_len_of(self, base_node):
        """数组表达式的长度；解析不出返回 None。"""
        ty = self.type_of_chain(base_node)
        if ty is None:
            return None
        return ty.array

    def check_oob(self, e: "Index", label: str = ""):
        """统一的数组越界检查，读写共用。

        label 只用于提示语（"写入"/"读取"），不影响判定。
        """
        ci = const_int(e.idx)
        if ci is None:
            return
        n = self.array_len_of(e.base)
        if n is None:
            return
        if ci >= n or ci < 0:
            what = "写入" if label == "write" else "读取"
            self.err("E13", e.line, e.col,
                     f"数组下标 {ci} 越界（长度是 {n}，{what}）",
                     f"合法下标是 0..{n - 1}")
    def check_member(self, e) -> Optional[str]:
        """成员访问检查：结构体上访问不存在的成员要报错。

        这是 C 里最高频的笔误之一（把 n.next 写成 n.nxt），
        编译器给的 "no member named" 学生常常看不懂，
        这里直接指出并给出最接近的正确拼写。
        """
        self.check_member_base(e)
        # 成员链里夹着的下标表达式也要检查：fp->defs[i].rank 的 i。
        # 不查的话，i 不会被标记为"已使用"（误报 W6），
        # 未声明的下标变量也不报（漏报 E1）。
        self.check_member_chain_exprs(e)

        base_var = self.member_base_var(e)
        if not isinstance(base_var, Ident):
            return None
        sym = self.scope.lookup(base_var.name)
        if sym is None:
            return None

        ty = self.expand_typedef(sym.vtype)
        # 逐层走过成员链，把类型往下推（a.b.c 需要逐级解析）
        chain = []
        node = e
        while isinstance(node, Member):
            chain.append(node)
            node = node.base
        chain.reverse()

        cur_ty = ty
        for m in chain:
            if cur_ty is None:
                return None
            cur_ty = self.expand_typedef(cur_ty)
            # 指针要解引用一层才能取成员（a->x 与 (*a).x 等价）
            if m.arrow or cur_ty.ptr > 0:
                if cur_ty.ptr == 0:
                    return None
                cur_ty = Type(base=cur_ty.base, ptr=cur_ty.ptr - 1,
                              array=None, dims=[])
            sd = self.resolve_struct(cur_ty)
            if sd is None or not sd.members:
                return None          # 定义不可见，不做判断
            names = {mem[1]: mem[0] for mem in sd.members}
            if m.name not in names:
                close = _closest_name(m.name, names)
                hint = (f"是不是想写 {close!r}？" if close else
                        f"可用的成员：{'、'.join(sorted(names))}")
                self.err("E15", m.line, m.col,
                         f"结构体 {sd.tag or cur_ty.base} 没有成员 {m.name!r}",
                         hint)
                return None
            cur_ty = self.expand_typedef(names[m.name])
        return cur_ty.base if cur_ty else None


    def check_member_chain_exprs(self, e):
        """检查成员链里夹着的下标/调用等子表达式。

        例：fp->defs[i].rank
             ^^^^^^^^^^^^^ 需要检查 i；base 部分由 check_member_base 管。
        只处理 Index 节点的 idx（元素越界另外由 check_oob 管）。
        """
        seen = 0
        node = e
        while seen < 32:
            seen += 1
            if isinstance(node, Member):
                node = node.base
                continue
            if isinstance(node, Index):
                # 下标表达式本身要检查（未声明/未初始化都会在此报出）
                self.check_expr(node.idx)
                node = node.base
                continue
            break

    # 类型尺寸表（64 位模型，教学用简化，不考虑对齐填充）
    TYPE_SIZE = {
        "char": 1, "signed char": 1, "unsigned char": 1,
        "short": 2, "unsigned short": 2,
        "int": 4, "unsigned int": 4, "signed int": 4,
        "long": 8, "unsigned long": 8, "long long": 8,
        "float": 4, "double": 8,
        "void": 1,          # GCC 扩展：sizeof(void)==1
    }

    def sizeof_node(self, node) -> int:
        """计算 sizeof 操作数的字节数。

        支持三种写法：
          sizeof(int)          -> 类型名
          sizeof(x)            -> 变量
          sizeof(struct S)     -> 结构体
        算不出时返回 4（保守），绝不崩溃。
        """
        # 情形 1：类型名或 struct X（parser 用 Ident 承载）
        if isinstance(node, Ident):
            name = node.name
            if name in self.TYPE_SIZE:
                return self.TYPE_SIZE[name]
            if name.startswith("struct "):
                sd = self.structs.get(name[len("struct "):])
                return self._sizeof_struct(sd) if sd else 4
            # 匿名结构体的 typedef 名
            if name in self.structs:
                return self._sizeof_struct(self.structs[name])
            if name in self.typedefs:
                return self._sizeof_type(self.typedefs[name])
            # 情形 2：变量名
            sym = self.scope.lookup(name)
            if sym is not None:
                return self._sizeof_type(sym.vtype)
            return 4

        # 情形 3：其他表达式，先推类型
        if isinstance(node, UnOp) and node.op == "sizeof":
            return 8        # sizeof(sizeof(x)) 极少见，按 size_t 宽度
        return 4

    def _sizeof_type(self, ty) -> int:
        """按类型算尺寸。数组要乘元素个数。"""
        if ty is None:
            return 4
        base = ty.base or "int"
        # 指针统一 8 字节
        if ty.ptr > 0:
            return 8
        elem = self.TYPE_SIZE.get(base)
        if elem is None:
            if base.startswith("struct "):
                sd = self.structs.get(base[len("struct "):])
                elem = self._sizeof_struct(sd) if sd else 4
            elif base in self.structs:
                elem = self._sizeof_struct(self.structs[base])
            elif base in self.typedefs:
                return self._sizeof_type(self.typedefs[base])
            else:
                elem = 4
        # 数组：元素尺寸 × 第一维长度
        n = ty.array
        if n is None and ty.dims:
            n = ty.dims[0]
        if isinstance(n, int) and n > 0:
            return elem * n
        return elem

    def _sizeof_struct(self, sd) -> int:
        """结构体尺寸 = 各成员尺寸之和（不考虑对齐填充，教学简化）。"""
        if sd is None or not sd.members:
            return 4
        total = 0
        for mty, _name in sd.members:
            total += self._sizeof_type(mty)
        return total or 4

    def check_return_local_addr(self, ret) -> None:
        """检查是否返回了局部变量的地址（D.1 号禁区）。

        `return &local;` 是最经典的悬垂指针来源：
        函数一返回栈帧就失效，调用方拿到的是野指针。
        教材讲"函数返回指针"时列为头号错误，gcc 也只给
        -Wreturn-local-addr 警告，程序照跑不误然后打出垃圾值。

        检测范围（保守，只报确凿的）：
          - return &局部变量
          - return 局部数组名（数组名本身退化成地址）
        不报全局变量/静态变量/malloc 的结果——那些是合法的。
        """
        if self.cur_func is None:
            return
        v = ret.value
        # 情形 1：return &x
        if isinstance(v, UnOp) and v.op == "&":
            target = v.operand
            if isinstance(target, Ident):
                sym = self.scope.lookup(target.name)
                if sym is None:
                    return
                if sym.kind in ("param",):
                    return          # 形参的地址来自调用方，合法
                # 全局变量与静态变量返回地址是合法的
                if self.globals.lookup_local(target.name) is sym:
                    return
                self.warn("W9", ret.line, ret.col,
                          f"返回了局部变量 {target.name!r} 的地址，"
                          f"该地址在函数返回后失效",
                          "改用全局变量、static 变量，或用 malloc 分配")
                return
        # 情形 2：return 局部数组名（数组名退化为首地址）
        if isinstance(v, Ident):
            sym = self.scope.lookup(v.name)
            if sym is None or sym.kind != "var":
                return
            if sym.vtype is None or sym.vtype.array is None:
                return              # 不是数组，返回的是值，合法
            if self.globals.lookup_local(v.name) is sym:
                return              # 全局数组可以返回
            self.warn("W9", ret.line, ret.col,
                      f"返回了局部数组 {v.name!r}（数组名退化为首地址），"
                      f"该地址在函数返回后失效",
                      "改用全局数组、static 数组，或用 malloc 分配")
    def check_lvalue(self, e) -> Optional[str]:
        """左值检查：只验证"这个标识符存不存在"，不检查是否已初始化。

        写入一个未初始化变量是完全合法的，把它当错误报是典型的误报。
        """
        if isinstance(e, Ident):
            sym = self.scope.lookup(e.name)
            if sym is None:
                # 来自被忽略头文件的符号，不该报未声明。
                # 这一处特别容易漏：&PyArray_Type 作为函数实参时走左值路径，
                # 早期只修了 check_expr 侧，导致这类全局对象仍被误报。
                if (e.name in STD_MACROS or e.name in self.typedefs
                        or _is_type_name(e.name)
                        or _is_likely_external_macro(e.name)):
                    return "int"
                self.err("E1", e.line, e.col,
                         f"使用了未声明的变量 {e.name!r}",
                         "检查拼写，或先声明它")
                return None
            if sym.kind == "func":
                return "func"
            sym.used = True
            return sym.vtype.base
        if isinstance(e, Index):
            # 下标表达式的 base 与 idx 都是"读"，走普通检查
            self.check_expr(e.base)
            self.check_expr(e.idx)
            # 写入数组元素同样要查越界：arr[-1] = 1 也是错的
            self.check_oob(e, "write")
            return "int"
        if isinstance(e, UnOp) and e.op == "*":
            return self.check_expr(e.operand)
        if isinstance(e, UnOp) and e.op == "&":
            # &v 是取地址：v 只是"被取地址"，不算读取它的值。
            # scanf("%d", &v) 里的 v 就是典型情况，按读取检查会误报未初始化。
            return self.check_lvalue(e.operand)
        if isinstance(e, Member):
            # 写成员字段同样要走成员检查（拼错的字段名要报），
            # 但不把整个结构体变量当成"被读取"。
            return self.check_member(e)
        return self.check_expr(e)

    def check_call(self, call: Call) -> Optional[str]:
        if not isinstance(call.func, Ident):
            # 函数指针调用：(*fn)(...) / obj->fp(...)。
            # 看不到被调函数的签名，所以按外部函数处理：
            # &x 视为可能被写入并标记已初始化。
            # 不这么做，(*(getter))(&flag) 之后用 flag 会误报未初始化。
            self.check_expr(call.func)
            for a in call.args:
                if isinstance(a, UnOp) and a.op == "&" \
                        and isinstance(a.operand, Ident):
                    self.check_lvalue(a)
                    sym = self.scope.lookup(a.operand.name)
                    if sym:
                        sym.initialized = True
                else:
                    self.check_expr(a)
            return None

        name = call.func.name
        fn = self.funcs.get(name)

        # 函数指针变量调用：typedef 出来的类型 base 是 "funcptr"，
        # 或 typedef 名本身指向函数指针。
        # 不先认出来，cb(arg) 会被当成"调用未声明的函数 cb"而误报 E5。
        # 实测：void reg(void (*cb)(void *), void *arg) { cb(arg); }
        sym_call = self.scope.lookup(name) if fn is None else None
        if sym_call is not None and sym_call.kind != "func":
            cty = self.expand_typedef(sym_call.vtype)
            if cty is not None and (cty.base == "funcptr" or cty.ptr > 0):
                sym_call.used = True
                for a in call.args:
                    self.check_expr(a)
                return "int"

        # 标准库函数：不报未声明，并按其语义检查参数
        if fn is None and name in STD_FUNCS:
            minargs, maxargs, out_mode = STD_FUNCS[name]
            n = len(call.args)
            if n < minargs or n > maxargs:
                rng = (f"{minargs}" if minargs == maxargs
                       else f"{minargs}~{maxargs}" if maxargs < 99
                       else f"至少 {minargs}")
                self.err("E14", call.line, call.col,
                         f"标准库函数 {name} 需要 {rng} 个参数，传了 {n} 个")
            for i, a in enumerate(call.args):
                # 写出位置：scanf 的第 2 个起，clock_gettime 的第 2 个
                # out_modes 里的数字表示"第几个参数起是输出"（0 基）。
                # 用精确索引，不能写成 i >= 2 —— 那会把
                # WriteFile(..., &dw, NULL) 的 NULL 也当左值报错。
                is_out = False
                if out_mode in ("varargs_out", "needs_addr_out"):
                    is_out = i >= 1
                elif isinstance(out_mode, int):
                    is_out = i == out_mode
                elif out_mode == "second_out":
                    is_out = i == 1
                elif out_mode == "third_out":
                    is_out = i == 2
                if is_out:
                    # 输出参数分两种，语义完全不同：
                    #   &x    —— scanf 家族：输入项必须是地址表达式
                    #   裸名   —— va_start(argp, var)：直接写变量
                    # 早期版本把两种一视同仁地标记为已初始化，结果
                    # `scanf("%d", v)`（漏写 &）这种高频真实错误被一起吃掉，
                    # 连"显然传错"都报不出来——宽容反而制造了漏报。
                    if isinstance(a, UnOp) and a.op == "&" \
                            and isinstance(a.operand, Ident):
                        self.check_lvalue(a)
                        sym = self.scope.lookup(a.operand.name)
                        if sym:
                            sym.initialized = True
                    elif isinstance(a, Ident):
                        # 要求地址的函数收到裸变量名 = 真实错误
                        if out_mode == "needs_addr_out":
                            self.err("E16", a.line, a.col,
                                     f"{name} 的输入参数需要取地址，"
                                     f"实际传的是变量 {a.name!r}",
                                     f"应写成 &{a.name}")
                        else:
                            sym = self.scope.lookup(a.name)
                            if sym is not None:
                                sym.initialized = True
                                sym.used = True
                    else:
                        self.check_expr(a)
                else:
                    self.check_expr(a)
            # malloc/calloc 返回指针
            if name in ("malloc", "calloc", "realloc"):
                return "void*"
            if name in ("strlen", "strcmp", "atoi", "abs", "getchar",
                        "putchar", "printf", "scanf"):
                return "int"
            if name in ("malloc",):
                return "void*"
            return "int"

        if fn is None:
            # 来自被忽略头文件的函数（Windows API、CPython C-API、
            # numpy 等），看不到声明就容忍，不报 E5。
            if _looks_like_external_fn(name):
                for a in call.args:
                    # 看不到声明就不知道哪几个参数是输出。
                    # 保守做法：&x 一律视为"可能被写入"并标记已初始化，
                    # 否则 ReadFile(..., &dwWritten, ...) 之后每次使用
                    # 都会被误报"使用未初始化变量"。
                    if isinstance(a, UnOp) and a.op == "&" \
                            and isinstance(a.operand, Ident):
                        self.check_lvalue(a)
                        sym = self.scope.lookup(a.operand.name)
                        if sym:
                            sym.initialized = True
                    else:
                        self.check_expr(a)
                return "int"
            self.err("E5", call.line, call.col,
                     f"调用了未声明的函数 {name!r}",
                     "检查拼写，或先添加函数声明")
            for a in call.args:
                self.check_expr(a)
            return None
            self.err("E5", call.line, call.col,
                     f"调用了未声明的函数 {name!r}",
                     "检查拼写，或先添加函数声明")
            for a in call.args:
                self.check_expr(a)
            return None

        # 参数个数检查（有函数体时才严格，声明可能省略形参名）
        expected = len(fn.params)
        got = len(call.args)
        if fn.body is not None and expected != got:
            self.err("E4", call.line, call.col,
                     f"函数 {name!r} 需要 {expected} 个参数，但传了 {got} 个",
                     "对照函数定义检查调用")
        for a in call.args:
            self.check_expr(a)
        return fn.ret_type.base if fn.ret_type else None


def analyze(prog: Program) -> list:
    return Analyzer().analyze(prog)


if __name__ == "__main__":
    import sys
    from parser import parse
    src = open(sys.argv[1], encoding="utf-8").read()
    for d in analyze(parse(src)):
        print(d)


def _closest_name(target: str, candidates) -> Optional[str]:
    """找最接近的成员名（编辑距离 <= 2 且唯一），用于"是不是想写 X"提示。"""
    best, best_d = None, 3
    for c in candidates:
        d = _edit_distance(target.lower(), c.lower())
        if d < best_d:
            best, best_d = c, d
    return best if best_d <= 2 else None


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein 距离，只用于小字符串的拼写建议。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]

def _looks_like_external_fn(name: str) -> bool:
    """判断函数名是否"来自看不到的头文件"（不该报 E5）。

    覆盖：Windows API（SetErrorMode）、CPython C-API（PyErr_Occurred）、
    numpy（PyArray_...）、以及驼峰命名的框架函数。
    学生自己的函数通常是全小写或下划线风格（add、get_node），
    所以驼峰/前缀规则误判概率很低。
    """
    if not isinstance(name, str) or not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    # 标准库常见函数已在 STD_FUNCS 里，这里只兜头文件里的
    if name[0].isupper():
        return True
    for prefix in ("Py", "NPY_", "npy_", "PyArray_", "_Py", "F2PY_"):
        if name.startswith(prefix) and len(name) > len(prefix):
            rest = name[len(prefix):]
            if rest[:1].isupper():
                return True
    return False