"""语法分析器：token 流 -> AST。

采用递归下降 + 运算符优先级爬升（precedence climbing）。
支持 C 子集：
  声明（int/float/void，含指针、数组）、函数定义、
  if/else、while、for、return、break、continue、
  表达式（赋值、逻辑、比较、算术、一元、调用、下标、成员）。

错误处理策略：报告第一处错误并带上位置，不尝试恢复。
对教学工具来说，"指出第一个真实错误"比"一次报一堆"有用。
"""
from dataclasses import dataclass, field
from typing import Optional

from lexer import Token, Tok, tokenize


# ---------------- AST 节点 ----------------

@dataclass
class Node:
    line: int = 0
    col: int = 0


@dataclass
class Program(Node):
    decls: list = field(default_factory=list)


@dataclass
class FuncDef(Node):
    name: str = ""
    ret_type: object = None
    params: list = field(default_factory=list)
    body: Optional["Block"] = None


@dataclass
class VarDecl(Node):
    name: str = ""
    vtype: object = None
    init: object = None


@dataclass
class StructDef(Node):
    """struct node { ... }; 或 typedef struct {...} Node, *LinkList;

    真实作业几乎一定会用结构体，不支持就等于不能用。
    这里只记录成员名与类型，不做内存布局，够语义分析用。
    """
    tag: str = ""            # struct 后的标签，可能为空
    members: list = field(default_factory=list)   # [(Type, name)]
    typedefs: list = field(default_factory=list)  # [(newname, Type)]


@dataclass
class DeclGroup(Node):
    """一条声明语句里定义的多个变量： int a = 1, b = 2;

    C 里这是极常见的写法，早期版本每条声明只允许一个变量，
    导致 int a = 1, b = 2; 直接语法报错。
    """
    decls: list = field(default_factory=list)


@dataclass
class Block(Node):
    stmts: list = field(default_factory=list)


@dataclass
class If(Node):
    cond: object = None
    then: object = None
    els: object = None


@dataclass
class While(Node):
    cond: object = None
    body: object = None


@dataclass
class Label(Node):
    """goto 标签：again:"""
    name: str = ""


@dataclass
class Goto(Node):
    name: str = ""


@dataclass
class Switch(Node):
    expr: object = None
    body: object = None


@dataclass
class Case(Node):
    value: object = None        # None 表示 default
    stmt: object = None


@dataclass
class DoWhile(Node):
    body: object = None
    cond: object = None


@dataclass
class For(Node):
    init: object = None
    cond: object = None
    step: object = None
    body: object = None


@dataclass
class Return(Node):
    value: object = None


@dataclass
class Break(Node):
    pass


@dataclass
class Continue(Node):
    pass


@dataclass
class ExprStmt(Node):
    expr: object = None


@dataclass
class Num(Node):
    value: object = None
    is_float: bool = False
    suffix: str = ""    # L/U/UL 等，用于 int vs long vs unsigned


@dataclass
class Str(Node):
    value: str = ""


@dataclass
class Ident(Node):
    name: str = ""


@dataclass
class BinOp(Node):
    op: str = ""
    left: object = None
    right: object = None


@dataclass
class UnOp(Node):
    op: str = ""
    operand: object = None
    # (类型)x 显式转换里被转换到的类型。
    # 原来转换算子是"纯语法糖"，类型信息在解析时就被丢掉了，
    # 于是 (int)ptr 之后语义层仍把它当成指针，任何基于类型的检查
    # 都会误判。显式转换本来就该屏蔽隐式转换告警，所以必须留下来。
    cast_type: object = None


@dataclass
class Ternary(Node):
    cond: object = None
    then: object = None
    els: object = None


@dataclass
class Assign(Node):
    op: str = "="
    target: object = None
    value: object = None


@dataclass
class InitList(Node):
    """初始化列表：int a[] = {1,2,3}; 或 struct T x = {1, ""};"""
    items: list = field(default_factory=list)


@dataclass
class Call(Node):
    func: object = None
    args: list = field(default_factory=list)


@dataclass
class Index(Node):
    base: object = None
    idx: object = None


@dataclass
class Member(Node):
    base: object = None
    name: str = ""
    arrow: bool = False


@dataclass
class Type:
    base: str = "int"
    ptr: int = 0            # 指针层数
    array: Optional[int] = None
    dims: list = field(default_factory=list)   # 多维数组的各维长度
    # 无符号标记。base 仍归一化成 int / char，这样 sizeof / 尺寸表 /
    # 结构体解析等既有逻辑一行都不用改；符号性单独保留，
    # 供语义层做"有符号/无符号混合比较"检查（W2）。
    # 不保留的话 `unsigned int a` 和 `int a` 完全无法区分，
    # 混合比较这类真实缺陷就永远查不出来。
    unsigned: bool = False
    # 存储类。static 关键：语义层要靠它区分"在栈上"和"不在栈上"
    # （static 局部数组在 .data/.bss，局部自动变量在栈上）。
    # 早期 parse_type 直接跳过 static，导致 W19 爆栈检查把
    # `static int a[1000000];` 也报成爆栈——那是合法且安全的。
    storage: str = ""
    # long 标记。归一化后 long/short 的 base 都变成 int，
    # 但溢出范围不同（long 是 64 位）。W14 需要这个信息，
    # 否则 long 变量的常量运算会被按 int 范围误报。
    long_flag: bool = False

    def __repr__(self):
        arr = "".join(f"[{d}]" for d in self.dims) if self.dims else (
            f"[{self.array}]" if self.array is not None else "")
        return f"{self.base}{'*' * self.ptr}{arr}"


# ---------------- 解析器 ----------------

# 二元运算符优先级。数字越大结合越紧。
PREC = {
    "||": 1, "&&": 2,
    "|": 3, "^": 4, "&": 5,
    "==": 6, "!=": 6,
    "<": 7, ">": 7, "<=": 7, ">=": 7,
    "<<": 8, ">>": 8,
    "+": 9, "-": 9,
    "*": 10, "/": 10, "%": 10,
}

ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}


class ParseError(Exception):
    def __init__(self, msg, line, col):
        super().__init__(f"{line}:{col}: {msg}")
        self.msg, self.line, self.col = msg, line, col


class Parser:
    # C 标准库与常见头文件里的 typedef 名。
    # 真实代码大量使用 (size_t)x 这类转换，而我方看不到头文件，
    # 必须预置，否则会把括号转换误判成普通括号表达式而语法报错。
    BUILTIN_TYPEDEFS = {
        "size_t", "ssize_t", "ptrdiff_t", "wchar_t", "int8_t", "int16_t",
        "int32_t", "int64_t", "uint8_t", "uint16_t", "uint32_t", "uint64_t",
        "intptr_t", "uintptr_t", "FILE", "time_t", "clock_t", "off_t",
        "bool", "DWORD", "WORD", "BYTE", "BOOL", "HANDLE", "LPVOID",
        "UINT", "LONG", "ULONG", "CHAR", "LPSTR", "LPCSTR", "VOID",
        "npy_intp", "npy_uintp", "Py_ssize_t", "PyObject", "PyArrayObject",
        "va_list", "jmp_buf",
        # Python C-API 的常见 typedef（函数指针类型，转换里高频出现）
        "destructor", "printfunc", "getattrfunc", "setattrfunc",
        "reprfunc", "hashfunc", "callfunc", "cmpfunc", "getattrofunc",
        "initproc", "newfunc", "freefunc", "visitproc", "traverseproc",
        "inquiry", "objobjproc", "PyCFunction", "PyObject",
        "PyTypeObject", "PyMethodDef", "PyModuleDef", "PyMemberDef",
    }

    # 递归深度上限。C 语法里括号、三目、一元运算都是右/左递归的，
    # 每层 C 结构要吃好几个 Python 栈帧，几百层就打满默认递归限制，
    # 表现为 RecursionError 直接崩掉（学生看到 traceback 而不是诊断）。
    # 超限时报可读的 ParseError，而不是让 Python 抛栈溢出。
    MAX_NEST_DEPTH = 200

    def __init__(self, toks):
        self.toks = toks
        self.i = 0
        # 已知的 typedef 类型名。解析到 typedef 时登记，
        # 用于区分 (LinkList)x 和 (*(init))() —— 后者不是类型转换。
        self.known_types = set(self.BUILTIN_TYPEDEFS)
        # 已出现的变量名，用于区分 (var) 和 (TypeName)
        self.known_vars = set()
        # 当前递归深度，由 enter_nest / leave_nest 维护
        self._depth = 0

    def enter_nest(self):
        """进入一层嵌套。超限直接抛可读的解析错误。"""
        self._depth += 1
        if self._depth > self.MAX_NEST_DEPTH:
            t = self.cur
            raise ParseError(
                f"嵌套层级过深（超过 {self.MAX_NEST_DEPTH} 层），"
                f"请简化表达式或拆成多条语句", t.line, t.col)

    def leave_nest(self):
        self._depth -= 1

    # ---- 基础工具 ----
    @property
    def cur(self) -> Token:
        return self.toks[self.i]

    def peek(self, k=1) -> Token:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else self.toks[-1]

    def at(self, value: str) -> bool:
        return self.cur.value == value

    def at_kind(self, kind: Tok) -> bool:
        return self.cur.kind == kind

    def advance(self) -> Token:
        t = self.cur
        if self.i < len(self.toks) - 1:
            self.i += 1
        return t

    def expect(self, value: str) -> Token:
        if not self.at(value):
            t = self.cur
            raise ParseError(f"期望 {value!r}，实际是 {t.value!r}", t.line, t.col)
        return self.advance()

    def expect_kind(self, kind: Tok, what: str) -> Token:
        if not self.at_kind(kind):
            t = self.cur
            raise ParseError(f"期望 {what}，实际是 {t.value!r}", t.line, t.col)
        return self.advance()

    # ---- 类型 ----
    # 调用约定修饰符：Windows 代码里 static DWORD WINAPI Foo() 很常见，
    # 它们不影响类型判断，跳过即可。
    CALL_CONV = {"WINAPI", "CALLBACK", "APIENTRY", "NTAPI", "stdcall",
                 "cdecl", "fastcall", "__stdcall", "__cdecl", "__fastcall",
                 "_stdcall", "_cdecl", "PASCAL", "FAR", "NEAR",
                 "__attribute__", "__inline", "__restrict"}

    def skip_call_conv(self):
        """跳过调用约定与 pragma 类修饰符，最多连续跳几个。"""
        n = 0
        while self.cur.kind == Tok.IDENT and self.cur.value in self.CALL_CONV:
            self.advance()
            n += 1
            # __attribute__((...)) 形式
            if self.at("(") and n:
                pass
        return n

    def parse_type(self) -> Optional[Type]:
        # 存储类与限定符：static / const / extern / inline 不影响类型判断，
        # 直接跳过。真实代码里 static 函数和全局变量很常见。
        # 但 static 要记下来：W19（爆栈检查）靠它区分栈上与 .data/.bss。
        storage = ""
        while self.cur.kind == Tok.KW and self.cur.value in (
                "static", "const", "extern", "inline", "register", "volatile"):
            if self.cur.value == "static":
                storage = "static"
            self.advance()
        ty = self._parse_type_inner()
        if ty is not None and storage:
            ty.storage = storage
        return ty

    def _parse_type_inner(self) -> Optional[Type]:
        # 调用约定往往夹在类型和函数名之间
        self.skip_call_conv()

        t = self.cur
        # 多词基本类型：unsigned long long / long double / signed char ...
        if t.kind == Tok.KW and t.value in (
                "signed", "unsigned", "short", "long"):
            words = []
            while self.cur.kind == Tok.KW and self.cur.value in (
                    "signed", "unsigned", "short", "long", "int", "char",
                    "double", "float"):
                words.append(self.advance().value)
            # 归一化到基础类型，用于类型检查
            if "double" in words or "float" in words:
                base = "double" if "double" in words else "float"
            elif "char" in words:
                base = "char"
            else:
                base = "int"
            # 归一化会丢掉符号性，单独记下来：unsigned int 与 int
            # 在比较/赋值语义上不同，丢了就查不出混合符号比较。
            ty = Type(base=base, unsigned=("unsigned" in words))
            # long 也单独记：归一化后 base 都是 int，但 long 的溢出
            # 范围不同。W14 查常量溢出时需要知道"这个变量是 long"，
            # 否则 `long a=2147483647; long b=a+1;` 会被误判 int 溢出。
            if "long" in words:
                ty.long_flag = True
            while self.at("*"):
                self.advance()
                ty.ptr += 1
            return ty

        if t.kind == Tok.KW and t.value in ("int", "float", "void",
                                            "char", "double"):
            base = self.advance().value
            ty = Type(base=base)
            while self.at("*"):
                self.advance()
                ty.ptr += 1
            return ty
        if t.kind == Tok.KW and t.value == "struct":
            self.advance()
            if self.at("{"):
                # 匿名结构体，标签为空
                return Type(base="struct")
            name = self.expect_kind(Tok.IDENT, "结构体名").value
            ty = Type(base="struct " + name)
            while self.at("*"):
                self.advance()
                ty.ptr += 1
            return ty
        # typedef 出来的名字（Node / LinkList / DWORD 等）在语法层就是 IDENT。
        # 判据：IDENT 后面跟 IDENT 或 * 或 WINAPI 之类，说明前者是类型。
        if t.kind == Tok.IDENT and (
                t.value in self.known_types
                or self.peek().kind == Tok.IDENT
                or self.peek().value == "*"
                or self.peek().value in self.CALL_CONV):
            name = self.advance().value
            ty = Type(base=name)
            while self.at("*"):
                self.advance()
                ty.ptr += 1
            self.skip_call_conv()
            return ty

        # typedef 出来的名字，例如 Node / LinkList。
        # 它们在语义层才有类型信息，语法层只能当成自定义类型名。
        if t.kind == Tok.IDENT and self.peek().value == ";":
            return None
        return None

    # ---- 顶层 ----
    def parse(self) -> Program:
        """解析整个程序，带错误恢复。

        C 的语法错误（尤其漏分号）是最常见的一类，而早期版本
        遇到第一个语法错误就整份文件放弃——后面所有语义错误
        都看不到。实测 stu_17：漏一个分号，老师标的 7 条错误里
        5 条（其中 4 个★致命）完全不可见。

        恢复策略：以"顶层声明"为恢复单元。某个声明解析失败时，
        收集这个错误，跳到下一个可能的声明起点（行首的
        typedef/struct/类型名），继续解析后面的内容。

        收集到的错误放在 prog.errors 里，由调用方决定怎么呈现。
        这样 check/report 能同时给出语法错误和后续的语义诊断。
        """
        prog = Program(line=1, col=1)
        prog.errors = []
        guard = 0
        while not self.at_kind(Tok.EOF):
            guard += 1
            if guard > 10000:            # 防御：避免恢复逻辑自身死循环
                break
            start = self.i
            try:
                prog.decls.append(self.parse_top())
            except ParseError as e:
                prog.errors.append(e)
                # 无条件跳到下一个声明起点。
                # 早期写成"只有没产出节点时才恢复"，但
                # _decl_count_before 从来没更新过，那个判断永远
                # 失效 —— 于是坏掉的那段被逐个 token 报错，
                # 刷出几十条垃圾诊断。
                self._recover_to_next_decl()
                # 恢复后没前进就强制消耗一个 token，防死循环
                if self.i == start:
                    self.advance()
        return prog

    def _recover_to_next_decl(self):
        """跳到下一个可能的顶层声明起点。

        策略（从简单到复杂，按顺序试）：
          A) 如果当前 token 是 `}`，跳过它；如果后面紧跟
             类型关键字/`typedef`/`struct`，那就是下一个声明
          B) 否则一路跳到最近的 `;`，再跳到它后面
          C) 之后如果当前 token 看起来像声明起点就停，
             不像就再跳一轮
          D) 最多跳 500 个 token 就放弃

        为什么不能"只找行首类型关键字"：漏分号时后面可能是一整段
        语句（`while (...) { ... }`），每个 token 都在行首附近，
        逐个报"期望类型声明"会刷出几十条垃圾诊断（实测 stu_17
        初版刷了 20+ 条）。
        """
        for _round in range(50):
            if self.at_kind(Tok.EOF):
                return
            # A) 先消化掉紧跟的右花括号
            if self.cur.value == "}":
                self.advance()
                continue
            # C) 已经像声明起点就停
            if self._looks_like_decl_start():
                return
            # B) 跳到最近的 ; 之后
            moved = False
            skipped = 0
            while not self.at_kind(Tok.EOF) and skipped < 500:
                skipped += 1
                t = self.cur
                self.advance()
                moved = True
                if t.value == ";":
                    break
                if t.value == "}" and self._looks_like_decl_start():
                    break
            if not moved:
                return
        return

    def _looks_like_decl_start(self) -> bool:
        """当前 token 是否像顶层声明的开头。"""
        t = self.cur
        if t.kind == Tok.EOF:
            return False
        if t.kind == Tok.KW and t.value in (
                "typedef", "struct", "enum", "union", "int", "char",
                "void", "float", "double", "unsigned", "signed",
                "long", "short", "static", "const", "extern", "bool"):
            return True
        # 未知类型名（typedef 出来的）+ 名字
        if t.kind == Tok.IDENT and self.peek().kind == Tok.IDENT:
            return True
        return False


    def parse_top(self):
        # typedef struct {...} Node, *LinkList;  真实作业的标配
        if self.cur.kind == Tok.KW and self.cur.value == "typedef":
            return self.parse_typedef()

        # 裸的 struct 定义：struct node { ... };
        if (self.cur.kind == Tok.KW and self.cur.value == "struct"
                and self.peek().value in ("{",) or
                (self.cur.value == "struct" and self.peek(1).kind == Tok.IDENT
                 and self.peek(2).value == "{")):
            return self.parse_struct_only()

        ty = self.parse_type()
        if ty is None:
            # typedef 出来的类型名（Node / LinkList）在语法层就是 IDENT。
            # 判断依据：IDENT 后面跟 IDENT 或紧跟 *，说明前者是类型。
            if self.at_kind(Tok.IDENT):
                nxt = self.peek()
                if (nxt.kind == Tok.IDENT or nxt.value == "*"):
                    tname = self.advance().value
                    ty = Type(base=tname)
                    while self.at("*"):
                        self.advance()
                        ty.ptr += 1
                else:
                    t = self.cur
                    raise ParseError(
                        f"期望类型声明，实际是 {t.value!r}", t.line, t.col)
            else:
                t = self.cur
                raise ParseError(f"期望类型声明，实际是 {t.value!r}",
                                 t.line, t.col)

        name_tok = self.expect_kind(Tok.IDENT, "标识符")
        name = name_tok.value

        if self.at("("):
            return self.parse_func_rest(ty, name, name_tok)
        # 全局变量（可能带逗号）
        return self.parse_decl_group(ty, name, name_tok)

    def _parse_struct_body(self):
        """解析 { 成员... }，返回成员列表。

        成员声明的两种合法形态：
          带类型    int a; int *b; float xx;
          省略类型  int r, c;      // 逗号后的 c 继承 int

        省略类型是 C89/C99 的标准语法，学生声明多个同类型成员时
        极常见（`typedef struct { int r, c; } Pos;`）。
        早期版本假定每个成员都带类型，遇到省略就报
        "结构体成员缺少类型"——而 gcc 完全接受这段代码。
        """
        self.expect("{")
        members = []
        while not self.at("}"):
            if self.at_kind(Tok.EOF):
                raise ParseError("结构体未闭合（缺少 }）", 0, 0)
            mty = self.parse_type()
            if mty is None:
                t = self.cur
                raise ParseError(f"结构体成员缺少类型，实际是 {t.value!r}",
                                 t.line, t.col)
            # 一个声明里可以逗号串起多个同名类型成员
            while True:
                # 成员可能是指针
                while self.at("*"):
                    self.advance()
                mname = self.expect_kind(Tok.IDENT, "成员名").value
                m2 = mty
                if self.at("["):
                    self.advance()
                    # 数组长度可能是来自头文件的宏（MAX_PATH 等），
                    # 解析不出来就记为未知，不做越界检查，不能报语法错。
                    if self.at_kind(Tok.NUM):
                        size = self.advance()
                        try:
                            m2 = Type(base=mty.base, ptr=mty.ptr,
                                      array=int(size.value, 0))
                        except ValueError:
                            m2 = Type(base=mty.base, ptr=mty.ptr, array=None)
                    elif self.at_kind(Tok.IDENT):
                        self.advance()          # 未知长度宏
                        m2 = Type(base=mty.base, ptr=mty.ptr, array=None)
                    else:
                        t = self.cur
                        raise ParseError("数组长度无法识别", t.line, t.col)
                    self.expect("]")
                members.append((m2, mname))
                if self.at(","):
                    self.advance()      # 省略类型：下一个成员复用 mty
                    continue
                break
            self.expect(";")
        self.expect("}")
        return members

    def parse_typedef(self):
        kw = self.advance()          # typedef
        # typedef struct [tag] { ... } A, *B;
        if self.cur.kind == Tok.KW and self.cur.value == "struct":
            self.advance()
            tag = ""
            if self.at_kind(Tok.IDENT):
                tag = self.advance().value
            members = []
            if self.at("{"):
                members = self._parse_struct_body()
            sd = StructDef(line=kw.line, col=kw.col, tag=tag, members=members)
            # 后面的别名列表
            while True:
                ptr = 0
                while self.at("*"):
                    self.advance()
                    ptr += 1
                alias = self.expect_kind(Tok.IDENT, "类型别名").value
                self.known_types.add(alias)
                sd.typedefs.append(
                    (alias, Type(base="struct " + tag if tag else "struct",
                                 ptr=ptr)))
                if self.at(","):
                    self.advance()
                    continue
                break
            self.expect(";")
            return sd

        # typedef int myint;
        base_ty = self.parse_type()
        if base_ty is None:
            t = self.cur
            # 未知类型别名，跳过整条语句以保持健壮
            while not self.at(";") and not self.at_kind(Tok.EOF):
                self.advance()
            if self.at(";"):
                self.advance()
            return StructDef(line=t.line, col=t.col)

        # 函数指针 typedef：typedef PyObject *(*fortranfunc)(PyObject *, ...);
        # 形如 类型 ( * 名字 ) ( 参数表 )
        if self.at("("):
            save = self.i
            self.advance()
            ptr = 0
            while self.at("*"):
                self.advance()
                ptr += 1
            if self.at_kind(Tok.IDENT) and self.peek().value == ")":
                fname = self.advance().value
                self.expect(")")
                if self.at("("):
                    depth = 0
                    while not self.at_kind(Tok.EOF):
                        if self.at("("):
                            depth += 1
                        elif self.at(")"):
                            depth -= 1
                            if depth == 0:
                                self.advance()
                                break
                        self.advance()
                sd = StructDef(line=kw.line, col=kw.col)
                self.known_types.add(fname)
                sd.typedefs.append(
                    (fname, Type(base="funcptr", ptr=ptr + 1)))
                if self.at(";"):
                    self.advance()
                return sd
            self.i = save

        sd = StructDef(line=kw.line, col=kw.col)
        while True:
            ptr = 0
            while self.at("*"):
                self.advance()
                ptr += 1
            alias = self.expect_kind(Tok.IDENT, "类型别名").value
            self.known_types.add(alias)
            sd.typedefs.append(
                (alias, Type(base=base_ty.base, ptr=base_ty.ptr + ptr,
                             unsigned=base_ty.unsigned)))
            if self.at(","):
                self.advance()
                continue
            break
        self.expect(";")
        return sd

    def parse_struct_only(self):
        kw = self.cur
        self.advance()               # struct
        tag = ""
        if self.at_kind(Tok.IDENT):
            tag = self.advance().value
        members = []
        if self.at("{"):
            members = self._parse_struct_body()
        if self.at(";"):
            self.advance()
        return StructDef(line=kw.line, col=kw.col, tag=tag, members=members)

    def parse_func_rest(self, ret_type, name, name_tok) -> FuncDef:
        self.expect("(")
        params = []
        if not self.at(")"):
            # f(void) 表示无参数，不是"一个 void 参数"。
            # 真实作业里几乎每个无参函数都这么写。
            if (self.at("void") and self.peek().value == ")"):
                self.advance()
            else:
                while True:
                    # 可变参数 ... ：C 里表示后面还有任意个参数
                    if self.at("..."):
                        self.advance()
                        params.append((Type(base="varargs"), "..."))
                        break
                    pty = self.parse_param_type()
                    if pty is None:
                        t = self.cur
                        raise ParseError("参数缺少类型", t.line, t.col)
                    pname, extra_ptr = self.parse_param_name()
                    if pname:
                        self.known_vars.add(pname)
                        # 数组退化 / 函数指针形参都让类型多一层指针。
                        # 漏掉这一步，cb(arg) 会被误判成"调用未声明的函数"。
                        if extra_ptr and pty is not None:
                            pty = Type(base=pty.base,
                                       ptr=pty.ptr + extra_ptr,
                                       array=pty.array,
                                       dims=list(pty.dims))
                    params.append((pty, pname))
                    if self.at(","):
                        self.advance()
                        continue
                    break
        self.expect(")")
        fn = FuncDef(line=name_tok.line, col=name_tok.col,
                     name=name, ret_type=ret_type, params=params)
        if self.at(";"):
            self.advance()          # 函数声明，无函数体
            fn.body = None
            return fn
        fn.body = self.parse_block()
        return fn

    def parse_param_type(self):
        """参数类型：可能是内置类型，也可能是 typedef 的名字。"""
        ty = self.parse_type()
        if ty is not None:
            while self.at("*"):
                self.advance()
                ty.ptr += 1
            return ty
        if self.at_kind(Tok.IDENT):
            # const char *prompt 这类形式
            if self.at("const"):
                self.advance()
            if self.at_kind(Tok.IDENT):
                base = self.peek()
                # IDENT 后面跟 IDENT 或 * 才说明它是类型名
                if base.kind == Tok.IDENT or base.value == "*":
                    name = self.advance().value
                    ty = Type(base=name)
                    while self.at("*"):
                        self.advance()
                        ty.ptr += 1
                    return ty
        if self.at("const"):
            self.advance()
            return self.parse_param_type()
        return None

    def parse_param_name(self):
        """参数名。可能是 argv[] 这种数组退化，也可能是
        函数指针 void (*dtor)(void *) 这种形式。

        返回 (名字, 额外指针层数)。函数指针形参必须把"它是指针"
        这个信息带回去，否则类型记成 void，调用 cb(arg) 时会被
        误判成"调用未声明的函数"。
        """
        # 函数指针参数：(*name)(...)
        if self.at("(") and self.peek().value == "*":
            save = self.i
            self.advance()                      # (
            self.advance()                      # *
            stars = 1
            while self.at("*"):
                self.advance()
                stars += 1
            if self.at_kind(Tok.IDENT) and self.peek().value == ")":
                pname = self.advance().value
                self.advance()                  # )
                if self.at("("):
                    depth = 0
                    while not self.at_kind(Tok.EOF):
                        if self.at("("):
                            depth += 1
                        elif self.at(")"):
                            depth -= 1
                            if depth == 0:
                                self.advance()
                                break
                        self.advance()
                return (pname, stars)
            self.i = save
        if not self.at_kind(Tok.IDENT):
            return ("", 0)
        name = self.advance().value
        extra = 0
        # char *argv[] 里的 [] ：数组形参等价于指针，丢掉维度
        while self.at("[") and self.peek().value == "]":
            self.advance()
            self.advance()
            extra += 1          # 数组退化也等效于加一层指针
        return (name, extra)

    def parse_var_rest(self, vtype, name, name_tok) -> VarDecl:
        """解析单个变量名之后的部分：[数组维度] [= 初值] ; 或 , 后续变量

        支持多维数组 int a[3][4]：只记录第一维长度用于越界检查，
        其余维度丢弃（教学工具不做完整的内存布局）。
        """
        dims = []
        while self.at("["):
            self.advance()
            # 长度可以是常量表达式：MAX_PATH + 1、(N/2) 等。
            # 求不出具体数值时记为 None（表示未知），不做越界检查，
            # 绝不能因为长度是宏就报语法错——那样真实代码全崩。
            dim = None
            if self.at_kind(Tok.NUM) and self.peek().value == "]":
                # 最常见情形：纯数字长度，记下来可用于越界检查
                try:
                    dim = int(self.cur.value, 0)
                except ValueError:
                    dim = None
                self.advance()
            elif not self.at("]"):
                # 常量表达式：2 * 3、N/2、MAX_PATH + 1 等。
                # 解析成 AST 后尽量折成常量；折不出来（含未知宏）才记 None。
                # 早期版本这里直接跳过不解析，导致 a[2*3] 这种
                # 明明可以算出长度的数组，越界检查完全失效。
                try:
                    dim_expr = self.parse_assign()
                    dim = _fold_const_dim(dim_expr)
                except Exception:
                    dim = None
                    # 解析失败则保守跳过，保证不因为维度写法特殊而崩
                    depth = 0
                    while not self.at_kind(Tok.EOF):
                        if self.at("["):
                            depth += 1
                        elif self.at("]"):
                            if depth == 0:
                                break
                            depth -= 1
                        self.advance()
            self.expect("]")
            dims.append(dim)
        if dims:
            vtype.array = dims[0]
            vtype.dims = dims
        init = None
        if self.at("="):
            self.advance()
            init = self.parse_assign()
        vd = VarDecl(line=name_tok.line, col=name_tok.col,
                     name=name, vtype=vtype, init=init)
        self.known_vars.add(name)
        return vd

    def parse_decl_group(self, vtype, name, name_tok):
        """解析可能带逗号的变量声明组，返回 DeclGroup 或单个 VarDecl。"""
        first = self.parse_var_rest(vtype, name, name_tok)
        if not self.at(","):
            self.expect(";")
            return first

        group = DeclGroup(line=name_tok.line, col=name_tok.col,
                          decls=[first])
        while self.at(","):
            self.advance()
            # 后续变量沿用同一基础类型，但指针层数可能不同：int a, *p;
            # 符号性也要沿用：unsigned int a, *p; 两个都是无符号。
            sub = Type(base=vtype.base, ptr=0, array=None,
                       unsigned=vtype.unsigned)
            while self.at("*"):
                self.advance()
                sub.ptr += 1
            nt = self.expect_kind(Tok.IDENT, "变量名")
            group.decls.append(
                self.parse_var_rest(sub, nt.value, nt))
        self.expect(";")
        return group

    # ---- 语句 ----
    def parse_local_type(self):
        """局部声明里的类型：内置类型，或 typedef 出来的名字。

        判定 typedef 名的依据：IDENT 后面紧跟 IDENT 或 *，
        例如 Node *p; / LinkList head; / Node *prev, *cur;
        如果是 a = 1; 这种，IDENT 后面是 = 或 ;，就不是类型。
        """
        ty = self.parse_type()
        if ty is not None:
            while self.at("*"):
                self.advance()
                ty.ptr += 1
            return ty

        if self.at("const"):
            self.advance()
            return self.parse_local_type()

        if self.at_kind(Tok.IDENT):
            nxt = self.peek()
            if (self.cur.value in self.known_types
                    or nxt.kind == Tok.IDENT or nxt.value == "*"):
                name = self.advance().value
                ty = Type(base=name)
                while self.at("*"):
                    self.advance()
                    ty.ptr += 1
                return ty
        return None

    def parse_switch_body(self):
        """switch 体：{ case ... : stmt ... default: ... }"""
        self.expect("{")
        cases = []
        while not self.at("}"):
            if self.at_kind(Tok.EOF):
                raise ParseError("switch 未闭合（缺少 }）", 0, 0)
            if self.at("case"):
                self.advance()
                val = self.parse_assign()
                self.expect(":")
                # 收集一直到下一个 case/default/} 的所有语句
                stmts = []
                while not (self.at("case") or self.at("default") or self.at("}")):
                    stmts.append(self.parse_stmt())
                    if self.at_kind(Tok.EOF):
                        break
                cases.append(Case(line=val.line, col=val.col, value=val,
                                  stmt=Block(line=val.line, col=val.col,
                                             stmts=stmts)))
            elif self.at("default"):
                self.advance()
                self.expect(":")
                stmts = []
                while not (self.at("case") or self.at("default") or self.at("}")):
                    stmts.append(self.parse_stmt())
                    if self.at_kind(Tok.EOF):
                        break
                cases.append(Case(line=0, col=0, value=None,
                                  stmt=Block(line=0, col=0, stmts=stmts)))
            else:
                # switch 体里允许前置声明
                cases.append(self.parse_stmt())
        self.expect("}")
        return Block(line=0, col=0, stmts=cases)

    def parse_block(self) -> Block:
        self.enter_nest()
        try:
            return self._parse_block_inner()
        finally:
            self.leave_nest()

    def _parse_block_inner(self) -> Block:
        lb = self.expect("{")
        blk = Block(line=lb.line, col=lb.col)
        while not self.at("}"):
            if self.at_kind(Tok.EOF):
                raise ParseError("代码块未闭合（缺少 }）", lb.line, lb.col)
            blk.stmts.append(self.parse_stmt())
        self.expect("}")
        return blk

    def parse_stmt(self):
        self.enter_nest()
        try:
            return self._parse_stmt_inner()
        finally:
            self.leave_nest()

    def _parse_stmt_inner(self):
        t = self.cur

        # goto 标签：标识符后面紧跟冒号。
        # 注意 peek(k=1) 表示"往后第 k 个"，peek() 即下一个 token，
        # peek(1) 与 peek() 相同；要再往后看必须用 peek(2)。
        # 这里判断：标识符 + ':' + 下一个不是 ':'（排除 ::）。
        if (t.kind == Tok.IDENT and self.peek().value == ":"
                and self.peek(2).value != ":"):
            self.advance()
            self.expect(":")
            return Label(line=t.line, col=t.col, name=t.value)

        if self.at("{"):
            return self.parse_block()

        ty = self.parse_local_type()
        if ty is not None:
            name_tok = self.expect_kind(Tok.IDENT, "变量名")
            return self.parse_decl_group(ty, name_tok.value, name_tok)

        if t.kind == Tok.KW:
            if t.value == "if":
                self.advance()
                self.expect("(")
                cond = self.parse_expr()
                self.expect(")")
                then = self.parse_stmt()
                els = None
                if self.at("else"):
                    self.advance()
                    els = self.parse_stmt()
                return If(line=t.line, col=t.col, cond=cond, then=then, els=els)
            if t.value == "goto":
                self.advance()
                name = self.expect_kind(Tok.IDENT, "标签名").value
                self.expect(";")
                return Goto(line=t.line, col=t.col, name=name)
            if t.value == "switch":
                self.advance()
                self.expect("(")
                expr = self.parse_expr()
                self.expect(")")
                body = self.parse_switch_body()
                return Switch(line=t.line, col=t.col, expr=expr, body=body)
            if t.value == "case":
                self.advance()
                val = self.parse_assign()
                self.expect(":")
                stmt = None if self.at("case") or self.at("default") or self.at("}") \
                    else self.parse_stmt()
                return Case(line=t.line, col=t.col, value=val, stmt=stmt)
            if t.value == "default":
                self.advance()
                self.expect(":")
                stmt = None if self.at("case") or self.at("default") or self.at("}") \
                    else self.parse_stmt()
                return Case(line=t.line, col=t.col, value=None, stmt=stmt)
            if t.value == "do":
                # do { ... } while (cond);
                self.advance()
                body = self.parse_stmt()
                if not self.at("while"):
                    raise ParseError("do 之后缺少 while", t.line, t.col)
                self.advance()
                self.expect("(")
                cond = self.parse_expr()
                self.expect(")")
                self.expect(";")
                return DoWhile(line=t.line, col=t.col, body=body, cond=cond)
            if t.value == "while":
                self.advance()
                self.expect("(")
                cond = self.parse_expr()
                self.expect(")")
                body = self.parse_stmt()
                return While(line=t.line, col=t.col, cond=cond, body=body)
            if t.value == "for":
                self.advance()
                self.expect("(")
                init = None
                if not self.at(";"):
                    ity = self.parse_type()
                    if ity is not None:
                        nt = self.expect_kind(Tok.IDENT, "变量名")
                        init = self.parse_decl_group(ity, nt.value, nt)
                    else:
                        init = ExprStmt(expr=self.parse_expr())
                        self.expect(";")
                else:
                    self.advance()
                cond = None if self.at(";") else self.parse_expr()
                self.expect(";")
                step = None if self.at(")") else self.parse_expr()
                self.expect(")")
                body = self.parse_stmt()
                return For(line=t.line, col=t.col, init=init, cond=cond,
                           step=step, body=body)
            if t.value == "return":
                self.advance()
                v = None if self.at(";") else self.parse_expr()
                self.expect(";")
                return Return(line=t.line, col=t.col, value=v)
            if t.value == "break":
                self.advance()
                self.expect(";")
                return Break(line=t.line, col=t.col)
            if t.value == "continue":
                self.advance()
                self.expect(";")
                return Continue(line=t.line, col=t.col)

        if self.at(";"):
            self.advance()
            return Block(line=t.line, col=t.col)   # 空语句

        e = self.parse_expr()
        self.expect(";")
        return ExprStmt(line=t.line, col=t.col, expr=e)

    # ---- 表达式 ----
    def parse_expr(self):
        """逗号表达式：a = 1, b = 2 —— 取最后一个的值。

        for 的 step 里极常见：for (p=p->next, ++n) ...
        """
        e = self.parse_assign()
        while self.at(","):
            self.advance()
            right = self.parse_assign()
            e = BinOp(line=e.line, col=e.col, op=",", left=e, right=right)
        return e

    def parse_assign(self):
        left = self.parse_ternary()
        if self.cur.value in ASSIGN_OPS and self.cur.kind == Tok.OP:
            op = self.advance().value
            right = self.parse_assign()      # 右结合
            return Assign(line=left.line, col=left.col, op=op,
                          target=left, value=right)
        return left

    def parse_ternary(self):
        """三目运算符 cond ? a : b，右结合，优先级低于所有二元运算。"""
        self.enter_nest()
        try:
            return self._parse_ternary_inner()
        finally:
            self.leave_nest()

    def _parse_ternary_inner(self):
        cond = self.parse_binary(1)
        if self.at("?"):
            q = self.advance()
            then = self.parse_assign()
            self.expect(":")
            els = self.parse_ternary()
            return Ternary(line=q.line, col=q.col, cond=cond,
                           then=then, els=els)
        return cond

    def parse_binary(self, min_prec):
        left = self.parse_unary()
        while True:
            t = self.cur
            if t.kind != Tok.OP or t.value not in PREC:
                break
            prec = PREC[t.value]
            if prec < min_prec:
                break
            self.advance()
            right = self.parse_binary(prec + 1)   # 左结合
            left = BinOp(line=left.line, col=left.col, op=t.value,
                         left=left, right=right)
        return left

    def parse_unary(self):
        t = self.cur
        if t.kind == Tok.OP and t.value in ("-", "+", "!", "~", "*", "&", "++", "--"):
            self.advance()
            operand = self.parse_unary()
            return UnOp(line=t.line, col=t.col, op=t.value, operand=operand)

        if t.kind == Tok.KW and t.value == "sizeof":
            self.advance()
            if self.at("("):
                self.advance()
                ty = self.parse_type()
                if ty is not None and self.at(")"):
                    self.advance()
                    return UnOp(line=t.line, col=t.col, op="sizeof",
                                operand=Ident(line=t.line, col=t.col,
                                              name=repr(ty)))
                e = self.parse_expr()
                self.expect(")")
                return UnOp(line=t.line, col=t.col, op="sizeof", operand=e)
            return UnOp(line=t.line, col=t.col, op="sizeof",
                        operand=self.parse_unary())

        return self.parse_postfix()

    def parse_postfix(self):
        self.enter_nest()
        try:
            return self._parse_postfix_inner()
        finally:
            self.leave_nest()

    def _parse_postfix_inner(self):
        e = self.parse_primary()
        while True:
            t = self.cur
            if self.at("("):
                self.advance()
                args = []
                # va_arg(ap, int)：第二个参数是类型而不是表达式。
                # 不特殊处理的话解析器会在 'int' 上直接崩
                # （实测 numpy 那份 1400 行文件就是这么卡住的）。
                fname = e.name if isinstance(e, Ident) else ""
                if not self.at(")"):
                    while True:
                        if fname in ("va_arg", "va_copy") and args:
                            ty = self.try_parse_cast_type()
                            if ty is not None and (
                                    self.at(",") or self.at(")")):
                                args.append(Ident(line=t.line, col=t.col,
                                                  name=repr(ty)))
                            else:
                                args.append(self.parse_assign())
                        else:
                            args.append(self.parse_assign())
                        if self.at(","):
                            self.advance()
                            continue
                        break
                self.expect(")")
                e = Call(line=t.line, col=t.col, func=e, args=args)
            elif self.at("["):
                self.advance()
                idx = self.parse_expr()
                self.expect("]")
                e = Index(line=t.line, col=t.col, base=e, idx=idx)
            elif self.at(".") or self.at("->"):
                arrow = self.advance().value == "->"
                name = self.expect_kind(Tok.IDENT, "成员名").value
                e = Member(line=t.line, col=t.col, base=e, name=name,
                           arrow=arrow)
            elif self.at("++") or self.at("--"):
                op = self.advance().value
                e = UnOp(line=t.line, col=t.col, op="post" + op, operand=e)
            else:
                break
        return e

    def parse_primary(self):
        self.enter_nest()
        try:
            return self._parse_primary_inner()
        finally:
            self.leave_nest()

    def _parse_primary_inner(self):
        t = self.cur
        if self.at("{"):
            # 初始化列表 {1, 2, 3} 或 {INVALID_HANDLE_VALUE, ""}
            # 也支持 C99 指定初始化器 {.a = 1, .b = 2}（Python 扩展里到处都是）
            self.advance()
            items = []
            if not self.at("}"):
                while True:
                    if self.at("{"):
                        items.append(self.parse_primary())
                    elif self.at(".") and self.peek().kind == Tok.IDENT \
                            and self.peek(2).value == "=":
                        # .field = value
                        self.advance()          # .
                        self.advance()          # 字段名
                        self.advance()          # =
                        items.append(self.parse_assign())
                    else:
                        items.append(self.parse_assign())
                    if self.at(","):
                        self.advance()
                        # 允许尾随逗号 {1,2,}
                        if self.at("}"):
                            break
                        continue
                    break
            self.expect("}")
            return InitList(line=t.line, col=t.col, items=items)
        if t.kind == Tok.NUM:
            self.advance()
            v = t.value
            if v.lower().startswith("0x"):
                return Num(line=t.line, col=t.col,
                           value=_safe_int(v, 16), suffix=t.suffix)
            if "." in v or "e" in v.lower():
                try:
                    return Num(line=t.line, col=t.col, value=float(v),
                               is_float=True)
                except (ValueError, OverflowError):
                    return Num(line=t.line, col=t.col, value=0.0,
                               is_float=True)
            return Num(line=t.line, col=t.col,
                       value=_safe_int(v, 10), suffix=t.suffix)
        if t.kind == Tok.STR:
            self.advance()
            # C 里相邻字符串字面量会自动拼接："abc" "def" == "abcdef"
            # 真实代码里长字符串跨行时几乎都这么写。
            val = t.value
            while self.cur.kind == Tok.STR:
                val += self.advance().value
            return Str(line=t.line, col=t.col, value=val)
        if t.kind == Tok.IDENT:
            self.advance()
            return Ident(line=t.line, col=t.col, name=t.value)
        if self.at("("):
            # 类型转换 (LinkList)malloc(...) / (int)x / (Node *)
            # 判断方式：括号内是类型名，且紧跟右括号。
            save = self.i
            self.advance()
            cast_ty = self.try_parse_cast_type()
            if cast_ty is not None and self.at(")"):
                self.advance()
                # 转换运算符本身不影响后续求值，但目标类型要留下：
                # 语义层用它来判断"显式转换"从而压制隐式转换告警，
                # 也用它推导表达式类型（(int)ptr 不该还是 pointer）。
                return UnOp(line=t.line, col=t.col, op="cast",
                            operand=self.parse_unary(), cast_type=cast_ty)
            self.i = save
            self.advance()
            e = self.parse_expr()
            self.expect(")")
            return e
        raise ParseError(f"无法解析的表达式起始 {t.value!r}", t.line, t.col)

    def try_parse_cast_type(self):
        """尝试解析一个类型名，失败则返回 None（不回退位置，由调用方处理）。"""
        start = self.i
        if (self.cur.kind == Tok.KW
                and self.cur.value in ("int", "float", "char", "double",
                                       "void", "signed", "unsigned",
                                       "short", "long")):
            while (self.cur.kind == Tok.KW
                   and self.cur.value in ("signed", "unsigned", "short",
                                          "long", "int", "char", "float",
                                          "double", "void")):
                self.advance()
            while self.at("*"):
                self.advance()
            return Type(base="int")
        if self.cur.kind == Tok.KW and self.cur.value == "struct":
            self.advance()
            if self.at_kind(Tok.IDENT):
                self.advance()
            while self.at("*"):
                self.advance()
            return Type(base="struct")
        if self.at_kind(Tok.IDENT):
            name = self.advance().value
            ptr = 0
            while self.at("*"):
                self.advance()
                ptr += 1
            # 判定是否类型转换，按可信度依次判断：
            # 1) 是已见过的变量 -> 绝不是类型，回退（(*(init))() 就靠这条）；
            # 2) 是已登记的 typedef 或内置类型名 -> 是转换；
            # 3) 带星号 -> 视为转换（(Node*)0 这类指针转换）。
            if name in self.known_vars:
                self.i = start
                return None
            if ptr == 0 and name not in self.known_types:
                # 兜底：从未作为变量出现过的标识符，按"未知头文件里的类型名"
                # 容忍（npy_int、ternaryfunc 等）。
                # known_vars 已经排除了变量，所以 (x)+1 不会被误判；
                # 剩下能进到这里的只可能是类型名。
                looks_like_type = (
                    len(name) >= 3
                    and (name[0].isalpha() or name[0] == "_")
                    and all(ch.isalnum() or ch == "_" for ch in name)
                )
                if not looks_like_type:
                    self.i = start
                    return None
            return Type(base=name, ptr=ptr)
        self.i = start
        return None


def parse(src: str) -> Program:
    return Parser(tokenize(src)).parse()


if __name__ == "__main__":
    import sys
    src = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 \
        else "int main(){ int a = 1; return a + 2; }"
    prog = parse(src)
    for d in prog.decls:
        print(d)


def _looks_like_type_name(name: str) -> bool:
    """粗判标识符是否像类型名（用于 (Type)x 转换）。

    只用于"括号内单个标识符后面紧跟表达式"这种含糊场景。
    宁可少吞一点，也不要吞掉 (x)+1 这类普通括号表达式——
    所以要求名字里不含大写开头之外的可疑字符，且长度 >= 4。
    """
    if not isinstance(name, str) or len(name) < 4:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(ch.isalnum() or ch == "_" for ch in name)

def _fold_const_dim(node):
    """把数组长度表达式折成整数常量，折不出返回 None。

    只做纯常量折叠（字面量 + 算术/位运算），不依赖语义层的符号表——
    维度里通常只有数字和宏，出现变量名就放弃（返回 None）。
    """
    if isinstance(node, Num) and not node.is_float:
        try:
            return int(node.value)
        except (TypeError, ValueError):
            return None
    if isinstance(node, UnOp) and node.op in ("-", "+"):
        v = _fold_const_dim(node.operand)
        if v is None:
            return None
        return -v if node.op == "-" else v
    if isinstance(node, BinOp):
        a = _fold_const_dim(node.left)
        b = _fold_const_dim(node.right)
        if a is None or b is None:
            return None
        try:
            if node.op == "+": return a + b
            if node.op == "-": return a - b
            if node.op == "*": return a * b
            if node.op == "/": return int(a / b) if b else None
            if node.op == "%": return a % b if b else None
            if node.op == "<<": return a << b if 0 <= b < 64 else None
            if node.op == ">>": return a >> b if 0 <= b < 64 else None
            if node.op == "&": return a & b
            if node.op == "|": return a | b
            if node.op == "^": return a ^ b
        except (ZeroDivisionError, ValueError, OverflowError):
            return None
    return None

# Python 3.11+ 默认限制整数转字符串/字符串转整数为 4300 位。
# 学生写错一个超长数字（粘贴错误、多打几个 0）就会让整个工具崩掉，
# 而这时候最需要的是"给出诊断"而不是 traceback。
_MAX_INT_DIGITS = 4000


def _safe_int(text, base=10):
    """安全地把字面量转成 int。超长时截断并给一个保守值。

    C 里整数字面量超过类型范围是未定义行为/实现定义，
    教学工具没必要真算出那个天文数字，只要不崩就行。
    这里超长时返回首段数字的值——保持符号和量级大致正确，
    并让 const_fold 之类下游逻辑仍然拿到一个 int。
    """
    body = text
    if base == 16 and body.lower().startswith("0x"):
        body = body[2:]
    body = body.lstrip("+-")
    if len(body) > _MAX_INT_DIGITS:
        body = body[:_MAX_INT_DIGITS]
    try:
        return int(text[:1] + body if text[:1] in "+-" else body, base)
    except (ValueError, OverflowError):
        return 0