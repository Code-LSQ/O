import os
import re
import sys
import shutil
import argparse
import subprocess

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.util import AUTHOR, APP_NAME, VERSION, root, logo_ico, logo_png, logo_icn

mainpy = root / "o.py"
src = root / "src"
plugin = root / "plugin"
README = root / "README.md"
output = root / ".." / "dist"

# 自动推送发版  -  python build.py release 1.0.0
# 发行版规划，Linux 和 macOS 目前是计划中状态。
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


def install():

    dist = root / ".." / "dist"
    work = root / ".." / "build"

    try:
        import PyInstaller.__main__
    except ImportError:
        print("未安装 PyInstaller")
        return False

    if sys.platform == "win32":  # Windows
        args = [
            str(mainpy),
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

    else:
        return False
    
    for dep in pluginLib():
        args.append(f"--hidden-import={dep}")

    print(args)
    return PyInstaller.__main__.run(args)


def runGit(cmd):
    """执行 git 命令，先打印命令，失败时打印错误并退出"""
    print(f"> {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root))
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(f"git 命令失败: {' '.join(cmd)}")


def release(new_version):
    """发布脚本：同步版本号、合并提交、打 tag 并推送。用法: python build.py release 1.0.0"""
    if not re.fullmatch(r"\d+\.\d+\.\d+", new_version):
        sys.exit(f"版本号格式错误: {new_version}，应为 1.0.0 形式")

    py_path = src / "util.py"
    pyproj_path = root / "pyproject.toml"
    print(f"当前版本: {VERSION} -> 新版本: {new_version}")

    current_ver = tuple(int(x) for x in VERSION.split("."))
    new_ver = tuple(int(x) for x in new_version.split("."))
    if new_ver <= current_ver:
        if new_ver == current_ver:
            sys.exit(f"版本号已是 {new_version}，无需重复发布")
        sys.exit(f"新版本号 {new_version} 不能低于当前版本 {VERSION}")

    print(f"将更新 {py_path} 与 {pyproj_path} 的版本号")
    print(f"将执行: git add / commit / tag / push V{new_version}")
    status = subprocess.run(["git", "status", "--short"], capture_output=True, text=True, cwd=str(root))
    print(status.stdout, end="")
    input("回车确认发布，Ctrl+C 取消...")

    py_text = py_path.read_text(encoding="utf-8")
    py_text = re.sub(r'(?m)^VERSION = "[^"]+"', f'VERSION = "{new_version}"', py_text)
    py_path.write_text(py_text, encoding="utf-8")

    pyproj_text = re.sub(r'(?m)^version = "[^"]+"', f'version = "{new_version}"', pyproj_path.read_text(encoding="utf-8"))
    pyproj_path.write_text(pyproj_text, encoding="utf-8")

    runGit(["git", "add", "-A"])
    runGit(["git", "commit", "-m", f"V{new_version}"])
    runGit(["git", "tag", f"V{new_version}"])
    runGit(["git", "push", APP_NAME, "main", "--tags"])
    print(f"发布完成 - V{new_version}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="O 构建/发布工具，使用 Nuitka 构建")
    sub = parser.add_subparsers(dest="command")
    release_parser = sub.add_parser("release", help="同步版本号、提交、打 tag 并推送")
    release_parser.add_argument("version", help="新版本号，如 1.0.0")
    release_parser.set_defaults(func=lambda args: release(args.version))

    pyinstall_parser = sub.add_parser("pyinstall", help="使用 PyInstaller 打包，不保证可用性与兼容性")
    pyinstall_parser.set_defaults(func=lambda args: install())

    args = parser.parse_args()

    if hasattr(args, "func"):
        return args.func(args)

    if sys.platform == "win32":  # Windows
        args = [
            sys.executable,
            "-m",
            "nuitka",
            "--msvc=latest",    # "--mingw64",
            "--windows-console-mode=attach",
            "--mode=standalone",
            "--jobs=4",
            "--remove-output",
            f"--main={mainpy}",
            f"--output-dir={output}",
            f"--file-version={VERSION}",
            f"--product-name={APP_NAME}",
            f"--company-name={AUTHOR}",
            f"--windows-icon-from-ico={logo_ico}",
            f"--file-description={APP_NAME}",
            f"--copyright=Copyright(C) 2026 {AUTHOR}",
            # f"--include-data-dir={src}=src",
            "--include-plugin-directory=src",
            "--include-data-dir=src/icon=src/icon",
            "--include-data-dir=src/lang=src/lang",
            "--include-data-dir=src/theme=src/theme",
            "--include-data-file=README.md=README.md",
            "--enable-plugin=pyside6",
            "--show-modules",
            # "--show-progress",
            # "--show-memory",
            # "--show-scons",
            "--include-module=shiboken6",
            "--nofollow-import-to=tkinter",
            "--nofollow-import-to=unittest",
            "--assume-yes-for-downloads",
        ]

    elif sys.platform == "linux":  # Linux
        pass

    elif sys.platform == "darwin":  # macOS
        pass

    for dep in pluginLib():
        args.append(f"--include-module={dep}")
    print(args)
    subprocess.run(args)

    dist = output / "o.dist"
    o_path = output / APP_NAME
    os.rename(dist, o_path)
    shutil.copytree(plugin, o_path / "plugin")


if __name__ == "__main__":
    sys.exit(main())
