# -*- coding: utf-8 -*-
"""一键打包成 exe —— 把 csub 发给别人的最简方式。

用法：
    python build_exe.py

产物：
    dist/csub.exe          单文件，双击即用
    csub-v1.0-win64.zip    连同说明文档的交付包

前置：
    pip install pyinstaller

说明：
    打包用的是 PyInstaller 的 --onefile 模式，运行时会把 src/
    解压到临时目录（sys._MEIPASS），app.py 里有对应的路径处理。
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

DIST = os.path.join(ROOT, "dist")
ZIP_NAME = "csub-v1.0-win64.zip"


def run(cmd, **kw):
    print("  $ " + " ".join(cmd))
    return subprocess.run(cmd, **kw)


def main():
    # 1) 检查 PyInstaller
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("缺少 PyInstaller，先装：")
        print("    pip install pyinstaller")
        return 1

    # 2) 清理
    print("[1/4] 清理旧产物")
    for d in ("build", "dist"):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            shutil.rmtree(p)
    for f in os.listdir(ROOT):
        if f.endswith(".spec"):
            os.remove(os.path.join(ROOT, f))
    os.makedirs(DIST, exist_ok=True)

    # 3) 打包
    print("[2/4] 打包 exe（约 1 分钟）")
    cmd = [sys.executable, "-m", "PyInstaller",
           "--onefile",
           "--name", "csub",
           "--add-data", "src;src",
           "--console",
           "--noconfirm",
           "--clean",
           "app.py"]
    r = run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  打包失败：")
        print((r.stderr or r.stdout)[-1500:])
        return 1
    exe = os.path.join(DIST, "csub.exe")
    if not os.path.exists(exe):
        print("  没生成 exe")
        return 1
    print("  csub.exe  %.1f MB" % (os.path.getsize(exe) / 1024 / 1024))

    # 4) 带说明文档一起压缩
    print("[3/4] 生成交付包")
    # 说明文档放在项目根目录（dist/ 会被清理掉）
    readme = os.path.join(ROOT, "使用说明.md")
    if not os.path.exists(readme):
        print("  警告：使用说明.md 不存在，只打包 exe")
    zpath = os.path.join(ROOT, ZIP_NAME)
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(exe, "csub.exe")
        if os.path.exists(readme):
            z.write(readme, "使用说明.md")
    print("  %s  %.1f MB" % (ZIP_NAME,
                             os.path.getsize(zpath) / 1024 / 1024))

    # 5) 清理中间产物
    print("[4/4] 清理")
    build = os.path.join(ROOT, "build")
    if os.path.isdir(build):
        shutil.rmtree(build)
    for f in os.listdir(ROOT):
        if f.endswith(".spec"):
            os.remove(os.path.join(ROOT, f))

    print()
    print("完成。发给别人的文件：")
    print("  " + zpath)
    print()
    print("对方解压后双击 csub.exe 即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
