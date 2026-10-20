#!/usr/bin/env python3
"""IR -> x86-64 汇编（Windows ABI）—— 可行性验证版。

这是 captain 用来验证「后端到底能不能做」的最小实现，
不是最终交付。最终版由 codegen-backend 在 tools/codegen.py 里做。

设计取舍（都写清楚，方便后来者判断）：
- 变量全部放栈上，不做寄存器分配。教学工具够用，且不会出错。
- 每个变量占 8 字节，用 [rbp-8*N] 寻址。
- Windows x64 ABI：整型参数依次走 rcx/rdx/r8/r9，返回值在 eax。
  （本机 gcc 是 MSYS2 ucrt64，实测确认，不是 System V）
- 不生成 .seh_* 异常处理指令，先试能不能链接。
- 只支持整数，浮点/数组/指针/结构体全部不做。
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from parser import parse
from preprocess import preprocess
from ir import IRGen, Instr

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Windows x64 ABI 整型参数寄存器
ARG_REGS = ["rcx", "rdx", "r8", "r9"]

# 二元运算 -> 汇编
BIN_OPS = {
    "+": "add", "-": "sub", "*": "imul",
}


class AsmGen:
    def __init__(self):
        self.lines = []
        self.var_slot = {}      # 变量名 -> 栈偏移
        self.next_slot = 0
        self.label_of = {}      # IR 标签 -> asm 标签
        self.epilogue_label = None

    def slot(self, name):
        """给变量分配栈槽位（每个 8 字节）。"""
        if name not in self.var_slot:
            self.next_slot += 1
            self.var_slot[name] = self.next_slot * 8
        return self.var_slot[name]

    def emit(self, text):
        self.lines.append("\t" + text)

    def label(self, text):
        self.lines.append(text + ":")

    def is_int(self, s):
        if not isinstance(s, str):
            return False
        return s.lstrip("-").isdigit()

    # ---- 装载 / 存储 ----
    def load(self, operand, reg="eax"):
        """把操作数装进寄存器。操作数可能是常量、变量或临时变量。"""
        if self.is_int(operand):
            self.emit(f"mov {reg}, {operand}")
            return
        off = self.slot(operand)
        self.emit(f"mov {reg}, DWORD PTR [rbp-{off}]")

    def store(self, dst, reg="eax"):
        off = self.slot(dst)
        self.emit(f"mov DWORD PTR [rbp-{off}], {reg}")

    # ---- 主体 ----
    def gen_function(self, fn_name, instrs):
        self.var_slot = {}
        self.next_slot = 0
        self.label_of = {}
        self.epilogue_label = f".L_{fn_name}_end"

        self.lines.append("")
        self.label(fn_name)

        # 先扫一遍 IR 收集变量和标签，确定栈帧大小
        for ins in instrs:
            if ins.op == "label":
                self.label_of[ins.extra] = self.asm_label(ins.extra)
            for f in (ins.dst, ins.a1, ins.a2):
                if f and not self.is_int(f) and f not in ("",):
                    self.slot(f)
        for ins in instrs:
            if ins.op == "call":
                for f in ins.extra.split(","):
                    f = f.strip()
                    if f and not self.is_int(f):
                        self.slot(f)

        # 栈帧大小按 16 对齐（Windows ABI 要求）
        frame = ((self.next_slot * 8) + 15) // 16 * 16
        frame = max(frame, 32)

        self.emit("push rbp")
        self.emit("mov rbp, rsp")
        self.emit(f"sub rsp, {frame}")

        # 参数从寄存器搬到栈上（按形参顺序）。
        # 注意宽度：内存是 DWORD(32 位)，所以必须用寄存器的 32 位名，
        # 写 rcx 会报 "operand type mismatch for mov"（实测）。
        # rcx->ecx, rdx->edx, r8->r8d, r9->r9d
        ARG_REGS_32 = ["ecx", "edx", "r8d", "r9d"]
        for i, pname in enumerate(getattr(self, "cur_params", [])):
            if i < len(ARG_REGS_32):
                off = self.slot(pname)
                self.emit(f"mov DWORD PTR [rbp-{off}], {ARG_REGS_32[i]}")

        for ins in instrs:
            self.gen_instr(ins)

        self.label(f".L_{fn_name}_end")
        self.emit("mov rsp, rbp")
        self.emit("pop rbp")
        self.emit("ret")

    def asm_label(self, ir_label):
        """IR 标签名转成合法汇编标签。"""
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in ir_label)
        return f".L_{safe}"

    def gen_instr(self, ins: Instr):
        op = ins.op

        if op == "label":
            self.label(self.asm_label(ins.extra))
            return

        if op == "goto":
            self.emit(f"jmp {self.asm_label(ins.extra)}")
            return

        if op in ("if", "ifTrue"):
            # if: 条件为假则跳；ifTrue: 条件为真则跳
            self.load(ins.a1)
            self.emit("test eax, eax")
            jmp = "je" if op == "if" else "jne"
            self.emit(f"{jmp} {self.asm_label(ins.extra)}")
            return

        if op == "return":
            if ins.a1:
                self.load(ins.a1)
            self.emit(f"jmp {self.epilogue_label}")
            return

        if op == "=":
            if self.is_int(ins.a1):
                self.emit(f"mov eax, {ins.a1}")
            else:
                self.load(ins.a1)
            self.store(ins.dst)
            return

        if op in BIN_OPS:
            self.load(ins.a1, "eax")
            if self.is_int(ins.a2):
                if op == "*":
                    self.emit(f"imul eax, {ins.a2}")
                else:
                    self.emit(f"{BIN_OPS[op]} eax, {ins.a2}")
            else:
                off = self.slot(ins.a2)
                if op == "*":
                    self.emit(f"imul eax, DWORD PTR [rbp-{off}]")
                else:
                    self.emit(f"{BIN_OPS[op]} eax, DWORD PTR [rbp-{off}]")
            self.store(ins.dst)
            return

        if op in ("/", "%"):
            # idiv 需要被除数在 edx:eax，除数在别处
            self.load(ins.a1, "eax")
            self.emit("cdq")
            if self.is_int(ins.a2):
                self.emit(f"mov ecx, {ins.a2}")
                self.emit("idiv ecx")
            else:
                off = self.slot(ins.a2)
                self.emit(f"idiv DWORD PTR [rbp-{off}]")
            self.store(ins.dst, "eax" if op == "/" else "edx")
            return

        if op in ("<", ">", "<=", ">=", "==", "!="):
            self.load(ins.a1, "eax")
            if self.is_int(ins.a2):
                self.emit(f"cmp eax, {ins.a2}")
            else:
                off = self.slot(ins.a2)
                self.emit(f"cmp eax, DWORD PTR [rbp-{off}]")
            setcc = {"<": "setl", ">": "setg", "<=": "setle",
                     ">=": "setge", "==": "sete", "!=": "setne"}[op]
            self.emit(setcc + " al")
            self.emit("movzx eax, al")
            self.store(ins.dst)
            return

        if op in ("&&", "||"):
            # IR 里 && 和 || 是普通二元运算（不是跳转），
            # 但 C 语义要求短路：a && b 在 a 为假时不求值 b。
            # 这里用跳转实现，保证求值顺序正确。
            # 注意：本探针的实现假设 b 已经求值（IR 已经把两侧都算出来了），
            # 所以严格说短路语义在这里丢失了 —— 对纯表达式无害，
            # 但若 b 含函数调用会有副作用差异。正式版要修。
            self.load(ins.a1, "eax")
            self.emit("test eax, eax")
            skip = f".L_{ins.dst}_skip"
            self.emit(f"je {skip}" if op == "&&" else f"jne {skip}")
            self.load(ins.a2, "eax")
            self.store(ins.dst)
            self.emit(f"jmp .L_{ins.dst}_done")
            self.label(skip)
            self.emit("mov eax, 0" if op == "&&" else "mov eax, 1")
            self.store(ins.dst)
            self.label(f".L_{ins.dst}_done")
            return

        if op in ("&", "|", "^", "<<", ">>"):
            self.load(ins.a1, "eax")
            if self.is_int(ins.a2):
                self.emit(f"mov ecx, {ins.a2}")
            else:
                off = self.slot(ins.a2)
                self.emit(f"mov ecx, DWORD PTR [rbp-{off}]")
            asm = {"&": "and", "|": "or", "^": "xor",
                   "<<": "shl", ">>": "sar"}[op]
            self.emit(f"{asm} eax, ecx")
            self.store(ins.dst)
            return

        if op == "call":
            # Windows ABI：前 4 个参数走 rcx/rdx/r8/r9
            args = [a.strip() for a in ins.extra.split(",") if a.strip()]
            regs32 = ["ecx", "edx", "r8d", "r9d"]
            for i, a in enumerate(args[:4]):
                self.load(a, regs32[i])
            # Windows 要求调用前留 32 字节影子空间
            self.emit("sub rsp, 32")
            self.emit(f"call {ins.a1}")
            self.emit("add rsp, 32")
            if ins.dst:
                self.store(ins.dst)
            return

        if op == "neg":
            self.load(ins.a1, "eax")
            self.emit("neg eax")
            self.store(ins.dst)
            return

        if op == "not":
            # 逻辑非：C 语义是"零变 1，非零变 0"
            self.load(ins.a1, "eax")
            self.emit("test eax, eax")
            self.emit("sete al")
            self.emit("movzx eax, al")
            self.store(ins.dst)
            return

        if op == "nop":
            return

        # 不认识的指令：显式报出来，不要静默生成错误代码
        self.emit(f"; [不支持] {op}")

    def gen_program(self, prog, params_of):
        # 按 IR 里的 func 标签切分函数
        code = IRGen().gen_program(prog)
        funcs = []
        cur = None
        for ins in code:
            if ins.op == "label" and ins.extra.startswith("func "):
                name = ins.extra[len("func "):]
                cur = (name, [])
                funcs.append(cur)
                continue
            if ins.op == "label" and ins.extra.startswith("end func "):
                cur = None
                continue
            if cur is not None:
                cur[1].append(ins)

        # .globl 是必需的：不加的话 MinGW 会按 GUI 程序链接，
        # 报 "undefined reference to WinMain"（实测）。
        header = ["\t.intel_syntax noprefix", "\t.text"]
        for name, _instrs in funcs:
            header.append(f"\t.globl {name}")
        self.lines = list(header)
        for name, instrs in funcs:
            self.cur_params = params_of.get(name, [])
            self.gen_function(name, instrs)
        return "\n".join(self.lines) + "\n"


def collect_params(prog):
    """从 AST 里取每个函数的形参名列表（IR 的 call 不记形参表）。"""
    out = {}
    for d in prog.decls:
        if hasattr(d, "name") and hasattr(d, "params"):
            out[d.name] = [p for _t, p in d.params if p]
    return out


def compile_to_asm(src_path, out_path):
    src = open(src_path, encoding="utf-8").read()
    pr = preprocess(src)
    prog = parse(pr.text)
    gen = AsmGen()
    asm = gen.gen_program(prog, collect_params(prog))
    open(out_path, "w", encoding="utf-8").write(asm)
    return out_path


def build_and_run(asm_path):
    """汇编 -> 链接 -> 运行，返回退出码。"""
    exe = os.path.splitext(asm_path)[0] + ".exe"
    r = subprocess.run(["gcc", "-o", exe, asm_path],
                       capture_output=True, timeout=60,
                       creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        return None, r.stderr.decode("utf-8", "replace")
    p = subprocess.run([exe], capture_output=True, timeout=10,
                       creationflags=CREATE_NO_WINDOW)
    return p.returncode, ""


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python _probe_codegen.py file.c")
        sys.exit(2)
    src = sys.argv[1]
    asm = os.path.splitext(src)[0] + "_probe.s"
    compile_to_asm(src, asm)
    code, err = build_and_run(asm)
    if code is None:
        print("构建失败:")
        print(err[:800])
    else:
        print(f"退出码: {code}")
