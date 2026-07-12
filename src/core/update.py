import zipfile
import shutil

import requests

from src.util import root, data_dir, logger, UPDATE

# GitHub API 对未认证的匿名请求存在频率限制，手动更新无需额外线程


UPDATE_ZIP = data_dir / "update.zip"
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
            "version": data["tag_name"].lstrip("v"),
            "body": data.get("body", ""),
            "assets": data.get("assets", []),
        }
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
        return True
    except zipfile.BadZipFile:
        logger.exception("更新包损坏")
    except Exception:
        logger.exception("解压更新包时出错")
    return False


def writeUpdateScript():
    """在 root 生成 update.cmd"""
    update_cmd = root / "update.cmd"
    content = r"""@echo off
chcp 65001 >nul
:wait
tasklist /fi "imagename eq O.exe" 2>nul | find /i "O.exe" >nul
if not errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto wait
)
cd /d "%~dp0"
for /f "delims=" %%i in ('dir /b /a-d 2^>nul') do if /i not "%%i"=="data" del /f /q "%%i" 2>nul
for /f "delims=" %%i in ('dir /b /ad 2^>nul') do if /i not "%%i"=="data" rmdir /s /q "%%i" 2>nul
xcopy /s /e /y "data\update\*" "." >nul
rmdir /s /q "data\update" >nul 2>nul
if exist "data\update.zip" del /f /q "data\update.zip" >nul 2>nul
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
        if UPDATE_DIR.exists():
            shutil.rmtree(UPDATE_DIR)
    except Exception:
        logger.exception("清理临时文件时出错")
