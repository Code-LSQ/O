import os
import sys
import threading
from urllib.parse import urlparse

import requests

from src.util import APP_NAME, VERSION, root, data_dir, logger, arch

# GitHub API 对未认证的匿名请求存在频率限制，如果要自动更新，每小时检查一次更新足够了，手动更新可以不用写额外线程

URL = f"https://api.github.com/repos/Code-LSQ/{APP_NAME}/releases/latest"

def checkUpdate(url, current_version):
    try:
        # 发送请求如果请求失败(如404, 403)，抛出异常
        response = requests.get(url)
        response.raise_for_status()
        # 解析返回的JSON数据
        data = response.json()
        # 获取最新版本的tag_name，并去除开头的'v'
        latest_version = data["tag_name"].lstrip('v')

        # 与当前版本比较
        if latest_version != current_version:
            logger.info(f"发现新版本 {latest_version}！当前版本为 {current_version}。")

            return True
        else:
            logger.info("当前已是最新版本。")
            return False

    except requests.exceptions.RequestException:
        logger.exception("检查更新时发生网络错误")
        return False
    except KeyError:
        logger.exception("解析API响应时出错，未找到预期字段")
        return False
    except Exception:
        logger.exception("发生未知错误")
        return False


def urlConvert(url: str):
    """把 https://github.com/{author}/{repo}  https://github.com/{author}/{repo}/releases  一类的网址，转化成 https://api.github.com/repos/{author}/{repo}/releases/latest """
    if not url:
        return ""
    if '://' not in url:
        url = 'https://' + url
    parsed = urlparse(url)
    hostname = (parsed.hostname or '').removeprefix('www.')
    if hostname != 'github.com':
        return url
    path = parsed.path.strip('/')
    if path.endswith('.git'):
        path = path[:-4]
    parts = path.split('/')
    if len(parts) < 2:
        return url
    author, repo = parts[0], parts[1]
    if len(parts) >= 4 and parts[2] == 'releases' and parts[3] == 'tag':
        tag = parts[4] if len(parts) > 4 else 'latest'
        return f"https://api.github.com/repos/{author}/{repo}/releases/tags/{tag}"
    return f"https://api.github.com/repos/{author}/{repo}/releases/latest"

