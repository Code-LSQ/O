import os
import re
import shutil
from pathlib import Path

"""
不处理 PyQt5 ，只在 PySide6 和 PyQt6 之间互相转换
核心API转换:
    - `Signal` -> `pyqtSignal`
    - `Slot` -> `pyqtSlot`
    - `Property` -> `pyqtProperty`
枚举 (Enum) 语法转换 (最关键部分):
    - `QClassName.EnumType.Value` -> `QClassName.Value` (例如: QFileDialog.FileMode.ExistingFiles -> QFileDialog.ExistingFiles)
    - `Qt.EnumType.Value` -> `Qt.Value` (例如: Qt.AlignmentFlag.AlignCenter -> Qt.AlignCenter)
全局模块名转换
    - PySide6 和 PyQt6
"""

EXCLUDE_FOLDER = {".git", "Python", "python", "python3", "__pycache__", "build", "dist"}
INCLUDE_EXTENSION = {".py", ".md", ".toml", ".txt"}


REPLACE_RULE_PySide_to_PyQt = [
    (re.compile(r'\b(?!pyqt)Signal\b'), 'pyqtSignal'),
    (re.compile(r'\b(?!pyqt)Slot\b'), 'pyqtSlot'),
    (re.compile(r'\b(?!pyqt)Property\b'), 'pyqtProperty'),
    (re.compile(r'\b(Q[A-Z][a-zA-Z0-9_]+)\.([A-Z][a-zA-Z]+)\.([a-zA-Z0-9_]+)\b'), r'\1.\3'),
    # 负向前瞻 `(?!emit|...|)` 避免错误替换 Qt 的方法名
    (re.compile(r'\bQt\.([A-Z][a-zA-Z]+)\.(?!emit|connect|disconnect|sender)([a-zA-Z0-9_]+)\b'), r'Qt.\2'),
    (re.compile(r'PySide6'), 'PyQt6'),
]

REPLACE_RULE_PyQt_to_PySide = [
    (re.compile(r'\bpyqtSignal\b'), 'Signal'),
    (re.compile(r'\bpyqtSlot\b'), 'Slot'),
    (re.compile(r'\bpyqtProperty\b'), 'Property'),
    (re.compile(r'PyQt6'), 'PySide6'),
]

def convert_file(path: Path, flag):
    if flag == 1:
        rule = REPLACE_RULE_PySide_to_PyQt
    elif flag == 2:
        rule = REPLACE_RULE_PyQt_to_PySide

    try:
        with open(path, 'r', encoding='utf-8', newline='') as file_in:
            content = file_in.read()
    except UnicodeDecodeError:
        print(f"[skip] {path} is not UTF-8 text")
        return

    for pattern, replace in rule:
        content = pattern.sub(replace, content)

    with open(path, 'w', encoding='utf-8', newline='') as file_out:
        file_out.write(content)

def convert_dir(path: Path, flag):
    for current_root, dir_names, file_names in os.walk(path):
        dir_names[:] = [d for d in dir_names if d not in EXCLUDE_FOLDER]

        for file_name in file_names:
            file_path = Path(current_root) / file_name
            if file_name == Path(__file__).name:
                continue
            if file_path.suffix.lower() not in INCLUDE_EXTENSION:
                continue
            convert_file(file_path, flag)


# 转换单文件，文件名加 _convert，形式是 name_convert.py，转换目录，目录名加 _convert。 先复制再转换。
def main():

    flag = int(input("选项：\n1. PySide6 -> PyQt6\n2. PyQt6 -> PySide6\n"))
    if flag not in [1, 2]:
        print("请重新输入")
        return
    workpath = Path(input("请输入文件或文件夹路径\n").strip("\"' "))

    if workpath.is_file():
        dirpath = workpath.resolve().parent
        output = dirpath / f"{workpath.stem}_convert{workpath.suffix.lower()}"
        shutil.copy2(workpath, output)
        convert_file(output, flag)

    elif workpath.is_dir():
        output = workpath.parent / f"{workpath.name}_convert"
        shutil.copytree(workpath, output, dirs_exist_ok=True)
        convert_dir(output, flag)

    else:
        print("请输入有效路径")
        return


if __name__ == "__main__":
    main()

