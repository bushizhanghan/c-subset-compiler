#!/usr/bin/env python3
"""汇编 -> 可执行 -> 取退出码。

把 `tools/codegen.py` 产出的 .s 交给 gcc 汇编链接（gcc 会调 as + ld），
跑起来，把 main 的返回值当退出码读回来。

用法:
  python tools/asm_run.py out.s              # 汇编、链接、运行，打印退出码
  python tools/asm_run.py out.s --keep       # 保留可执行文件
  python tools/asm_run.py out.s -o app.exe   # 指定可执行文件路径
  python tools/asm_run.py --keysym           # 打印本脚本的关键约定

退出码语义：
  进程退出码 = main 的返回值。
  Windows 上退出码是 32 位的，这里统一按**有符号**解释，
  所以 return -1 读回来是 -1 而不是 4294967295。
  本脚本自身的失败（汇编失败、超时）用 2 表示，绝不与程序的退出码混淆。
"""
import argparse
import os
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def run(cmd, timeout=60):
    """跑一条命令，返回 (returncode, stdout, stderr)，全部解码为文本。"""
    r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                       creationflags=CREATE_NO_WINDOW)
    return (r.returncode,
            r.stdout.decode("utf-8", "replace"),
            r.stderr.decode("utf-8", "replace"))


def signed_exit(code):
    """把 Windows/Linux 的 32 位退出码按有符号解释。"""
    code &= 0xFFFFFFFF
    return code - 0x100000000 if code >= 0x80000000 else code


def assemble(s_path, exe_path, cflags=("-O0",), timeout=120):
    """汇编 + 链接。返回 (ok, 输出文本)。

    用 gcc 而不是裸 as/ld：gcc 会自动带上 CRT 启动代码，
    否则 _start / mainCRTStartup 没人提供，链接不过。
    """
    cmd = ["gcc", *cflags, "-o", exe_path, s_path]
    try:
        rc, out, err = run(cmd, timeout=timeout)
    except FileNotFoundError:
        return False, "未找到 gcc（需要 MSYS2 ucrt64 或任意 MinGW-w64 的 gcc）"
    except subprocess.TimeoutExpired:
        return False, "gcc 超时"
    msg = (out + err).strip()
    return rc == 0, msg


def execute(exe_path, timeout=30):
    """跑可执行文件，返回 (退出码 或 None, 说明)。"""
    try:
        rc, _out, _err = run([exe_path], timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"运行超时（>{timeout}s，可能是死循环）"
    except OSError as e:
        return None, f"无法执行 {exe_path}: {e}"
    return signed_exit(rc), ""


def build_and_run(s_path, exe_path=None, keep=False, timeout=30, quiet=False):
    """完整链路：.s -> 可执行 -> 退出码。返回 (退出码 或 None, 说明)。"""
    s_path = os.path.abspath(s_path)
    if not os.path.exists(s_path):
        return None, f"找不到汇编文件 {s_path}"
    if exe_path is None:
        exe_path = os.path.splitext(s_path)[0] + (".exe" if os.name == "nt" else "")
    exe_path = os.path.abspath(exe_path)

    ok, msg = assemble(s_path, exe_path)
    if not ok:
        return None, "汇编/链接失败:\n" + msg
    if not quiet:
        print(f"[asm_run] 已生成 {exe_path}", file=sys.stderr)

    code, why = execute(exe_path, timeout=timeout)
    if not keep:
        try:
            os.remove(exe_path)
        except OSError:
            pass
    return code, why


def main(argv=None):
    ap = argparse.ArgumentParser(description="汇编 -> 可执行 -> 取退出码")
    ap.add_argument("asm", nargs="?", help="由 codegen.py 生成的 .s 文件")
    ap.add_argument("-o", "--output", help="可执行文件路径")
    ap.add_argument("--keep", action="store_true", help="保留可执行文件")
    ap.add_argument("--timeout", type=int, default=30, help="运行超时秒数")
    ap.add_argument("--quiet", action="store_true", help="不打印过程信息")
    ap.add_argument("--keysym", action="store_true",
                    help="打印本脚本依赖的 ABI/汇编约定")
    args = ap.parse_args(argv)

    if args.keysym:
        print(KEYSYM)
        return 0
    if not args.asm:
        ap.print_help()
        return 2

    code, why = build_and_run(args.asm, args.output, keep=args.keep,
                              timeout=args.timeout, quiet=args.quiet)
    if code is None:
        print(f"[asm_run] {why}", file=sys.stderr)
        return 2                      # 2 = 本工具失败，不是程序的返回值
    print(f"{code}")
    return code


KEYSYM = """\
本工具链依赖的约定（Windows x64 / MSYS2 ucrt64 gcc 16.1.0 实测）:
  .globl main      必须有，否则 MinGW 按 GUI 程序找 WinMain，链接报
                   undefined reference to `WinMain'
  参数寄存器       ecx / edx / r8d / r9d（整型）；rcx / rdx / r8 / r9（指针）
                   内存 DWORD 必须配 32 位寄存器名，QWORD 配 64 位名
  返回值           eax
  影子空间         call 前 rsp 处必须留 32 字节给被调函数
  栈对齐           rsp 在 call 处必须 16 字节对齐
  除法             cdq 把 eax 符号扩展到 edx:eax，再 idiv；商在 eax，余数在 edx
  比较             cmp + setcc al + movzx eax, al
  右移             sar（有符号），不是 shr
  .seh_*           纯计算函数可以不生成；需要栈回溯/异常时必须补
"""


if __name__ == "__main__":
    sys.exit(main())
