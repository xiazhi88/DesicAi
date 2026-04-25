"""
豆包API客户端
集成字节跳动豆包大模型API
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
import time
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger

try:
    from volcengine.auth.SignerV4 import SignerV4
    from volcengine.base.Request import Request
    from volcengine.Credentials import Credentials
    VOLCENGINE_SDK_AVAILABLE = True
except ImportError:
    logger.warning("火山引擎SDK未安装，请运行: pip install volcengine")
    VOLCENGINE_SDK_AVAILABLE = False




class DoubaoClient:
    """豆包API客户端"""

    def __init__(
        self,
        api_key: str,
        endpoint_id: str,
        model: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        default_timeout: int = 10
    ):
        """
        初始化豆包客户端

        Args:
            api_key: 豆包API密钥（用于聊天API的Bearer Token）
            endpoint_id: 端点ID（推理接入点）
            model: 模型名称（可选，如：doubao-seed-1-6-lite-251015, doubao-seed-1-6-flash-250828,
                   doubao-seed-1-6-thinking-250715, doubao-seed-1-6-251015）
                   如果不提供，使用endpoint_id作为模型标识
            access_key: 火山引擎AccessKeyId（用于Usage API签名鉴权）
            secret_access_key: 火山引擎SecretAccessKey（用于Usage API签名鉴权）
            default_timeout: 默认超时时间（秒）
        """
        self.api_key = api_key
        self.endpoint_id = endpoint_id
        self.model = model or endpoint_id  # 如果提供了model则使用model，否则使用endpoint_id
        self.access_key = access_key
        self.secret_access_key = secret_access_key
        self.base_url = "https://ark.cn-beijing.volces.com/api/v3"
        self.usage_api_url = "https://open.volcengineapi.com"
        self.default_timeout = default_timeout



    def chat_completion(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        use_json_mode: bool = False,
        timeout: Optional[int] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        stream: bool = False
    ) -> Dict:
        """
        调用豆包聊天补全API

        Args:
            messages: 消息列表
            temperature: 温度参数
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
        print(headers)

        # 使用指定模型或默认模型
        model_name = model or self.model

        payload = {
            "model": self.endpoint_id,  # endpoint_id 是推理接入点
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream
        }
        
        

        payload["thinking"] = {"type": "disabled"} # enabled disabled
        if payload['thinking']['type']=='disabled':
            payload['reasoning_effort']='minimal'
        else:
            payload['reasoning_effort']='low'
        # 启用JSON模式
        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        #print(payload)

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
            logger.debug(f"豆包API响应: {json.dumps(result, ensure_ascii=False)[:200]}")

            return {
                'success': True,
                'data': result
            }

        except requests.exceptions.Timeout:
            logger.warning(f"⏰ 豆包API请求超时（{actual_timeout}秒）")


            return {
                'success': False,
                'error': 'timeout',
                'error_message': f'请求超时（{actual_timeout}秒）'
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"豆包API请求失败: {e}")
            print(url, headers, payload, actual_timeout, stream)


            return {
                'success': False,
                'error': 'request_failed',
                'error_message': str(e)
            }

    def chat(self, prompt: str, temperature: float = 0.5, timeout: Optional[int] = None) -> Optional[str]:
        """
        简化的聊天接口（单轮对话）

        Args:
            prompt: 用户提示词
            temperature: 温度参数
            timeout: 超时时间（秒），None则使用默认值

        Returns:
            AI响应内容（字符串）
        """
        messages = [
            {"role": "user", "content": prompt}
        ]

        result = self.chat_completion(messages, temperature=temperature, timeout=timeout)

        if result['success']:
            try:
                content = result['data']['choices'][0]['message']['content']
                logger.debug(f"豆包AI响应: {content[:200]}...")
                return content
            except (KeyError, IndexError) as e:
                logger.error(f"解析豆包响应失败: {e}, 原始数据: {result['data']}")
                return None
        else:
            logger.error(f"豆包API调用失败: {result.get('error_message', result.get('error'))}")
            return None

    def chat_json(self, prompt: str, temperature: float = 0.5, timeout: Optional[int] = None) -> Optional[Dict]:
        """
        JSON模式聊天接口（返回JSON对象）

        Args:
            prompt: 用户提示词（应包含JSON格式要求）
            temperature: 温度参数
            timeout: 超时时间（秒），None则使用默认值

        Returns:
            解析后的JSON字典，超时返回特殊标记 {'error': 'timeout'}
        """
        messages = [
            {"role": "user", "content": prompt}
        ]

        result = self.chat_completion(messages, temperature=temperature, use_json_mode=True, timeout=timeout)

        if result['success']:
            try:
                content = result['data']['choices'][0]['message']['content']
                logger.debug(f"豆包AI JSON响应: {content[:200]}...")
                # 解析JSON
                return json.loads(content)
            except (KeyError, IndexError) as e:
                logger.error(f"解析豆包响应失败: {e}, 原始数据: {result['data']}")
                return None
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析失败: {e}, 内容: {content}")
                return None
        else:
            # 返回包含错误信息的字典
            error_type = result.get('error', 'unknown')
            logger.error(f"豆包API调用失败: {result.get('error_message', error_type)}")
            return {'error': error_type, 'error_message': result.get('error_message', '')}

    def get_trading_advice(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.5
    ) -> Optional[str]:
        """
        获取交易建议（支持系统提示词）

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            temperature: 温度参数（较低值更保守）

        Returns:
            AI响应内容
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        result = self.chat_completion(messages, temperature=temperature)

        if result['success']:
            try:
                content = result['data']['choices'][0]['message']['content']
                return content
            except (KeyError, IndexError) as e:
                logger.error(f"解析响应失败: {e}")
                return None
        else:
            return None

    def _prepare_signed_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None
    ) :
        """
        准备带签名的请求（用于火山引擎API）

        Args:
            method: HTTP方法（GET/POST等）
            path: API路径
            params: URL查询参数
            data: 请求体数据

        Returns:
            签名后的Request对象
        """
        if not VOLCENGINE_SDK_AVAILABLE:
            raise Exception("火山引擎SDK未安装，请运行: pip install volcengine")

        if not self.access_key or not self.secret_access_key:
            raise Exception("未配置AccessKey，无法进行签名鉴权")

        # 处理参数类型
        if params:
            for key in params:
                if type(params[key]) in [int, float, bool]:
                    params[key] = str(params[key])
                elif type(params[key]) == list:
                    params[key] = ",".join(params[key])

        # 创建Request对象
        r = Request()
        r.set_shema("https")
        r.set_method(method)
        r.set_connection_timeout(self.default_timeout)
        r.set_socket_timeout(self.default_timeout)

        # 设置请求头
        mheaders = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        r.set_headers(mheaders)

        # 设置查询参数
        if params:
            r.set_query(params)

        # 设置路径
        r.set_path(path)

        # 设置请求体
        if data is not None:
            r.set_body(json.dumps(data))

        # 生成签名（使用AccessKey进行签名）
        credentials = Credentials(self.access_key, self.secret_access_key, "ark", "cn-beijing")
        SignerV4.sign(r, credentials)

        return r

    def get_usage(
        self,
        interval: int = 86400,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None
    ) -> Dict:
        """
        查询Token使用情况（豆包官方API，使用签名鉴权）

        Args:
            interval: 查询粒度，按天：86400，按小时：3600
            start_time: 开始时间戳（秒），默认为今天0点
            end_time: 结束时间戳（秒），默认为当前时间

        Returns:
            使用情况数据字典
        """
        try:
            # 检查SDK和密钥
            if not VOLCENGINE_SDK_AVAILABLE:
                return {
                    'success': False,
                    'error': 'sdk_not_available',
                    'error_message': '火山引擎SDK未安装，请运行: pip install volcengine'
                }

            if not self.access_key or not self.secret_access_key:
                return {
                    'success': False,
                    'error': 'no_access_key',
                    'error_message': '未配置AccessKey，无法调用Usage API'
                }

            # 默认时间范围：今天
            if start_time is None:
                today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                start_time = int(today.timestamp())

            if end_time is None:
                end_time = int(datetime.now().timestamp())

            # 构建请求参数
            params = {
                "Action": "GetUsage",
                "Version": "2024-01-01"
            }

            data = {
                "Interval": interval,
                "StartTime": start_time,
                "EndTime": end_time
            }

            logger.debug(f"查询豆包Token使用情况: {start_time} -> {end_time}, interval={interval}")

            # 准备带签名的请求
            request = self._prepare_signed_request(
                method="POST",
                path="/",
                params=params,
                data=data
            )

            # 发送请求
            session = requests.Session()
            url = f"https://ark.cn-beijing.volcengineapi.com{request.path}"
            if request.query:
                url += "?" + "&".join([f"{k}={v}" for k, v in request.query.items()])

            response = session.request(
                method=request.method,
                url=url,
                headers=request.headers,
                data=request.body,
                timeout=self.default_timeout
            )
            response.raise_for_status()

            result = response.json()
            logger.debug(f"豆包Usage API响应: {json.dumps(result, ensure_ascii=False)[:500]}")

            return {
                'success': True,
                'data': result
            }

        except requests.exceptions.Timeout:
            logger.warning(f"⏰ 豆包Usage API请求超时")
            return {
                'success': False,
                'error': 'timeout',
                'error_message': '请求超时'
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"豆包Usage API请求失败: {e}")
            return {
                'success': False,
                'error': 'request_failed',
                'error_message': str(e)
            }

        except Exception as e:
            logger.error(f"豆包Usage API调用异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                'success': False,
                'error': 'unknown_error',
                'error_message': str(e)
            }

    def get_today_usage(self) -> Dict:
        """
        获取今日Token使用情况（便捷方法）

        Returns:
            今日使用情况
        """
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start_time = int(today.timestamp())
        end_time = int(datetime.now().timestamp())

        return self.get_usage(
            interval=86400,  # 按天查询
            start_time=start_time,
            end_time=end_time
        )

    def get_recent_usage(self, days: int = 7) -> Dict:
        """
        获取最近N天的Token使用情况

        Args:
            days: 天数（默认7天）

        Returns:
            使用情况列表
        """
        end_time = int(datetime.now().timestamp())
        start_date = datetime.now() - timedelta(days=days)
        start_time = int(start_date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

        return self.get_usage(
            interval=86400,  # 按天查询
            start_time=start_time,
            end_time=end_time
        )

    def chat_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        timeout: Optional[int] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None
    ) -> Dict:
        """
        调用豆包聊天补全API（支持Tool Calling）

        Args:
            messages: 消息列表
            tools: 工具定义列表（OpenAI格式）
            temperature: 温度参数
            max_tokens: 最大令牌数
            timeout: 超时时间（秒），None则使用默认值
            session_id: 会话ID（用于token使用跟踪）
            model: 模型名称（可覆盖初始化时的默认模型）

        Returns:
            API响应（包含tool_calls信息）
        """
        url = f"{self.base_url}/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        # 使用指定模型或默认模型
        model_name = model or self.model

        payload = {
            "model": self.endpoint_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": tools,  # 添加工具定义
            "tool_choice": "auto"  # 自动选择是否调用工具
        }

        # 使用传入的timeout或默认值
        actual_timeout = timeout if timeout is not None else self.default_timeout

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=actual_timeout)
            response.raise_for_status()

            result = response.json()
            logger.debug(f"豆包API响应: {json.dumps(result, ensure_ascii=False)[:200]}")

            # 提取tool_calls
            choice = result.get('choices', [{}])[0]
            message = choice.get('message', {})
            tool_calls = message.get('tool_calls', [])

            return {
                'success': True,
                'data': result,
                'tool_calls': tool_calls,
                'content': message.get('content'),
                'finish_reason': choice.get('finish_reason')
            }

        except requests.exceptions.Timeout:
            logger.warning(f"⏰ 豆包API请求超时（{actual_timeout}秒）")


            return {
                'success': False,
                'error': 'timeout',
                'error_message': f'请求超时（{actual_timeout}秒）'
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"豆包API请求失败: {e}")

            return {
                'success': False,
                'error': 'request_failed',
                'error_message': str(e)
            }


if __name__ == '__main__':
    # 测试
    from src.config.settings import config

    if config.DOUBAO_API_KEY and config.DOUBAO_ENDPOINT_ID:
        client = DoubaoClient(
            api_key=config.DOUBAO_API_KEY,
            endpoint_id=config.DOUBAO_ENDPOINT_ID,
            model=config.DOUBAO_MODEL
        )

        print(f"使用模型: {client.model}")

        # 简单测试
        response = client.get_trading_advice(
            system_prompt="你是一个专业的量化交易助手。",
            user_prompt="BTC当前价格50000，24小时涨幅3%，应该买入还是持有？给出简短建议。"
        )

        if response:
            print("AI响应:", response)
        else:
            print("API调用失败")
    else:
        print("请配置豆包API密钥")
