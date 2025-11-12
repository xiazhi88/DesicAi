"""
SPA 静态文件服务器
支持前端路由回退，所有未匹配的路由都返回 index.html
同时启动 FastAPI 后端服务
"""
import http.server
import socketserver
import os
import sys
import threading
import signal
from urllib.parse import urlparse

# 全局标志，用于优雅关闭服务器
shutdown_flag = threading.Event()
uvicorn_server = None

class SPAHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # 解析请求路径
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        # 如果是 /api 开头的请求，返回 404（API 请求）
        if path.startswith('/api'):
            self.send_error(404)
            return

        # 构建完整文件路径
        full_path = os.path.join(self.directory, path.lstrip('/'))

        # 如果文件存在，正常返回
        if os.path.exists(full_path) and os.path.isfile(full_path):
            return super().do_GET()

        # 否则返回 index.html（SPA 路由回退）
        self.path = '/index.html'
        return super().do_GET()

def start_api_server():
    """在独立线程中启动 FastAPI 后端服务"""
    global uvicorn_server
    try:
        # 添加项目根目录到 Python 路径
        project_root = os.path.dirname(os.path.abspath(__file__))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        # 导入并启动 FastAPI 应用
        from src.ui.api_server import app
        import uvicorn

        print(f"""
    ===============================================================

          后端 API 服务器已启动

          API地址: http://localhost:1234
          管理界面: http://localhost:1234/manager
          仪表盘: http://localhost:1234/dashboard

    ===============================================================
        """)

        # 创建 uvicorn 服务器实例
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=1234,
            log_level="info",
            limit_concurrency=100,
            timeout_keep_alive=5
        )
        uvicorn_server = uvicorn.Server(config)

        # 启动服务器
        uvicorn_server.run()
    except Exception as e:
        if not shutdown_flag.is_set():
            print(f"启动 API 服务器失败: {e}")
            import traceback
            traceback.print_exc()


def signal_handler(sig, frame):
    """处理 Ctrl+C 信号"""
    print("\n\n收到停止信号，正在关闭服务器...")
    shutdown_flag.set()

    # 关闭 API 服务器
    if uvicorn_server:
        print("正在关闭后端 API 服务器...")
        uvicorn_server.should_exit = True

def start_spa_server():
    """启动 SPA 静态文件服务器"""
    PORT = 1235
    DIRECTORY = "dist"

    # 检查 dist 目录是否存在
    if not os.path.exists(DIRECTORY):
        print(f"错误: 目录 {DIRECTORY} 不存在")
        exit(1)

    # 获取 dist 目录的绝对路径
    dist_path = os.path.abspath(DIRECTORY)

    # 创建自定义 Handler，指定服务目录
    class CustomSPAHandler(SPAHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=dist_path, **kwargs)

    httpd = socketserver.TCPServer(("0.0.0.0", PORT), CustomSPAHandler)
    httpd.allow_reuse_address = True

    print(f"""
    ===============================================================

          前端 SPA 服务器已启动

          访问地址: http://localhost:{PORT}
          网络地址: http://0.0.0.0:{PORT}

          支持前端路由，按 Ctrl+C 停止所有服务器

    ===============================================================
    """)

    try:
        # 设置超时以便能响应关闭信号
        httpd.timeout = 1
        while not shutdown_flag.is_set():
            httpd.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        print("正在关闭前端服务器...")
        httpd.server_close()
        print("前端服务器已停止")


if __name__ == "__main__":
    # 注册 Ctrl+C 信号处理器
    signal.signal(signal.SIGINT, signal_handler)

    # 在独立线程中启动 API 服务器
    api_thread = threading.Thread(target=start_api_server, daemon=False)
    api_thread.start()

    # 在主线程中启动 SPA 服务器
    try:
        start_spa_server()
    except Exception as e:
        print(f"SPA 服务器异常: {e}")

    # 等待 API 服务器线程结束
    if api_thread.is_alive():
        print("等待后端服务器完全关闭...")
        api_thread.join(timeout=3)
        if api_thread.is_alive():
            print("后端服务器关闭超时，强制退出")

    print("\n所有服务器已停止")
    sys.exit(0)
