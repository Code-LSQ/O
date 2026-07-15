import os
import re
import base64
from pathlib import Path
from urllib.parse import unquote
from typing import Optional, Tuple, List, Dict

import markdown

from src.util import logger


_MARKDOWN_EXTENSIONS = [
    'extra',
    'tables',
    'fenced_code',
    'nl2br',
    'sane_lists',
    'smarty',
    'toc',
]

_renderer = markdown.Markdown(extensions=_MARKDOWN_EXTENSIONS)


def _buildFullHtml(html_body: str) -> str:
    """将 Markdown 转换后的 HTML body 包装为完整 HTML 文档"""
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{
    box-sizing: border-box;
    min-width: 200px;
    max-width: 980px;
    margin: 0 auto;
    padding: 45px;
}}
@media (max-width: 767px) {{
    body {{
        padding: 15px;
    }}
}}
.markdown-body {{
    box-sizing: border-box;
    min-width: 200px;
    max-width: 100%;
    margin: 0 auto;
    padding: 0;
}}
.markdown-body img {{
    max-width: 100% !important;
    width: 100%;
    height: auto;
    display: block;
}}
.markdown-body pre {{
    position: relative;
    overflow-x: auto;
}}
.markdown-body code {{
    font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
}}
.markdown-body .hljs {{
    background: transparent;
    padding: 0;
}}
.markdown-body .toc {{
    background: #f6f8fa;
    border-radius: 6px;
    padding: 16px;
    margin-bottom: 16px;
}}
.markdown-body .toc ul {{
    list-style-type: none;
    padding-left: 1em;
    margin: 0;
}}
.markdown-body .toc li {{
    margin: 4px 0;
}}
.markdown-body .toc a {{
    color: #0366d6;
    text-decoration: none;
}}
.markdown-body .toc a:hover {{
    text-decoration: underline;
}}
#toc-nav {{
    max-height: 300px;
    overflow-y: auto;
    position: sticky;
    top: 0;
}}
h1, h2, h3, h4, h5, h6 {{
    scroll-margin-top: 20px;
}}
</style>
</head>
<body>
<article class="markdown-body">
{html_body}
</article>
</body>
</html>"""


def extractToc(content: str) -> List[Dict[str, str]]:
    """提取markdown中的标题
    
    Args:
        content: markdown文本内容
    
    Returns:
        标题列表，每个元素包含level(级别)、text(标题文本)、anchor(锚点)键
    """
    headings = []
    seen_anchors: Dict[str, int] = {}
    pattern = r'^(#{1,6})\s+(.+)$'
    char_pos = 0
    
    for i, line in enumerate(content.split('\n')):
        clean_line = line.rstrip('\r')
        match = re.match(pattern, clean_line)
        if match:
            level = len(match.group(1))
            text = match.group(2).strip()
            anchor = _generateAnchor(text, seen_anchors)
            
            headings.append({
                'level': level,
                'text': text,
                'anchor': anchor,
                'line': i + 1,
                'char_pos': char_pos
            })
        
        char_pos += len(line) + 1
    
    return headings

def _imageToDataUri(file_path: str, image_path: str) -> Optional[str]:
    """将本地图片转换为data URI"""
    try:
        if not file_path:
            return None

        md_dir = Path(file_path).parent.resolve()
        image_path = unquote(image_path.replace('\\', '/'))
        image_file = (md_dir / image_path).resolve()

        if not image_file.exists() or not image_file.is_file():
            logger.warning(f"图片文件不存在: {image_file}")
            return None

        with open(image_file, "rb") as f:
            image_data = f.read()

        mime_types = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp',
            '.svg': 'image/svg+xml',
            '.ico': 'image/x-icon',
        }

        ext = image_file.suffix.lower()
        mime_type = mime_types.get(ext, 'application/octet-stream')
        b64_data = base64.b64encode(image_data).decode("utf-8")

        return f"data:{mime_type};base64,{b64_data}"
    except Exception:
        logger.exception(f"图片转换失败: {image_path}")
        return None


def _isRemoteUrl(url: str) -> bool:
    return url.startswith(('http://', 'https://', 'data:', 'file://')) or url.startswith('//')


def _generateAnchor(text: str, seen: Dict[str, int]) -> str:
    anchor = re.sub(r'[^\w\u4e00-\u9fff\-]', '', text).replace(' ', '-').lower()
    if not anchor:
        anchor = 'heading'
    if anchor in seen:
        seen[anchor] += 1
        anchor = f"{anchor}-{seen[anchor]}"
    else:
        seen[anchor] = 0
    return anchor


def _processHtmlImgTags(content: str, file_path: str) -> str:
    """处理HTML中的img标签路径"""
    def replaceImg(match):
        attrs = match.group(1)
        src_match = re.search(r'src\s*=\s*["\']([^"\']+)["\']', attrs)
        if not src_match:
            return match.group(0)
        src = src_match.group(1)
        if _isRemoteUrl(src):
            return match.group(0)
        if file_path:
            new_src = _imageToDataUri(file_path, src)
            if new_src:
                new_attrs = re.sub(r'src\s*=\s*["\'][^"\']+["\']', '', attrs).strip()
                new_attrs = re.sub(r'\s*width\s*=\s*["\'][^"\']*["\']', '', new_attrs).strip()
                new_attrs = re.sub(r'\s*height\s*=\s*["\'][^"\']*["\']', '', new_attrs).strip()
                new_attrs = re.sub(r'\s*style\s*=\s*["\'][^"\']*["\']', '', new_attrs).strip()
                return f'<img src="{new_src}" style="max-width:100%;width:100%" {new_attrs}>'
        return match.group(0)
    return re.sub(r'<img\s+([^>]+)>', replaceImg, content)


def _processImagePaths(content: str, file_path: str) -> str:
    """处理markdown中的图片路径"""
    def replaceImage(match):
        alt_text = match.group(1)
        src = match.group(2)
        title = match.group(3) if match.group(3) else ''
        
        if _isRemoteUrl(src):
            return match.group(0)
        if file_path:
            new_src = _imageToDataUri(file_path, src)
            if new_src is None:
                return match.group(0)
            if title:
                return f'![{alt_text}]({new_src} "{title}")'
            return f'![{alt_text}]({new_src})'
        
        return match.group(0)
    
    pattern = r'!\[([^\]]*)\]\(([^)]+)(?:\s+"([^"]*)")?\)'
    return re.sub(pattern, replaceImage, content)


def _processRelativeLinks(content: str, file_path: str) -> str:
    """处理相对路径链接"""
    if not file_path:
        return content
    
    try:
        md_dir = Path(file_path).parent.resolve()
        
        def replaceLink(match):
            link_text = match.group(1)
            href = match.group(2)
            title = match.group(3) if match.group(3) else ''
            
            if href.startswith(('http://', 'https://', 'mailto:', '#')):
                return match.group(0)
            
            target_path = (md_dir / href).resolve()
            
            if target_path.exists() and target_path.suffix.lower() in ['.md', '.markdown']:
                href = f"file:///{str(target_path).replace(os.sep, '/')}"
            
            if title:
                return f'[{link_text}]({href} "{title}")'
            return f'[{link_text}]({href})'
        
        pattern = r'\[([^\]]+)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)'
        return re.sub(pattern, replaceLink, content)
    except Exception:
        logger.exception("链接处理失败")
        return content


def _enhanceHtml(html_body: Optional[str]) -> str:
    """增强HTML功能"""
    if html_body is None:
        return ""
    html_body = re.sub(
        r'<pre><code class="language-(\w+)">',
        r'<pre><code class="language-\1 hljs">',
        html_body
    )
    
    html_body = re.sub(
        r'<pre><code>',
        r'<pre><code class="hljs">',
        html_body
    )
    
    return html_body


def renderMarkdown(content: str, file_path: str = None) -> Optional[str]:
    """将markdown内容渲染为HTML
    
    Args:
        content: markdown文本内容
        file_path: 源文件路径（用于处理相对路径）
    """
    try:
        _renderer.reset()
        content = _processImagePaths(content, file_path)
        content = _processHtmlImgTags(content, file_path)
        content = _processRelativeLinks(content, file_path)
        html_body = _renderer.convert(content)
        if html_body is None:
            return None
        html_body = _enhanceHtml(html_body)
        return _buildFullHtml(html_body)
    except Exception:
        logger.exception("Markdown渲染失败")
        return None


def renderForView(content: str, file_path: str = None) -> tuple[Optional[str], bool]:
    """渲染markdown用于编辑器视图模式
    
    Args:
        content: markdown文本内容
        file_path: 源文件路径
    
    Returns:
        (html, success): 渲染后的HTML和是否成功
    """
    if not content:
        return None, False
    
    html = renderMarkdown(content, file_path)
    if html:
        return html, True
    
    return None, False