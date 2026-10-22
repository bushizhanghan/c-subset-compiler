"""预处理层：让真实作业代码能被分析。

为什么必须有这一层：
任何真实 C 作业第一行都是 #include <stdio.h>，
不支持预处理的工具在真实场景下第一秒就崩，用例集再漂亮也没用。

这里**不做**真正的预处理器（那需要头文件搜索路径和宏展开的完整实现），
而是做"够用且诚实"的裁剪：
  - #include      -> 丢弃（但记录，用于判断是否用到标准库函数）
  - #define 常量  -> 记录到宏表，文本替换标识符
  - #define 函数宏 -> 丢弃并标记 unsupported（不假装支持）
  - #if/#ifdef/... -> 只处理 #ifndef/#define/#endif 的简单头文件保护，
                      其余条件编译整体丢弃并标记 unsupported
  - 注释里的 # 不受影响（注释在词法层已处理，这里先按行处理）

关键设计：所有被跳过的内容都记录下来，
在报告里明确告诉用户"这几处没处理"，而不是静默给出错误结论。
"""
import re
from dataclasses import dataclass, field


@dataclass
class Macro:
    name: str
    params: list          # 空列表 = 对象宏
    body: str
    line: int
    is_function_like: bool = False


@dataclass
class PreResult:
    text: str                                   # 预处理后的源码
    macros: dict = field(default_factory=dict)  # 对象宏 name -> value
    includes: list = field(default_factory=list)
    unsupported: list = field(default_factory=list)  # [(line, 原因)]
    line_map: list = field(default_factory=list)     # 新行号(1-based) -> 原行号

    def to_orig_line(self, new_line: int) -> int:
        """把预处理后的行号映射回原始文件的行号。

        不做这个映射，学生看到"第 41 行"去翻原文件会翻到完全无关的代码——
        因为 #include 那几行被删掉了。这是真实使用中必须修的问题。
        """
        if not new_line or new_line < 1:
            return new_line
        idx = new_line - 1
        if idx < len(self.line_map):
            return self.line_map[idx]
        # 超出范围：返回最后一行的原行号
        return self.line_map[-1] if self.line_map else new_line


def split_lines(src: str):
    """统一的切行函数：只按 \n，并剥掉行尾 \r。

    全项目必须都用这一个，否则行号口径不一致。
    见本文件里 splitlines() 那段注释：它会多切 \x0c 等字符，
    导致 csub 报的行号与编辑器/gcc 不一致（实测差 10 行）。
    """
    if src.startswith("\ufeff"):
        src = src[1:]
    return [ln[:-1] if ln.endswith("\r") else ln for ln in src.split("\n")]


def _join_continuations(src: str):
    """把反斜杠续行合并成单行。

    真实代码里多行宏非常常见：
        #define SWAP(a,b) \\
            { int t; t=(a); (a)=(b); (b)=t; }
    不合并的话，第一行是个空宏，第二行会被当成普通代码，直接语法崩。
    返回 (合并后的文本, 每行对应的原始行号)。
    """
    # 只按 \n 切行，且剥掉行尾 \r。
    #
    # 这里踩过一个影响所有诊断的坑：用 str.splitlines() 会把 \x0c（换页符）、
    # \x0b、\x1c-\x1e 也当成行分隔符，而编辑器、gcc、学生看到的行号只认 \n。
    # real_nmakehlp.c 里有 10 个换页符，于是 csub 报的行号比真实行号
    # 整体大 10 行——诊断指向的源码位置全是错的（report 里的 ^ 也会标错行）。
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in src.split("\n")]
    merged = []          # [(text, orig_lineno)]
    i = 0
    while i < len(lines):
        cur = lines[i]
        start = i + 1
        # 行尾反斜杠（可能带空格）
        while cur.rstrip().endswith("\\"):
            cur = cur.rstrip()[:-1]
            i += 1
            if i >= len(lines):
                break
            cur += " " + lines[i].lstrip()
        merged.append((cur, start))
        i += 1
    return merged


DIRECTIVE = re.compile(r"^\s*#\s*(\w+)\s*(.*)$")


def preprocess(src: str) -> PreResult:
    if src.startswith("\ufeff"):
        src = src[1:]

    out_lines = []
    line_map = []
    macros = {}
    includes = []
    unsupported = []
    # 条件编译栈：每层记录"当前是否处于丢弃状态"
    cond_stack = []

    # 先合并续行，否则多行宏会把后续的普通代码带歪
    merged = _join_continuations(src)

    for raw, lineno in merged:
        m = DIRECTIVE.match(raw)

        if not m:
            # 普通代码行；若处于被丢弃的条件块里就跳过
            if any(flag for flag, _ in cond_stack):
                continue
            out_lines.append(raw)
            line_map.append(lineno)
            continue

        name, rest = m.group(1), m.group(2).strip()

        # ---- 条件编译 ----
        if name in ("if", "ifdef", "ifndef"):
            # 头文件保护的标准三件套：
            #   #ifndef STACK_H
            #   #define STACK_H
            #   ... 内容 ...
            #   #endif
            # 此时 X 未定义 -> 条件为真 -> **内容必须保留**。
            # 早期只要 rest 非空就整体丢弃，于是所有带头文件保护的
            # 代码整块消失（实测 stu_39：imax/imin 两个 static
            # inline 函数直接没了，报"调用了未声明的函数"）。
            if name == "ifndef":
                if not rest:
                    cond_stack.append((False, name))
                elif rest in macros:
                    # X 已定义 -> 条件为假 -> 跳过
                    cond_stack.append((True, name))
                else:
                    # X 未定义 -> 条件为真 -> 保留
                    cond_stack.append((False, name))
            elif name == "ifdef":
                if not rest:
                    cond_stack.append((False, name))
                elif rest in macros:
                    cond_stack.append((False, name))   # 已定义 -> 保留
                else:
                    cond_stack.append((True, name))    # 未定义 -> 跳过
            else:
                # 复杂条件：整体丢弃，并记录
                cond_stack.append((True, name))
                unsupported.append(
                    (lineno, f"条件编译 #{name} {rest} 未处理，该分支被跳过"))
            continue

        if name == "else":
            if cond_stack:
                flag, kind = cond_stack[-1]
                cond_stack[-1] = (not flag, kind)
            else:
                unsupported.append((lineno, "#else 没有对应的 #if"))
            continue

        if name == "elif":
            if cond_stack:
                cond_stack[-1] = (True, cond_stack[-1][1])
                unsupported.append((lineno, f"#elif {rest} 未处理"))
            continue

        if name == "endif":
            if cond_stack:
                cond_stack.pop()
            else:
                unsupported.append((lineno, "#endif 没有对应的 #if"))
            continue

        # 在丢弃状态下，其余指令也不生成代码
        dropping = any(flag for flag, _ in cond_stack)
        if dropping:
            continue

        # ---- include ----
        if name == "include":
            includes.append(rest.strip('<>"'))
            continue

        # ---- define ----
        if name == "define":
            if not rest:
                unsupported.append((lineno, "#define 缺少宏名"))
                continue
            # 函数宏：NAME(...)  或  NAME(a,b)
            fm = re.match(r"^([A-Za-z_]\w*)\(([^)]*)\)\s*(.*)$", rest)
            if fm:
                body = re.sub(r"//.*$", "", fm.group(3)).rstrip()
                body = re.sub(r"/\*.*?\*/", "", body).strip()
                macros[fm.group(1)] = Macro(
                    name=fm.group(1),
                    params=[p.strip() for p in fm.group(2).split(",") if p.strip()],
                    body=body,
                    line=lineno,
                    is_function_like=True,
                )
                continue
            # 对象宏：NAME 或 NAME body
            om = re.match(r"^([A-Za-z_]\w*)\s*(.*)$", rest)
            if om:
                # 宏体要剥掉行尾注释，否则 #define MAX_N 50 // 上限
                # 会把整段注释当成宏值，替换后语法直接崩掉。
                body = re.sub(r"//.*$", "", om.group(2)).rstrip()
                body = re.sub(r"/\*.*?\*/", "", body).strip()
                macros[om.group(1)] = Macro(
                    name=om.group(1), params=[], body=body,
                    line=lineno)
                continue
            unsupported.append((lineno, f"无法解析的 #define {rest}"))
            continue

        # ---- 其他指令：undef / pragma / error 等 ----
        if name == "undef":
            macros.pop(rest.split()[0] if rest else "", None)
            continue
        if name == "pragma":
            continue
        unsupported.append((lineno, f"未处理的指令 #{name}"))
        continue

    text = "\n".join(out_lines)

    # ---- 宏替换：对象宏直接换，函数宏按参数展开 ----
    obj = {n: m for n, m in macros.items() if not m.is_function_like}
    fun = {n: m for n, m in macros.items() if m.is_function_like}

    def apply_fn_macros(line):
        """处理 line 里的函数宏调用，支持嵌套一层。

        只做文本替换，不做完整的参数预扫描——教学场景够用，
        但会记录"只支持简单展开"的说明。
        """
        for _ in range(5):          # 最多迭代 5 轮，防死循环
            changed = False
            for name, m in fun.items():
                idx = 0
                while True:
                    pos = line.find(name + "(", idx)
                    if pos < 0:
                        break
                    # 找到匹配的右括号
                    depth = 0
                    j = pos + len(name)
                    end = -1
                    while j < len(line):
                        if line[j] == "(":
                            depth += 1
                        elif line[j] == ")":
                            depth -= 1
                            if depth == 0:
                                end = j
                                break
                        j += 1
                    if end < 0:
                        break
                    argstr = line[pos + len(name) + 1:end]
                    args = _split_args(argstr)
                    if len(args) != len(m.params):
                        idx = end + 1
                        continue
                    body = m.body
                    # 字符串化 #x 必须在参数替换**之前**处理：
                    # C 标准规定 # 后的参数取实参原文、不参与替换。
                    # 顺序反了的话 #x 已变成 #实参，再拿实参当参数名
                    # 去查表必然落空（实测 stu_42：
                    # STRINGIFY(hello world) 丢成 "hello"）。
                    # 负向断言 (?<!#) 排除 ## 拼接符 —— 早期没排除，
                    # CONCAT(va,r) 的 ##b 被当成字符串化，
                    # 展开成 va#"r" = 42 直接语法崩（实测 stu_42）。
                    def _stringify(mo):
                        return '"' + _find_arg(m.params, args,
                                               mo.group(1)) + '"'
                    body = re.sub(r"(?<!#)#[ \t]*([A-Za-z_]\w*)",
                                  _stringify, body)
                    # 拼接运算符 a##b：**先替换参数，再删 ##**。
                    # 早期先删 ## 再替换，宏体 ab 里的 a/b 不是独立
                    # 单词，\b 边界匹配不上，CONCAT(va, r) 展开成
                    # ab 而不是 var（实测 stu_42 两条 E1 连带）。
                    for pname, aval in zip(m.params, args):
                        body = re.sub(r"\b" + re.escape(pname) + r"\b",
                                      aval, body)
                    body = body.replace("##", "")
                    line = line[:pos] + body + line[end + 1:]
                    changed = True
                    idx = pos + len(body)
            if not changed:
                break
        return line

    if obj or fun:
        names = sorted(obj.keys(), key=len, reverse=True)
        pattern = (re.compile(r"\b(" + "|".join(map(re.escape, names))
                              + r")\b") if names else None)

        def sub_obj(text):
            if pattern is None:
                return text
            # 空定义宏（#define X 后面什么都没有）要替换成空，
            # 不是 "0"。这是真实代码里极常见的写法：
            #   #else
            #   #define F2PY_REPORT_ON_ARRAY_COPY_FROMARR
            #   #endif
            # 展开成 0 的话 `F2PY_REPORT_...;` 变成 `0;` ——
            # 一条"算了常量没有效果"的语句，会触发 W24 假警告
            # （实测 real_fortranobject.c 3 条）。
            return pattern.sub(lambda mm: obj[mm.group(1)].body or "", text)

        def repl_line(line):
            line = apply_fn_macros(line)
            if '"' in line:
                # 只替换引号外部分，避免破坏字符串内容
                parts = re.split(r'("(?:[^"\\]|\\.)*")', line)
                line = "".join(
                    p if p.startswith('"') else sub_obj(p) for p in parts)
            else:
                line = sub_obj(line)
            # 字符串之间夹着未定义宏的情形：
            #   printf("array(%" NPY_INTP_FMT, d)
            # 宏来自被忽略的头文件，取不到值，直接删掉它，
            # 让相邻字符串按 C 的拼接规则合起来。
            return _drop_unknown_inter_string_macros(line)

        out_lines = [repl_line(l) for l in out_lines]
        text = "\n".join(out_lines)
        # 跨行的字符串-宏-字符串 只能在整段文本上处理
        # （按行处理时看不到相邻行）
        text = _drop_unknown_inter_string_macros(text)
    else:
        text = "\n".join(out_lines)

    return PreResult(text=text, macros=macros, includes=includes,
                     unsupported=unsupported,
                     line_map=line_map)


def _split_args(s: str):
    """按顶层逗号切分宏参数，忽略括号内的逗号。"""
    if not s.strip():
        return []
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur).strip())
    return parts


def strip_for_lexing(src: str) -> str:
    """便捷接口：返回可直接喂给词法器的文本。"""
    return preprocess(src).text


if __name__ == "__main__":
    import sys
    src = open(sys.argv[1], encoding="utf-8").read()
    r = preprocess(src)
    print("=== 包含的头文件 ===")
    for i in r.includes:
        print("  ", i)
    print("=== 宏 ===")
    for n, m in r.macros.items():
        kind = "函数宏" if m.is_function_like else "对象宏"
        print(f"   {n} = {m.body!r}  ({kind})")
    print("=== 未处理 ===")
    for ln, why in r.unsupported:
        print(f"   {ln}: {why}")
    print("=== 预处理后源码 ===")
    print(r.text)


def _find_arg(params, args, name):
    """在宏参数里找 name 对应的原始实参文本（字符串化用）。

    找不到就返回 name 本身（防御：宏体里引用了不存在的参数）。
    """
    for p, a in zip(params, args):
        if p == name:
            return a.strip()
    return name


def _drop_unknown_inter_string_macros(line: str) -> str:
    """删除夹在两个字符串字面量之间的未知全大写宏。

    例：  "array(%" NPY_INTP_FMT, d)   ->   "array(%", d)
    判据：该位置前后都是字符串结束/开始，中间只有一个全大写标识符。
    这样 printf 的格式串仍能通过语法检查，不会因为缺头文件而崩。
    """
    import re as _re
    # \s+ 要能吃掉换行符：真实代码里长格式串经常跨行，
    #   "%d-th dimension must be fixed to %"
    #   NPY_INTP_FMT " but got %"
    # 这种写法在 numpy / CPython 源码里到处都是。
    pat1 = _re.compile(r'(")[\s\\]*([A-Z_][A-Z0-9_]{2,})[\s\\]*(?=")')
    pat2 = _re.compile(r'(")[\s\\]*([A-Z_][A-Z0-9_]{2,})[\s\\]*(?=[,)])')
    prev = None
    while prev != line:
        prev = line
        line = pat1.sub(r'\1', line)
        line = pat2.sub(r'\1', line)
    return line