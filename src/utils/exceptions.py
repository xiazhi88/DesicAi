"""
OKX API 异常定义
"""


class OKXException(Exception):
    """OKX API 基础异常"""
    def __init__(self, message: str, code: str = None):
        self.message = message
        self.code = code
        super().__init__(self.message)


class OKXAuthException(OKXException):
    """认证异常"""
    pass


class OKXAPIException(OKXException):
    """API 请求异常"""
    def __init__(self, message: str, code: str = None, response: dict = None):
        super().__init__(message, code)
        self.response = response


class OKXParamException(OKXException):
    """参数异常"""
    pass


class OKXNetworkException(OKXException):
    """网络异常"""
    pass


class OKXWebSocketException(OKXException):
    """WebSocket 异常"""
    pass
