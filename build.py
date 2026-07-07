import os
import sys
import shutil
from pathlib import Path

import PyInstaller.__main__

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.util import APP_NAME, VERSION, logo_ico, logo_png, logo_icn

root = Path(__file__).resolve().parent
dist = root / ".." / "dist"
work = root / ".." / "build"
mainpy = str(root / "o.py")

# 预计发行版
# Windows，两种，x64、ARM64 的 .zip。
# Linux，两种，x64、ARM64的  .AppImage ，可能会加上 .deb。
# macOS，两种，x64、ARM64的 .dmg，可能会加上 .app。


def pycache(path: Path):
    for pycache_dir in path.rglob("__pycache__"):
        if pycache_dir.is_dir():
            try:
                print(f"删除 {pycache_dir}")
                shutil.rmtree(pycache_dir)
            except Exception as e:
                print(f"删除 {pycache_dir} 失败: {e}")


def pluginLib():
    """扫描 /plugin 内所有 .py 文件，解析 PluginLib 得到额外依赖"""
    # 原本准备研究 PyInstaller 怎么打包库，并对文件复制或打包进行模仿，把库的文件放到 /plugin 文件夹（或者直接用 pip 局部安装），在插件类的 file 中定义，插件自身也从 /plugin 下导入库，从而做到删除插件就删除库，但是过于复杂了，感觉没必要……暂时放弃……
    import ast

    plugin_dir = root / "plugin"
    deps = []

    for f in plugin_dir.glob("*.py"):
        if f.name == "__init__.py":
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError:
            continue

        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "PluginLib"
                and isinstance(node.value, ast.List)
            ):
                for e in node.value.elts:
                    if isinstance(e, ast.Constant) and isinstance(e.value, str):
                        dep = e.value.split("==", 1)[0].strip().lower()
                        if dep:
                            deps.append(dep)
                break

    return deps


def main():
    if sys.platform == "win32":  # Windows
        args = [
            mainpy,
            "--onedir",
            "--noconfirm",
            "--contents-directory=.",
            f"--name={APP_NAME}",
            f"--icon={logo_ico}",
            f"--specpath={root}",
            f"--distpath={dist}",
            f"--workpath={work}",
            "--add-data=README.md;.",
            "--add-data=src;src",
            "--add-data=plugin;plugin",
            "--hidden-import=shiboken6",
            "--exclude-module",
            "tkinter",
            "--windowed",
        ]

    elif sys.platform == "linux":  # Linux
        args = [
            mainpy,
            "--onedir",
            "--noconfirm",
            "--contents-directory=.",
            f"--name={APP_NAME}",
            f"--icon={logo_png}",
            f"--specpath={root}",
            f"--distpath={dist}",
            f"--workpath={work}",
            "--add-data=README.md:.",
            "--add-data=src:src",
            "--add-data=plugin:plugin",
            "--hidden-import=shiboken6",
            "--exclude-module",
            "tkinter",
            "--windowed",
        ]

    elif sys.platform == "darwin":  # macOS
        args = [
            mainpy,
            "--onedir",
            "--noconfirm",
            "--contents-directory=.",
            f"--name={APP_NAME}",
            f"--icon={logo_icn}",
            f"--specpath={root}",
            f"--distpath={dist}",
            f"--workpath={work}",
            "--add-data=README.md:.",
            "--add-data=src:src",
            "--add-data=plugin:plugin",
            "--hidden-import=shiboken6",
            "--exclude-module",
            "tkinter",
            "--windowed",
        ]

    # for dep in pluginLib():
    #     args.append(f"--hidden-import={dep}")

    pycache(root)

    return PyInstaller.__main__.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
