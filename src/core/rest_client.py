"""
OKX REST API 客户端
"""
import requests
import json
from typing import Optional, Dict, Any
from loguru import logger

from ..utils.exceptions import (
    OKXAPIException,
    OKXNetworkException,
    OKXParamException
)
from .auth import OKXAuth


class OKXRestClient:
    """OKX REST API 客户端"""

    # API基础URL
    BASE_URL = 'https://www.okx.com'
    DEMO_BASE_URL = 'https://www.okx.com'  # 模拟盘使用相同域名

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        passphrase: Optional[str] = None,
        is_demo: bool = False,
        timeout: int = 30,
        proxy: Optional[str] = None,
        use_proxy: bool = True
    ):
        """
        初始化REST客户端

        Args:
            api_key: API Key (公共接口可不传)
            secret_key: Secret Key
            passphrase: API Passphrase
            is_demo: 是否为模拟盘
            timeout: 请求超时时间(秒)
            proxy: 代理地址 (如 'http://127.0.0.1:8889')
            use_proxy: 是否启用代理（默认True）
        """
        self.auth = None
        if api_key and secret_key and passphrase:
            self.auth = OKXAuth(api_key, secret_key, passphrase, is_demo)

        self.base_url = self.BASE_URL

        self.timeout = timeout
        self.session = requests.Session()

        # 配置代理
        if use_proxy:
            if not proxy:
                logger.info(f"✓ 未提供代理端口（直连）")
            else:
                proxy_url = proxy   
                self.session.proxies = {
                    'http': proxy_url,
                    'https': proxy_url
                }
                logger.info(f"✓ 已启用代理: {proxy_url}")
        else:
            logger.info("✓ 未启用代理（直连）")

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        auth_required: bool = False
    ) -> Dict[str, Any]:
        """
        发送HTTP请求

        Args:
            method: 请求方法
            endpoint: API端点
            params: URL参数
            data: 请求体数据
            auth_required: 是否需要认证

        Returns:
            响应数据

        Raises:
            OKXAPIException: API错误
            OKXNetworkException: 网络错误
        """
        url = self.base_url + endpoint

        # 构建查询字符串
        query_string = ''
        if params:
            query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
            url += '?' + query_string

        # 准备请求
        headers = {'Content-Type': 'application/json'}
        body = ''

        if data:
            body = json.dumps(data)

        # 添加认证
        if auth_required:
            if not self.auth:
                raise OKXParamException("需要提供API凭证")

            request_path = endpoint
            if query_string:
                request_path += '?' + query_string

            auth_headers = self.auth.get_headers(method, request_path, body)
            headers.update(auth_headers)

        # 发送请求
        try:
            #logger.debug(f"请求: {method} {url}")
            if data:
                logger.debug(f"请求体: {body}")

            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                data=body if body else None,
                timeout=self.timeout
            )

            #logger.debug(f"响应状态: {response.status_code}")
            #logger.debug(f"响应内容: {response.text}")

        except requests.exceptions.Timeout:
            raise OKXNetworkException(f"请求超时: {url}")
        except requests.exceptions.ConnectionError as e:
            raise OKXNetworkException(f"连接错误: {str(e)}")
        except Exception as e:
            raise OKXNetworkException(f"网络异常: {str(e)}")

        # 解析响应
        try:
            result = response.json()
        except ValueError:
            raise OKXAPIException(
                f"无效的JSON响应: {response.text}",
                code=str(response.status_code)
            )


        return result

    def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        auth_required: bool = False
    ) -> Dict[str, Any]:
        """GET请求"""
        return self._request('GET', endpoint, params=params, auth_required=auth_required)

    def post(
        self,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        auth_required: bool = True
    ) -> Dict[str, Any]:
        """POST请求"""
        return self._request('POST', endpoint, data=data, auth_required=auth_required)

    def delete(
        self,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        auth_required: bool = True
    ) -> Dict[str, Any]:
        """DELETE请求"""
        return self._request('DELETE', endpoint, data=data, auth_required=auth_required)
