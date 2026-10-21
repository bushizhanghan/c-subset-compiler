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


INT_MAX = 2147483647
INT_MIN = -2147483648

# 逐字面量最大位数（截断阈值），超过的值不做溢出判断（parser 已截断）
_SAFE_INT_MAX_DIGITS = 4000


def check_const_overflow(node, _depth=0):
    """检查常量表达式是否溢出 int 范围。

    返回 (value, overflow)：
      value    计算结果（Python 无限精度，不会自己溢出）
      overflow bool，是否超出 [-2147483648, 2147483647]

    只看**编译期可算出**的表达式。运行时变量的溢出不做判断。

    踩过的坑：不能直接改 const_int——它是数组越界、switch 标签
    等检查的底层工具，改它会让一大堆现有检查静默失效。
    """
    if _depth > 20:
        return (None, False)
    if isinstance(node, Num) and not node.is_float:
        suf = (node.suffix or "").lower()
        if "l" in suf or "u" in suf:
            return (int(node.value), False)
        v = int(node.value)
        if len(str(v).lstrip("-")) > _SAFE_INT_MAX_DIGITS:
            return (v, False)
        return (v, v > INT_MAX or v < INT_MIN)
    if isinstance(node, UnOp) and node.op in ("-", "+"):
        v, ov = check_const_overflow(node.operand, _depth + 1)
        if v is None:
            return (None, False)
        r = -v if node.op == "-" else v
        return (r, ov or r > INT_MAX or r < INT_MIN)
    if isinstance(node, BinOp) and node.op in ("+", "-", "*", "<<", ">>"):
        a, ova = check_const_overflow(node.left, _depth + 1)
        b, ovb = check_const_overflow(node.right, _depth + 1)
        if a is None or b is None:
            return (None, False)
        # 操作数本身是 long/unsigned → 整个表达式不判溢出
        lf = (node.left.suffix or "").lower() if isinstance(node.left, Num) else ""
        rf = (node.right.suffix or "").lower() if isinstance(node.right, Num) else ""
        if "l" in lf or "u" in lf or "l" in rf or "u" in rf:
            return (None, False)
        try:
            if node.op == "+":
                r = a + b
            elif node.op == "-":
                r = a - b
            elif node.op == "*":
                r = a * b
            elif node.op == "<<":
                r = a << b
            else:
                r = a >> b
            return (r, ova or ovb or r > INT_MAX or r < INT_MIN)
        except (ValueError, OverflowError):
            return (None, False)
    # 除法/取模/位运算/比较不产生溢出（结果在操作数范围内）
    return (None, False)


class VT(str):
    """表达式的推导类型：基类型名（仍是 str）+ 结构信息。

    为什么继承 str 而不是换成新对象：check_expr 的返回值在十几处被
    直接和 "float" / "int" 比较（W3/W4/W5 的判据）。换成新对象要改遍
    整个文件，且漏改一处就静默失效——那正是这个项目最不能接受的
    失效方式（诊断悄悄不报，测试还是绿的）。

    继承 str 之后 `vt == "float"` 照旧成立，新信息（指针层、数组、
    符号性）走属性。由此才能修掉三类真实的漏报：

      - `char c = "A"`        —— 需要知道右边是 char*（ptr=1）
      - `int x = p + 1`       —— 需要知道右边是指针
      - `if (b < a)` 混合符号 —— 需要知道 a 是无符号（Type.unsigned）

    这些信息以前在表达式层全部丢掉，只剩一个 base 字符串。
    """
    __slots__ = ("ptr", "array", "unsigned", "dims")

    def __new__(cls, base="int", ptr=0, array=None, unsigned=False, dims=()):
        self = super().__new__(cls, base)
        self.ptr = ptr
        self.array = array
        self.unsigned = unsigned
        self.dims = tuple(dims or ())
        return self

    def text(self) -> str:
        """人话版类型名，用于提示语。"""
        s = str(self)
        if self.array is not None:
            s = f"{s}[{self.array}]"
        return s + "*" * self.ptr


def _vt_of_type(ty, decay: bool = True):
    """把声明里的 Type 转成表达式层的 VT。

    decay=True 表示"数组名在表达式里退化成首地址"（C 的数组退化规则）：
    每退化一次指针层 +1，所以 int a[3] 的表达式类型是 (ptr=1, array=3)，
    而 int *p[3]（元素是指针的数组）退化后是 ptr=2。

    声明目标（赋值左边）不能用退化口径，否则 `int a[3]; a = x;`
    会被当成给指针赋值。赋值检查那边显式传 decay=False。
    """
    if ty is None:
        return None
    base = ty.base or "int"
    is_arr = ty.array is not None or bool(ty.dims)
    ptr = ty.ptr
    if is_arr and decay:
        ptr = ty.ptr + 1
    return VT(base, ptr=ptr,
              array=ty.array if is_arr else None,
              unsigned=bool(getattr(ty, "unsigned", False)),
              dims=tuple(ty.dims or ()))


def _is_plain(vt, name=None) -> bool:
    """是不是"裸的标量类型"（没有指针层、不是数组）。

    继承 str 之后 `VT("int", ptr=1) == "int"` 也是 True，
    既有判据（W3/W4/W5）如果直接拿来比，会把 int* 当成 int。
    """
    if vt is None:
        return False
    if getattr(vt, "ptr", 0) > 0 or getattr(vt, "array", None) is not None:
        return False
    return name is None or vt == name


def _wrap_char(v: int) -> int:
    """把整数按 char（本机有符号，1 字节）回绕后的值，用于提示语。"""
    return ((v + 128) % 256) - 128


def _is_null_const(node) -> bool:
    """判断表达式是不是"空指针常量"：0 / NULL / (void*)0。

    这一类赋给指针是合法且高频的写法（`char *p = NULL;`），
    不豁免就会在真实代码上批量误报。
    """
    if node is None:
        return False
    if isinstance(node, Num) and not node.is_float:
        try:
            return int(node.value) == 0
        except (TypeError, ValueError):
            return False
    if isinstance(node, Ident):
        if node.name in ("NULL", "nullptr"):
            return True
        return STD_MACROS.get(node.name) in ("0", "0L", "0UL")
    if isinstance(node, UnOp) and node.op == "cast":
        return _is_null_const(node.operand)
    return False


def _is_explicit_cast(node) -> bool:
    """右边写了显式转换 (int)ptr / (char*)p 时不再告警。

    显式转换本身就是"我知道我在转"的表达，告警只会制造噪音；
    而漏掉这条豁免，真实代码里成百上千个 (char*) 转换会全变成假警告。
    """
    return isinstance(node, UnOp) and node.op == "cast"


# 判"这是不是一个整数类目标"，用于指针->整数的隐式转换检查。
# 故意只认内置名：typedef 出来的 DWORD / size_t / HANDLE 在我们这里
# 没有可靠的符号与宽度信息，拿它们做类型判断只会制造误报。
PLAIN_NUMERIC = ("int", "char", "float", "double")
INT_CHAR_SIGNED = ("int", "char")

# char 的表示范围（本机 x86-64：char 是有符号的）
CHAR_MIN, CHAR_MAX = -128, 127


# 标准库函数的返回类型。只登记"确定知道"的那些。
#
# 为什么必须显式登记、而不能默认 int：fopen 返回 FILE*、strdup 返回
# char*、strstr 返回 char*。默认 int 会让 `FILE *fp = fopen(...)`
# 变成"整数赋给指针"，实测 real_nmakehlp.c 上就是这么冒出 6 条假警告的。
# 没登记的返回 None（未知），语义层就不会基于它下任何结论。
STD_FUNCS_RET = {
    # 返回指针
    "malloc": ("void", 1), "calloc": ("void", 1), "realloc": ("void", 1),
    "strdup": ("char", 1), "strstr": ("char", 1), "strchr": ("char", 1),
    "strrchr": ("char", 1), "strtok": ("char", 1), "strcat": ("char", 1),
    "strcpy": ("char", 1), "strncpy": ("char", 1), "strncat": ("char", 1),
    "memset": ("void", 1), "memcpy": ("void", 1), "memmove": ("void", 1),
    "memchr": ("void", 1), "fgets": ("char", 1), "getenv": ("char", 1),
    "fopen": ("void", 1), "fdopen": ("void", 1), "tmpfile": ("void", 1),
    "bsearch": ("void", 1), "clock_gettime": ("int", 0),
    # 返回 int
    "fclose": ("int", 0), "fprintf": ("int", 0), "fscanf": ("int", 0),
    "printf": ("int", 0), "puts": ("int", 0), "putchar": ("int", 0),
    "getchar": ("int", 0), "scanf": ("int", 0), "sscanf": ("int", 0),
    "sprintf": ("int", 0), "snprintf": ("int", 0), "fputs": ("int", 0),
    "strlen": ("size_t", 0), "strcmp": ("int", 0), "strncmp": ("int", 0),
    "strspn": ("int", 0), "strcspn": ("int", 0), "memcmp": ("int", 0),
    "atoi": ("int", 0), "atol": ("int", 0), "abs": ("int", 0),
    "labs": ("int", 0), "rand": ("int", 0), "srand": ("void", 0),
    "isalpha": ("int", 0), "isdigit": ("int", 0), "isspace": ("int", 0),
    "toupper": ("int", 0), "tolower": ("int", 0), "time": ("int", 0),
    "clock": ("int", 0), "fflush": ("int", 0), "exit": ("void", 0),
    "free": ("void", 0), "perror": ("void", 0), "qsort": ("void", 0),
    "assert": ("void", 0), "lstrlen": ("int", 0),
}


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
    # 声明所在的作用域深度。用于检测「块作用域逃逸」：
    # 把 &inner（深度 2）赋给外层的指针 p（深度 1），
    # 出块后 inner 已销毁，p 变成悬垂指针。
    scope_depth: int = 0
    # 数组是否有任何元素被写过（W21 读未初始化元素用）。
    # None 表示不是数组或不追踪。
    array_written: bool = True


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
        # 当前生效的循环变量区间栈：(变量名, 最小下标, 最大下标)
        # 供循环体内的 arr[i] 越界检查（W16）使用
        self._loop_ranges = []
        # 已报过 E2 的变量（函数名, 声明行, 声明列）：
        # 同一变量只报一次，避免循环里刷屏
        self._reported_e2 = set()
        # 当前作用域深度，用于块作用域逃逸检测（W20）
        self._scope_depth = 0
        # 已知是常量的局部变量（W14b 用）。
        # 只记"字面量直接赋值"的变量，被重新赋值立即删除，
        # 绝不做跨控制流的传播——那会产生假阳性。
        self._const_vars = {}

    # ---- 工具 ----
    def err(self, code, line, col, msg, hint=""):
        self.diags.append(Diag("error", code, line, col, msg, hint))

    def warn(self, code, line, col, msg, hint=""):
        self.diags.append(Diag("warning", code, line, col, msg, hint))

    def push(self):
        self._scope_depth += 1
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
        self._scope_depth -= 1

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
        # 单个函数且没有 main：多半是**作业里的一个函数片段**，
        # 不是完整程序。报 error 会让 check/report 直接终止，
        # 学生看不到后面真正的错误（实测 stu_02 就是这样被吃掉的）。
        # 改成提示级：既说明情况，又不阻塞其他诊断。
        self.warn("W17", 1, 1, "没有找到 main 函数",
                 "如果这是完整程序，需要 int main() 作为入口；"
                 "如果只是作业里的单个函数，可以忽略这条")

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
        # W18：递归无法到达基线条件 → 无限递归
        self.check_recursion_terminates(fn)
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
    def _check_stack_array(self, vd: VarDecl):
        """W19：函数内声明超大数组 → 爆栈。

        阈值 256KB（约 6.5 万个 int）。低于它的数组在栈上没问题；
        高于它的在 Windows（1MB 栈）上极易崩，Linux（8MB）也危险。
        全局变量和 static 不在栈上，不报。
        """
        if self.scope is self.globals:
            return
        ty = vd.vtype
        if ty is None:
            return
        # static 局部变量在 .data/.bss，不在栈上，不爆栈
        if getattr(ty, "storage", "") == "static":
            return
        # dims 是完整维度列表；array 是 dims[0] 的冗余副本。
        # 早期把两者拼起来算，100000 算成 100000*100000（9765625KB）；
        # 后来改成去重，又把 int[1000][1000] 的合法重复维度去成 1000。
        # 正确做法：只信 dims，dims 为空时才退回 array。
        dims = list(ty.dims or [])
        if not dims:
            if ty.array is None:
                return
            dims = [ty.array]
        # 只对常量维度算总大小；含变量的维度算不出来，保守不报
        total = 1
        for d in dims:
            if not isinstance(d, int) or d <= 0:
                return
            total *= d
        # 元素大小
        elem = {"char": 1, "short": 2, "int": 4, "long": 8,
                "float": 4, "double": 8}.get(ty.base or "int", 4)
        if ty.ptr:
            elem = 8
        nbytes = total * elem
        if nbytes > 256 * 1024:
            self.warn("W19", vd.line, vd.col,
                      f"局部数组 {vd.name!r} 约 {nbytes // 1024}KB，"
                      f"放在栈上很容易溢出（Windows 栈默认 1MB）",
                      "改成全局变量、static，或用 malloc 分配")

    def check_vardecl(self, vd: VarDecl):
        # 重复声明
        if self.scope.lookup_local(vd.name):
            old = self.scope.lookup_local(vd.name)
            self.err("E3", vd.line, vd.col,
                     f"变量 {vd.name!r} 重复声明",
                     f"上一次声明在 {old.line}:{old.col}")
            return

        # W19：局部大数组爆栈
        # `int a[1000000];` 在 main 里要 4MB，Windows 栈默认 1MB，
        # 一进 main 就段错误。这是 OJ 学生的经典错误——他们看的是
        # "内存限制 64MB" 却把数组放栈上（实测 stu_09，两个 100 万
        # int 数组共 8MB，gcc 完全不报，一跑就崩）。
        self._check_stack_array(vd)

        # 全局变量在 C 里默认零初始化，不存在"未初始化"问题。
        # 早期版本一视同仁，导致 Windows 代码里大量全局缓冲区
        # 被误报 E2（如 ReadFile 写入的 dwWritten）。
        is_global = self.scope is self.globals
        sym = Symbol(name=vd.name, vtype=vd.vtype, kind="var",
                     line=vd.line, col=vd.col,
                     initialized=is_global,
                     scope_depth=0 if is_global else self._scope_depth)
        if vd.init is not None:
            t = self.check_expr(vd.init)
            sym.initialized = True
            # W14b：声明初始化时就可能溢出（int big = 2147483647+1）
            self._check_decl_overflow(vd)
            self.check_assign_compat(vd.vtype, t, vd.init, vd.name,
                                     src_node=vd.init)
        elif vd.vtype.array is not None or vd.vtype.dims:
            # 数组不需要整体初始化：元素可以逐个赋值，也可能整块被
            # 函数写入（GetFullPathName(szPath, n, szCwd, NULL)）。
            # 注意要同时看 dims：维度写成常量表达式（MAX_PATH + 1）时
            # array 是 None，但 dims 有值。只看 array 会漏掉这种。
            sym.initialized = True
            # 但"整体可读"不等于"真的写过任何元素"。W21 要的是后者：
            # int a[4]; printf("%d", a[2]); 是真实高频错误（err_15），
            # 而这里的 initialized=True 会让它完全查不出来。
            # 单独用一个标记记录"是否有任何元素被写过"。
            sym.array_written = False
        self.scope.declare(sym)

    def check_assign_compat(self, target: Type, src, node, name: str,
                            src_node=None):
        """声明/初始化时的类型兼容检查（目标来自声明里的 Type）。"""
        self.check_assign_compat_vt(_vt_of_type(target, decay=False),
                                    src, node, name, src_node)

    def check_assign_compat_vt(self, target_vt, src, node, name: str,
                               src_node=None):
        """赋值/初始化时的类型兼容检查（目标已是表达式层 VT）。

        历史上这里只有一条 float->int 的截断检查（W4），因为表达式层
        只传出 Type.base 一个字符串，指针层和符号性全丢了，
        想多查一条都没有素材。现在 src 是带结构的 VT，才能补上：

          W4   float 赋给 int/char，小数部分被截断
          W11  指针与整数隐式转换（两个方向都查）
          W12  char 赋了超出范围的值（含 char 算术结果回赋 char）

        每一类都带"合法豁免"，宁可少报也不误报：
          - 显式转换 (int)p / (char*)p 不报（写的人知道自己在做什么）
          - 空指针常量 0 / NULL 赋给指针不报（合法且高频）
          - typedef 出来的类型名不参与判断（DWORD、size_t、HANDLE
            的真实宽度/符号我们看不到，猜就是制造假错误）
        """
        if src is None or target_vt is None:
            return

        # ---- E18：给数组整体赋值 ----
        # C 规定数组名是不可修改的左值，`char c[4]; c = "hello";`
        # 是编译错误，gcc 报 "assignment to expression with array type"。
        # 学生从 Python/Java 过来时极容易这么写，而且错误信息看不懂。
        # 注意：初始化列表 int a[3] = {1,2,3} 是合法的，不在检查范围。
        if getattr(target_vt, "array", None) is not None:
            self.err("E18", node.line, node.col,
                     f"不能给数组 {name!r} 整体赋值",
                     "数组名是不可修改的左值；要初始化就在声明处写 "
                     "{...}，要改内容就逐个元素或 strcpy")
            return

        # ---- W4：float 赋给 int/char ----
        if target_vt.ptr == 0 and target_vt in ("int", "char") \
                and _is_plain(src, "float"):
            self.warn("W4", node.line, node.col,
                      f"把 float 赋给 {name!r}（{str(target_vt)}），"
                      f"小数部分会被截断",
                      "确认是否需要 float 类型，或显式写成 (int) 转换")
            return

        # 显式转换过就到此为止：写的人已经明确表达了意图
        if _is_explicit_cast(src_node):
            return

        # ---- W11：指针 -> 整数 ----
        if src.ptr > 0 and target_vt.ptr == 0 \
                and str(target_vt) in PLAIN_NUMERIC \
                and not _is_null_const(src_node) \
                and not self._is_str_into_char_array(target_vt, src, src_node):
            self.warn("W11", node.line, node.col,
                      f"把指针（{src.text()}）赋给整数目标 {name!r}"
                      f"（{str(target_vt)}）：地址会被截断成一个奇怪的整数",
                      "若只是借用地址，请用指针类型存；确实要转整数就显式写 "
                      "(long) 转换")
            return

        # ---- W11：整数 -> 指针 ----
        if target_vt.ptr > 0 and src.ptr == 0 \
                and str(src) in PLAIN_NUMERIC \
                and not _is_null_const(src_node):
            self.warn("W11", node.line, node.col,
                      f"把整数（{src.text()}）赋给指针 {name!r}："
                      f"这不是一个有效的地址",
                      "要表示空指针请写 0 或 NULL；其他地址必须来自 "
                      "取地址 &x / 数组名 / malloc")
            return

        # ---- W12：给 char 赋超出范围的值 ----
        # 只看**右值**节点：传进来的是整条赋值语句的话，const_int 永远折不出
        # 常量，这条检查会静默失效（实测 `c = 300` 一条都不报）。
        if str(target_vt) == "char" and target_vt.ptr == 0 and src.ptr == 0 \
                and not target_vt.unsigned:
            self.check_char_range(src_node if src_node is not None else node,
                                  name)

    def _is_str_into_char_array(self, target_vt, src, src_node) -> bool:
        """`char buf[16] = "abc";` 是合法的初始化，不是指针赋给整数。

        数组初始化时右边写字符串字面量是 C 的标准写法（逐字节拷贝，
        不含取地址语义）。判据要同时看三件事，缺一个就会误伤：
          - 目标 base 是 char
          - 目标是数组（array/dims 有值）
          - 右边确实是字符串字面量节点
        """
        if str(target_vt) != "char":
            return False
        if target_vt.array is None and not target_vt.dims:
            return False
        return isinstance(src_node, Str)

    def check_char_range(self, node, name: str):
        """char 赋值的范围检查（W12）。

        char 只有 1 字节，范围 -128..127。两处会静默出错：

          1) 常量直接超范围： char c = 300;      -> 变成 44
          2) char 参与算术后回赋：c = c + 200;   -> 同样被截断

        为什么值得报：这两种写法 GCC 默认一声不响（要 -Wconversion
        才提示），学生只会看到"数值莫名其妙变了"。

        只在**确有把握**时报：
          - 情形 1 要求能折成常量
          - 情形 2 要求算术的一侧是 char 变量、另一侧是超过 char 范围的常量
        真实代码里 `c = c + 1` 这类自增完全不会命中。
        """
        # 情形 1：常量表达式直接超范围
        ci = const_int(node)
        if ci is not None:
            if ci < CHAR_MIN or ci > CHAR_MAX:
                self.warn("W12", node.line, node.col,
                          f"把 {ci} 赋给 char 变量 {name!r}，"
                          f"超出 char 的表示范围（{CHAR_MIN}..{CHAR_MAX}），"
                          f"会变成 {_wrap_char(ci)}",
                          "改用 int，或确认截断后的值是否就是本意")
            return
        if not isinstance(node, BinOp) or node.op not in ("+", "-", "*", "/"):
            return
        # 情形 2：char 参与算术，另一侧是超范围的整数常量
        for side, other in ((node.left, node.right), (node.right, node.left)):
            vt = self._vt_at(side)
            if vt is None or not _is_plain(vt, "char"):
                continue
            oc = const_int(other)
            if oc is not None and (oc > CHAR_MAX or oc < CHAR_MIN):
                self.warn("W12", node.line, node.col,
                          f"char 变量参与算术后赋回 {name!r}：整数提升会把"
                          f"结果变成 int（这里含常量 {oc}），"
                          f"写回 char 时超出 {CHAR_MIN}..{CHAR_MAX} 会被截断",
                          "先把中间结果存到 int 变量里，再决定是否转回 char")
                return


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
            # W13：if 条件后直接跟分号 = 空分支
            # `if (a > b); max = a;` 是教材专门警告的陷阱：
            # 分号构成 if 的整个 then 分支，紧跟的代码变成无条件执行。
            # 缩进看起来像有分支，实际什么都不做。
            # gcc -Wall 只给 -Wempty-body 警告，很多人不开。
            if self._is_empty_body(s.then):
                self.warn("W13", s.line, s.col,
                          "if 的条件后面直接跟了分号，then 分支是空的",
                          "后面那行代码会无条件执行；删掉分号或改成 {}")
            self.check_stmt(s.then)
            if s.els is not None:
                self.check_stmt(s.els)
        elif isinstance(s, While):
            self.check_condition(s.cond, "while")
            # W15：条件变量在循环体内从未被修改 → 死循环
            self.check_loop_advance(s, "while")
            # 豁免：条件里含函数调用时，空循环体几乎总是故意的。
            if self._is_empty_body(s.body) and not _has_call(s.cond):
                self.warn("W13", s.line, s.col,
                          "while 的循环体是空语句",
                          "若是故意的（忙等待）请加注释说明；否则代码不会按预期执行")
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
            if s.init is not None:
                self.check_stmt(s.init)
            if s.cond is not None:
                self.check_condition(s.cond, "for")
                # W15：for 的 step 也不推进 → 死循环（i-- 方向反了）
                self.check_loop_advance(s, "for")
            if s.step is not None:
                self.check_expr(s.step)
            # W16：记录循环变量的取值区间，供循环体内的下标检查
            rng = self._for_range(s)
            if rng:
                self._loop_ranges.append(rng)
            self.loop_depth += 1
            self.check_stmt(s.body)
            self.loop_depth -= 1
            if rng:
                self._loop_ranges.pop()
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
        # 浮点比较的精度风险（W5）。
        #
        # 判据必须落在"被比较的两个操作数"上，而不是条件整体的类型：
        # 比较运算的结果在 C 里恒为 int，用 cond 的类型去判断永远不成立。
        # 早期版本恰好是拿 cond 的类型比 float —— 那时比较运算直接返回
        # 操作数类型才碰巧能work；现在 binop_type 如实返回 int 之后，
        # 不看操作数就会把这条检查整条丢掉（实测 err_06 丢了 W5）。
        if isinstance(cond, BinOp) and cond.op in ("==", "!=", "<", ">",
                                                   "<=", ">="):
            lt = self._vt_at(cond.left)
            rt = self._vt_at(cond.right)
            is_float_cmp = (_is_plain(lt, "float") or _is_plain(rt, "float")
                            or lt == "double" or rt == "double")
            if is_float_cmp:
                # 只有"浮点表达式与 0 直接比较"才有那种特定的精度风险。
                # 区分文案：`if (f == 0)` 和 `if (a == b)` 都是坏味道，
                # 但说成"与 0 比较"在后者上是错的，学生会以为代码里有个 0。
                is_zero_cmp = (
                    (isinstance(cond.right, Num) and cond.right.value == 0)
                    or (isinstance(cond.left, Num) and cond.left.value == 0)
                )
                if is_zero_cmp:
                    self.warn("W5", cond.line, cond.col,
                              "浮点值与 0 直接比较，可能因精度出错",
                              "改用 fabs(x) < 1e-6 之类的写法")
                elif cond.op in ("==", "!="):
                    self.warn("W5", cond.line, cond.col,
                              "浮点值用 == 或 != 比较，可能因精度出错",
                              "浮点运算有误差，建议比较差值是否小于阈值")

    # ---- 表达式 ----
    def check_expr(self, e):
        """返回表达式的推导类型（VT：基类型 + 指针/数组/符号性）。

        VT 是 str 子类，所以既有的 `t == "float"` 判据全部照旧成立；
        新增的结构信息走属性，供指针/字符/符号检查使用。
        未知返回 None。

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
            t = self._check_expr_inner(e)
            # 把类型挂在节点上：后面的范围检查/实参检查要用到子表达式的
            # 类型，重算一遍会把同一条诊断重复报出，也浪费。
            if e is not None:
                try:
                    e._vt = t
                except Exception:
                    pass
            return t
        finally:
            self._expr_depth -= 1

    def _vt_at(self, node):
        """取子表达式已缓存的类型（没有就返回 None，绝不重算）。"""
        return getattr(node, "_vt", None)

    def _check_expr_inner(self, e):
        if e is None:
            return None

        if isinstance(e, Num):
            return VT("float") if e.is_float else VT("int")

        if isinstance(e, Str):
            # 字符串字面量：指向首字符的指针，只读。
            return VT("char", ptr=1)

        if isinstance(e, Ident):
            # sizeof(int) 里的 int 是类型名，不是变量。
            # parser 用 repr(Type) 把这个名字塞进 Ident，这里要认出来。
            sym = self.scope.lookup(e.name)
            # NULL 这类标准库常量
            if sym is None and e.name in STD_MACROS:
                return VT("int")
            # sizeof(Node) 里的 typedef 名
            if sym is None and e.name in self.typedefs:
                return VT("int")
            if sym is None and _is_type_name(e.name):
                return VT("int")
            # 框架里的小写类型名（npy_intp 等），当类型处理
            if sym is None and (e.name.startswith("npy_")
                                or e.name.startswith("NPY_")):
                return VT("int")
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
                return VT("func")
            if not sym.initialized:
                # 同一个变量只报第一次。学生在循环里反复用一个
                # 未初始化变量时（实测 stu_01 的 while(a!='\\0')），
                # 早期版本每个使用点都报，一条错误刷出 3 行，
                # 学生以为有 3 个错，实际只有 1 个。
                key = (self.cur_func.name if self.cur_func else "",
                       sym.line, sym.col)
                if key not in self._reported_e2:
                    self._reported_e2.add(key)
                    self.err("E2", e.line, e.col,
                             f"使用了未初始化的变量 {e.name!r}",
                             f"它声明在 {sym.line}:{sym.col}，但没有赋初值")
            sym.used = True
            # 数组名在表达式里退化成首地址（C 的数组退化）
            return _vt_of_type(sym.vtype, decay=True)

        if isinstance(e, BinOp):
            # W14：常量表达式整数溢出
            # gcc 的 -Woverflow 在编译期算得出来的情况会报，
            # csub 目前完全不查——这是确定性缺陷。
            _, _ov = check_const_overflow(e)
            if _ov:
                _val, _ = check_const_overflow(e)
                self.warn("W14", e.line, e.col,
                          f"常量表达式溢出 int 范围"
                          f"（结果 {_val}，合法范围 {INT_MIN} ~ {INT_MAX}）",
                          "结果会被截断，实际值和你预期的不一样")
            lt = self.check_expr(e.left)
            rt = self.check_expr(e.right)
            # 除零 / 模零
            if e.op in ("/", "%") and isinstance(e.right, Num) and e.right.value == 0:
                self.err("E12", e.line, e.col,
                         f"除数是常量 0（{e.op} 运算）",
                         "运行时会崩溃或触发未定义行为")
            # 整数除法丢精度（除零已报错时不再重复提示）
            #
            # 例外 1：sizeof(a)/sizeof(b) 是取数组长度的标准惯用法，
            # 两个操作数都是 sizeof，结果本来就是元素个数，不存在"丢小数"。
            # 早期无脑报警，把 `sizeof(paths)/sizeof(paths[0])` 这种
            # 教科书写法也报成 W3（实测 real_nmakehlp.c:798 就是这样）。
            #
            # 例外 2：除以 2 的幂（2、4、8...）在算法里几乎总是有意的
            # 整数除法——二分查找的 mid=(l+r)/2、递归的 n/2、
            # 位运算替代的 n>>1。学生照"改成浮点"的提示去做，
            # 反而会把递归和二分全毁掉（实测 stu_02 的
            # countBinary(n/2)+1 被误报）。
            # 除法结果被用于下标、循环条件、递归参数时，一律不报。
            # 但字面量直接相除（5/2、7/2）仍要报——那正是 W3 的初衷：
            # 学生写 5/2 期望 2.5，实际得 2。区分依据：分子也是字面量
            # 就说明是手算常量的笔误。
            _is_sizeof = lambda n: (isinstance(n, UnOp) and n.op == "sizeof")
            _int_div_ok = False
            if isinstance(e.right, Num) and not e.right.is_float \
                    and not isinstance(e.left, Num):
                _dv = e.right.value
                if isinstance(_dv, int) and _dv > 0:
                    # 2 的幂：二分/递归/位运算惯用法
                    if _dv & (_dv - 1) == 0:
                        _int_div_ok = True
                    # 10/100/1000：取数字的某一位（d%10、n/100）
                    elif _dv in (10, 100, 1000, 10000):
                        _int_div_ok = True
            if (e.op == "/" and _is_plain(lt, "int") and _is_plain(rt, "int")
                    and not (isinstance(e.right, Num) and e.right.value == 0)
                    and not (_is_sizeof(e.left) and _is_sizeof(e.right))
                    and not _int_div_ok):
                self.warn("W3", e.line, e.col,
                          "整数相除会丢弃小数部分",
                          "若确实要整数结果可忽略；要精确结果就把任一操作数写成浮点，如 5.0 / 2")
            return self.binop_type(e, lt, rt)

        if isinstance(e, UnOp):
            # 类型转换 (Node *)malloc(...) / (int)p：
            # 目标类型写在节点上（cast_type），显式转换的结果就是目标类型。
            # 以前这里返回操作数类型、把转换当纯语法糖，于是 (int)ptr 之后
            # 语义层仍认为它是指针——基于类型的检查全部失准。
            if e.op == "cast":
                self.check_expr(e.operand)
                ct = getattr(e, "cast_type", None)
                if ct is not None:
                    return _vt_of_type(ct, decay=False)
                return self._vt_at(e.operand)
            # 取地址 &x 只是取地址，不读 x 的值，所以不该检查 x 是否初始化。
            # 不这么处理的话，struct S *p = &s; 之后再用 s 会被误报未初始化
            # （实测 (*p).a 就因这条链路报了 s 未初始化）。
            if e.op == "&":
                t = self.check_lvalue(e.operand)
                if t is None:
                    return None
                return VT(str(t), ptr=t.ptr + 1, array=None,
                          unsigned=t.unsigned)
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
                        vt = _vt_of_type(sym.vtype, decay=True)
                        return VT(str(vt), ptr=max(vt.ptr - 1, 0),
                                  array=None, unsigned=vt.unsigned)
                t = self.check_expr(base)
                if t is None:
                    return None
                return VT(str(t), ptr=max(t.ptr - 1, 0), array=None,
                          unsigned=t.unsigned)
            # sizeof 在算类型尺寸之前不能调 check_expr(e.operand)：
            # 操作数可能是类型名（sizeof(int)），当成变量检查会误报 E1。
            if e.op == "sizeof":
                sz = self.sizeof_node(e.operand)
                # 把算出的尺寸挂在节点上，供 ir.py 生成真实常量
                try:
                    e.size_value = sz
                except Exception:
                    pass
                return VT("int")
            t = self.check_expr(e.operand)
            if e.op in ("!",):
                return VT("int")
            if e.op in ("++", "--", "post++", "post--"):
                return t
            if e.op in ("-", "+", "~") and t is not None:
                # 一元运算里的整数提升：char 参与运算后是 int
                return VT("int") if _is_plain(t, "char") else t
            return t

        if isinstance(e, Ternary):
            self.check_expr(e.cond)
            t1 = self.check_expr(e.then)
            t2 = self.check_expr(e.els)
            if _is_plain(t1, "float") or _is_plain(t2, "float") \
                    or t1 == "double" or t2 == "double":
                return VT("float")
            if t1 is not None and t1.ptr > 0:
                return t1
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
            # W20：块作用域逃逸
            self._check_scope_escape(e)
            # W22：复合赋值里又写了同一个变量
            self._check_compound_self_ref(e)
            # W14b：赋值时右侧含"常量变量"的溢出（err_38）
            self._check_assign_overflow(e)
            # 复合赋值（+= -= ...）的"目标类型"不是左值类型：
            # p += 1 在 C 里就是指针加，属于合法写法。
            if e.op == "=" and tt is not None:
                self.check_assign_compat_table(tt, vt, e, e.target,
                                               e.value)
            return tt

        if isinstance(e, Call):
            return self.check_call(e)

        if isinstance(e, Index):
            bt = self.check_expr(e.base)
            self.check_expr(e.idx)
            self.check_oob(e, "read")
            # W16：循环内的 arr[i] 区间越界（off-by-one）
            self._check_index_range(e)
            # W21：读了从未写过任何元素的数组
            self._check_uninit_array_read(e)
            return self.index_type(bt)

        if isinstance(e, InitList):
            for it in e.items:
                self.check_expr(it)
            return None

        if isinstance(e, Member):
            return self.check_member(e)

        return None

    def binop_type(self, e: "BinOp", lt, rt):
        """二元运算的结果类型（贴近 C 的通常算术转换）。

        必须真的算，不能像早期那样 `return lt or rt`：
        char 参与算术后要提升成 int，指针加整数仍是指针，
        这两条直接决定 W11/W12 能不能报对。
        """
        op = e.op
        # 指针运算：ptr ± int -> ptr；ptr - ptr -> int；指针比较 -> int
        lp = lt is not None and lt.ptr > 0
        rp = rt is not None and rt.ptr > 0
        if op in ("+", "-"):
            if lp and rp:
                return VT("int")
            if lp:
                return VT(str(lt), ptr=lt.ptr, array=None,
                          unsigned=lt.unsigned)
            if rp:
                return VT(str(rt), ptr=rt.ptr, array=None,
                          unsigned=rt.unsigned)
        if op in ("==", "!=", "<", ">", "<=", ">="):
            # 混合符号比较：C 会把有符号操作数转成无符号再比，
            # -1 于是变成 4294967295。这是真实且极难自查的坑。
            self.check_mixed_sign_compare(e, lt, rt)
            return VT("int")
        if op in ("&&", "||"):
            return VT("int")
        # 浮点优先
        if _is_plain(lt, "float") or _is_plain(rt, "float") \
                or lt == "double" or rt == "double":
            return VT("float")
        # 整数提升：char/short 参与运算一律变 int
        if lt is not None and _is_plain(lt, "char") \
                or rt is not None and _is_plain(rt, "char"):
            return VT("int")
        # 有符号/无符号：只要有 unsigned 而宽度够，结果就是 unsigned
        if lt is not None and getattr(lt, "unsigned", False) \
                or rt is not None and getattr(rt, "unsigned", False):
            return VT("int", unsigned=True)
        return lt or rt

    def check_mixed_sign_compare(self, e: "BinOp", lt, rt):
        """W2：有符号与无符号混合比较。

            unsigned int a = 5;
            int b = -1;
            if (b < a) { ... }        // -1 被转成 4294967295，结果恒假

        C 的"通常算术转换"规定：int 与 unsigned int 一起运算时，
        int 先转成 unsigned。于是负的有符号数变成一个巨大的正数，
        比较结果与数学直觉相反。教材在"类型转换"一节专门讲这条，
        而 gcc 要 -Wsign-compare（-Wextra 里）才提示。

        只在**确凿**时报，避免误报：
          - 两侧都得是内置的整数类（int/char），指针比较不算
          - 只有一侧 unsigned、另一侧 signed
          - 有符号那侧不能是已折成非负常量的字面量：
            `if (n < 10u)` 里的 10u 是常量、非负，不构成陷阱
        """
        if lt is None or rt is None:
            return
        if lt.ptr > 0 or rt.ptr > 0:
            return                      # 指针比较，不同语义，不碰
        if _is_plain(lt, "float") or _is_plain(rt, "float") \
                or lt == "double" or rt == "double":
            return                      # 不是整数比较
        lu = bool(getattr(lt, "unsigned", False))
        ru = bool(getattr(rt, "unsigned", False))
        if lu == ru:
            return                      # 同符号，没有陷阱
        # 任一侧能折成非负常量就不构成陷阱：
        # `if (n < 10u)` 是常规写法，unsigned 那侧是常量，不会翻车。
        for node in (e.left, e.right):
            c = const_int(node)
            if c is not None and c >= 0:
                return
        self.warn("W2", e.line, e.col,
                  "有符号与无符号混合比较：有符号侧会被转成无符号，"
                  "负数会变成一个很大的正数，比较结果可能与直觉相反",
                  "两边统一成同一符号再比较，如 (unsigned)b < a 或"
                  "把变量声明成同一种符号；也可用显式转换表达意图")

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
        if cur_ty is None:
            return None
        # 返回结构化的 VT：成员是数组时同样要退化（p->name 是 char*），
        # 成员是 int 时不能带上指针层，两者都影响调用处的类型判断。
        return _vt_of_type(cur_ty, decay=True)


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
    def _is_empty_body(self, stmt) -> bool:
        """判断 if/while 的分支是不是空语句。

        parser 把 `if (c);` 的 then 解析成 stmts 为空的 Block。
        真正的 `if (c) { }` 也是空块，但那种写法意图明确。
        这里统一按空块处理，理由：
          - 误报代价低（一句提示）
          - 漏报代价高（逻辑错误静默通过）
        """
        from parser import Block as _B
        if isinstance(stmt, _B):
            return len(stmt.stmts) == 0
        return False
    def check_loop_advance(self, s, ctx):
        """W15：循环无法正常终止。

        两类检测：
        A) 条件变量在循环体内从未被修改 → 死循环
        B) for 的 step 方向与条件方向相反 → 死循环
           （for (i=0; i<10; i--) 中 i-- 在往下走，条件要求往上走）
        """
        if _has_call(s.cond):
            return
        conds = []
        self._collect_cmp_idents(s.cond, conds)
        if not conds:
            return
        modified = set()
        has_exit = [False]
        self._collect_loop_effects(s.body, modified, has_exit)
        if hasattr(s, "step") and s.step is not None:
            self._collect_loop_effects(s.step, modified, has_exit)
            # B) for 方向检查
            self._check_for_direction(s, conds)
        if has_exit[0]:
            return
        unmodified = [c for c in conds if c not in modified]
        if unmodified:
            self.warn("W15", s.line, s.col,
                      f"循环条件里的变量 {'、'.join(unmodified)} 在循环体内"
                      f"从未被修改，可能是死循环",
                      "在循环体内添加 i = i + 1，或加 break")

    def _check_for_direction(self, s, conds):
        """for (i=0; i<10; i--) → step 方向与条件方向相反 → 死循环。

        只在"循环至少会执行一次"时才报：
        - i=0; i<10; i-- → 进入循环后永远到不了 10（死循环）
        - i=0; i>10; i++ → 条件一开始就 false，循环不进入，不是死循环
        """
        step = s.step
        if step is None or s.cond is None:
            return
        step_var, step_dir = self._parse_step(step)
        if step_var is None or step_dir == 0:
            return
        cond_var, cond_dir = self._parse_cond_dir(s.cond)
        if cond_var is None or cond_dir == 0:
            return
        if cond_var != step_var:
            return
        # 循环是否至少进入一次：检查 init 值是否满足条件
        init_val = self._get_for_init_value(s, step_var)
        if init_val is not None:
            bound = self._get_cond_bound(s.cond)
            if bound is not None:
                if cond_dir > 0 and not (init_val < bound):
                    return  # 条件一开始就不满足
                if cond_dir < 0 and not (init_val > bound):
                    return  # 条件一开始就不满足
        # step 递减 + 条件递增 → 永远到不了条件
        if step_dir < 0 and cond_dir > 0:
            self.warn("W15", s.line, s.col,
                      f"for 循环的 step 是递减（{step_var}--），"
                      f"但条件要求递增（{step_var} < N）",
                      "改成 i++ 或把条件改成 i > N")
        elif step_dir > 0 and cond_dir < 0:
            self.warn("W15", s.line, s.col,
                      f"for 循环的 step 是递增（{step_var}++），"
                      f"但条件要求递减（{step_var} > N）",
                      "改成 i-- 或把条件改成 i < N")

    def _get_for_init_value(self, s, var):
        """从 for 的 init 里取变量的初始值（如果是常量）。"""
        if s.init is None:
            return None
        node = s.init
        if isinstance(node, VarDecl) and node.name == var and node.init:
            return const_int(node.init)
        if isinstance(node, ExprStmt) and isinstance(node.expr, Assign):
            if (isinstance(node.expr.target, Ident)
                    and node.expr.target.name == var):
                return const_int(node.expr.value)
        return None

    def _get_cond_bound(self, cond):
        """从条件里取常量边界值。"""
        if isinstance(cond, BinOp) and cond.op in ("<", "<=", ">", ">="):
            if isinstance(cond.right, Num):
                return int(cond.right.value)
            if isinstance(cond.left, Num):
                return int(cond.left.value)
        return None

    def _parse_step(self, step):
        """解析 step 表达式，返回 (变量名, 方向)。
        i-- → ('i', -1)；i++ → ('i', +1)；i -= 1 → ('i', -1)；
        i = i - 1 → ('i', -1)；i = i + 1 → ('i', +1)。"""
        if isinstance(step, UnOp) and step.op in ("post--", "--"):
            if isinstance(step.operand, Ident):
                return (step.operand.name, -1)
        if isinstance(step, UnOp) and step.op in ("post++", "++"):
            if isinstance(step.operand, Ident):
                return (step.operand.name, +1)
        if isinstance(step, Assign):
            if isinstance(step.target, Ident):
                # i -= 1 / i += 1
                if step.op == "-=":
                    return (step.target.name, -1)
                if step.op == "+=":
                    return (step.target.name, +1)
                # i = i - 1 / i = i + 1（值是 BinOp）
                if step.op == "=" and isinstance(step.value, BinOp):
                    if step.value.op == "-" and isinstance(step.value.right, Num):
                        return (step.target.name, -1)
                    if step.value.op == "+" and isinstance(step.value.right, Num):
                        return (step.target.name, +1)
        return (None, 0)

    def _parse_cond_dir(self, cond):
        """解析条件，返回 (变量名, 方向)。
        i < N → ('i', +1)；i <= N → ('i', +1)；
        i > N → ('i', -1)；i >= N → ('i', -1)。"""
        if isinstance(cond, BinOp) and cond.op in ("<", "<="):
            if isinstance(cond.left, Ident):
                return (cond.left.name, +1)
        if isinstance(cond, BinOp) and cond.op in (">", ">="):
            if isinstance(cond.left, Ident):
                return (cond.left.name, -1)
        return (None, 0)

    def _collect_cmp_idents(self, node, out, _depth=0):
        """收集比较表达式里"应该被推进"的变量。

        只在「变量 vs 常量」的比较中收集（如 while (i < 10)）。
        `cur != head` / `cur->num != S` 这种两端都是变量/成员的比较，
        head/S 是比较目标，不该被要求修改——如果不收窄就会误报。
        """
        if node is None or _depth > 30:
            return
        if isinstance(node, BinOp) and node.op in ("<", ">", "<=", ">="):
            l, r = node.left, node.right
            if isinstance(l, Ident) and isinstance(r, Num):
                out.append(l.name)
            elif isinstance(r, Ident) and isinstance(l, Num):
                out.append(r.name)
            return
        if isinstance(node, BinOp) and node.op in ("==", "!="):
            l, r = node.left, node.right
            if isinstance(l, Ident) and isinstance(r, Num):
                out.append(l.name)
            elif isinstance(r, Ident) and isinstance(l, Num):
                out.append(r.name)
            return
        for attr in ("left", "right", "operand", "cond", "then", "els"):
            child = getattr(node, attr, None)
            if child is None:
                continue
            if isinstance(child, list):
                for c in child:
                    self._collect_cmp_idents(c, out, _depth + 1)
            else:
                self._collect_cmp_idents(child, out, _depth + 1)

    def _collect_loop_effects(self, node, modified, has_exit, _depth=0):
        """扫描循环体：哪些变量被赋值、是否有退出路径。"""
        if node is None or _depth > 40:
            return
        name = type(node).__name__
        if name in ("Break", "Goto", "Return"):
            has_exit[0] = True
            return
        if name == "Call":
            has_exit[0] = True
            return
        if name == "Assign" and node.target:
            if isinstance(node.target, Ident):
                modified.add(node.target.name)
            elif isinstance(node.target, UnOp) and node.target.op == "*":
                has_exit[0] = True
        if name == "UnOp" and node.op in ("++", "--", "post++", "post--"):
            if isinstance(node.operand, Ident):
                modified.add(node.operand.name)
        for attr in ("body", "stmts", "then", "els", "value", "target",
                     "init", "cond", "step", "left", "right", "operand",
                     "base", "idx", "args", "func", "expr"):
            child = getattr(node, attr, None)
            if child is None:
                continue
            if isinstance(child, list):
                for c in child:
                    self._collect_loop_effects(c, modified, has_exit, _depth + 1)
            else:
                self._collect_loop_effects(child, modified, has_exit, _depth + 1)

    def _for_range(self, s):
        """推导 for 循环变量的下标区间 (变量名, min, max)。

        只处理标准形态：for (i = K; i <op> N; i = i ± K)
        - op 是 <  → max = N - 1
        - op 是 <= → max = N
        - op 是 >  → min = N + 1
        - op 是 >= → min = N

        推不出来（条件含变量、step 非 ±K 等）就返回 None，
        调用方据此不做越界判断——宁可漏报也不误报。
        """
        if s.init is None or s.cond is None or s.step is None:
            return None
        # 取 init 的变量名与初值
        var = None
        if isinstance(s.init, VarDecl) and s.init.init is not None:
            var, init_v = s.init.name, const_int(s.init.init)
        elif isinstance(s.init, ExprStmt) and isinstance(s.init.expr, Assign) \
                and isinstance(s.init.expr.target, Ident):
            var, init_v = s.init.expr.target.name, const_int(s.init.expr.value)
        else:
            return None
        if var is None or init_v is None:
            return None
        # 条件：i <op> N
        c = s.cond
        if not (isinstance(c, BinOp) and c.op in ("<", "<=", ">", ">=")):
            return None
        if not (isinstance(c.left, Ident) and c.left.name == var):
            return None
        bound = const_int(c.right)
        if bound is None:
            return None
        # step 方向
        step_var, step_dir = self._parse_step(s.step)
        if step_var != var or step_dir == 0:
            return None
        # 推导上下界
        if step_dir > 0:
            hi = bound - 1 if c.op == "<" else bound
            return (var, init_v, hi)
        else:
            lo = bound + 1 if c.op == ">" else bound
            return (var, lo, init_v)

    def _check_index_range(self, e):
        """W16：循环内的 arr[i] 下标可能超出数组长度。

        依赖 self._loop_ranges（由 For 处理时压栈）。
        只在区间"确定"超出时才报；区间推不出来就静默跳过。
        """
        if not self._loop_ranges or not isinstance(e.idx, Ident):
            return
        var = e.idx.name
        rng = None
        for cand in reversed(self._loop_ranges):
            if cand[0] == var:
                rng = cand
                break
        if rng is None:
            return
        _v, lo, hi = rng
        n = self.array_len_of(e.base)
        if n is None:
            return
        if hi >= n:
            self.warn("W16", e.line, e.col,
                      f"循环变量 {var} 最大可取 {hi}，但数组长度只有 {n}",
                      f"合法下标 0..{n - 1}，检查循环条件是否是 <= 应为 <")
        elif lo < 0:
            self.warn("W16", e.line, e.col,
                      f"循环变量 {var} 最小可取 {lo}，数组下标不能为负",
                      "检查循环条件或初值")

    def check_recursion_terminates(self, fn):
        """W18：递归调用无法到达基线条件 → 无限递归（栈溢出）。

        检测逻辑：
        1. 找出函数对自己的递归调用（c(n-1, x) 里 c 就是 fn.name）
        2. 取递归调用里第一个参数的变化量（n-1 → -1，n → 0，n+1 → +1）
        3. 看基线条件 if (n == K) / if (n <= K) 等：
           - 参数递减且基线是 ==0/<=K → 安全
           - 参数不变 → 一定无限递归
           - 参数递增 → 一定无限递归（除非基线在递增方向）
           - 递减但步长跨过基线值 → 可能漏（如 n-2 而基线只有 ==0，
             n 起始为奇数时永远到不了 0）

        保守规则：只在**确凿**时报。方向判断不了、基线形态不标准
        就一律不报——递归是真实代码里的常见手段，误报代价高。
        """
        name = fn.name
        # 收集递归调用：(参数变化量, 行, 列)
        rec_calls = []
        self._collect_rec_calls(fn.body, name, rec_calls, 0)
        if not rec_calls:
            return
        # 基线条件：函数体第一层 if 里的形参比较
        base = self._find_recursion_base(fn)
        if base is None:
            return                      # 没有可识别的基线，不判断
        pname, op, kval = base
        for delta, line, col in rec_calls:
            if delta == 0:
                self.warn("W18", line, col,
                          f"递归调用 {name}(...) 时参数 {pname} 没有变化，"
                          f"会无限递归导致栈溢出",
                          "让递归参数逐步靠近基线条件，如 n - 1")
                return
            # 参数递增，而基线在下方（== 0 / <= K）→ 越跑越远
            if delta > 0 and op in ("==", "<=", "<"):
                self.warn("W18", line, col,
                          f"递归调用让 {pname} 递增（+{delta}），"
                          f"但基线条件是 {pname} {op} {kval}，"
                          f"永远到不了基线",
                          f"把递归参数改成递减，如 {pname} - 1")
                return
            # 参数递减但步长跨过基线：n-2 而基线是 ==0
            if delta < 0 and op == "==":
                step = -delta
                if kval % step != 0:
                    self.warn("W18", line, col,
                              f"递归调用让 {pname} 每次减 {step}，"
                              f"但基线条件是 {pname} == {kval}，"
                              f"{kval} 不是 {step} 的倍数时会跳过基线",
                              f"基线条件改成 {pname} <= 0，"
                              f"或让步长能整除 {kval}")
                    return

    def _collect_rec_calls(self, node, fname, out, _depth=0):
        """收集对 fname 的递归调用，解析第一个参数的变化量。"""
        if node is None or _depth > 40:
            return
        if isinstance(node, Call) and isinstance(node.func, Ident) \
                and node.func.name == fname:
            delta = self._param_delta(node)
            if delta is not None:
                out.append((delta, node.line, node.col))
            return
        for attr in ("body", "stmts", "then", "els", "value", "target",
                     "left", "right", "operand", "cond", "init", "step",
                     "args", "base", "idx"):
            child = getattr(node, attr, None)
            if child is None:
                continue
            if isinstance(child, list):
                for c in child:
                    self._collect_rec_calls(c, fname, out, _depth + 1)
            else:
                self._collect_rec_calls(child, fname, out, _depth + 1)

    def _param_delta(self, call):
        """解析递归调用第一个参数相对形参的变化量。

        c(n-1, x) → -1    c(n+1, x) → +1    c(n, x) → 0
        不是「形参 ± 常量」的形态就返回 None（不判断）。
        """
        if not call.args:
            return None
        a0 = call.args[0]
        # 直接传形参本身：delta 0
        if isinstance(a0, Ident):
            return 0
        if isinstance(a0, BinOp) and a0.op in ("+", "-") \
                and isinstance(a0.left, Ident) \
                and isinstance(a0.right, Num) \
                and not a0.right.is_float:
            v = int(a0.right.value)
            return v if a0.op == "+" else -v
        return None

    def _find_recursion_base(self, fn):
        """找基线条件：函数体第一层 if 里对第一个形参的比较。

        返回 (形参名, 运算符, 常量值)，找不到返回 None。
        只认 `n == 0` / `n <= 0` / `n < 1` 这类标准形态。
        """
        pname = None
        for pty, pn in fn.params:
            if pn:
                pname = pn
                break
        if pname is None or not isinstance(fn.body, Block):
            return None
        for s in fn.body.stmts:
            if isinstance(s, If):
                c = s.cond
                if isinstance(c, BinOp) and c.op in ("==", "<=", "<",
                                                     ">=", ">"):
                    if isinstance(c.left, Ident) and c.left.name == pname \
                            and isinstance(c.right, Num) \
                            and not c.right.is_float:
                        return (pname, c.op, int(c.right.value))
            # 只看第一层，遇不到标准形态就放弃
        return None

    def _check_uninit_array_read(self, e):
        """W21：读一个从未写过任何元素的局部数组。

            int a[4];               // 没有任何元素被赋值
            printf("%d", a[2]);     // 读到的是栈上残留的垃圾值

        gcc 完全不报（它不追踪元素级初始化）。

        保守规则，只在确凿时报：
          - 必须是局部数组（全局数组零初始化，安全）
          - 必须没有任何元素被写过（array_written 仍为 False）
          - 必须是读，不是写（写路径走 check_lvalue，不调这里）
          - 数组作为整体传给函数时不算（那可能被函数写入）
        """
        if not isinstance(e.base, Ident):
            return
        sym = self.scope.lookup(e.base.name)
        if sym is None or sym.vtype is None:
            return
        vt = sym.vtype
        if vt.array is None and not vt.dims:
            return                    # 不是数组
        if sym.scope_depth <= 0:
            return                    # 全局数组零初始化
        if getattr(sym, "array_written", True):
            return                    # 已经有元素被写过
        # 检查是不是同一行内的自我赋值（a[0]=a[0] 这种退化情况）
        self.warn("W21", e.line, e.col,
                  f"数组 {e.base.name!r} 从未被赋值就读取元素，"
                  f"读到的是栈上残留的垃圾值",
                  "先初始化元素（循环赋值，或声明时写 = {...}）")

    def _check_decl_overflow(self, vd):
        """W14b：声明初始化时右侧表达式溢出。

            int over = big + 1;     // big 是已知常量 2147483647

        和 _check_assign_overflow 共用常量表，区别只在于
        入口是声明语句而非赋值语句。真正的 err_38 是这种形态：
        `int big = 2147483647; int over = big + 1;`
        两行都是 VarDecl，都走这里。
        """
        ty = vd.vtype
        if ty is None:
            return
        if ty.array is not None or ty.dims or ty.ptr > 0:
            return                    # 只查标量
        val = self._eval_const(vd.init)
        if val is None:
            return
        # long / unsigned 不适用 int 范围
        if self._const_var_suffix(vd.init):
            return
        # 目标类型是 long 也不按 int 范围判断（else 分支同理）
        if (ty.base or "int") in ("long", "double", "float"):
            return
        if val > INT_MAX or val < INT_MIN:
            self.warn("W14", vd.line, vd.col,
                      f"表达式结果 {val} 超出 int 范围"
                      f"（合法范围 {INT_MIN} ~ {INT_MAX}），会被截断",
                      "若确实需要大数，改用 long long")
        elif (ty.base or "int") not in ("long", "double", "float"):
            # 记录常量值供后续表达式使用。
            # 注意：parser 把 long 归一化成 int（ty.base 永远是 int），
            # 所以靠 ty.long_flag 区分。long 变量不记账：
            # `long a=2147483647; long b=a+1;` 若记账，
            # a+1=2147483648 会按 int 范围误报（实测踩过）。
            if INT_MIN <= val <= INT_MAX \
                    and not getattr(ty, "long_flag", False):
                self._const_vars[vd.name] = val

    def _check_assign_overflow(self, e):
        """W14b：赋值右侧含「常量变量」的表达式溢出。

            int big = 2147483647;
            int over = big + 1;      // 溢出，实际得 -2147483648

        W14 只查纯字面量表达式，这里补上变量参与的形态。

        做法：用 const_int_ext 求值（认识"已知是常量的变量"），
        超范围时报 W14。故意做得比 W14 更保守：
          - 只在赋值语句检查，不做全局常量传播
          - 常量表只认"字面量直接赋值"的变量，不认任何计算得来的
          - 一旦变量被重新赋值，常量状态立刻失效

        为什么不做完整常量传播：那会让常量表跨控制流存活
        （if (c) x = 5; else x = 7; 之后 x 仍是 5），
        产生假阳性。这里宁可少查也不引入那种风险。
        """
        if e.op not in ("=", "+=", "-=", "*="):
            return
        val = self._eval_const(e.value)
        if val is None:
            # 右侧不是常量表达式：目标变量的常量状态失效
            if isinstance(e.target, Ident):
                self._const_vars.pop(e.target.name, None)
            return
        # 目标如果是常量变量，把它的值记下来（供后续表达式用）
        target_is_const = False
        if isinstance(e.target, Ident):
            sym = self.scope.lookup(e.target.name)
            if sym is not None and sym.vtype is not None:
                if sym.vtype.array is None and not sym.vtype.dims \
                        and sym.vtype.ptr == 0:
                    target_is_const = True
        # 检查溢出
        if val > INT_MAX or val < INT_MIN:
            # long / unsigned 不适用 int 范围，跳过
            suf = self._const_var_suffix(e.value)
            if suf:
                return
            self.warn("W14", e.line, e.col,
                      f"表达式结果 {val} 超出 int 范围"
                      f"（合法范围 {INT_MIN} ~ {INT_MAX}），"
                      f"会被截断",
                      "若确实需要大数，改用 long long")
            return
        # 记下常量值（仅在安全时）
        if target_is_const and e.op == "=":
            self._const_vars[e.target.name] = val

    def _eval_const(self, node, _depth=0):
        """求常量表达式的值；含非常量变量时返回 None。

        只认「字面量」和「已知是常量的变量」，不做任何传播。
        """
        if node is None or _depth > 20:
            return None
        if isinstance(node, Num) and not node.is_float:
            return int(node.value)
        if isinstance(node, Ident):
            return self._const_vars.get(node.name)
        if isinstance(node, UnOp) and node.op in ("-", "+"):
            v = self._eval_const(node.operand, _depth + 1)
            if v is None:
                return None
            return -v if node.op == "-" else v
        if isinstance(node, BinOp) and node.op in ("+", "-", "*"):
            a = self._eval_const(node.left, _depth + 1)
            b = self._eval_const(node.right, _depth + 1)
            if a is None or b is None:
                return None
            try:
                if node.op == "+":
                    return a + b
                if node.op == "-":
                    return a - b
                return a * b
            except (ValueError, OverflowError):
                return None
        return None

    def _const_var_suffix(self, node):
        """表达式里若有 long/unsigned 字面量，返回其后缀（跳过溢出检查）。"""
        if node is None:
            return ""
        if isinstance(node, Num):
            suf = (node.suffix or "").lower()
            return suf if ("l" in suf or "u" in suf) else ""
        for attr in ("left", "right", "operand"):
            child = getattr(node, attr, None)
            if child is not None:
                s = self._const_var_suffix(child)
                if s:
                    return s
        return ""

    def _check_compound_self_ref(self, e):
        """W22：复合赋值右侧又出现同一个变量。

            rev += rev + t * d;      // 学生本意 rev = rev + t*d

        `x += expr` 展开是 `x = x + expr`。如果 expr 里又出现 x，
        实际语义变成 `x = 2x + ...`，结果翻倍。stu_02 就是这么错的：
        二进制转换里累加值每次翻倍，输出完全不对。

        为什么这个判据够窄：
          - 正常代码里 `x += x` 极其罕见（"把自己加一遍"几乎总是笔误）
          - `x += x * 2` 这种"翻倍"有意图的写法，本可以写成 x *= 3，
            但为免误报，只在右侧**直接或间接出现 x 本身**时报，
            且 `*=` `&=` `|=` `<<=` 这些本身就可能合法的自引用运算不报
          - 只查算术类：+= -= *= /= %=
          - 指针复合运算（p += p）不报，那通常是真的

        实测：11 份学生代码里只有 stu_02 撞到；4 份开源代码 0 命中。
        """
        if e.op not in ("+=", "-=", "*=", "/=", "%="):
            return
        if not isinstance(e.target, Ident):
            return
        name = e.target.name
        # 指针目标不查：p += q - p 这类指针算术是常见写法
        sym = self.scope.lookup(name)
        if sym is not None and sym.vtype is not None \
                and sym.vtype.ptr > 0:
            return
        # 右侧是函数调用（或含调用）不查：
        # `x += f(x)` 语义就是 x = x + f(x)，意图明确，不是笔误。
        # 实测踩到：int f(int x){return x+1;} x += f(x); 被误报。
        if self._expr_has_call(e.value, 0):
            return
        # 右侧表达式里是否引用了同名变量
        if not self._expr_mentions(e.value, name, 0):
            return
        self.warn("W22", e.line, e.col,
                  f"复合赋值 {name} {e.op} ... 的右侧又出现了 {name}，"
                  f"实际会执行 {name} = {name} {e.op[0]} (...)，"
                  f"结果可能翻倍",
                  f"如果本意是 {name} = {name} + ...，请把 {e.op} 改成 =")

    def _expr_has_call(self, node, depth):
        """表达式里是否含函数调用（W22 用：含调用的复合赋值不查）。"""
        if node is None or depth > 30:
            return False
        if isinstance(node, Call):
            return True
        if isinstance(node, UnOp):
            return self._expr_has_call(node.operand, depth + 1)
        if isinstance(node, BinOp):
            return (self._expr_has_call(node.left, depth + 1)
                    or self._expr_has_call(node.right, depth + 1))
        if isinstance(node, Ternary):
            return (self._expr_has_call(node.cond, depth + 1)
                    or self._expr_has_call(node.then, depth + 1)
                    or self._expr_has_call(node.els, depth + 1))
        return False

    def _expr_mentions(self, node, name, depth):
        """表达式里是否引用了指定变量名。"""
        if node is None or depth > 30:
            return False
        if isinstance(node, Ident):
            return node.name == name
        if isinstance(node, UnOp):
            return self._expr_mentions(node.operand, name, depth + 1)
        if isinstance(node, BinOp):
            return (self._expr_mentions(node.left, name, depth + 1)
                    or self._expr_mentions(node.right, name, depth + 1))
        if isinstance(node, Ternary):
            return (self._expr_mentions(node.cond, name, depth + 1)
                    or self._expr_mentions(node.then, name, depth + 1)
                    or self._expr_mentions(node.els, name, depth + 1))
        if isinstance(node, Call):
            return any(self._expr_mentions(a, name, depth + 1)
                       for a in node.args)
        return False

    def _check_scope_escape(self, e):
        """W20：把内层块的局部变量地址赋给外层指针 → 出块后悬垂。

            int *p = 0;
            {
                int inner = 7;
                p = &inner;      // inner 在这个 } 就死了
            }
            printf("%d", *p);    // p 指向已销毁的栈内存

        判据：赋值右侧是 &内层变量，左侧指针的声明作用域比它更浅。
        只报「&裸变量名」的形态，&a[i]、&s.member 这些不查
        （数组/结构体成员的生命周期判定更复杂，保守起见不做）。
        """
        if e.op != "=":
            return
        rhs = e.value
        if not (isinstance(rhs, UnOp) and rhs.op == "&"
                and isinstance(rhs.operand, Ident)):
            return
        inner = self.scope.lookup(rhs.operand.name)
        if inner is None:
            return
        if inner.kind == "param":
            return                    # 形参指向调方的对象，生命周期更长
        # 内层变量必须是局部自动变量，且不在全局作用域
        if inner.scope_depth <= 0:
            return                    # 全局/static，一直有效
        # 左侧指针的作用域
        if not isinstance(e.target, Ident):
            return
        outer = self.scope.lookup(e.target.name)
        if outer is None or outer.scope_depth <= 0:
            return                    # 目标是全局指针，写得进去就是错的，但不在此查
        if outer.scope_depth < inner.scope_depth:
            self.warn("W20", e.line, e.col,
                      f"把内层变量 {rhs.operand.name!r} 的地址赋给外层指针 "
                      f"{e.target.name!r}：出块后该地址失效",
                      "让变量的生命周期覆盖指针的使用范围，"
                      "或改用 malloc 分配")

    def check_lvalue(self, e):
        """左值检查：只验证"这个标识符存不存在"，不检查是否已初始化。

        写入一个未初始化变量是完全合法的，把它当错误报是典型的误报。

        返回值是**左值自身**的类型（decay=False）：赋值目标的口径必须和
        表达式口径分开。`int a[3]; int *p = a;` 右边 a 退化成指针，
        而 `a = ...` 左边 a 是数组本身——混用会让"给数组名赋值"
        被当成给指针赋值，指针与整数混用就永远查不出来。
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
                    return VT("int")
                self.err("E1", e.line, e.col,
                         f"使用了未声明的变量 {e.name!r}",
                         "检查拼写，或先声明它")
                return None
            if sym.kind == "func":
                return VT("func")
            sym.used = True
            return _vt_of_type(sym.vtype, decay=False)
        if isinstance(e, Index):
            # 下标表达式的 base 与 idx 都是"读"，走普通检查
            bt = self.check_expr(e.base)
            self.check_expr(e.idx)
            # 写入数组元素同样要查越界：arr[-1] = 1 也是错的
            self.check_oob(e, "write")
            # W16：循环内写入 arr[i] 的区间越界
            self._check_index_range(e)
            # 标记数组"有元素被写过"，供 W21 判断
            if isinstance(e.base, Ident):
                sym = self.scope.lookup(e.base.name)
                if sym is not None:
                    sym.array_written = True
            return self.index_type(bt)
        if isinstance(e, UnOp) and e.op == "*":
            t = self.check_expr(e.operand)
            if t is None:
                return None
            return VT(str(t), ptr=max(t.ptr - 1, 0), array=None,
                      unsigned=t.unsigned)
        if isinstance(e, UnOp) and e.op == "&":
            # &v 是取地址：v 只是"被取地址"，不算读取它的值。
            # scanf("%d", &v) 里的 v 就是典型情况，按读取检查会误报未初始化。
            t = self.check_lvalue(e.operand)
            if t is None:
                return None
            return VT(str(t), ptr=t.ptr + 1, array=None, unsigned=t.unsigned)
        if isinstance(e, Member):
            # 写成员字段同样要走成员检查（拼错的字段名要报），
            # 但不把整个结构体变量当成"被读取"。
            return self.check_member(e)
        return self.check_expr(e)

    def index_type(self, bt):
        """数组/指针取一次下标的元素类型。"""
        if bt is None:
            return VT("int")        # 推导不到就退回 int，绝不因此误报
        if bt.ptr > 0:
            return VT(str(bt), ptr=bt.ptr - 1, array=None,
                      unsigned=bt.unsigned)
        if bt.dims:
            rest = list(bt.dims)[1:]
            return VT(str(bt), ptr=0,
                      array=rest[0] if rest else None,
                      unsigned=bt.unsigned, dims=rest)
        if bt.array is not None:
            return VT(str(bt), ptr=0, array=None, unsigned=bt.unsigned)
        return VT(str(bt), ptr=0, array=None, unsigned=bt.unsigned)

    def check_assign_compat_table(self, tt, vt, node, target_node, value_node):
        """按左值类型做赋值兼容检查。

        左值可能不是裸标识符（`*p = q`、`a[i] = q`、`s.f = q`），
        这种情况下也应当检查，否则"把指针存进 int 数组元素"这类
        同样真实的错误就漏掉了。左值类型来自 check_lvalue（已带结构）。
        """
        if tt is None or vt is None:
            return
        if str(tt) == "func":
            return
        name = (target_node.name if isinstance(target_node, Ident)
                else self._describe_lvalue(target_node))
        self.check_assign_compat_vt(tt, vt, node, name, value_node)

    def _describe_lvalue(self, node) -> str:
        """给非标识符左值起一个人话名字，用于提示语。"""
        if isinstance(node, Index):
            base = self._describe_lvalue(node.base)
            return f"{base}[...]"
        if isinstance(node, Member):
            base = self._describe_lvalue(node.base)
            sep = "->" if node.arrow else "."
            return f"{base}{sep}{node.name}"
        if isinstance(node, UnOp) and node.op == "*":
            return f"*{self._describe_lvalue(node.operand)}"
        if isinstance(node, Ident):
            return node.name
        return "该位置"

    def check_call(self, call: Call) -> Optional[str]:
        # 任何函数调用都可能通过指针改写调用方的变量，
        # 常量表必须整个作废——否则 `f(&x); ... x + 1` 会按旧值
        # 算成常量，进而产生假的溢出诊断。
        # 保守但正确：宁可少查，不制造假阳性。
        self._const_vars.clear()
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
                return None         # 函数指针的签名看不到，返回未知

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
                        # 裸名传给 scanf：要看它是什么。
                        #   标量变量 v      → 漏写 &，真错误
                        #   数组名 a        → 数组退化成首地址，完全正确
                        #   指针变量 p      → 值是地址，也正确
                        # 早期不分青红皂白一律报 E16，导致
                        # `char a[21]; scanf("%20s", a);` 这种标准写法
                        # 被判成 ERROR（实测 stu_11，gcc 零诊断）。
                        # 误报比漏报伤害大得多：学生会去改正确的代码。
                        sym = self.scope.lookup(a.name)
                        is_addr = False
                        if sym is not None:
                            vt = sym.vtype
                            if vt is not None and (vt.array is not None
                                                   or vt.dims
                                                   or vt.ptr > 0):
                                is_addr = True
                        if is_addr:
                            # 已经是地址，按正常表达式检查并标记已写入
                            self.check_expr(a)
                            if sym is not None:
                                sym.initialized = True
                                # scanf 家族明确是输出参数，传进去的数组
                                # 会被写入。不标记的话 W21 会误报
                                # "数组从未被赋值就读取"（实测 stu_11：
                                # scanf("%20s", a) 之后读 a[i] 被误判）。
                                if out_mode == "needs_addr_out":
                                    sym.array_written = True
                        elif out_mode == "needs_addr_out":
                            self.err("E16", a.line, a.col,
                                     f"{name} 的输入参数需要取地址，"
                                     f"实际传的是变量 {a.name!r}",
                                     f"应写成 &{a.name}")
                        else:
                            if sym is not None:
                                sym.initialized = True
                                sym.used = True
                    else:
                        self.check_expr(a)
                else:
                    self.check_expr(a)
            # 返回类型查表；没登记的一律"未知"。
            # 早期这里默认 int，于是 fopen/strdup/strstr 的返回值被当成
            # 整数，赋给 FILE*/char* 变量就变成假警告（实测 nmakehlp 6 条）。
            ret = STD_FUNCS_RET.get(name)
            if ret is None:
                return None
            base, ptr = ret
            # size_t 是无符号类型。丢掉这个信息，`i < strlen(s)`
            # 的有符号/无符号混合比较就查不出来（实测 stu_05，
            # gcc 的 -Wsign-compare 报了，csub 漏了）。
            uns = (base == "size_t")
            return VT(base, ptr=ptr, unsigned=uns)

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
                # 看不到声明就不知道它返回什么，必须老实返回"未知"。
                # 早期这里一律返回 int，于是 PyObject_New / PyDict_GetItem
                # 这类外部函数的返回值被当成 int，再赋给指针变量时
                # 就命中"把整数赋给指针"——实测 real_fortranobject.c
                # 一次冒出 26 条这种假警告。看不到就不知道，猜必误报。
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
        for i, a in enumerate(call.args):
            at = self.check_expr(a)
            # 实参类型比对：个数对上了不代表类型对。
            # 参数个数不符时不再逐个比类型——那时候位置已经错位，
            # 报出来的"类型不匹配"只会误导（例如少传一个参数后，
            # 每个实参都对着错的形参）。
            if fn.body is not None and expected == got and i < expected:
                self.check_arg_type(fn, i, a, at)
        if fn.ret_type is None:
            return None
        return _vt_of_type(fn.ret_type, decay=False)

    def check_arg_type(self, fn, index: int, arg_node, arg_vt):
        """实参 -> 形参的类型兼容检查（W10）。

        针对的是教材里 "数组作函数参数" 那一节的经典错误：

            int sum_all(int v) { ... }         // 形参写成了 int
            printf("%d", sum_all(a));          // 实参是数组

        数组名退化成首地址传进去，形参按 int 收，函数里累加的是
        一个垃圾地址值。gcc 给 "makes integer from pointer"，
        csub 原来完全不查实参类型。

        只报"指针/数组 -> 裸整数形参"这一个方向，而且是 warning：
          - 整数 -> 形参是指针 不报（真实代码里已有大量 0/NULL 写法，
            而看不到声明的外部函数更多，报必误报）
          - 形参是 typedef 名（DWORD、size_t、Py_ssize_t）不报，
            我们不知道它到底是不是指针
        """
        if index >= len(fn.params) or arg_vt is None:
            return
        pty = fn.params[index][0]
        if pty is None:
            return
        pvt = _vt_of_type(pty, decay=False)
        if pvt.ptr > 0 or str(pvt) not in PLAIN_NUMERIC:
            return                      # 形参要的就是指针/不透明类型
        if arg_vt.ptr == 0 or _is_null_const(arg_node) \
                or _is_explicit_cast(arg_node):
            return
        pname = fn.params[index][1] or f"第 {index + 1} 个参数"
        self.warn("W10", arg_node.line, arg_node.col,
                  f"把{'数组' if arg_vt.array is not None else '指针'}"
                  f"（{arg_vt.text()}）传给 {fn.name!r} 的整数形参 "
                  f"{pname!r}（{str(pvt)}）：传进去的是地址，"
                  f"函数里拿它当数值用",
                  f"把形参改成指针（{str(pvt)} *{pname}），"
                  "或传数组元素 a[i]")


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

def _has_call(node, _depth=0):
    """判断表达式里是否含函数调用（用于空循环体的豁免判定）。"""
    if node is None or _depth > 40:
        return False
    name = type(node).__name__
    if name == "Call":
        return True
    for attr in ("left", "right", "operand", "cond", "then", "els",
                 "base", "idx", "value", "target"):
        child = getattr(node, attr, None)
        if child is None:
            continue
        if isinstance(child, list):
            if any(_has_call(c, _depth + 1) for c in child):
                return True
        elif _has_call(child, _depth + 1):
            return True
    return False