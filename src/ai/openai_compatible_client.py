"""
OpenAI-compatible API client.
"""
import json
import os
import sys
from typing import Dict, List, Optional


def find_project_root():
    cwd = os.getcwd()
    if os.path.exists(os.path.join(cwd, ".env")) or os.path.exists(os.path.join(cwd, "data")):
        return cwd

    current = cwd
    for _ in range(5):
        if (
            os.path.exists(os.path.join(current, ".env"))
            or os.path.exists(os.path.join(current, "data"))
            or os.path.exists(os.path.join(current, "logs"))
        ):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return cwd


project_root = find_project_root()
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import requests
from loguru import logger


class OpenAICompatibleClient:
    """通用 OpenAI Chat Completions 兼容客户端。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        default_timeout: int = 20,
    ):
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.default_timeout = default_timeout

    def chat_completion(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 8000,
        use_json_mode: bool = False,
        timeout: Optional[int] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        stream: bool = False,
    ) -> Dict:
        if not self.base_url:
            return {
                "success": False,
                "error": "missing_base_url",
                "error_message": "未配置 OpenAI-compatible API 端点",
            }

        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}

        actual_timeout = timeout if timeout is not None else self.default_timeout

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=actual_timeout,
                stream=stream,
            )
            response.raise_for_status()

            if stream:
                def generate():
                    for line in response.iter_lines():
                        if not line:
                            continue
                        line_str = line.decode("utf-8")
                        if not line_str.startswith("data: "):
                            continue
                        data_str = line_str[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            yield json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                return {"success": True, "stream": generate()}

            result = response.json()
            logger.debug(f"OpenAI-compatible API响应: {json.dumps(result, ensure_ascii=False)[:200]}")
            return {"success": True, "data": result}

        except requests.exceptions.Timeout:
            logger.warning(f"⏰ OpenAI-compatible API请求超时（{actual_timeout}秒）")
            return {
                "success": False,
                "error": "timeout",
                "error_message": f"请求超时（{actual_timeout}秒）",
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"OpenAI-compatible API请求失败: {e}")
            return {
                "success": False,
                "error": "request_failed",
                "error_message": str(e),
            }

    def chat(
        self,
        prompt: str,
        temperature: float = 0.5,
        timeout: Optional[int] = None,
        model: Optional[str] = None,
    ) -> Optional[str]:
        messages = [{"role": "user", "content": prompt}]
        result = self.chat_completion(messages, temperature=temperature, timeout=timeout, model=model)

        if result["success"]:
            try:
                return result["data"]["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                logger.error(f"解析 OpenAI-compatible 响应失败: {e}, 原始数据: {result['data']}")
                return None

        logger.error(f"OpenAI-compatible API调用失败: {result.get('error_message', result.get('error'))}")
        return None

    def chat_json(
        self,
        prompt: str,
        temperature: float = 0.5,
        timeout: Optional[int] = None,
        model: Optional[str] = None,
    ) -> Optional[Dict]:
        messages = [{"role": "user", "content": prompt}]
        result = self.chat_completion(
            messages,
            temperature=temperature,
            use_json_mode=True,
            timeout=timeout,
            model=model,
        )

        if result["success"]:
            try:
                content = result["data"]["choices"][0]["message"]["content"]
                return json.loads(content)
            except (KeyError, IndexError) as e:
                logger.error(f"解析 OpenAI-compatible 响应失败: {e}, 原始数据: {result['data']}")
                return None
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析失败: {e}, 内容: {content}")
                return None

        error_type = result.get("error", "unknown")
        logger.error(f"OpenAI-compatible API调用失败: {result.get('error_message', error_type)}")
        return {"error": error_type, "error_message": result.get("error_message", "")}

    def get_trading_advice(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.5,
        model: Optional[str] = None,
    ) -> Optional[str]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        result = self.chat_completion(messages, temperature=temperature, model=model)

        if result["success"]:
            try:
                return result["data"]["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                logger.error(f"解析响应失败: {e}")
                return None

        return None
