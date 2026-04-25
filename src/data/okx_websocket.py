"""
OKX WebSocket 封装类
使用 websocket-client 库实现稳定的 WebSocket 连接
"""
import json
import time
import threading
import websocket
from typing import Callable, Optional, Dict, Any
from loguru import logger


class OkxWebSocket:
    """
    OKX WebSocket 连接封装

    使用回调函数模式，由调用方提供具体的消息处理逻辑
    """

    def __init__(
        self,
        url: str,
        name: str = "WebSocket",
        on_message: Optional[Callable] = None,
        on_open: Optional[Callable] = None,
        on_close: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
        ping_interval: int = 20,
        proxy: Optional[str] = None,
        use_proxy: bool = True
    ):
        """
        初始化 WebSocket 连接

        Args:
            url: WebSocket URL
            name: 连接名称（用于日志）
            on_message: 消息回调函数 callback(message: str)
            on_open: 连接打开回调 callback(ws)
            on_close: 连接关闭回调 callback(ws, close_code, close_msg)
            on_error: 错误回调 callback(ws, error)
            ping_interval: 应用层ping间隔（秒），默认20秒
            proxy: 代理地址 (如 'http://127.0.0.1:8889')
            use_proxy: 是否启用代理（默认True）
        """
        self.url = url
        self.name = name
        self.ping_interval = ping_interval

        # 代理配置
        self.use_proxy = use_proxy
        self.proxy = proxy or 'http://127.0.0.1:8889'  # 默认使用本地8889端口

        # 用户回调函数
        self.user_on_message = on_message
        self.user_on_open = on_open
        self.user_on_close = on_close
        self.user_on_error = on_error

        # WebSocket 对象
        self.ws: Optional[websocket.WebSocketApp] = None

        # 运行状态
        self.running = False
        self.connected = False

        # 心跳定时器
        self.ping_timer: Optional[threading.Timer] = None

    def connect(self):
        """建立 WebSocket 连接（在当前线程中阻塞运行）"""
        if self.running:
            logger.warning(f"{self.name} 已在运行中")
            return

        self.running = True

        # 显示代理状态
        if self.use_proxy:
            logger.info(f"{self.name} 开始连接: {self.url} (通过代理: {self.proxy})")
        else:
            logger.info(f"{self.name} 开始连接: {self.url} (直连)")

        # 创建 WebSocketApp
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_pong=self._on_pong
        )

        # 准备运行参数
        run_kwargs = {
            'ping_interval': None,  # 禁用协议层ping，使用应用层ping
            'ping_timeout': None
        }

        # 配置代理（websocket-client需要完整的代理类型）
        if self.use_proxy:
            # websocket-client支持: http, socks4, socks5, socks4a, socks5h
            # 将 http://host:port 转换为 http 代理格式
            proxy_url = self.proxy

            # 解析代理URL
            if proxy_url.startswith('http://'):
                proxy_type = 'http'
                proxy_host_port = proxy_url[7:]  # 去掉 'http://'
            elif proxy_url.startswith('https://'):
                proxy_type = 'http'  # HTTPS代理也使用http类型
                proxy_host_port = proxy_url[8:]  # 去掉 'https://'
            elif proxy_url.startswith('socks5://'):
                proxy_type = 'socks5'
                proxy_host_port = proxy_url[9:]
            elif proxy_url.startswith('socks4://'):
                proxy_type = 'socks4'
                proxy_host_port = proxy_url[9:]
            else:
                # 默认为http代理
                proxy_type = 'http'
                proxy_host_port = proxy_url

            # 检查是否包含认证信息
            if '@' in proxy_host_port:
                # 格式: user:pass@host:port
                auth_part, host_port = proxy_host_port.split('@', 1)
                username, password = auth_part.split(':', 1)
                host, port = host_port.split(':')

                run_kwargs['proxy_type'] = proxy_type
                run_kwargs['http_proxy_host'] = host
                run_kwargs['http_proxy_port'] = int(port)
                run_kwargs['http_proxy_auth'] = (username, password)
            else:
                # 格式: host:port
                parts = proxy_host_port.split(':')
                run_kwargs['proxy_type'] = proxy_type
                run_kwargs['http_proxy_host'] = parts[0]
                run_kwargs['http_proxy_port'] = int(parts[1]) if len(parts) > 1 else 8889

        # 运行（阻塞）
        self.ws.run_forever(**run_kwargs)

    def _on_open(self, ws):
        """内部：连接打开处理"""
        self.connected = True
        logger.info(f"✓ {self.name} 连接已建立")

        # 启动应用层心跳
        self._start_ping()

        # 调用用户回调
        if self.user_on_open:
            try:
                self.user_on_open(ws)
            except Exception as e:
                logger.error(f"{self.name} on_open回调错误: {e}")

    def _on_message(self, ws, message):
        """内部：消息处理"""
        try:
            # 处理 pong 响应
            if message == 'pong':
                logger.debug(f"{self.name} 收到pong")
                return

            # 调用用户回调
            if self.user_on_message:
                self.user_on_message(message)

        except Exception as e:
            logger.error(f"{self.name} 消息处理错误: {e}")
            import traceback
            logger.error(f"  堆栈跟踪:\n{traceback.format_exc()}")

    def _on_error(self, ws, error):
        """内部：错误处理"""
        logger.error(f"{self.name} 错误: {type(error).__name__} - {error}")

        # 调用用户回调
        if self.user_on_error:
            try:
                self.user_on_error(ws, error)
            except Exception as e:
                logger.error(f"{self.name} on_error回调错误: {e}")

    def _on_close(self, ws, close_code, close_msg):
        """内部：连接关闭处理"""
        self.connected = False
        logger.warning(f"{self.name} 连接关闭: {close_code} - {close_msg}")

        # 停止心跳
        self._stop_ping()

        # 调用用户回调
        if self.user_on_close:
            try:
                self.user_on_close(ws, close_code, close_msg)
            except Exception as e:
                logger.error(f"{self.name} on_close回调错误: {e}")

    def _on_pong(self, ws, data):
        """内部：pong响应处理"""
        logger.debug(f"{self.name} 收到pong帧")

    def _start_ping(self):
        """启动应用层ping定时器"""
        def send_ping():
            if self.running and self.ws and self.connected:
                try:
                    self.ws.send('ping')
                    logger.debug(f"{self.name} 发送ping")
                except Exception as e:
                    logger.error(f"{self.name} 发送ping失败: {e}")
                    return

                # 继续定时
                self.ping_timer = threading.Timer(self.ping_interval, send_ping)
                self.ping_timer.start()

        # 首次启动
        self.ping_timer = threading.Timer(self.ping_interval, send_ping)
        self.ping_timer.start()
        logger.debug(f"{self.name} 心跳定时器已启动（间隔{self.ping_interval}秒）")

    def _stop_ping(self):
        """停止ping定时器"""
        if self.ping_timer:
            self.ping_timer.cancel()
            self.ping_timer = None
            logger.debug(f"{self.name} 心跳定时器已停止")

    def send(self, data: Any):
        """
        发送数据

        Args:
            data: 可以是字符串或字典（字典会自动转JSON）
        """
        if not self.ws or not self.connected:
            logger.error(f"{self.name} 未连接，无法发送数据")
            return False

        try:
            if isinstance(data, dict):
                data = json.dumps(data)

            self.ws.send(data)
            return True
        except Exception as e:
            logger.error(f"{self.name} 发送数据失败: {e}")
            return False

    def subscribe(self, channels: list):
        """
        订阅频道

        Args:
            channels: 频道列表，格式: [{"channel": "trades", "instId": "BTC-USDT-SWAP"}, ...]
        """
        subscribe_msg = {
            "op": "subscribe",
            "args": channels
        }
        success = self.send(subscribe_msg)
        if success:
            logger.info(f"{self.name} 订阅 {len(channels)} 个频道")
        return success

    def close(self):
        """关闭连接"""
        logger.info(f"{self.name} 正在关闭...")
        self.running = False
        self._stop_ping()

        if self.ws:
            self.ws.close()

        self.connected = False

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self.connected

    def reconnect(self):
        """重新连接"""
        logger.info(f"{self.name} 准备重连...")
        self.close()
        time.sleep(2)  # 等待2秒后重连
        self.connect()
