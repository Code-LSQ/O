import os
import json
import random
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, ClassVar

import requests

from src.util import logger, EXTENSION, imageBase64, fileTree

_session = requests.Session()


def _resolveContentImage(content_parts: List[Dict]):
    """移除 content 数组中的图片，仅保留纯文本"""
    i = 0
    while i < len(content_parts):
        part = content_parts[i]
        if not isinstance(part, dict):
            i += 1
            continue
        if part.get("type") == "image_url":
            content_parts.pop(i)
            continue
        i += 1

def resolveImageUrls(messages: List[Dict]) -> List[Dict]:
    """遍历消息列表，移除 content 中的图片，仅保留纯文本"""
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            _resolveContentImage(content)
    return messages

def _getProfile(settings) -> dict:
    """获取当前激活的配置"""
    active_profile = settings.get("active", "")
    profiles = settings.get("profiles", {})
    if not profiles:
        return {}
    if not active_profile or active_profile not in profiles:
        active_profile = next(iter(profiles), "")
    return profiles.get(active_profile, {})

class AIBaseAdapter(ABC):
    """AI适配器基类
    
    所有AI服务适配器都需要继承此类并实现抽象方法。
    负责处理不同AI服务API的差异，提供统一的接口。
    
    子类需要实现的方法：
    - getApiUrl(): 获取API URL
    - getHeaders(): 获取HTTP请求头
    - buildChatRequest(): 构建聊天请求体
    - parseChatResponse(): 解析聊天响应
    - getModelListUrl(): 获取模型列表URL
    - parseModelsResponse(): 解析模型列表响应
    """
    
    _requires_api_key: bool = True

    def __init__(self, config, api_key: Optional[str] = None, api_url: Optional[str] = None):
        self.config = config
        self._override_api_key = api_key
        self._override_api_url = api_url
    
    def _getProfileUrl(self, default_url: str) -> str:
        profile = _getProfile(self.config)
        return profile.get("api_url", "") or default_url
    
    @abstractmethod
    def getApiUrl(self, model: str = "") -> str:
        """获取API URL"""
        pass
    
    @abstractmethod
    def getHeaders(self, api_key: str) -> Dict[str, str]:
        """获取请求头"""
        pass
    
    @abstractmethod
    def buildChatRequest(self, model: str, messages: List[Dict], temperature: float, max_tokens: int) -> Dict[str, Any]:
        """构建聊天请求体"""
        pass
    
    @abstractmethod
    def parseChatResponse(self, response: requests.Response) -> str:
        """解析聊天响应"""
        pass
    
    @abstractmethod
    def getModelListUrl(self) -> str:
        """获取模型列表 URL"""
        pass
    
    @abstractmethod
    def parseModelsResponse(self, response: requests.Response) -> List[str]:
        """解析模型列表响应"""
        pass
    
    def parseUsage(self, response_data: dict) -> tuple[int, int]:
        """从响应数据中提取 (input_tokens, output_tokens)，默认返回 (0,0)"""
        return 0, 0
    
    def _buildStreamOptions(self) -> dict:
        """构建流式的额外请求参数，默认不添加"""
        return {}
    
    @staticmethod
    def _extractError(response: requests.Response, prefix: str) -> str:
        """从非 200 响应中提取错误消息"""
        error_msg = f"{prefix}: {response.status_code}"
        try:
            error_data = response.json()
            if "error" in error_data and "message" in error_data["error"]:
                error_msg += f" - {error_data['error']['message']}"
            elif "error" in error_data and isinstance(error_data["error"], str):
                error_msg += f" - {error_data['error']}"
        except Exception:
            error_msg += f" - {response.text}"
        return error_msg
    
    def _validateRequest(self, api_key: str, api_url: str):
        if self._requires_api_key and not api_key:
            raise AIError("API Key未设置")
        if not api_url:
            raise AIError("API URL未设置")
    
    def _doRequest(self, url: str, headers: dict, json_data: dict = None, timeout: int = 60, stream: bool = False, method: str = 'POST'):
        try:
            if method == 'GET':
                response = _session.get(url, headers=headers, timeout=timeout)
            else:
                response = _session.post(url, headers=headers, json=json_data, timeout=timeout, stream=stream)
            if response.status_code != 200:
                error_msg = self._extractError(response, "API请求失败")
                logger.error(f"AI API错误: {error_msg}")
                raise AIError(error_msg)
            return response
        except AIError:
            raise
        except requests.exceptions.Timeout:
            raise AIError("请求超时，请检查网络连接")
        except requests.exceptions.ConnectionError:
            raise AIError("网络连接错误")
        except Exception as e:
            raise AIError(f"未知错误: {str(e)}")
    
    def chat(self, messages: List[Dict], model: str, temperature: float = 0.7, max_tokens: int = 2000) -> tuple[str, int, int]:
        api_key = self._getApiKey()
        api_url = self.getApiUrl(model)
        self._validateRequest(api_key, api_url)
        
        data = self.buildChatRequest(model, messages, temperature, max_tokens)
        headers = self.getHeaders(api_key)
        
        try:
            response = self._doRequest(api_url, headers, json_data=data, timeout=60)
            logger.info(f"AI API请求: model={model}, status={response.status_code}")
            resp_data = response.json()
            text = self.parseChatResponse(response)
            in_t, out_t = self.parseUsage(resp_data)
            return text, in_t, out_t
        except json.JSONDecodeError:
            raise AIError("API返回格式错误")
        except KeyError as e:
            raise AIError(f"API返回数据缺少必要字段: {e}")
    
    def getModels(self) -> List[str]:
        api_key = self._getApiKey()
        api_url = self.getApiUrl()
        self._validateRequest(api_key, api_url)
        
        headers = self.getHeaders(api_key)
        
        try:
            models_url = self.getModelListUrl()
            response = self._doRequest(models_url, headers, timeout=30, method='GET')
            logger.info(f"获取模型列表: status={response.status_code}")
            return self.parseModelsResponse(response)
        except AIError:
            raise
        except Exception as e:
            raise AIError(f"获取模型列表失败: {str(e)}")
    
    def _getApiKey(self) -> str:
        """获取API Key"""
        if self._override_api_key:
            return str(self._override_api_key).strip()
        profile = _getProfile(self.config)
        key = profile.get("api_key")
        if key:
            return str(key).strip()
        return ""

    def testConnection(self) -> bool:
        """测试API连接"""
        try:
            self.getModels()
            return True
        except Exception:
            return False

    def streamChat(self, messages: List[Dict], model: str, callback, temperature: float = 0.7, max_tokens: int = 2000) -> tuple[int, int]:
        api_key = self._getApiKey()
        api_url = self.getApiUrl(model)
        self._validateRequest(api_key, api_url)
        
        data = self.buildChatRequest(model, messages, temperature, max_tokens)
        data["stream"] = True
        data.update(self._buildStreamOptions())
        headers = self.getHeaders(api_key)
        
        response = self._doRequest(api_url, headers, json_data=data, timeout=120, stream=True)
        logger.info(f"AI流式请求: model={model}, status={response.status_code}")
        
        _, in_t, out_t = self.parseStreamResponse(response, callback)
        return in_t, out_t
    
    @staticmethod
    def _extractStreamChunk(obj: dict) -> str:
        """从 SSE 响应对象中提取 content chunk"""
        choices = obj.get('choices')
        if not choices or not choices[0]:
            return None
        delta = choices[0].get('delta', {})
        chunk = delta.get('content')
        return chunk if chunk is not None else None

    def parseStreamResponse(self, response: requests.Response, callback) -> tuple[str, int, int]:
        """解析流式响应，子类可覆盖
        Returns:
            (完整内容, input_tokens, output_tokens)
        """
        content = ""
        last_data = None
        for line in response.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8") if isinstance(line, bytes) else line
            if line.startswith('data: '):
                data = line[6:]
                if data.strip() == '[DONE]':
                    break
                try:
                    obj = json.loads(data)
                    last_data = obj
                    chunk = self._extractStreamChunk(obj)
                    if chunk is not None:
                        content += chunk
                        callback(chunk)
                except json.JSONDecodeError:
                    continue
        in_t, out_t = self.parseUsage(last_data) if last_data else (0, 0)
        return content, in_t, out_t

class OpenAIAdapter(AIBaseAdapter):
    """OpenAI兼容适配器（适用于 OpenAI、DeepSeek、OpenRouter 等）

    接口格式：
    POST {base_url}/chat/completions
    Authorization: Bearer {api_key}
    body: {"model", "messages", "temperature", "max_tokens", "stream"}

    模型列表：
    GET {base_url}/models

    大多数 AI API 采用此格式，是所有兼容适配器的基类"""
    
    def getApiUrl(self, model: str = "") -> str:
        if self._override_api_url:
            return f"{str(self._override_api_url).strip().rstrip('/')}/chat/completions"
        url = self._getProfileUrl("https://api.deepseek.com")
        return f"{str(url).strip().rstrip('/')}/chat/completions"

    def getHeaders(self, api_key: str) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    
    def buildChatRequest(self, model: str, messages: List[Dict], temperature: float, max_tokens: int) -> Dict[str, Any]:
        return {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
    
    def _buildStreamOptions(self) -> dict:
        return {"stream_options": {"include_usage": True}}
    
    def parseChatResponse(self, response: requests.Response) -> str:
        result = response.json()
        if "choices" not in result or len(result["choices"]) == 0:
            raise AIError("API返回格式错误")
        return result["choices"][0]["message"]["content"]
    
    def parseUsage(self, response_data: dict) -> tuple[int, int]:
        usage = response_data.get("usage", {})
        return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    
    def getModelListUrl(self) -> str:
        url = self._getProfileUrl("https://api.deepseek.com")
        return f"{str(url).strip().rstrip('/')}/models"
    
    def parseModelsResponse(self, response: requests.Response) -> List[str]:
        result = response.json()
        if "data" not in result:
            raise AIError("API返回格式错误")
        
        models = []
        for model_data in result["data"]:
            if "id" in model_data:
                models.append(model_data["id"])
        return sorted(models)

class OllamaAdapter(AIBaseAdapter):
    """Ollama 本地模型适配器，差异：无需 API Key；接口路径 /api/chat；响应为 message.content 而非 choices"""
    
    _requires_api_key: bool = False
    
    def getApiUrl(self, model: str = "") -> str:
        url = self._getProfileUrl("http://127.0.0.1:11434")
        return f"{str(url).strip().rstrip('/')}/api/chat"
    
    def getHeaders(self, api_key: str) -> Dict[str, str]:
        return {
            "Content-Type": "application/json"
        }
    
    def buildChatRequest(self, model: str, messages: List[Dict], temperature: float, max_tokens: int) -> Dict[str, Any]:
        ollama_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                texts = []
                images = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        texts.append(part.get("text", ""))
                    elif isinstance(part, dict) and part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:image"):
                            _, b64 = url.split("base64,", 1)
                            images.append(b64)
                msg_dict = {"role": role, "content": "\n".join(texts)}
                if images:
                    msg_dict["images"] = images
                ollama_messages.append(msg_dict)
            else:
                ollama_messages.append({"role": role, "content": content})

        return {
            "model": model,
            "messages": ollama_messages,
            "temperature": temperature,
            "stream": False,
            "options": {
                "num_predict": max_tokens
            },
        }
    
    def parseChatResponse(self, response: requests.Response) -> str:
        result = response.json()
        if "message" not in result:
            raise AIError("API返回格式错误")
        return result["message"].get("content", "")

    def parseUsage(self, response_data: dict) -> tuple[int, int]:
        return response_data.get("prompt_eval_count", 0), response_data.get("eval_count", 0)
    
    def parseStreamResponse(self, response: requests.Response, callback) -> tuple[str, int, int]:
        content = ""
        last_data = None
        for line in response.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8") if isinstance(line, bytes) else line
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            last_data = obj
            if "message" in obj and "content" in obj["message"]:
                chunk = obj["message"]["content"]
                if chunk:
                    content += chunk
                    callback(chunk)
            if obj.get("done"):
                break
        in_t = last_data.get("prompt_eval_count", 0) if last_data else 0
        out_t = last_data.get("eval_count", 0) if last_data else 0
        return content, in_t, out_t
    
    def getModelListUrl(self) -> str:
        url = self._getProfileUrl("http://127.0.0.1:11434")
        return f"{str(url).strip().rstrip('/')}/api/tags"
    
    def parseModelsResponse(self, response: requests.Response) -> List[str]:
        result = response.json()
        if "models" not in result:
            raise AIError("API返回格式错误")
        
        models = []
        for model_data in result["models"]:
            if "name" in model_data:
                models.append(model_data["name"])
        return sorted(models)

class ClaudeAdapter(AIBaseAdapter):
    """Claude (Anthropic) 适配器，差异：认证用 x-api-key，不使用 Authorization Bearer；system 消息不混入 messages"""
    
    ANTHROPIC_VERSION = "2023-06-01"
    
    def getApiUrl(self, model: str = "") -> str:
        url = self._getProfileUrl("https://api.anthropic.com")
        return f"{str(url).strip().rstrip('/')}/v1/messages"
    
    def getHeaders(self, api_key: str) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": self.ANTHROPIC_VERSION
        }
    
    def buildChatRequest(self, model: str, messages: List[Dict], temperature: float, max_tokens: int) -> Dict[str, Any]:
        system_parts = []
        anthropic_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                content = msg.get("content", "")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            system_parts.append(part.get("text", ""))
                elif content:
                    system_parts.append(str(content))
                continue
            if role == "assistant":
                role = "assistant"
            else:
                role = "user"

            content = msg.get("content", "")
            if isinstance(content, list):
                claude_parts = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        claude_parts.append(part)
                    elif part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:image"):
                            mime = url.split(";")[0][5:]
                            data = url.split("base64,")[1]
                            claude_parts.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime,
                                    "data": data
                                }
                            })
                anthropic_messages.append({"role": role, "content": claude_parts})
            else:
                anthropic_messages.append({"role": role, "content": content})

        request = {
            "model": model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        if system_parts:
            request["system"] = "\n".join(system_parts)
        return request
    
    def parseChatResponse(self, response: requests.Response) -> str:
        result = response.json()
        if "content" not in result:
            raise AIError("API返回格式错误")
        
        content = result["content"]
        if isinstance(content, list) and len(content) > 0:
            return content[0].get("text", "")
        return str(content)
    
    def parseUsage(self, response_data: dict) -> tuple[int, int]:
        usage = response_data.get("usage", {})
        return usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    
    def getModelListUrl(self) -> str:
        url = self._getProfileUrl("https://api.anthropic.com")
        return f"{str(url).strip().rstrip('/')}/v1/models"
    
    def parseModelsResponse(self, response: requests.Response) -> List[str]:
        result = response.json()
        if "data" not in result:
            raise AIError("API返回格式错误")
        
        models = []
        for model_data in result["data"]:
            if "id" in model_data:
                models.append(model_data["id"])
        return sorted(models)

class GeminiAdapter(AIBaseAdapter):
    """Gemini (Google) 适配器，差异：API Key 在 URL query 参数中；角色为 assistant 而不是 model；请求体用 contents/parts 结构"""
    
    def getApiUrl(self, model: str = "") -> str:
        url = self._getProfileUrl("https://generativelanguage.googleapis.com")
        profile = _getProfile(self.config)
        if not model:
            model = profile.get("model", "") or ""
        api_key = self._getApiKey()
        return f"{str(url).strip().rstrip('/')}/v1beta/models/{model}:generateContent?key={api_key}"
    
    def getHeaders(self, api_key: str) -> Dict[str, str]:
        return {
            "Content-Type": "application/json"
        }
    
    def buildChatRequest(self, model: str, messages: List[Dict], temperature: float, max_tokens: int) -> Dict[str, Any]:
        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                continue
            if role == "assistant":
                role = "model"
            else:
                role = "user"

            content = msg.get("content", "")
            if isinstance(content, list):
                parts = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        parts.append({"text": part.get("text", "")})
                    elif part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:image"):
                            mime = url.split(";")[0][5:]
                            data = url.split("base64,")[1]
                            parts.append({"inlineData": {"mimeType": mime, "data": data}})
                contents.append({"role": role, "parts": parts})
            else:
                contents.append({"role": role, "parts": [{"text": content}]})

        return {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            }
        }
    
    def parseChatResponse(self, response: requests.Response) -> str:
        result = response.json()
        if "candidates" not in result or len(result["candidates"]) == 0:
            raise AIError("API返回格式错误")
        
        candidate = result["candidates"][0]
        if "content" not in candidate:
            raise AIError("API返回格式错误")
        
        content = candidate["content"]
        if "parts" not in content or len(content["parts"]) == 0:
            raise AIError("API返回格式错误")
        
        return content["parts"][0].get("text", "")
    
    def parseUsage(self, response_data: dict) -> tuple[int, int]:
        meta = response_data.get("usageMetadata", {})
        return meta.get("promptTokenCount", 0), meta.get("candidatesTokenCount", 0)
    
    def getModelListUrl(self) -> str:
        url = self._getProfileUrl("https://generativelanguage.googleapis.com")
        api_key = self._getApiKey()
        return f"{str(url).strip().rstrip('/')}/v1beta/models?key={api_key}"
    
    def parseModelsResponse(self, response: requests.Response) -> List[str]:
        result = response.json()
        if "models" not in result:
            raise AIError("API返回格式错误")
        
        models = []
        for model_data in result["models"]:
            if "name" in model_data:
                name = model_data["name"]
                if name.startswith("models/"):
                    name = name[7:]
                models.append(name)
        return sorted(models)

AI_ADAPTER = [
    ("Claude", ClaudeAdapter, "https://api.anthropic.com/v1/messages"),
    ("DeepSeek", OpenAIAdapter, "https://api.deepseek.com"),
    ("Gemini", GeminiAdapter, "https://generativelanguage.googleapis.com"),
    ("Ollama", OllamaAdapter, "http://127.0.0.1:11434"),
    ("OpenAI", OpenAIAdapter, "https://api.openai.com"),
    ("OpenRouter", OpenAIAdapter, "https://openrouter.ai/api/v1"),
    ("New API", OpenAIAdapter, "https://newapi.pro/v1"),
    ("阿里云百炼大模型", OpenAIAdapter, "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("字节跳动火山引擎", OpenAIAdapter, "https://ark.cn-beijing.volces.com/api/v3"),
    ("腾讯混元大模型", OpenAIAdapter, "https://api.hunyuan.cloud.tencent.com/v1"),
    ("硅基流动", OpenAIAdapter, "https://api.siliconflow.cn"),
    ("自定义", None, ""),
]

class AIError(Exception):
    """AI相关错误"""
    pass

def getAdapterEndpoint(endpoint_name: str, config, api_key: Optional[str] = None, api_url: Optional[str] = None) -> AIBaseAdapter:
    """根据端点名称获取对应的适配器"""
    for name, cls, url in AI_ADAPTER:
        if name == endpoint_name and cls is not None:
            return cls(config, api_key=api_key, api_url=api_url)
    return OpenAIAdapter(config, api_key=api_key, api_url=api_url)


def getAdapterUrl(api_url: str, config, api_key: Optional[str] = None) -> AIBaseAdapter:
    """根据 API URL 推测并返回对应的适配器"""
    url_lower = api_url.lower() if api_url else ""
    
    if "anthropic" in url_lower:
        return ClaudeAdapter(config, api_key=api_key, api_url=api_url)
    elif "generativelanguage" in url_lower or "googleapis" in url_lower:
        return GeminiAdapter(config, api_key=api_key, api_url=api_url)
    elif "ollama" in url_lower or "127.0.0.1:11434" in url_lower:
        return OllamaAdapter(config, api_key=api_key, api_url=api_url)
    else:
        return OpenAIAdapter(config, api_key=api_key, api_url=api_url)


class AIClient:
    """AI客户端"""

    _lb_failures: ClassVar[Dict[str, int]] = {}
    _lb_disabled: ClassVar[Dict[str, bool]] = {}
    _LB_THRESHOLD = 3

    def __init__(self, config=None, profile_name=None):
        self.config = config or {}
        self._adapter = None
        self._active_profile = None
        if profile_name:
            self._switchProfile(profile_name)

    def _switchProfile(self, name):
        """切换到指定名称的AI配置"""
        profiles = self.config.get("profiles", {})
        if name not in profiles:
            profile_names = list(profiles.keys())
            if profile_names:
                name = profile_names[0]
            else:
                logger.warning(f"配置 [{name}] 不存在，使用当前激活的配置")
                return
        self._active_profile = name

    def _getProfile(self) -> dict:
        profiles = self.config.get("profiles", {})
        if not profiles:
            return {}
        active = self._active_profile or self.config.get("active", "")
        if not active or active not in profiles:
            active = next(iter(profiles), "")
        return profiles.get(active, {})

    def _getEndpointName(self) -> str:
        """获取当前选中的端点名称"""
        profile = self._getProfile()
        api_url = profile.get("api_url", "") or "https://api.deepseek.com"
        
        for name, cls, url in AI_ADAPTER:
            if url == api_url:
                return name
        
        if not api_url or api_url == "https://api.deepseek.com":
            return "DeepSeek"
        
        if "custom" in str(api_url).lower() or not api_url:
            return "自定义"
        
        return "DeepSeek"
    
    @staticmethod
    def _endpointNameFromUrl(api_url: str) -> str:
        for name, cls, url in AI_ADAPTER:
            if url == api_url:
                return name
        return "自定义" if "custom" in str(api_url).lower() else "DeepSeek"
    
    def _lbRecord(self, name: str, success: bool):
        # 连续三次失败时禁用
        if success:
            self.__class__._lb_failures.pop(name, None)
            self.__class__._lb_disabled.pop(name, None)
        else:
            cnt = self.__class__._lb_failures.get(name, 0) + 1
            self.__class__._lb_failures[name] = cnt
            if cnt >= self._LB_THRESHOLD:
                self.__class__._lb_disabled[name] = True
                logger.warning(f"配置 [{name}] {cnt}次连续失败，已禁用")
    
    def _lbPickGroups(self) -> Optional[List[Dict[str, Any]]]:
        """按优先级分组返回 [{name: cfg, ...}, ...]，保留完整配置信息的权重"""
        lb = self.config.get("load_balance", {})
        if not lb.get("enabled"):
            return None
        pc = lb.get("profiles", {})
        if not pc:
            return None

        groups = {}
        for name, cfg in pc.items():
            pri = cfg.get("priority", 1)
            if pri == 0 or name in self.__class__._lb_disabled:
                continue
            groups.setdefault(pri, {})[name] = cfg

        if not groups:
            return None

        return [groups[pri] for pri in sorted(groups)]
    
    def _getAdapter(self):
        """获取适配器实例"""
        if self._adapter is None:
            endpoint_name = self._getEndpointName()
            self._adapter = getAdapterEndpoint(endpoint_name, self.config)
        return self._adapter
    
    def getModel(self) -> str:
        """获取模型"""
        profile = self._getProfile()
        model = profile.get("model", "") or "deepseek-chat"
        return str(model).strip()
    
    def getTemperature(self) -> float:
        """获取温度"""
        profile = self._getProfile()
        temp = profile.get("temperature", 0.7)
        if temp is None:
            return 0.7
        return float(temp)
    
    def getMaxTokens(self) -> int:
        """获取最大token数"""
        profile = self._getProfile()
        tokens = profile.get("max_tokens", 2000)
        if tokens is None:
            return 2000
        return int(tokens)
    
    def getPrompt(self, name: str) -> Optional[str]:
        """根据名称获取提示词内容"""
        return self.config.get("prompts", {}).get(name)
    
    def _extractUserMessage(self, messages: List[Dict[str, str]]) -> str:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                    return "\n".join(texts) if texts else ""
                return str(content)
        return ""
    
    def _buildPromptContent(self, prompt_content: str, user_message: str) -> str:
        if "{request}" in prompt_content:
            return prompt_content.replace("{request}", user_message)
        return prompt_content
    
    def _prepareMessages(self, messages: List[Dict[str, str]], prompt_name: Optional[str] = None) -> List[Dict]:
        request_messages = []

        system_prompt = self.getPrompt("系统提示词")
        if system_prompt:
            request_messages.append({"role": "system", "content": system_prompt})

        prompt_content = None
        if prompt_name:
            prompt_content = self.getPrompt(prompt_name)
            if prompt_content:
                final_prompt = self._buildPromptContent(prompt_content, self._extractUserMessage(messages))
                request_messages.append({"role": "system", "content": final_prompt})

        has_request_placeholder = "{request}" in (prompt_content or "")
        if has_request_placeholder:
            last_user_idx = -1
            for i, msg in enumerate(messages):
                if msg.get("role") == "user":
                    last_user_idx = i
            for i, msg in enumerate(messages):
                if i != last_user_idx:
                    request_messages.append(msg)
        else:
            request_messages.extend(messages)

        resolveImageUrls(request_messages)
        return request_messages
    
    def _lbExecute(self, request_messages, model, temperature, max_tokens, callback=None):
        groups = self._lbPickGroups()
        if groups:
            last_err = None
            for group_dict in groups:
                remaining = dict(group_dict)
                while remaining:
                    total_w = sum(cfg.get("weight", 1) for cfg in remaining.values())
                    r = random.random() * total_w
                    cum = 0
                    selected = None
                    for name, cfg in remaining.items():
                        cum += cfg.get("weight", 1)
                        if r <= cum:
                            selected = name
                            break

                    profile = self.config.get("profiles", {}).get(selected, {})
                    if not profile:
                        remaining.pop(selected, None)
                        continue

                    ep_name = self._endpointNameFromUrl(profile.get("api_url", ""))
                    adapter = getAdapterEndpoint(ep_name, self.config,
                                                    api_key=profile.get("api_key", ""),
                                                    api_url=profile.get("api_url", ""))
                    lb_model = profile.get("model", model)
                    lb_temp = profile.get("temperature", temperature)
                    lb_max_tokens = profile.get("max_tokens", max_tokens)
                    logger.info(f"负载均衡: 尝试 [{selected}] (model={lb_model})")
                    try:
                        if callback:
                            in_t, out_t = adapter.streamChat(messages=request_messages, model=lb_model, callback=callback, temperature=lb_temp, max_tokens=lb_max_tokens)
                            self._lbRecord(selected, True)
                            return in_t, out_t
                        else:
                            text, in_t, out_t = adapter.chat(messages=request_messages, model=lb_model, temperature=lb_temp, max_tokens=lb_max_tokens)
                            self._lbRecord(selected, True)
                            return text, in_t, out_t
                    except Exception as e:
                        logger.warning(f"负载均衡: [{selected}] 失败: {e}")
                        self._lbRecord(selected, False)
                        last_err = e
                        remaining.pop(selected, None)
            raise last_err or Exception("所有负载均衡配置均失败")

        adapter = self._getAdapter()
        if callback:
            return adapter.streamChat(messages=request_messages, model=model, callback=callback, temperature=temperature, max_tokens=max_tokens)
        else:
            return adapter.chat(messages=request_messages, model=model, temperature=temperature, max_tokens=max_tokens)
    
    def chat(self, messages: List[Dict[str, str]], prompt_name: Optional[str] = None) -> tuple[str, int, int]:
        model = self.getModel()
        request_messages = self._prepareMessages(messages, prompt_name)
        result = self._lbExecute(request_messages, model, self.getTemperature(), self.getMaxTokens())
        return result  # (text, in_t, out_t)
    
    def streamChat(self, messages: List[Dict[str, str]], callback, prompt_name: Optional[str] = None) -> tuple[int, int]:
        model = self.getModel()
        request_messages = self._prepareMessages(messages, prompt_name)
        result = self._lbExecute(request_messages, model, self.getTemperature(), self.getMaxTokens(), callback=callback)
        return result  # (in_t, out_t)
    
    def getModels(self) -> List[str]:
        """获取模型列表"""
        adapter = self._getAdapter()
        return adapter.getModels()
    
    def testConnection(self) -> bool:
        """测试API连接"""
        try:
            self.getModels()
            return True
        except Exception:
            return False

    _MAX_FILE_SIZE = 4 * 1024 * 1024
    _MAX_FOLDER_FILES = 50
    _MAX_FILE_CHARS = 10000

    def buildFileMessage(self, file_path: str) -> Optional[List[Dict]]:
        name = os.path.basename(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        size = os.path.getsize(file_path)

        if size > self._MAX_FILE_SIZE:
            logger.warning(f"文件过大已跳过: {name} ({size / 1024 / 1024:.1f}MB)")
            return None

        if ext in EXTENSION["IMAGE"]:
            img, mime = imageBase64(file_path)
            content = [
                {"type": "text", "text": f"请分析这张图片 ({name}):"},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img}"}}
            ]
            return [{"role": "user", "content": content}]
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return [{"role": "user", "content": f"文件: {name}\n\n{content}"}]

    def buildFolderMessage(self, folder_path: str) -> Optional[List[Dict]]:
        tree_lines = fileTree(Path(folder_path))
        tree_text = '\n'.join(tree_lines)

        folder_path = os.path.normpath(folder_path)
        text_files = []
        image_files = []

        for root, dirs, files in os.walk(folder_path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    continue
                if size > self._MAX_FILE_SIZE:
                    continue
                ext = os.path.splitext(fp)[1].lower()
                if ext in EXTENSION["IMAGE"]:
                    image_files.append(fp)
                else:
                    text_files.append(fp)

        if not text_files and not image_files:
            logger.warning(f"文件夹 {os.path.basename(folder_path)} 为空")
            return None

        total = len(text_files) + len(image_files)
        if total > self._MAX_FOLDER_FILES:
            logger.warning(f"文件数 {total} 超过限制 {self._MAX_FOLDER_FILES}，优先保留文本文件")
            surplus = total - self._MAX_FOLDER_FILES
            if len(image_files) >= surplus:
                image_files = image_files[:-surplus]
            else:
                remaining = surplus - len(image_files)
                image_files.clear()
                text_files = text_files[:-min(remaining, len(text_files))]

        content_parts = [
            {"type": "text", "text": f"项目文件结构：\n{tree_text}\n"},
            {"type": "text", "text": f"共 {len(text_files)} 个文本文件，{len(image_files)} 个图片文件：\n"}
        ]

        for fp in text_files:
            rel = os.path.relpath(fp, folder_path)
            name = rel.replace('\\', '/')
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                if len(content) > self._MAX_FILE_CHARS:
                    content_parts.append({"type": "text", "text": f"\n--- {name} ---\n[内容过长，仅显示前 {self._MAX_FILE_CHARS} 字符]\n{content[:self._MAX_FILE_CHARS]}\n...（共 {len(content)} 字符）"})
                else:
                    content_parts.append({"type": "text", "text": f"\n--- {name} ---\n{content}"})
            except Exception as e:
                content_parts.append({"type": "text", "text": f"\n--- {name} ---\n[读取失败: {e}]"})

        for fp in image_files:
            rel = os.path.relpath(fp, folder_path)
            name = rel.replace('\\', '/')
            try:
                data, mime = imageBase64(fp)
                content_parts.append({"type": "text", "text": f"\n--- {name} ---"})
                content_parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
            except Exception as e:
                content_parts.append({"type": "text", "text": f"\n--- {name} ---\n[图片加载失败: {e}]"})

        return [{"role": "user", "content": content_parts}]


def getAIClient(config, profile_name=None) -> AIClient:
    """获取AI客户端
    
    Args:
        config: AI 配置 dict（包含 profiles、prompts 等字段）
        profile_name: AI 配置名
    Returns:
        AIClient 实例
    """
    return AIClient(config, profile_name=profile_name)
