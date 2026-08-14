import os
import re
import sys
import argparse
import subprocess

import PyInstaller.__main__

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.util import APP_NAME, VERSION, root, logo_ico, logo_png, logo_icn

dist = root / ".." / "dist"
work = root / ".." / "build"
mainpy = str(root / "o.py")

# 发行版计划，Linux 和 macOS 目前是计划中状态。
# Windows，两种，x64、ARM64 的 .zip。
# Linux，两种，x64、ARM64的  .AppImage。
# macOS，两种，x64、ARM64的 .dmg，内部是 .app。


def pluginLib():
    """扫描 /plugin 内所有 .py 文件，解析 PluginLib 得到额外依赖，不限于第三方库，部分只在插件中使用的标准库最好也放进列表"""
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


def runGit(cmd):
    """执行 git 命令，先打印命令，失败时打印错误并退出"""
    print(f"> {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root))
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(f"git 命令失败: {' '.join(cmd)}")


def release(new_version, dry_run=False):
    """发布脚本：同步版本号、提交、打 tag 并推送。用法: python build.py release 1.0.0"""
    if not re.fullmatch(r"\d+\.\d+\.\d+", new_version):
        sys.exit(f"版本号格式错误: {new_version}，应为 1.0.0 形式")

    util_path = root / "src/util.py"
    pyproj_path = root / "pyproject.toml"
    util_text = util_path.read_text(encoding="utf-8")
    current = re.search(r'(?m)^VERSION = "([^"]+)"', util_text)
    if not current:
        sys.exit("未找到 VERSION 定义")
    print(f"当前版本: {current.group(1)} -> 新版本: {new_version}")

    current_ver = tuple(int(x) for x in current.group(1).split("."))
    new_ver = tuple(int(x) for x in new_version.split("."))
    if new_ver <= current_ver:
        if new_ver == current_ver:
            sys.exit(f"版本号已是 {new_version}，无需重复发布")
        sys.exit(f"新版本号 {new_version} 不能低于当前版本 {current.group(1)}")

    if dry_run:
        print(f"[dry-run] 将更新 {util_path} 与 {pyproj_path} 的版本号")
        print(f"[dry-run] 将执行: git add / commit / tag / push V{new_version}")
        return 0

    # 发布前必须保证工作区干净，防止把未完成的工作混进发布提交
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=str(root))
    if status.stdout.strip():
        sys.exit("工作区有未提交改动，请先提交或还原")

    util_text = re.sub(r'(?m)^VERSION = "[^"]+"', f'VERSION = "{new_version}"', util_text)
    util_path.write_text(util_text, encoding="utf-8")

    pyproj_text = re.sub(r'(?m)^version = "[^"]+"', f'version = "{new_version}"', pyproj_path.read_text(encoding="utf-8"))
    pyproj_path.write_text(pyproj_text, encoding="utf-8")

    runGit(["git", "add", "src/util.py", "pyproject.toml"])
    runGit(["git", "commit", "-m", f"V{new_version}"])
    runGit(["git", "tag", f"V{new_version}"])
    runGit(["git", "push", APP_NAME, "main", "--tags"])
    print(f"发布完成: V{new_version}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="O 构建/发布工具")
    sub = parser.add_subparsers(dest="command")
    release_parser = sub.add_parser("release", help="同步版本号、提交、打 tag 并推送")
    release_parser.add_argument("version", help="新版本号，如 1.0.0")
    release_parser.add_argument("--dry-run", action="store_true", help="仅预览，不修改文件与 git")
    args = parser.parse_args()

    if args.command == "release":
        return release(args.version, dry_run=args.dry_run)

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
            "--exclude-module=tkinter",
            "--exclude-module=unittest",
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
            "--exclude-module=tkinter",
            "--exclude-module=unittest",
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
            "--exclude-module=tkinter",
            "--exclude-module=unittest",
            "--windowed",
        ]

    for dep in pluginLib():
        args.append(f"--hidden-import={dep}")

    print(args)

    return PyInstaller.__main__.run(args)


if __name__ == "__main__":
    sys.exit(main())
