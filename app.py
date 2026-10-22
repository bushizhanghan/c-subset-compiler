#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""csub 桌面版启动器 —— 双击 exe 就能用。

和 web.py 的区别：
  1. 打包成 exe 后 __file__ 在临时解包目录（_MEIPASS），
     路径处理要兼容
  2. 启动后自动开浏览器，用户不用记 URL
  3. 关窗口不退出（服务继续），但控制台窗口关掉就停
  4. 没有控制台窗口时（--noconsole 打包），用托盘提示代替 print

用法（源码方式跑也可以）：
    python app.py            # 启动并开浏览器
    python app.py --port 9000
"""
import os
import sys
import threading
import time
import webbrowser


def base_dir():
    """拿到项目根目录 —— 兼容源码运行和 PyInstaller 打包。"""
    if getattr(sys, "frozen", False):
        # PyInstaller 解包目录；src 被打进去了
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE = base_dir()
sys.path.insert(0, os.path.join(BASE, "src"))

from web import (Handler, MAX_SRC, check_text, get_ir,   # noqa: E402
                 PAGE)
from http.server import ThreadingHTTPServer              # noqa: E402


def find_free_port(start=8777, tries=30):
    """从 start 开始找一个能绑定的端口。"""
    import socket
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return None


def main():
    port = None
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                pass
    if port is None:
        port = find_free_port(8777)

    if port is None:
        print("找不到可用端口（8777-8806 全被占用）")
        input("按回车退出...")
        return 1

    url = "http://127.0.0.1:%d" % port
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print("启动失败：%s" % e)
        input("按回车退出...")
        return 1

    print("=" * 52)
    print("  csub 诊断台已启动")
    print("  地址：%s" % url)
    print()
    print("  浏览器会自动打开。如果没打开，")
    print("  把这个地址复制到浏览器里：%s" % url)
    print()
    print("  检查完代码后，关掉这个黑窗口即可退出。")
    print("=" * 52)

    threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
