"""
OKX API 认证签名模块
"""
import hmac
import base64
from datetime import datetime
from typing import Optional


class OKXAuth:
    """OKX API 认证"""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        passphrase: str,
        is_demo: bool = False
    ):
        """
        初始化认证

        Args:
            api_key: API Key
            secret_key: Secret Key
            passphrase: API Passphrase
            is_demo: 是否为模拟盘
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.is_demo = is_demo

    def get_timestamp(self) -> str:
        """获取ISO格式时间戳"""
        return datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'

    def sign(self, timestamp: str, method: str, request_path: str, body: str = '') -> str:
        """
        生成签名

        Args:
            timestamp: 时间戳
            method: 请求方法 (GET, POST等)
            request_path: 请求路径
            body: 请求体

        Returns:
            签名字符串
        """
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(
            bytes(self.secret_key, encoding='utf-8'),
            bytes(message, encoding='utf-8'),
            digestmod='sha256'
        )
        signature = base64.b64encode(mac.digest()).decode()
        return signature

    def get_headers(
        self,
        method: str,
        request_path: str,
        body: str = ''
    ) -> dict:
        """
        获取请求头

        Args:
            method: 请求方法
            request_path: 请求路径
            body: 请求体

        Returns:
            请求头字典
        """
        timestamp = self.get_timestamp()
        signature = self.sign(timestamp, method, request_path, body)

        headers = {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }

        # 模拟盘需要额外的header
        if self.is_demo:
            headers['x-simulated-trading'] = '1'

        return headers
