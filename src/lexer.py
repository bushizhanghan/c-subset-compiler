"""词法分析器。

设计目标：教学用，所以每个 token 都带行列号，方便把错误指回源码位置。
不做 C 的完整词法（不处理预处理指令、不处理字符常量转义的全部情形），
支持 C 子集：整数/浮点常量、标识符、关键字、运算符、分隔符、注释。
"""
from dataclasses import dataclass
from enum import Enum, auto


class Tok(Enum):
    # 字面量与标识符
    NUM = auto()
    IDENT = auto()
    STR = auto()
    # 关键字
    KW = auto()
    # 运算符与分隔符
    OP = auto()
    # 结束
    EOF = auto()


KEYWORDS = {
    "int", "float", "void", "char", "double",
    "if", "else", "while", "for", "return", "break", "continue",
    "sizeof", "struct", "const", "static",
    "typedef", "union", "enum", "signed", "unsigned",
    "short", "long", "switch", "case", "default", "do", "goto", "extern",
    # C99/C11 限定符与类型。不登记成 KW 的话，
    # `char *restrict dst` 里的 restrict 会被当成参数名，
    # 后面跟着的 dst 无处安放 → 报"期望 ')'，实际是 'dst'"
    # （实测 stu_38，gcc 零诊断）。
    "restrict", "inline", "register", "volatile", "_Bool",
    "_Atomic", "_Noreturn", "_Complex", "_Imaginary", "_Alignas",
    "_Alignof", "_Static_assert", "_Thread_local", "_Generic",
}

# 按长度降序，保证最长匹配（>= 优先于 >）
OPERATORS = [
    "<<=", ">>=", "...",
    "==", "!=", "<=", ">=", "&&", "||", "++", "--",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
    "<<", ">>", "->",
    "+", "-", "*", "/", "%", "=", "<", ">", "!", "&", "|", "^", "~",
    "?", ":", ";", ",", ".", "(", ")", "[", "]", "{", "}",
]


@dataclass
class Token:
    kind: Tok
    value: str
    line: int
    col: int
    # 数字后缀（L/U/UL/F），用于区分 int / long / unsigned / float
    suffix: str = ""

    def __repr__(self):
        return f"{self.kind.name}({self.value!r})@{self.line}:{self.col}"


class LexError(Exception):
    def __init__(self, msg, line, col):
        super().__init__(f"{line}:{col}: {msg}")
        self.msg, self.line, self.col = msg, line, col


def tokenize(src: str):
    """把源码切成 token 列表，末尾必有 EOF。"""
    # Windows 下用 PowerShell/记事本存的 UTF-8 常带 BOM，
    # 不处理的话第一个字符就报"非法字符"，而用户看不出哪里错了。
    if src.startswith("\ufeff"):
        src = src[1:]

    toks = []
    i, line, col = 0, 1, 1
    n = len(src)

    def peek(k=0):
        return src[i + k] if i + k < n else ""

    while i < n:
        c = src[i]

        # 空白。C 标准里 \f(换页)、\v(垂直制表) 都是合法空白字符。
        # 必须显式吃掉：早期 preprocess 用 splitlines() 切行时
        # 这些字符被当成换行符吞掉了，正好掩盖了这个缺口；
        # 改成只按 \n 切行之后，real_nmakehlp.c 立刻报"非法字符 \x0c"。
        if c in " \t\r\f\v":
            i += 1
            col += 1
            continue
        if c == "\n":
            i += 1
            line += 1
            col = 1
            continue

        # 注释：// 与 /* */
        if c == "/" and peek(1) == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and peek(1) == "*":
            sl, sc = line, col
            i += 2
            col += 2
            closed = False
            while i < n:
                if src[i] == "*" and peek(1) == "/":
                    i += 2
                    col += 2
                    closed = True
                    break
                if src[i] == "\n":
                    line += 1
                    col = 1
                else:
                    col += 1
                i += 1
            if not closed:
                raise LexError("块注释未闭合", sl, sc)
            continue

        # 数字：支持十进制 / 0x 十六进制 / 小数 / 科学计数法 1e9
        if c.isdigit():
            sl, sc = line, col
            start = i
            is_float = False
            if c == "0" and peek(1) in "xX":
                i += 2
                col += 2
                while i < n and (src[i].isdigit() or src[i].lower() in "abcdef"):
                    i += 1
                    col += 1
            else:
                while i < n and src[i].isdigit():
                    i += 1
                    col += 1
                if i < n and src[i] == ".":
                    is_float = True
                    i += 1
                    col += 1
                    while i < n and src[i].isdigit():
                        i += 1
                        col += 1
                # 科学计数法：1e9 / 1.5E-3 / 2e+10
                if i < n and src[i] in "eE":
                    j = i + 1
                    if j < n and src[j] in "+-":
                        j += 1
                    if j < n and src[j].isdigit():
                        is_float = True
                        col += j - i
                        i = j
                        while i < n and src[i].isdigit():
                            i += 1
                            col += 1
            raw_num = src[start:i]
            # 数字后缀：0.0f / 100UL / 5ll 等，去掉后缀保留数值
            suf_start = i
            while i < n and src[i] in "fFlLuU":
                i += 1
                col += 1
            raw_num = src[start:suf_start]
            if i > suf_start and not is_float and raw_num.isdigit():
                # 100UL 仍是整数；但 5f 这种少见写法按浮点处理
                if "f" in src[suf_start:i].lower() and "." not in raw_num:
                    is_float = True
            suffix = src[suf_start:i] if i > suf_start else ""
            toks.append(Token(Tok.NUM, raw_num, sl, sc, suffix=suffix))
            continue

        # 标识符与关键字
        if c.isalpha() or c == "_":
            sl, sc = line, col
            start = i
            while i < n and (src[i].isalnum() or src[i] == "_"):
                i += 1
                col += 1
            word = src[start:i]
            toks.append(Token(Tok.KW if word in KEYWORDS else Tok.IDENT,
                              word, sl, sc))
            continue

        # 字符常量 'a' / '\n'，真实代码里极常见（如 scanf(" %c", &c)）
        # 也允许**多字符常量** 'ans'（C 合法，但值实现定义，gcc 只给
        # -Wmultichar 警告）。早期只读一个字符就要求闭合引号，
        # 于是 `{'ans''ans2'}` 被判成"字符常量未闭合"（实测 stu_21），
        # 而 gcc 认它只是"多字符常量" + 后面语法断了。
        if c == "'":
            sl, sc = line, col
            i += 1
            col += 1
            if i >= n:
                raise LexError("字符常量未闭合", sl, sc)
            chars = []
            while i < n and src[i] != "'":
                if src[i] == "\\" and i + 1 < n:
                    esc = src[i + 1]
                    chars.append({"n": "\n", "t": "\t", "r": "\r",
                                  "0": "\0", "\\": "\\", "'": "'",
                                  '"': '"'}.get(esc, esc))
                    i += 2
                    col += 2
                elif src[i] == "\n":
                    # 换行前还没闭合 → 真的是错的
                    raise LexError("字符常量未闭合", sl, sc)
                else:
                    chars.append(src[i])
                    i += 1
                    col += 1
            if i >= n:
                raise LexError("字符常量未闭合", sl, sc)
            i += 1
            col += 1
            ch = chars[0] if len(chars) == 1 else "".join(chars)
            # 字符常量在 C 里是 int 类型，这里存成 NUM 但标记来源。
            # 多字符常量 'ans' 是合法 C（值实现定义），gcc 按
            # "逐字节左移拼装"处理：'ans' = ('a'<<16)|('n'<<8)|'s'。
            # 早期直接 ord(ch)，多字符时崩在 ord 上（实测 stu_21）。
            if len(ch) == 1:
                val = ord(ch)
            else:
                val = 0
                for cc in ch:
                    val = (val << 8) | (ord(cc) & 0xFF)
            toks.append(Token(Tok.NUM, str(val), sl, sc))
            continue

        # 字符串字面量（只支持简单形式，不含转义全集）
        if c == '"':
            sl, sc = line, col
            i += 1
            col += 1
            start = i
            buf = []
            while i < n and src[i] != '"':
                if src[i] == "\\" and i + 1 < n:
                    esc = src[i + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}
                               .get(esc, "\\" + esc))
                    i += 2
                    col += 2
                    continue
                if src[i] == "\n":
                    raise LexError("字符串未闭合", sl, sc)
                buf.append(src[i])
                i += 1
                col += 1
            if i >= n:
                raise LexError("字符串未闭合", sl, sc)
            i += 1
            col += 1
            toks.append(Token(Tok.STR, "".join(buf), sl, sc))
            continue

        # 运算符：最长匹配
        matched = None
        for op in OPERATORS:
            if src.startswith(op, i):
                matched = op
                break
        if matched:
            toks.append(Token(Tok.OP, matched, line, col))
            i += len(matched)
            col += len(matched)
            continue

        raise LexError(f"非法字符 {c!r}", line, col)

    toks.append(Token(Tok.EOF, "<eof>", line, col))
    return toks


if __name__ == "__main__":
    import sys
    src = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 \
        else "int main(){ return 0; }"
    for t in tokenize(src):
        print(t)
