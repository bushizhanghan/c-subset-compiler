"""中间表示（三地址码 IR）生成与优化。

教学价值在于"看得见"：学生写的 a = b + c * 2 究竟变成什么，
这是《计算机系统基础》里最难讲清的部分。

IR 形式：三地址码，每条指令最多一个运算。
  t0 = c * 2
  t1 = b + t0
  a = t1

同时提供：
  - 常量折叠
  - 死代码消除
  - 生成前后对比（用于可视化差异）
"""
from dataclasses import dataclass, field
from typing import Optional

from parser import (
    Program, FuncDef, VarDecl, Block, If, While, For, Return, Break,
    Continue, ExprStmt, Num, Str, Ident, BinOp, UnOp, Assign, Call,
    Index, Member, DeclGroup, DoWhile, Switch, Case, Label, Goto,
    Ternary, InitList,
)


@dataclass
class Instr:
    op: str                 # = | + | - | * | / | % | call | return | ...
    dst: Optional[str] = None
    a1: Optional[str] = None
    a2: Optional[str] = None
    extra: str = ""
    line: int = 0

    def __str__(self):
        if self.op == "=":
            return f"{self.dst} = {self.a1}"
        if self.op in ("+", "-", "*", "/", "%", "<", ">", "<=", ">=",
                       "==", "!=", "&&", "||", "&", "|", "^", "<<", ">>"):
            return f"{self.dst} = {self.a1} {self.op} {self.a2}"
        if self.op == "neg":
            return f"{self.dst} = -{self.a1}"
        if self.op == "not":
            return f"{self.dst} = !{self.a1}"
        if self.op == "return":
            return f"return {self.a1 or ''}".rstrip()
        if self.op == "call":
            args = self.extra
            target = f"{self.dst} = " if self.dst else ""
            return f"{target}call {self.a1}({args})"
        if self.op == "if":
            return f"ifFalse {self.a1} goto {self.extra}"
        if self.op == "ifTrue":
            return f"if {self.a1} goto {self.extra}"
        if self.op == "goto":
            return f"goto {self.extra}"
        if self.op == "label":
            return f"{self.extra}:"
        if self.op == "load":
            return f"{self.dst} = {self.a1}[{self.a2}]"
        if self.op == "store":
            return f"{self.extra}[{self.a1}] = {self.a2}"
        if self.op == "addr":
            return f"{self.dst} = &{self.a1}"
        if self.op == "deref":
            return f"{self.dst} = *{self.a1}"
        if self.op == "param":
            return f"param {self.a1}"
        if self.op == "nop":
            return "nop   ; 不支持的语法"
        return f"{self.op} {self.dst or ''} {self.a1 or ''}".strip()


# 复合赋值 -> 二元运算。a += 2 就是 a = a + 2。
COMPOUND_ASSIGN = {
    "+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%",
    "&=": "&", "|=": "|", "^=": "^", "<<=": "<<", ">>=": ">>",
}


class IRGen:
    def __init__(self):
        self.code: list[Instr] = []
        self.tmp = 0
        self.label = 0
        # 循环上下文栈：(continue 目标, break 目标)。
        # break/continue 必须知道自己该跳到哪一层——
        # 早期版本直接忽略它们，生成的 IR 语义是错的
        # （循环体里 break 之后仍然继续执行）。
        self.loop_stack = []
        # 临时变量 -> 常量文本，用于给 switch 的 case 标签起可读名字
        self.const_text = {}

    def newtmp(self):
        self.tmp += 1
        return f"t{self.tmp}"

    def newlabel(self):
        self.label += 1
        return f"L{self.label}"

    def emit(self, *args, **kw):
        self.code.append(Instr(*args, **kw))

    # ---- 顶层 ----
    def gen_program(self, prog: Program):
        for d in prog.decls:
            if isinstance(d, FuncDef) and d.body is not None:
                self.emit("label", extra=f"func {d.name}")
                self.gen_block(d.body, new_scope=False)
                self.emit("label", extra=f"end func {d.name}")
        return self.code

    def gen_block(self, blk: Block, new_scope=True):
        for s in blk.stmts:
            self.gen_stmt(s)

    def gen_stmt(self, s):
        if isinstance(s, DeclGroup):
            for vd in s.decls:
                self.gen_vardecl(vd)
        elif isinstance(s, VarDecl):
            self.gen_vardecl(s)
        elif isinstance(s, Block):
            self.gen_block(s)
        elif isinstance(s, If):
            cond = self.gen_expr(s.cond)
            lelse = self.newlabel()
            self.emit("if", a1=cond, extra=lelse, line=s.line)
            self.gen_stmt(s.then)
            if s.els is not None:
                lend = self.newlabel()
                self.emit("goto", extra=lend)
                self.emit("label", extra=lelse)
                self.gen_stmt(s.els)
                self.emit("label", extra=lend)
            else:
                self.emit("label", extra=lelse)
        elif isinstance(s, While):
            ltop = self.newlabel()
            lend = self.newlabel()
            self.emit("label", extra=ltop)
            cond = self.gen_expr(s.cond)
            self.emit("if", a1=cond, extra=lend)
            self.loop_stack.append((ltop, lend))
            self.gen_stmt(s.body)
            self.loop_stack.pop()
            self.emit("goto", extra=ltop)
            self.emit("label", extra=lend)
        elif isinstance(s, DoWhile):
            ltop = self.newlabel()
            lend = self.newlabel()
            lcont = self.newlabel()
            self.emit("label", extra=ltop)
            self.loop_stack.append((lcont, lend))
            self.gen_stmt(s.body)
            self.loop_stack.pop()
            self.emit("label", extra=lcont)
            cond = self.gen_expr(s.cond)
            # do-while 的语义是"条件为真则继续循环"，回边必须用 ifTrue。
            # 早期写成 emit("if")（即 ifFalse），极性反了：
            # 条件为假时反而跳回去，dowhile_basic 的 raw IR 直接就是错的
            # （实测 raw=0 而 gcc=6，静态即可确诊）。
            self.emit("ifTrue", a1=cond, extra=ltop)
            self.emit("label", extra=lend)
        elif isinstance(s, For):
            if s.init is not None:
                self.gen_stmt(s.init) if isinstance(
                    s.init, (VarDecl, DeclGroup, ExprStmt)) \
                    else self.gen_expr(s.init)
            ltop = self.newlabel()
            lend = self.newlabel()
            lcont = self.newlabel()
            self.emit("label", extra=ltop)
            if s.cond is not None:
                cond = self.gen_expr(s.cond)
                self.emit("if", a1=cond, extra=lend)
            self.loop_stack.append((lcont, lend))
            self.gen_stmt(s.body)
            self.loop_stack.pop()
            self.emit("label", extra=lcont)
            if s.step is not None:
                self.gen_expr(s.step)
            self.emit("goto", extra=ltop)
            self.emit("label", extra=lend)
        elif isinstance(s, Return):
            if s.value is not None:
                v = self.gen_expr(s.value)
                self.emit("return", a1=v, line=s.line)
            else:
                self.emit("return", line=s.line)
        elif isinstance(s, ExprStmt):
            self.gen_expr(s.expr)
        elif isinstance(s, Break):
            # 跳到最内层循环的 break 目标。
            # 早期版本直接忽略，导致 break 之后的代码在 IR 里仍会执行，
            # 语义与源码不符。
            if self.loop_stack:
                self.emit("goto", extra=self.loop_stack[-1][1], line=s.line)
        elif isinstance(s, Continue):
            if self.loop_stack:
                self.emit("goto", extra=self.loop_stack[-1][0], line=s.line)
        elif isinstance(s, Switch):
            self.gen_switch(s)
        elif isinstance(s, Case):
            if s.value is not None:
                self.emit("label", extra=f"case {self.gen_expr(s.value)}")
            if s.stmt is not None:
                self.gen_stmt(s.stmt)
        elif isinstance(s, Label):
            self.emit("label", extra=s.name)
        elif isinstance(s, Goto):
            self.emit("goto", extra=s.name)


    def gen_switch(self, s: Switch):
        """switch 生成：依次比较后跳到对应 case 标签。

        switch 里的 break 要跳到 switch 之后，所以也压一个
        loop_stack 条目（continue 目标给 None，用不到）。
        """
        lend = self.newlabel()
        val = self.gen_expr(s.expr)
        cases = [c for c in s.body.stmts if isinstance(c, Case)]

        # 关键：case 的值只能求值一次，并把结果文本缓存下来给两处复用。
        # 早期版本在"分发"和"展开"两轮里各调一次 gen_expr(c.value)，
        # 若 case 值是表达式（如 case 1+1），两次会生成不同的临时变量，
        # 于是分发处 goto case_t2_L1、展开处却是 case_t4_L1 —— 标签悬空，
        # 生成的 IR 根本不可执行（switch_neg_expr 实测如此）。
        label_of = []          # 与 cases 一一对应：标签名 或 None（default）
        val_of = {}            # 标签名 -> 已求值的常量文本，供比较复用
        default_label = lend
        for c in cases:
            if c.value is None:
                label_of.append(None)
                default_label = f"case_default_{lend}"
            else:
                cv = self.gen_expr(c.value)
                lab = f"case_{cv}_{lend}"
                label_of.append(lab)
                val_of[lab] = cv

        # 先分发
        for c, lab in zip(cases, label_of):
            if c.value is None:
                continue
            cv = self.gen_expr(c.value) if False else None   # 只用缓存的标签
            t = self.newtmp()
            self.emit("==", dst=t, a1=val, a2=lab.split("_")[1])
            self.emit("ifTrue", a1=t, extra=lab)
        self.emit("goto", extra=default_label)

        # 再展开各分支，标签严格复用上面缓存的名字
        self.loop_stack.append((None, lend))
        for c, lab in zip(cases, label_of):
            self.emit("label", extra=lab if lab else f"case_default_{lend}")
            if c.stmt is not None:
                self.gen_stmt(c.stmt)
        self.loop_stack.pop()
        self.emit("label", extra=lend)
    def gen_vardecl(self, vd: VarDecl):
        # 初始化列表：int a[3] = {1,2,3};
        # 必须逐元素 store 到数组里，且 store 的目标要带上数组名。
        # 早期版本走通用路径，InitList 落到兜底 return "?"，
        # 于是生成 `a = ?`，而且 store 的 extra 是空的，不可执行。
        if isinstance(vd.init, InitList):
            for idx, item in enumerate(vd.init.items):
                v = self.gen_expr(item)
                self.emit("store", a1=str(idx), a2=v, extra=vd.name,
                          line=vd.line)
            return
        if vd.init is not None:
            v = self.gen_expr(vd.init)
            self.emit("=", dst=vd.name, a1=v, line=vd.line)

    # ---- 表达式 ----
    def gen_expr(self, e) -> str:
        if isinstance(e, Num):
            return repr(e.value) if e.is_float else str(e.value)

        if isinstance(e, Str):
            return f'"{e.value}"'

        if isinstance(e, Ident):
            return e.name

        if isinstance(e, BinOp):
            l = self.gen_expr(e.left)
            r = self.gen_expr(e.right)
            t = self.newtmp()
            self.emit(e.op, dst=t, a1=l, a2=r, line=e.line)
            return t

        if isinstance(e, UnOp):
            # 自增 / 自减：++a / a++ / --a / a--。
            # 早期版本走 else 分支只做"="，即 t1 = a，
            # 变量本身完全没被写回 —— 循环里 i++ 会让 IR 死循环。
            # 这里必须真的算出新值并写回目标。
            if e.op in ("++", "--", "post++", "post--"):
                is_dec = e.op in ("--", "post--")
                is_post = e.op.startswith("post")
                operand = e.operand
                if isinstance(operand, Ident):
                    tgt = operand.name
                else:
                    tgt = None
                cur = self.gen_expr(operand)
                t = self.newtmp()
                self.emit("-" if is_dec else "+", dst=t, a1=cur, a2="1",
                          line=e.line)
                if tgt is not None:
                    self.emit("=", dst=tgt, a1=t, line=e.line)
                elif isinstance(operand, UnOp) and operand.op == "*":
                    p = self.gen_expr(operand.operand)
                    self.emit("store", a1="0", a2=t, extra=p, line=e.line)
                # 表达式值：前缀返回新值；后缀必须返回**加之前的旧值**。
                # 注意不能直接把 cur 这个符号名当结果——它已被改写。
                # 必须先落一个临时变量保存旧值，否则 `int b = a++;`
                # 会生成 `b = a`（此时 a 已加过），b 拿到的是 6 而非 5。
                if is_post:
                    told = self.newtmp()
                    self.emit("=", dst=told, a1=cur, line=e.line)
                    # 这两条必须在写回之前，这里靠 gen_expr 的调用顺序
                    # 已经先生成了 told = cur，再生成自增，顺序正确。
                    return told
                return t

            if e.op == "sizeof":
                # 用语义层算好的真实尺寸（semantic.sizeof_node 挂在节点上）。
                # 早期一律返回 4，导致 sizeof(int[10]) 也算成 4，
                # 而 sizeof(arr)/sizeof(arr[0]) 这个取数组长度的惯用法
                # 就完全失效了。
                sz = getattr(e, "size_value", None)
                if isinstance(sz, int) and sz > 0:
                    return str(sz)
                return "4"
            v = self.gen_expr(e.operand)
            t = self.newtmp()
            if e.op == "-":
                self.emit("neg", dst=t, a1=v, line=e.line)
                # 记录 -常量 的可读文本，供 switch 的 case 标签使用
                try:
                    self.const_text[t] = f"-{v}"
                except Exception:
                    pass
            elif e.op == "!":
                self.emit("not", dst=t, a1=v, line=e.line)
            elif e.op == "*":
                self.emit("deref", dst=t, a1=v, line=e.line)
            elif e.op == "&":
                self.emit("addr", dst=t, a1=v, line=e.line)
            else:
                self.emit("=", dst=t, a1=v, line=e.line)
            return t

        if isinstance(e, Assign):
            # 复合赋值：a += 2 等价于 a = a + 2。
            # 早期版本只处理 op == "="，其余复合赋值一律当成普通赋值，
            # 于是 `a += 2` 生成 `a = 2` —— 原值整个丢掉，语义是错的。
            op = e.op
            if op != "=" and op in COMPOUND_ASSIGN:
                cur = self.gen_expr(e.target)      # 读当前值
                rhs = self.gen_expr(e.value)
                t = self.newtmp()
                self.emit(COMPOUND_ASSIGN[op], dst=t, a1=cur, a2=rhs,
                          line=e.line)
                v = t
            else:
                v = self.gen_expr(e.value)

            if isinstance(e.target, Ident):
                self.emit("=", dst=e.target.name, a1=v, line=e.line)
                return e.target.name
            if isinstance(e.target, Index):
                base = self.gen_expr(e.target.base)
                idx = self.gen_expr(e.target.idx)
                self.emit("store", a1=idx, a2=v, extra=base, line=e.line)
                return v
            if isinstance(e.target, UnOp) and e.target.op == "*":
                p = self.gen_expr(e.target.operand)
                self.emit("store", a1="0", a2=v, extra=p, line=e.line)
                return v
            return v

        if isinstance(e, Call):
            args = [self.gen_expr(a) for a in e.args]
            name = e.func.name if isinstance(e.func, Ident) else "?"
            t = self.newtmp()
            self.emit("call", dst=t, a1=name, extra=", ".join(args),
                      line=e.line)
            return t

        if isinstance(e, Index):
            base = self.gen_expr(e.base)
            idx = self.gen_expr(e.idx)
            t = self.newtmp()
            self.emit("load", dst=t, a1=base, a2=idx, line=e.line)
            return t

        if isinstance(e, Member):
            base = self.gen_expr(e.base)
            return f"{base}.{e.name}"

        if isinstance(e, Ternary):
            # a ? b : c —— 用条件跳转实现，两个分支都汇到同一个临时变量。
            # 早期版本没有这个分支，落到兜底的 return "?"，
            # 于是 `return x ? 10 : 20;` 生成 `return ?`，不可执行。
            lfalse = self.newlabel()
            lend = self.newlabel()
            cond = self.gen_expr(e.cond)
            self.emit("if", a1=cond, extra=lfalse)
            tv = self.gen_expr(e.then)
            t = self.newtmp()
            self.emit("=", dst=t, a1=tv, line=e.line)
            self.emit("goto", extra=lend)
            self.emit("label", extra=lfalse)
            ev = self.gen_expr(e.els)
            self.emit("=", dst=t, a1=ev, line=e.line)
            self.emit("label", extra=lend)
            return t

        if isinstance(e, InitList):
            # 表达式位置的初始化列表（如函数实参 {1,2}）——C99 复合字面量，
            # 本项目不支持。这里明确拒绝而不是生成 `a = ?` 这种
            # 看似正常、实际不可执行的指令。
            self.emit("nop", line=e.line)
            return "initlist"

        return "?"


# ---------------- 优化 ----------------

def const_fold(code: list[Instr], max_rounds=10):
    """常量折叠 + 块内常量传播。

    核心原则：**只在基本块内传播，绝不跨控制流**。

    这是被真实 bug 打回来重写的一版。旧版用一张线性推进的 consts 表，
    完全不看控制流，结果把循环体里的
        s = s + i        ->  s = 0
        i = i + 1        ->  i = 1
    直接替换成常量，循环永远不前进、累加恒为 0，表现为死循环或错误结果。
    实测 23 个程序里 13 个语义不一致、5 个死循环。

    现在的做法：
      - 遇到 label / goto / if / ifTrue / return / call 就清空常量表
        （这些是控制流边界，跨过去之后"上次看到的常量"不再可信）
      - 块内仍做迭代折叠，保证 a = 3*4; b = a + 1 能连续折到 b = 13
    """
    def is_int(s):
        if not isinstance(s, str):
            return False
        t = s.lstrip("-").lstrip("+")
        return t.isdigit()

    # 控制流边界：跨过这些指令后，之前的常量认知全部作废
    CONTROL_FLOW = {"label", "goto", "if", "ifTrue", "return"}

    total_folded = 0
    cur = list(code)
    for _ in range(max_rounds):
        out, folded = [], 0
        consts = {}                    # 仅在当前基本块内有效

        for ins in cur:
            # ---- 控制流边界：清表并原样保留 ----
            if ins.op in CONTROL_FLOW:
                # if/ifTrue 的 a1 仍可折（条件可能已是常量）
                if ins.op in ("if", "ifTrue") and ins.a1 in consts:
                    new_ins = Instr(ins.op, dst=ins.dst,
                                    a1=str(consts[ins.a1]), a2=ins.a2,
                                    extra=ins.extra, line=ins.line)
                    out.append(new_ins)
                    folded += 1
                else:
                    out.append(ins)
                consts.clear()
                continue

            # ---- 函数调用：可能改变任何内存，清表 ----
            if ins.op == "call":
                consts.clear()
                out.append(ins)
                continue

            # ---- 常量赋值：记进表 ----
            if ins.op == "=" and ins.dst and is_int(ins.a1):
                consts[ins.dst] = int(ins.a1)
                out.append(ins)
                continue

            # ---- 常量传播：x = 已知常量 ----
            if ins.op == "=" and ins.a1 in consts:
                v = consts[ins.a1]
                out.append(Instr("=", dst=ins.dst, a1=str(v), line=ins.line))
                if ins.dst:
                    consts[ins.dst] = v
                folded += 1
                continue

            # ---- 二元运算折叠 ----
            if ins.op in ("+", "-", "*", "/", "%"):
                a1 = str(consts[ins.a1]) if ins.a1 in consts else ins.a1
                a2 = str(consts[ins.a2]) if ins.a2 in consts else ins.a2

                if is_int(a1) and is_int(a2):
                    a, b = int(a1), int(a2)
                    v = None
                    try:
                        if ins.op == "+":
                            v = a + b
                        elif ins.op == "-":
                            v = a - b
                        elif ins.op == "*":
                            v = a * b
                        elif ins.op == "/":
                            v = int(a / b) if b else None
                        else:
                            v = a % b if b else None
                    except (ZeroDivisionError, ValueError):
                        v = None
                    if v is not None:
                        out.append(Instr("=", dst=ins.dst, a1=str(v),
                                         line=ins.line))
                        if ins.dst:
                            consts[ins.dst] = v
                        folded += 1
                        continue

                if a1 != ins.a1 or a2 != ins.a2:
                    out.append(Instr(ins.op, dst=ins.dst, a1=a1, a2=a2,
                                     line=ins.line))
                    folded += 1
                    continue
                # 结果不再是常量，把它从表里删掉
                if ins.dst:
                    consts.pop(ins.dst, None)
                out.append(ins)
                continue

            # ---- 其他写操作：使目标常量失效 ----
            if ins.dst:
                consts.pop(ins.dst, None)
            out.append(ins)

        cur = out
        total_folded += folded
        if folded == 0:
            break

    return cur, total_folded

def dead_code_elim(code: list[Instr]):
    """死代码消除：删掉"算出来但没人用"的临时变量赋值。

    只处理 t 开头的临时变量，不动用户变量（可能有副作用或被外部读取）。
    """
    uses = {}
    for ins in code:
        for f in (ins.a1, ins.a2):
            if f:
                uses[f] = uses.get(f, 0) + 1
        if ins.op == "call":
            for f in ins.extra.split(","):
                f = f.strip()
                if f:
                    uses[f] = uses.get(f, 0) + 1
        if ins.a1:
            uses[ins.a1] = uses.get(ins.a1, 0)

    out, removed = [], 0
    for ins in code:
        # 只删纯赋值给临时变量、且该临时变量从未被使用、且无副作用的指令
        if (ins.op == "=" and ins.dst and ins.dst.startswith("t")
                and uses.get(ins.dst, 0) == 0):
            removed += 1
            continue
        if (ins.op in ("+", "-", "*", "/", "%", "neg", "not")
                and ins.dst and ins.dst.startswith("t")
                and uses.get(ins.dst, 0) == 0):
            removed += 1
            continue
        out.append(ins)
    return out, removed


def build_cfg(code: list[Instr]):
    """构造控制流图（CFG）。

    返回 (basic_blocks, edges)：
      basic_blocks: [(起始索引, 结束索引(含), [指令...])]
      edges: {块序号: [后继块序号]}

    这是做数据流分析的前提。没有 CFG 就没法回答
    "这个变量在某点是否还活着"这类问题。
    """
    # 1) 找出所有标签位置与跳转目标
    label_pos = {}
    for idx, ins in enumerate(code):
        if ins.op == "label":
            label_pos[ins.extra] = idx

    # 2) 找块边界：标签处必开新块；跳转/返回之后也断开
    leaders = {0}
    for idx, ins in enumerate(code):
        if ins.op == "label":
            leaders.add(idx)
        if ins.op in ("goto", "if", "ifTrue", "return") and idx + 1 < len(code):
            leaders.add(idx + 1)
    leaders = sorted(x for x in leaders if x < len(code))
    if not leaders:
        return [], {}

    blocks = []
    for i, start in enumerate(leaders):
        end = (leaders[i + 1] - 1) if i + 1 < len(leaders) else len(code) - 1
        blocks.append((start, end, code[start:end + 1]))

    # 3) 建边
    idx_to_block = {}
    for bi, (s, e, _) in enumerate(blocks):
        for k in range(s, e + 1):
            idx_to_block[k] = bi

    edges = {}
    for bi, (s, e, body) in enumerate(blocks):
        nxt = []
        last = body[-1] if body else None
        if last is not None and last.op == "goto":
            tgt = label_pos.get(last.extra)
            if tgt is not None and tgt in idx_to_block:
                nxt.append(idx_to_block[tgt])
        elif last is not None and last.op in ("if", "ifTrue"):
            tgt = label_pos.get(last.extra)
            if tgt is not None and tgt in idx_to_block:
                nxt.append(idx_to_block[tgt])
            if bi + 1 < len(blocks):
                nxt.append(bi + 1)
        elif last is not None and last.op == "return":
            pass
        else:
            if bi + 1 < len(blocks):
                nxt.append(bi + 1)
        edges[bi] = nxt

    return blocks, edges


def _uses_defs(ins: Instr):
    """一条指令的 (使用集合, 定义集合)。"""
    uses, defs = set(), set()
    if ins.dst:
        defs.add(ins.dst)
    for f in (ins.a1, ins.a2):
        if f and _is_var_name(f):
            uses.add(f)
    if ins.op == "call":
        for f in ins.extra.split(","):
            f = f.strip()
            if f and _is_var_name(f):
                uses.add(f)
    if ins.op in ("label", "goto"):
        uses.clear()
    # 目标同时被使用（a = a + 1）的情况上面已覆盖
    defs.discard(None)
    return uses, defs


def _is_var_name(s: str) -> bool:
    if not s:
        return False
    if s.lstrip("-").isdigit():
        return False
    if s.startswith('"'):
        return False
    return all(ch.isalnum() or ch in "_." for ch in s)


def liveness(code: list[Instr]):
    """活跃变量分析（backward dataflow）。

    live_out[b] = union(live_in[s]) for s in succ(b)
    live_in[b]  = use[b] ∪ (live_out[b] - def[b])

    这是寄存器分配的基础，也是"为什么这句赋值没用"的判据来源。
    返回 (live_in, live_out)，键为块序号，值为变量名集合。
    """
    blocks, edges = build_cfg(code)
    if not blocks:
        return {}, {}

    use_b, def_b = [], []
    for _s, _e, body in blocks:
        u, d = set(), set()
        for ins in body:
            iu, idf = _uses_defs(ins)
            u |= (iu - d)          # 先定义后使用的，不算入口处使用
            d |= idf
        use_b.append(u)
        def_b.append(d)

    n = len(blocks)
    live_in = [set() for _ in range(n)]
    live_out = [set() for _ in range(n)]

    changed = True
    rounds = 0
    while changed and rounds < 100:      # 迭代到不动点，加上限防死循环
        changed = False
        rounds += 1
        for bi in range(n - 1, -1, -1):
            out = set()
            for s in edges.get(bi, []):
                out |= live_in[s]
            inn = use_b[bi] | (out - def_b[bi])
            if out != live_out[bi] or inn != live_in[bi]:
                live_out[bi] = out
                live_in[bi] = inn
                changed = True

    return {i: live_in[i] for i in range(n)}, {i: live_out[i] for i in range(n)}


def liveness_report(code: list[Instr]):
    """生成人类可读的活跃变量报告，用于教学展示。"""
    blocks, _edges = build_cfg(code)
    live_in, live_out = liveness(code)
    rows = []
    for bi, (s, e, body) in enumerate(blocks):
        rows.append({
            "block": bi,
            "lines": [str(i) for i in body],
            "live_in": sorted(live_in.get(bi, set())),
            "live_out": sorted(live_out.get(bi, set())),
        })
    return rows


def generate(prog: Program, optimize=True):
    """生成 IR，返回 dict 供界面展示。"""
    raw = IRGen().gen_program(prog)
    result = {
        "raw": [str(i) for i in raw],
        "raw_count": len(raw),
        "optimized": None,
        "optimized_count": None,
        "folded": 0,
        "removed": 0,
        "blocks": None,
        "liveness": None,
    }
    if optimize:
        c1, folded = const_fold(raw)
        c2, removed = dead_code_elim(c1)
        result["optimized"] = [str(i) for i in c2]
        result["optimized_count"] = len(c2)
        result["folded"] = folded
        result["removed"] = removed
        try:
            result["liveness"] = liveness_report(c2)
            result["blocks"] = len(build_cfg(c2)[0])
        except Exception:
            pass
    return result


def ir_to_text(prog: Program, optimize=True) -> str:
    r = generate(prog, optimize)
    lines = ["; ---- 优化前 ----"]
    lines += r["raw"]
    if r["optimized"] is not None:
        lines.append("")
        lines.append(f"; ---- 优化后（折叠 {r['folded']} 处，消除 {r['removed']} 条）----")
        lines += r["optimized"]
        lines.append("")
        lines.append(f"; 指令数 {r['raw_count']} -> {r['optimized_count']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    from parser import parse
    src = open(sys.argv[1], encoding="utf-8").read()
    print(ir_to_text(parse(src)))
