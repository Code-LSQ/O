import zipfile
import shutil

import requests

from src.util import root, data_dir, logger, UPDATE

# GitHub API 对未认证的匿名请求存在频率限制，需要留意，手动更新无需额外线程


UPDATE_ZIP = data_dir / "update.zip"
UPDATE_PART = UPDATE_ZIP.with_name(UPDATE_ZIP.name + ".part")  # 下载断点续传的临时文件，需一并清理
UPDATE_DIR = data_dir / "update"


def getReleaseInfo(url=None):
    """获取最新版本信息，返回 {"version": str, "body": str, "assets": [...]} 或 None"""
    if url is None:
        url = UPDATE
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return {
            "version": data["tag_name"].lstrip("vV"),
            "body": data.get("body", ""),
            "assets": data.get("assets", []),
        }
    except requests.exceptions.Timeout:
        logger.exception("检查更新超时")
        raise TimeoutError("check_timeout")
    except requests.exceptions.RequestException:
        logger.exception("检查更新时发生网络错误")
    except KeyError:
        logger.exception("解析API响应时出错，未找到预期字段")
    except Exception:
        logger.exception("发生未知错误")
    return None


def extractUpdate(zip_path, extract_dir):
    """解压 zip 到目标目录"""
    try:
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        logger.info(f"已解压更新包到 {extract_dir}")
        _flattenSingleDir(extract_dir)
        return True
    except zipfile.BadZipFile:
        logger.exception("更新包损坏")
    except Exception:
        logger.exception("解压更新包时出错")
    return False


def _flattenSingleDir(extract_dir):
    """若解压目录顶层只有一个目录且无散落文件，则将其内容上移一层。

    发布包为保留 O/ 顶层目录（用户手动解压时便于辨认），内部结构会嵌套一层；
    而 update.cmd 的复制命令期望平铺结构，故在此归一化，使两种打包方式都能正确更新。"""
    entries = list(extract_dir.iterdir()) if extract_dir.exists() else []
    dirs = [e for e in entries if e.is_dir()]
    if len(dirs) == 1 and len(dirs) == len(entries):
        inner = dirs[0]
        for item in inner.iterdir():
            shutil.move(str(item), str(extract_dir / item.name))
        shutil.rmtree(inner)
        logger.info(f"已将更新包内容从 {inner.name} 上移一层")


def writeUpdateScript():
    """在 root 生成 update.cmd"""
    update_cmd = root / "update.cmd"
    # 更新失败时必须有回退，不能删光旧文件后复制失败导致程序无法启动，故采用「先备份、再替换、失败还原」策略。
    # 备份与还原都用 robocopy，因为 xcopy 无法排除目录，会把 data 递归拷进备份目录造成死循环；robocopy 的 /XD data /XF 脚本自身 可干净排除。
    # robocopy 退出码 0-7 为成功，>=8 为失败。删除旧文件时须排除 update.cmd 自身，否则脚本在执行中途被删会导致后续复制/启动失败，自删只保留在末尾一条命令。
    content = r"""@echo off
chcp 65001 >nul
if exist "data\update_error.txt" del /f /q "data\update_error.txt" >nul 2>nul
:wait
tasklist /fi "imagename eq O.exe" 2>nul | find /i "O.exe" >nul
if not errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto wait
)
cd /d "%~dp0"
if not exist "data\update\O.exe" (
    echo 更新包不完整，已中止更新 > "data\update_error.txt"
    goto finish
)
if exist "data\update_backup" rmdir /s /q "data\update_backup" >nul 2>nul
robocopy "." "data\update_backup" /E /XD data /XF "%~nx0" /R:1 /W:1 /NFL /NDL /NJH /NJS >nul
if errorlevel 8 (
    echo 备份旧文件失败，已中止更新 > "data\update_error.txt"
    goto finish
)
for /f "delims=" %%i in ('dir /b /a-d 2^>nul') do if /i not "%%i"=="data" if /i not "%%i"=="%~nx0" del /f /q "%%i" 2>nul
for /f "delims=" %%i in ('dir /b /ad 2^>nul') do if /i not "%%i"=="data" rmdir /s /q "%%i" 2>nul
robocopy "data\update" "." /E /R:1 /W:1 /NFL /NDL /NJH /NJS >nul
if errorlevel 8 (
    echo 复制新文件失败，正在还原旧版本 > "data\update_error.txt"
    for /f "delims=" %%i in ('dir /b /a-d 2^>nul') do if /i not "%%i"=="data" if /i not "%%i"=="%~nx0" del /f /q "%%i" 2>nul
    for /f "delims=" %%i in ('dir /b /ad 2^>nul') do if /i not "%%i"=="data" rmdir /s /q "%%i" 2>nul
    robocopy "data\update_backup" "." /E /R:1 /W:1 /NFL /NDL /NJH /NJS >nul
)
rmdir /s /q "data\update" >nul 2>nul
if exist "data\update.zip" del /f /q "data\update.zip" >nul 2>nul
:finish
rmdir /s /q "data\update_backup" >nul 2>nul
start "" "O.exe"
del /f /q "%~f0" >nul 2>nul
exit
"""
    update_cmd.write_text(content, encoding="utf-8")
    logger.info(f"已生成更新脚本 {update_cmd}")
    return update_cmd


def cleanTemp():
    """清理临时文件"""
    try:
        if UPDATE_ZIP.exists():
            UPDATE_ZIP.unlink()
        if UPDATE_PART.exists():
            UPDATE_PART.unlink()
        if UPDATE_DIR.exists():
            shutil.rmtree(UPDATE_DIR)
    except Exception:
        logger.exception("清理临时文件时出错")
