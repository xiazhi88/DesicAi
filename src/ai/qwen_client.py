"""
通义千问 (Qwen) API客户端
集成阿里云通义千问大模型API (OpenAI兼容)
"""
import sys
import os

def find_project_root():
    """
    查找实际的项目根目录，处理开发环境和wheel安装两种场景

    当通过wheel安装时，代码位于site-packages，但项目文件(.env, data/, logs/)
    位于用户的实际项目目录。此函数通过查找标记文件来定位正确的项目根目录。
    """
    # 优先使用当前工作目录（用户运行应用的位置）
    cwd = os.getcwd()
    if os.path.exists(os.path.join(cwd, '.env')) or \
       os.path.exists(os.path.join(cwd, 'data')):
        return cwd

    # 向上搜索父目录（最多5层）
    current = cwd
    for _ in range(5):
        if os.path.exists(os.path.join(current, '.env')) or \
           os.path.exists(os.path.join(current, 'data')) or \
           os.path.exists(os.path.join(current, 'logs')):
            return current
        parent = os.path.dirname(current)
        if parent == current:  # 已到达根目录
            break
        current = parent

    # 如果都没找到，使用当前工作目录作为后备
    return cwd

# 添加项目根目录到路径
project_root = find_project_root()
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import requests
import json
from typing import Dict, List, Optional
from loguru import logger




class QwenClient:
    """通义千问 (Qwen) API客户端"""

    def __init__(
        self,
        api_key: str,
        model: str = "qwen-plus",
        default_timeout: int = 20
    ):
        """
        初始化通义千问客户端

        Args:
            api_key: 阿里云 API Key (DashScope)
            model: 模型名称（默认: qwen-plus）
                  可选: qwen-plus, qwen-turbo, qwen-max, qwen-max-longcontext
            default_timeout: 默认超时时间（秒）
        """
        self.api_key = api_key
        self.model = model
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
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
        stream: bool = False
    ) -> Dict:
        """
        调用通义千问聊天补全API

        Args:
            messages: 消息列表 [{"role": "user/assistant/system", "content": "..."}]
            temperature: 温度参数 (0-2)
            max_tokens: 最大令牌数
            use_json_mode: 是否使用JSON模式
            timeout: 超时时间（秒），None则使用默认值
            session_id: 会话ID（用于token使用跟踪）
            model: 模型名称（可覆盖初始化时的默认模型）
            stream: 是否使用流式输出

        Returns:
            API响应（如果stream=True，则返回包含生成器的字典）
        """
        url = f"{self.base_url}/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        # 使用指定模型或默认模型
        model_name = model or self.model

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream
        }

        # 启用JSON模式
        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}

        # 使用传入的timeout或默认值
        actual_timeout = timeout if timeout is not None else self.default_timeout

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=actual_timeout, stream=stream)
            response.raise_for_status()

            # 流式输出
            if stream:
                def generate():
                    for line in response.iter_lines():
                        if line:
                            line_str = line.decode('utf-8')
                            if line_str.startswith('data: '):
                                data_str = line_str[6:]  # 去掉 "data: " 前缀
                                if data_str.strip() == '[DONE]':
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                    yield chunk
                                except json.JSONDecodeError:
                                    continue

                return {
                    'success': True,
                    'stream': generate()
                }

            # 非流式输出
            result = response.json()
            logger.debug(f"Qwen API响应: {json.dumps(result, ensure_ascii=False)[:200]}")

            return {
                'success': True,
                'data': result
            }

        except requests.exceptions.Timeout:
            logger.warning(f"⏰ Qwen API请求超时（{actual_timeout}秒）")
            return {
                'success': False,
                'error': 'timeout',
                'error_message': f'请求超时（{actual_timeout}秒）'
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"Qwen API请求失败: {e}")
            return {
                'success': False,
                'error': 'request_failed',
                'error_message': str(e)
            }

    def chat(
        self,
        prompt: str,
        temperature: float = 0.5,
        timeout: Optional[int] = None,
        model: Optional[str] = None
    ) -> Optional[str]:
        """
        简化的聊天接口（单轮对话）

        Args:
            prompt: 用户提示词
            temperature: 温度参数
            timeout: 超时时间（秒），None则使用默认值
            model: 模型名称（可覆盖默认模型）

        Returns:
            AI响应内容（字符串）
        """
        messages = [
            {"role": "user", "content": prompt}
        ]

        result = self.chat_completion(
            messages,
            temperature=temperature,
            timeout=timeout,
            model=model
        )

        if result['success']:
            try:
                content = result['data']['choices'][0]['message']['content']
                logger.debug(f"Qwen AI响应: {content[:200]}...")
                return content
            except (KeyError, IndexError) as e:
                logger.error(f"解析Qwen响应失败: {e}, 原始数据: {result['data']}")
                return None
        else:
            logger.error(f"Qwen API调用失败: {result.get('error_message', result.get('error'))}")
            return None

    def chat_json(
        self,
        prompt: str,
        temperature: float = 0.5,
        timeout: Optional[int] = None,
        model: Optional[str] = None
    ) -> Optional[Dict]:
        """
        JSON模式聊天接口（返回JSON对象）

        Args:
            prompt: 用户提示词（应包含JSON格式要求）
            temperature: 温度参数
            timeout: 超时时间（秒），None则使用默认值
            model: 模型名称（可覆盖默认模型）

        Returns:
            解析后的JSON字典，超时返回特殊标记 {'error': 'timeout'}
        """
        messages = [
            {"role": "user", "content": prompt}
        ]

        result = self.chat_completion(
            messages,
            temperature=temperature,
            use_json_mode=True,
            timeout=timeout,
            model=model
        )

        if result['success']:
            try:
                content = result['data']['choices'][0]['message']['content']
                logger.debug(f"Qwen AI JSON响应: {content[:200]}...")
                # 解析JSON
                return json.loads(content)
            except (KeyError, IndexError) as e:
                logger.error(f"解析Qwen响应失败: {e}, 原始数据: {result['data']}")
                return None
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析失败: {e}, 内容: {content}")
                return None
        else:
            # 返回包含错误信息的字典
            error_type = result.get('error', 'unknown')
            logger.error(f"Qwen API调用失败: {result.get('error_message', error_type)}")
            return {'error': error_type, 'error_message': result.get('error_message', '')}

    def get_trading_advice(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.5,
        model: Optional[str] = None
    ) -> Optional[str]:
        """
        获取交易建议（支持系统提示词）

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            temperature: 温度参数（较低值更保守）
            model: 模型名称（可覆盖默认模型）

        Returns:
            AI响应内容
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        result = self.chat_completion(
            messages,
            temperature=temperature,
            model=model
        )

        if result['success']:
            try:
                content = result['data']['choices'][0]['message']['content']
                return content
            except (KeyError, IndexError) as e:
                logger.error(f"解析响应失败: {e}")
                return None
        else:
            return None


if __name__ == '__main__':
    # 测试
    from dotenv import load_dotenv

    # 加载环境变量
    load_dotenv()

    # 从.env读取Qwen配置
    qwen_api_key = os.getenv('QWEN_API_KEY')

    if qwen_api_key:
        client = QwenClient(
            api_key=qwen_api_key,
            model="qwen-plus"
        )

        # 简单测试
        response = client.get_trading_advice(
            system_prompt="你是一个专业的量化交易助手。",
            user_prompt="BTC当前价格50000，24小时涨幅3%，应该买入还是持有？给出简短建议。"
        )

        if response:
            print("AI响应:", response)
        else:
            print("API调用失败")

        # 测试JSON模式
        json_response = client.chat_json(
            prompt='分析BTC走势并以JSON格式返回：{"signal": "buy/sell/hold", "reason": "原因"}'
        )
        if json_response:
            print("\nJSON响应:", json.dumps(json_response, ensure_ascii=False, indent=2))
    else:
        print("请配置Qwen API密钥（环境变量: QWEN_API_KEY）")
