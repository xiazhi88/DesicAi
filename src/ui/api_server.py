"""
交易系统 Web Dashboard API 服务
使用 FastAPI 提供 RESTful API
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

project_root = find_project_root()
sys.path.insert(0, project_root)
import sqlite3
import json
import subprocess
import signal
import psutil
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from loguru import logger

from src.config.settings import config
from src.core.rest_client import OKXRestClient
from src.api.account import AccountAPI
from src.api.position import PositionAPI
from src.api.market import MarketAPI
from src.api.public import PublicAPI
from src.ai.token_usage_tracker import get_tracker
from src.ai.doubao_client import DoubaoClient
from src.ai.deepseek_client import DeepSeekClient
from src.ai.openai_compatible_client import OpenAICompatibleClient

app = FastAPI(title="OKX量化交易Dashboard", version="2.0")

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 自定义StaticFiles类，添加缓存头
from starlette.responses import FileResponse
import typing

class CachedStaticFiles(StaticFiles):
    def file_response(
        self,
        full_path: typing.Union[str, os.PathLike],
        stat_result: os.stat_result,
        scope: typing.Dict[str, typing.Any],
        status_code: int = 200,
    ) -> FileResponse:
        response = super().file_response(full_path, stat_result, scope, status_code)
        # 添加长期缓存头（1年）
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

# 挂载静态文件
static_path = os.path.join(project_root, "static")
os.makedirs(static_path, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_path), name="static")

# 挂载图标文件 (从 dist/icons 目录，使用自定义缓存类)
icons_path = os.path.join(project_root, "dist", "icons")
os.makedirs(icons_path, exist_ok=True)
app.mount("/icons", CachedStaticFiles(directory=icons_path), name="icons")


# 数据库路径
DB_PATH = os.path.join(project_root, "data", "trading_data.db")

# 📊 性能优化：数据库连接池（避免重复执行PRAGMA）
import queue
import threading

class DBConnectionPool:
    """
    SQLite连接池（只读优化版）

    优化点：
    - 复用连接，避免重复执行PRAGMA（减少150ms开销）
    - 使用队列管理连接，保证线程安全
    - WAL模式下，只读连接可以并发
    """
    def __init__(self, db_path: str, pool_size: int = 5):
        self.db_path = db_path
        self.pool_size = pool_size
        self._pool = queue.Queue(maxsize=pool_size)
        self._lock = threading.Lock()
        self._initialize_pool()

    def _create_connection(self):
        """创建并配置一个数据库连接"""
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row

        # 配置PRAGMA（只执行一次）
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA cache_size=-32000')
        conn.execute('PRAGMA read_uncommitted=1')

        return conn

    def _initialize_pool(self):
        """初始化连接池"""
        for _ in range(self.pool_size):
            conn = self._create_connection()
            self._pool.put(conn)
        logger.info(f"[OK] Database connection pool initialized ({self.pool_size} connections)")

    def get_connection(self):
        """从连接池获取连接"""
        try:
            # 尝试从池中获取连接（超时5秒）
            conn = self._pool.get(timeout=5)
            return conn
        except queue.Empty:
            # 连接池耗尽，临时创建新连接
            logger.warning("[WARN] Connection pool exhausted, creating temporary connection")
            return self._create_connection()

    def return_connection(self, conn):
        """归还连接到连接池"""
        try:
            # 尝试归还到池中（如果池未满）
            self._pool.put_nowait(conn)
        except queue.Full:
            # 池已满，关闭多余连接
            conn.close()

    def close_all(self):
        """关闭所有连接"""
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except queue.Empty:
                break
            logger.info("[OK] Connection pool closed")

# 创建全局连接池
_db_pool = None

def get_db_pool():
    """获取全局连接池（懒加载）"""
    global _db_pool
    if _db_pool is None:
        _db_pool = DBConnectionPool(DB_PATH, pool_size=5)
    return _db_pool


def getClient():
    # 构建代理配置
    proxy_url = None
    if config.PROXY_ENABLED:
        if config.PROXY_USERNAME and config.PROXY_PASSWORD:
            proxy_url = f'{config.PROXY_TYPE}://{config.PROXY_USERNAME}:{config.PROXY_PASSWORD}@{config.PROXY_HOST}:{config.PROXY_PORT}'
        else:
            proxy_url = f'{config.PROXY_TYPE}://{config.PROXY_HOST}:{config.PROXY_PORT}'

    okx_client = OKXRestClient(
        api_key=config.API_KEY,
        secret_key=config.SECRET_KEY,
        passphrase=config.PASSPHRASE,
        is_demo=config.IS_DEMO,
        proxy=proxy_url,
        use_proxy=config.PROXY_ENABLED
    )
    return okx_client
# 初始化OKX API（如果配置了）
okx_client = None
account_api = None
position_api = None
market_api = None
public_api = None
doubao_client = None
deepseek_client = None
custom_ai_client = None
ai_client = None  # 统一AI客户端接口

if config.is_configured():
    okx_client = getClient()
    account_api = AccountAPI(okx_client)
    position_api = PositionAPI(okx_client)
    market_api = MarketAPI(okx_client)
    public_api = PublicAPI(okx_client)

# 初始化AI客户端（根据配置选择）
def load_access_key():
    """从AccessKey.txt加载火山引擎AccessKey"""
    access_key_file = os.path.join(project_root, "AccessKey.txt")
    try:
        if os.path.exists(access_key_file):
            with open(access_key_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                access_key_id = lines[0].split(': ')[1].strip()
                secret_access_key = lines[1].split(': ')[1].strip()
                return access_key_id, secret_access_key
    except Exception as e:
        print(f"读取AccessKey失败: {e}")
    return None, None

if config.AI_PROVIDER == 'doubao' and config.DOUBAO_API_KEY and config.DOUBAO_ENDPOINT_ID:
    access_key_id, secret_access_key = load_access_key()
    doubao_client = DoubaoClient(
        api_key=config.DOUBAO_API_KEY,
        endpoint_id=config.DOUBAO_ENDPOINT_ID,
        model=config.DOUBAO_MODEL,
        access_key=access_key_id,
        secret_access_key=secret_access_key
    )
    ai_client = doubao_client
    print(f"[OK] Doubao AI enabled (model: {config.DOUBAO_MODEL})")

elif config.AI_PROVIDER == 'deepseek' and config.DEEPSEEK_API_KEY:
    deepseek_client = DeepSeekClient(
        api_key=config.DEEPSEEK_API_KEY,
        model=config.DEEPSEEK_MODEL,
        default_timeout=config.DEEPSEEK_TIMEOUT
    )
    ai_client = deepseek_client
    print(f"[OK] DeepSeek AI enabled (model: {config.DEEPSEEK_MODEL})")

elif config.AI_PROVIDER == 'qwen' and config.QWEN_API_KEY:
    from src.ai.qwen_client import QwenClient
    qwen_client = QwenClient(
        api_key=config.QWEN_API_KEY,
        model=config.QWEN_MODEL,
        default_timeout=config.QWEN_TIMEOUT
    )
    ai_client = qwen_client
    print(f"[OK] Qwen AI enabled (model: {config.QWEN_MODEL})")

elif config.AI_PROVIDER == 'custom' and config.CUSTOM_AI_BASE_URL and config.CUSTOM_AI_MODEL:
    custom_ai_client = OpenAICompatibleClient(
        api_key=config.CUSTOM_AI_API_KEY,
        base_url=config.CUSTOM_AI_BASE_URL,
        model=config.CUSTOM_AI_MODEL,
        default_timeout=config.CUSTOM_AI_TIMEOUT
    )
    ai_client = custom_ai_client
    print(f"[OK] Custom OpenAI-compatible AI enabled (model: {config.CUSTOM_AI_MODEL})")

# ==================== 交易产品和图标管理 ====================

# 全局变量：存储交易产品列表
_instruments_cache = None

def fetch_and_cache_instruments():
    """
    获取所有SWAP交易产品并下载图标

    返回:
        list: 交易产品列表
    """
    global _instruments_cache

    try:
        # 如果没有配置OKX API，使用临时客户端（不需要认证）
        if not public_api:
            # 构建代理配置
            proxy_url = None
            if config.PROXY_ENABLED:
                if config.PROXY_USERNAME and config.PROXY_PASSWORD:
                    proxy_url = f'{config.PROXY_TYPE}://{config.PROXY_USERNAME}:{config.PROXY_PASSWORD}@{config.PROXY_HOST}:{config.PROXY_PORT}'
                else:
                    proxy_url = f'{config.PROXY_TYPE}://{config.PROXY_HOST}:{config.PROXY_PORT}'

            temp_client = OKXRestClient(
                api_key='',
                secret_key='',
                passphrase='',
                is_demo=False,
                use_proxy=config.PROXY_ENABLED,
                proxy=proxy_url
            )
            temp_public_api = PublicAPI(temp_client)
        else:
            temp_public_api = public_api

        logger.info("[INFO] 开始获取交易产品列表...")

        # 获取所有SWAP交易产品
        result = temp_public_api.get_instruments(inst_type='SWAP')

        if result.get('code') != '0':
            logger.error(f"[ERROR] 获取交易产品失败: {result.get('msg')}")
            # 降级方案：从 dist/icons/ 目录读取PNG文件自动生成交易产品列表
            logger.info("[INFO] 降级方案：从本地图标文件生成交易产品列表...")
            icons_dir = os.path.join(project_root, "dist", "icons")

            if os.path.exists(icons_dir):
                import glob
                png_files = glob.glob(os.path.join(icons_dir, "*.png"))

                usdt_instruments = []
                for png_file in png_files:
                    # 从文件名提取币种符号（如 avax.png -> AVAX）
                    symbol = os.path.splitext(os.path.basename(png_file))[0].upper()

                    # 构造交易产品信息
                    usdt_instruments.append({
                        'instId': f'{symbol}-USDT-SWAP',
                        'instFamily': f'{symbol}-USDT',
                        'baseCcy': symbol,
                        'quoteCcy': 'USDT',
                        'state': 'live',  # 假设状态为live
                        'ctVal': '0.01',  # 默认值
                        'ctMult': '1',    # 默认值
                        'tickSz': '0.1',  # 默认值
                        'lotSz': '1'      # 默认值
                    })

                logger.info(f"[OK] 从 {len(png_files)} 个图标文件生成了 {len(usdt_instruments)} 个交易产品")
            else:
                logger.error(f"[ERROR] 图标目录不存在: {icons_dir}")
                return []
        else:
            instruments = result.get('data', [])
            logger.info(f"[OK] 成功获取 {len(instruments)} 个交易产品")

            # 过滤出USDT交易对
            usdt_instruments = [
                inst for inst in instruments
                if inst.get('instId', '').endswith('-USDT-SWAP')
            ]

            logger.info(f"[OK] 找到 {len(usdt_instruments)} 个USDT交易对")

        # 下载币种图标
        icons_dir = os.path.join(project_root, "dist", "icons")
        os.makedirs(icons_dir, exist_ok=True)

        # 获取所有需要下载的币种（去重）
        symbols = set()
        for inst in usdt_instruments:
            inst_id = inst.get('instId', '')
            if inst_id:
                # 从 "BTC-USDT-SWAP" 中提取 "BTC"
                symbol = inst_id.split('-')[0].lower()
                symbols.add(symbol)

        # 检查已存在的图标
        symbols_to_download = []
        skipped = 0
        for symbol in symbols:
            icon_path = os.path.join(icons_dir, f"{symbol}.png")
            if os.path.exists(icon_path):
                skipped += 1
            else:
                symbols_to_download.append(symbol)

        total_symbols = len(symbols)
        need_download = len(symbols_to_download)

        logger.info(f"[INFO] 图标统计: 总计 {total_symbols} 个，已存在 {skipped} 个，需要下载 {need_download} 个")

        if need_download == 0:
            logger.info("[OK] 所有图标已存在，跳过下载")
        else:
            # 使用线程池并行下载图标
            import requests
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import threading

            downloaded = 0
            failed = 0
            lock = threading.Lock()

            def download_icon(symbol):
                """下载单个币种图标"""
                nonlocal downloaded, failed

                icon_path = os.path.join(icons_dir, f"{symbol}.png")
                icon_url = f"https://www.okx.com/cdn/oksupport/asset/currency/icon/{symbol}.png"

                try:
                    # 使用代理下载（如果配置了）
                    proxies = None
                    if config.PROXY_ENABLED:
                        proxy_url = f'http://{config.PROXY_USERNAME}:{config.PROXY_PASSWORD}@{config.PROXY_HOST}:{config.PROXY_PORT}' if config.PROXY_USERNAME else f'http://{config.PROXY_HOST}:{config.PROXY_PORT}'
                        proxies = {
                            'http': proxy_url,
                            'https': proxy_url
                        }

                    response = requests.get(icon_url, timeout=10, proxies=proxies)

                    if response.status_code == 200:
                        with open(icon_path, 'wb') as f:
                            f.write(response.content)

                        with lock:
                            downloaded += 1
                            # 输出进度
                            progress = ((downloaded + failed) / need_download) * 100
                            logger.info(f"[{downloaded + failed}/{need_download}] ({progress:.1f}%) ✓ {symbol.upper()}")
                        return True
                    else:
                        with lock:
                            failed += 1
                            logger.warning(f"[{downloaded + failed}/{need_download}] ✗ {symbol.upper()}: HTTP {response.status_code}")
                        return False
                except Exception as e:
                    with lock:
                        failed += 1
                        logger.warning(f"[{downloaded + failed}/{need_download}] ✗ {symbol.upper()}: {e}")
                    return False

            # 使用线程池并行下载（最多10个并发）
            logger.info(f"[INFO] 开始并行下载 {need_download} 个图标...")
            with ThreadPoolExecutor(max_workers=10) as executor:
                # 提交所有下载任务
                futures = {executor.submit(download_icon, symbol): symbol for symbol in symbols_to_download}

                # 等待所有任务完成
                for future in as_completed(futures):
                    future.result()  # 获取结果（如果有异常会抛出）

            logger.info(f"[OK] 图标下载完成: 成功 {downloaded} 个，失败 {failed} 个，跳过 {skipped} 个")

        # 缓存结果（只保存instId和状态信息）
        _instruments_cache = [
            {
                'instId': inst.get('instId'),
                'instFamily': inst.get('instFamily'),
                'baseCcy': inst.get('baseCcy'),
                'quoteCcy': inst.get('quoteCcy'),
                'state': inst.get('state'),
                'ctVal': inst.get('ctVal'),
                'ctMult': inst.get('ctMult'),
                'tickSz': inst.get('tickSz'),
                'lotSz': inst.get('lotSz'),
            }
            for inst in usdt_instruments
        ]

        return _instruments_cache

    except Exception as e:
        logger.error(f"[ERROR] 获取交易产品失败: {e}")
        import traceback

        # 降级方案：从 dist/icons/ 目录读取PNG文件自动生成交易产品列表
        logger.info("[INFO] 降级方案：从本地图标文件生成交易产品列表...")
        icons_dir = os.path.join(project_root, "dist", "icons")

        try:
            if os.path.exists(icons_dir):
                import glob
                png_files = glob.glob(os.path.join(icons_dir, "*.png"))

                usdt_instruments = []
                for png_file in png_files:
                    # 从文件名提取币种符号（如 avax.png -> AVAX）
                    symbol = os.path.splitext(os.path.basename(png_file))[0].upper()

                    # 构造交易产品信息
                    usdt_instruments.append({
                        'instId': f'{symbol}-USDT-SWAP',
                        'instFamily': f'{symbol}-USDT',
                        'baseCcy': symbol,
                        'quoteCcy': 'USDT',
                        'state': 'live',  # 假设状态为live
                        'ctVal': '0.01',  # 默认值
                        'ctMult': '1',    # 默认值
                        'tickSz': '0.1',  # 默认值
                        'lotSz': '1'      # 默认值
                    })

                logger.info(f"[OK] 从 {len(png_files)} 个图标文件生成了 {len(usdt_instruments)} 个交易产品")

                # 缓存结果
                _instruments_cache = [
                    {
                        'instId': inst.get('instId'),
                        'instFamily': inst.get('instFamily'),
                        'baseCcy': inst.get('baseCcy'),
                        'quoteCcy': inst.get('quoteCcy'),
                        'state': inst.get('state'),
                        'ctVal': inst.get('ctVal'),
                        'ctMult': inst.get('ctMult'),
                        'tickSz': inst.get('tickSz'),
                        'lotSz': inst.get('lotSz'),
                    }
                    for inst in usdt_instruments
                ]

                return _instruments_cache
            else:
                logger.error(f"[ERROR] 图标目录不存在: {icons_dir}")
                return []
        except Exception as fallback_error:
            logger.error(f"[ERROR] 降级方案也失败: {fallback_error}")
            return []


def get_instruments_cache():
    """获取缓存的交易产品列表"""
    global _instruments_cache

    if _instruments_cache is None:
        _instruments_cache = fetch_and_cache_instruments()

    return _instruments_cache


# ==================== FastAPI 启动事件 ====================

@app.on_event("startup")
async def startup_event():
    """FastAPI 应用启动时执行"""
    logger.info("[INFO] FastAPI 应用启动中...")

    # 初始化 prompts.json（如果不存在）
    prompts_file = os.path.join(project_root, 'data', 'prompts.json')
    if not os.path.exists(prompts_file):
        try:
            os.makedirs(os.path.dirname(prompts_file), exist_ok=True)
            default_prompts = [
                {
                    "name": "默认Prompt",
                    "description": "",
                    "content": """### 1. 核心决策原则（交易哲学）\n\n\n\n* **概率思维**：不追求 "必胜" 交易，承认市场无确定性、仅有概率分布。所有决策均基于风险回报比与成功概率评估，坦然接受合理亏损（交易的必然组成部分）。\n\n* **风险第一**：任何分析前先明确单笔交易的最大潜在损失，严格遵守 "单次亏损不超过预设总资本风险限额" 规则，保护本金为首要职责。\n\n* **边缘捕捉**：聚焦市场微观 / 宏观结构的非均衡状态（源于情绪过度反应、流动性暂时失衡、宏观逻辑驱动的长期趋势等），综合基本面、技率 × 潜在盈利) - (下跌概率 × 潜在亏损)`，做空逻辑相反），仅参与预期价值为正的机会，并持续优化公式变量。\n\n* **复盘优化**：定期回顾过往决策，总结经验教训，修正错误交易逻辑。\n\n### 2. 数据处理与情境感知\n\n将 K 线、Tick 聚合数据等接收信息视为市场深层结构的表层现象，核心任务包括：\n\n\n\n* **解读叙事**：厘清当前市场的主导逻辑与核心驱动因素。\n\n* **识别周期**：判断市场处于趋势、震荡、反转或混乱阶段。\n\n* **感知情绪**：从价格波动、成交量变化中捕捉市场参与者的贪婪与恐惧程度。\n\n* **寻找共鸣 / 背离**：验证不同数据维度的一致性（共鸣）或矛盾性（背离），例如价格创新高但动能指标减弱的背离信号需重点 关注。\n\n### 3. 自主决策框架\n\n基于上述原则与感知，每笔交易决策需提供清晰逻辑链：\n\n\n\n* **【机会阐述】**：详细说明 交易的核心逻辑（如驱动因素、结构特征、机会本质等）。\n\n* **【概率与赔率评估】**：\n\n\n  * 入场合理性：明确当前价位与时机的核心优势（如支撑 / 阻力位、逻辑拐点等）。\n\n  * 成功概率：给出主观概率评估（示例：55%），需基于客观信息推导。\n\n  * 风险回报比：预估潜在盈利与潜在亏损的比例（示例：1:3 以上）。\n\n* **【具体操作建议】**：\n\n\n  * 方向：明确做多 / 做 空 \\[具体交易标的]。\n\n  * 止损：设置具体价位（示例：XX 元），该价位需对应 "核心交易逻辑失效" 的判断标准。\n\n  * 止盈 / 目标：设置具体价位（示例：XX 元），该价位需契合技术目标区、逻辑兑现点或概率优势耗尽的节点。\n\n  * 补充规则：开仓时设定的止盈止损需基于市场分析，无重大特殊情况不得频繁调整；若需调整，必须基于新的市场分析预判，避免因短期波动情绪化操作。\n\n* **【后续预案】**：\n\n\n  * 盈利方向移动：说明如何调整止损以保护已有利润（如移动止损、跟踪止损等具体方式）。\n\n  * 新信息出现：明确调整目标的触发条件（如宏观数据发布、技术形态破位等）。\n\n### 4. 行为禁令（不可逾越规则）\n\n\n\n* 禁止 报复性交易：亏损后不得为 "回本" 进行情绪化、超出常规风险限额的交易。\n\n* 禁止违背止损：止损位触及后必须无条件执行，不得以 "再等等" 为由随意移动止损。\n\n### 5. 整体账户管理\n\n\n\n* 历史持仓：客观分析过往多空交易绩效，识别方向性偏见、风险 控制漏洞等模式。\n\n* 历史决策：复盘过去决策逻辑，判断当初判断是否合理，是否需基于当前认知修正。\n\n* 历史收益：评估收益是否达成目标，分析亏损原因（判断失误 / 执行偏差）、盈利是否未达预期（提前离场 / 目标设置不合理），明确改进空间。\n\n### 6. 持仓条件（持续持有需满足）\n\n\n\n* 多空视角下的核心交易逻辑仍有效。\n\n* 市场未出现损害当前头寸方向的重大变化（如核 心驱动因素消失、基本面反转等）。\n\n* 重新评估多空风险后，仍支持当前持仓方向。\n\n### 7. 思考模型：强制应用框架\n\n每次 响应前，必须按以下顺序完成分析步骤：\n\n\n\n1. 独立思考：拒绝盲从市场共识，仅基于实时数据与核心逻辑形成独立观点。\n\n2. 多维度验证：动态权衡以下因素的重要性（无需平等对待）：\n\n* 价格行为与技术信号（支撑 / 阻力、动量指标、形态结构等）；\n\n* 市场微观结构（流动性分布、订单簿深度、买卖盘力量等）；\n\n* 多空力量对比（资金费率、未平仓合约变化、大额成交方向等） ；\n\n* 系统风险（波动率突变、资产相关性破裂、黑天鹅事件等）。\n\n1. 证伪思维：主动寻找推翻当前判断的反证（如看多则排查 隐藏看空信号，看空则排查隐藏看多信号）。\n\n2. 概率思维：所有决策需基于 0-100 的置信度评估，无确定性机会；多空置信度需依托客观证据权重，避免方向性偏见。\n\n3. 持续学习：参考历史决策、市场变化及持仓复盘结论，但不过度依赖过往模式（需结合实时 市场动态调整）。\n\n4. 避免过度调整：拒绝频繁变动仓位或交易参数，耐心等待市场逻辑兑现；若当前无持仓，基于新分析独立决策 ；无需调整时明确给出 "HOLD" 信号。\n\n5. 开仓方向：支持做多与做空，两者适用完全一致的风险管理标准。\n""",
                    "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "updated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            ]
            with open(prompts_file, 'w', encoding='utf-8') as f:
                json.dump(default_prompts, f, ensure_ascii=False, indent=2)
            logger.info(f"[OK] 已创建默认 prompts.json 文件: {prompts_file}")
        except Exception as e:
            logger.warning(f"[WARN] 创建 prompts.json 失败: {e}")

    # 在后台线程中获取交易产品列表（避免阻塞启动）
    import threading
    def load_instruments():
        try:
            instruments = fetch_and_cache_instruments()
            logger.info(f"[OK] 成功缓存 {len(instruments)} 个交易产品")
        except Exception as e:
            logger.warning(f"[WARN] 获取交易产品失败: {e}")

    thread = threading.Thread(target=load_instruments, daemon=True)
    thread.start()


# ==================== 机器人进程管理 ====================

# 全局变量：机器人进程和PID文件路径
bot_process = None
PID_FILE = os.path.join(project_root, "data", "bot.pid")

# 全局变量：数据采集器进程和PID文件路径
collector_process = None
COLLECTOR_PID_FILE = os.path.join(project_root, "data", "collector.pid")

# 全局变量：首次访问标记（服务器重启后自动重置）
_first_visit_flag = True


def is_bot_running() -> bool:
    """检查机器人是否正在运行"""
    try:
        # 检查PID文件是否存在
        if not os.path.exists(PID_FILE):
            return False

        # 读取PID
        with open(PID_FILE, 'r') as f:
            pid = int(f.read().strip())

        # 检查进程是否存在
        if psutil.pid_exists(pid):
            try:
                proc = psutil.Process(pid)
                # 检查进程名称是否包含python和enhanced_trading
                cmdline = ' '.join(proc.cmdline())
                if 'python' in cmdline.lower() and 'enhanced_trading' in cmdline:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # PID文件存在但进程不存在，删除过期的PID文件
        os.remove(PID_FILE)
        return False

    except Exception as e:
        print(f"检查机器人状态失败: {e}")
        return False

import psutil

def is_process_running(pid):
    """
    检查指定PID的进程是否在运行
    :param pid: 进程PID（整数）
    :return: True=运行中，False=未运行/无权限
    """
    try:
        # 根据PID获取进程对象
        process = psutil.Process(pid)
        # 判断进程是否存活（is_running() 检查进程状态，status() 可获取详细状态）
        return process.is_running()
    except psutil.NoSuchProcess:
        # 进程不存在（已终止/未启动）
        return False
    except psutil.AccessDenied:
        # 有该PID，但无权限访问（视为未运行，或根据需求处理）
        print(f"警告：无权限访问PID={pid}的进程")
        return False


    
    
def start_bot() -> dict:
    """启动交易机器人"""
    global bot_process

    try:
        # 检查是否已经在运行
        if is_bot_running():
            return {
                "success": False,
                "error": "机器人已在运行中"
            }

        # 短暂延迟，确保 .env 文件已完全写入（如果刚刚保存了配置）
        import time
        time.sleep(0.5)

        # 构建启动命令
        bot_script = os.path.join(project_root, "examples", "enhanced_trading.py")

        if not os.path.exists(bot_script):
            return {
                "success": False,
                "error": f"机器人脚本不存在: {bot_script}"
            }

        # 启动机器人进程（后台运行，持续监控模式，自动执行）
        # Windows下使用CREATE_NEW_CONSOLE创建新窗口
        print(' '.join([sys.executable, bot_script, "--continuous", "--auto-execute", "--interval", str(float(config.INTERVAL_MINUTES)*60 )]))
        if os.name == 'nt':  # Windows
            
            bot_process = subprocess.Popen(
                [sys.executable, bot_script, "--continuous", "--auto-execute", "--interval", str(float(config.INTERVAL_MINUTES)*60 )  ],
                cwd=project_root,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:  # Linux/Mac
            bot_process = subprocess.Popen(
                ["gnome-terminal","--",sys.executable, bot_script, "--continuous", "--auto-execute", "--interval", str(float(config.INTERVAL_MINUTES)*60)],
                cwd=project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid
            )


        # 等待一小段时间，检查进程是否成功启动
        import time
        time.sleep(5)
        with open(PID_FILE,'r') as f:
            pid = int(f.read().strip())
        # 检查进程是否还在运行
        poll_result = is_process_running(pid)
        if not poll_result :
            # 进程已经退出，启动失败
            error_msg = f"机器人进程启动后立即退出 "

            # 清理PID文件
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)

            return {
                "success": False,
                "error": error_msg
            }

        # 验证进程是否真的在运行
        if not is_bot_running():
            return {
                "success": False,
                "error": "机器人启动后无法验证运行状态"
            }

        return {
            "success": True,
            "message": "机器人启动成功，已在新窗口中运行",
            "pid": pid
        }

    except Exception as e:
        # 清理可能创建的PID文件
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)

        return {
            "success": False,
            "error": f"启动机器人失败: {str(e)}"
        }


def stop_bot() -> dict:
    """停止交易机器人"""
    global bot_process

    try:
        # 检查PID文件
        if not os.path.exists(PID_FILE):
            return {
                "success": False,
                "error": "机器人未运行（PID文件不存在）"
            }

        # 读取PID
        with open(PID_FILE, 'r') as f:
            pid = int(f.read().strip())

        # 检查进程是否存在
        if not psutil.pid_exists(pid):
            # 清理PID文件
            os.remove(PID_FILE)
            return {
                "success": False,
                "error": "机器人进程不存在"
            }

        # 终止进程
        try:
            proc = psutil.Process(pid)

            # Windows和Unix的终止方式不同
            if os.name == 'nt':  # Windows
                # 先尝试优雅终止
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    # 强制终止
                    proc.kill()
            else:  # Linux/Mac
                # 发送SIGTERM信号
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    # 发送SIGKILL信号强制终止
                    os.killpg(os.getpgid(pid), signal.SIGKILL)

            # 删除PID文件
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)

            bot_process = None

            return {
                "success": True,
                "message": "机器人已停止"
            }

        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            # 清理PID文件
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
            return {
                "success": False,
                "error": f"停止机器人失败: {str(e)}"
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"停止机器人失败: {str(e)}"
        }


# ==================== 数据采集器进程管理 ====================

def is_collector_running() -> bool:
    """检查数据采集器是否正在运行"""
    try:
        # 检查PID文件是否存在
        if not os.path.exists(COLLECTOR_PID_FILE):
            return False

        # 读取PID
        with open(COLLECTOR_PID_FILE, 'r') as f:
            pid = int(f.read().strip())

        # 检查进程是否存在
        if psutil.pid_exists(pid):
            try:
                proc = psutil.Process(pid)
                # 检查进程名称是否包含python和standalone_data_collector
                cmdline = ' '.join(proc.cmdline())
                if 'python' in cmdline.lower() and 'standalone_data_collector' in cmdline:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # PID文件存在但进程不存在，删除过期的PID文件
        os.remove(COLLECTOR_PID_FILE)
        return False

    except Exception as e:
        logger.error(f"检查数据采集器状态失败: {e}")
        return False


def get_collector_status() -> dict:
    """获取数据采集器详细状态"""
    try:
        if not is_collector_running():
            return {
                "running": False,
                "pid": None,
                "uptime": None,
                "cpu_percent": None,
                "memory_mb": None
            }

        # 读取PID
        with open(COLLECTOR_PID_FILE, 'r') as f:
            pid = int(f.read().strip())

        proc = psutil.Process(pid)

        # 计算运行时长
        create_time = proc.create_time()
        uptime_seconds = int(datetime.now().timestamp() - create_time)

        # 格式化运行时长
        hours = uptime_seconds // 3600
        minutes = (uptime_seconds % 3600) // 60
        seconds = uptime_seconds % 60
        uptime_str = f"{hours}时{minutes}分{seconds}秒"

        return {
            "running": True,
            "pid": pid,
            "uptime": uptime_str,
            "uptime_seconds": uptime_seconds,
            "cpu_percent": round(proc.cpu_percent(interval=0.1), 2),
            "memory_mb": round(proc.memory_info().rss / 1024 / 1024, 2)
        }

    except Exception as e:
        logger.error(f"获取数据采集器状态失败: {e}")
        return {
            "running": False,
            "pid": None,
            "uptime": None,
            "cpu_percent": None,
            "memory_mb": None,
            "error": str(e)
        }


def start_collector() -> dict:
    """启动数据采集器"""
    global collector_process

    try:
        # 检查是否已经在运行
        if is_collector_running():
            return {
                "success": False,
                "error": "数据采集器已在运行中"
            }

        # 构建启动命令
        collector_script = os.path.join(project_root, "standalone_data_collector.py")

        if not os.path.exists(collector_script):
            return {
                "success": False,
                "error": f"数据采集器脚本不存在: {collector_script}"
            }

        # 从配置读取参数构建命令行
        cmd_args = [
            sys.executable,
            collector_script,
            '--history-days', str(config.COLLECTOR_HISTORY_DAYS),
            '--symbols', config.COLLECTOR_SYMBOLS,
            '--timeframes', config.COLLECTOR_TIMEFRAMES,
            '--status-interval', str(config.COLLECTOR_STATUS_INTERVAL),
            '--data-timeout', str(config.COLLECTOR_DATA_TIMEOUT)
        ]

        # 启动数据采集器进程（后台运行）
        # Windows下使用CREATE_NEW_CONSOLE创建新窗口
        if os.name == 'nt':  # Windows
            logger.info(f"启动数据采集器: {' '.join(cmd_args)}")
            collector_process = subprocess.Popen(
                cmd_args,
                cwd=project_root,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:  # Linux/Mac
            collector_process = subprocess.Popen(
                ["gnome-terminal","--"]+cmd_args,
                cwd=project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid
            )



        # 等待一小段时间，检查进程是否成功启动
        import time
        time.sleep(5)

        with open(COLLECTOR_PID_FILE,'r') as f:
            pid = int(f.read().strip())
        
        poll_result = is_process_running(pid)
        logger.info(f'{pid}:{poll_result}')
        if not poll_result :
            # 进程已经退出，启动失败
            error_msg = f"数据采集器进程启动后立即退出"


            # 清理PID文件
            if os.path.exists(COLLECTOR_PID_FILE):
                os.remove(COLLECTOR_PID_FILE)

            return {
                "success": False,
                "error": error_msg
            }

        # 验证进程是否真的在运行
        if not is_collector_running():
            return {
                "success": False,
                "error": "数据采集器启动后无法验证运行状态"
            }

        return {
            "success": True,
            "message": "数据采集器启动成功，已在新窗口中运行",
            "pid": pid
        }

    except Exception as e:
        # 清理可能创建的PID文件
        if os.path.exists(COLLECTOR_PID_FILE):
            os.remove(COLLECTOR_PID_FILE)

        return {
            "success": False,
            "error": f"启动数据采集器失败: {str(e)}"
        }


def stop_collector() -> dict:
    """停止数据采集器"""
    global collector_process

    try:
        # 检查PID文件
        if not os.path.exists(COLLECTOR_PID_FILE):
            return {
                "success": False,
                "error": "数据采集器未运行（PID文件不存在）"
            }

        # 读取PID
        with open(COLLECTOR_PID_FILE, 'r') as f:
            pid = int(f.read().strip())

        # 检查进程是否存在
        if not psutil.pid_exists(pid):
            # 清理PID文件
            os.remove(COLLECTOR_PID_FILE)
            return {
                "success": False,
                "error": "数据采集器进程不存在"
            }

        # 终止进程
        try:
            proc = psutil.Process(pid)

            # Windows和Unix的终止方式不同
            if os.name == 'nt':  # Windows
                # 先尝试优雅终止
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except psutil.TimeoutExpired:
                    # 强制终止
                    proc.kill()
            else:  # Linux/Mac
                # 发送SIGTERM信号
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except psutil.TimeoutExpired:
                    # 发送SIGKILL信号强制终止
                    os.killpg(os.getpgid(pid), signal.SIGKILL)

            # 删除PID文件
            if os.path.exists(COLLECTOR_PID_FILE):
                os.remove(COLLECTOR_PID_FILE)

            collector_process = None

            return {
                "success": True,
                "message": "数据采集器已停止"
            }

        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            # 清理PID文件
            if os.path.exists(COLLECTOR_PID_FILE):
                os.remove(COLLECTOR_PID_FILE)
            return {
                "success": False,
                "error": f"停止数据采集器失败: {str(e)}"
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"停止数据采集器失败: {str(e)}"
        }


def get_db_connection():
    """
    获取数据库连接（从连接池）

    性能优化：
    - 复用已配置的连接，避免重复执行PRAGMA（减少150ms）
    - 连接池管理，支持并发请求
    """
    pool = get_db_pool()
    return pool.get_connection()


@contextmanager
def get_db_connection_ctx():
    """
    数据库连接上下文管理器（自动归还连接）

    使用方式：
        with get_db_connection_ctx() as conn:
            cursor = conn.cursor()
            cursor.execute(...)
        # 连接自动归还到连接池
    """
    pool = get_db_pool()
    conn = pool.get_connection()
    try:
        yield conn
    finally:
        pool.return_connection(conn)


# ==================== API 路由 ====================



@app.get("/api/account/balance")
async def get_account_balance():
    global account_api
    global okx_client
    """获取账户余额"""
    if not account_api:
        raise HTTPException(status_code=500, detail="API未配置")

    try:
        okx_client=getClient()
        account_api=AccountAPI(okx_client)
        
        result = account_api.get_balance(ccy='USDT')
        if result.get('code') != '0':
            raise HTTPException(status_code=500, detail=balance.get('error', '获取余额失败'))

            
        else:
            data=result.get('data',[])[0]
            data['success']=True
            return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions")
async def get_positions():
    """获取当前持仓（增强版：含开仓时间、收益率、手续费）"""
    if not position_api:
        raise HTTPException(status_code=500, detail="API未配置")

    def safe_float(value, default=0.0):
        """安全转换为浮点数，处理空字符串和None"""
        if value is None or value == '':
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    try:
        result = position_api.get_contract_positions(inst_type='SWAP')
        if result['code'] == '0':
            positions = result.get('data', [])
            # 过滤掉数量为0的持仓
            active_positions = []
            for p in positions:
                if safe_float(p.get('pos', 0)) == 0:
                    continue

                # 计算额外信息
                upl = safe_float(p.get('upl', 0))  # 未实现盈亏
                upl_ratio = safe_float(p.get('uplRatio', 0))  # 盈亏比例
                margin = safe_float(p.get('margin', 0))  # 保证金
                imr = safe_float(p.get('imr', 0))  # 初始保证金（备用）
                fee = abs(safe_float(p.get('fee', 0)))  # 累计手续费（取绝对值）

                # 计算收益率（基于保证金）
                # 收益率 = (未实现盈亏 - 手续费) / 保证金 * 100
                if margin > 0:
                    profit_rate = ((upl - fee) / margin) * 100
                elif imr > 0:
                    # 如果margin为0，使用初始保证金imr
                    profit_rate = ((upl - fee) / imr) * 100
                else:
                    profit_rate = upl_ratio * 100  # 使用API返回的盈亏比例

                # 格式化开仓时间
                ctime_ms = int(p.get('cTime', 0))
                if ctime_ms > 0:
                    open_time = datetime.fromtimestamp(ctime_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')
                    # 计算持仓天数
                    days_held = (datetime.now().timestamp() - ctime_ms / 1000) / 86400
                else:
                    open_time = '--'
                    days_held = 0

                # 添加增强字段
                enhanced_pos = dict(p)
                enhanced_pos['openTime'] = open_time
                enhanced_pos['daysHeld'] = round(days_held, 1)
                enhanced_pos['profitRate'] = round(profit_rate, 2)
                enhanced_pos['totalFee'] = round(fee, 4)
                enhanced_pos['uplRatioPercent'] = round(upl_ratio * 100, 2)

                active_positions.append(enhanced_pos)

            return {"success": True, "data": active_positions}
        else:
            raise HTTPException(status_code=500, detail=result.get('msg', '获取持仓失败'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/conversations")
async def get_conversations(limit: int = 50, session_id: Optional[str] = None):
    """获取AI对话记录（优化版：只返回列表必需字段，不含大文本）"""
    try:
        from src.ai.data_manager import DataManager
        data_manager = DataManager()

        # 获取当前配置的API_KEY
        api_key = config.API_KEY if config.API_KEY else 'default'

        # 从MySQL获取对话记录
        conversations = data_manager.get_conversations(
            limit=limit,
            session_id=session_id,
            api_key=api_key
        )

        return {"success": True, "data": conversations}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/conversations/{conv_id}")
async def get_conversation_detail(conv_id: int):
    """获取单条对话的完整详情（包含prompt和response）"""
    try:
        from src.ai.data_manager import DataManager
        data_manager = DataManager()

        # 获取当前配置的API_KEY
        api_key = config.API_KEY if config.API_KEY else 'default'

        # 从MySQL获取对话详情
        conversation = data_manager.get_conversation_detail(
            conv_id=conv_id,
            api_key=api_key
        )

        if not conversation:
            raise HTTPException(status_code=404, detail="对话记录不存在")

        return {"success": True, "data": conversation}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/strategy_logs")
async def get_strategy_logs(limit: int = 100, session_id: Optional[str] = None, log_type: Optional[str] = None):
    """获取策略执行日志（优化版：使用索引优化查询）"""
    with get_db_connection_ctx() as conn:
        cursor = conn.cursor()

        try:
            # 优化：根据过滤条件构建最优查询（利用索引）
            if session_id and log_type:
                # 有两个过滤条件
                query = '''
                    SELECT id, session_id, inst_id, log_type, log_level, message, data, created_at
                    FROM strategy_logs
                    WHERE session_id = ? AND log_type = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                '''
                params = [session_id, log_type, limit]
            elif session_id:
                # 只过滤session_id（使用idx_strategy_logs_session索引）
                query = '''
                    SELECT id, session_id, inst_id, log_type, log_level, message, data, created_at
                    FROM strategy_logs
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                '''
                params = [session_id, limit]
            elif log_type:
                # 只过滤log_type（使用idx_strategy_logs_type_time索引）
                query = '''
                    SELECT id, session_id, inst_id, log_type, log_level, message, data, created_at
                    FROM strategy_logs
                    WHERE log_type = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                '''
                params = [log_type, limit]
            else:
                # 无过滤条件（使用idx_strategy_logs_created_at索引）
                query = '''
                    SELECT id, session_id, inst_id, log_type, log_level, message, data, created_at
                    FROM strategy_logs
                    ORDER BY created_at DESC
                    LIMIT ?
                '''
                params = [limit]

            cursor.execute(query, params)
            rows = cursor.fetchall()

            # 使用列表推导式优化
            logs = [
                {
                    "id": row['id'],
                    "session_id": row['session_id'],
                    "inst_id": row['inst_id'],
                    "log_type": row['log_type'],
                    "log_level": row['log_level'],
                    "message": row['message'],
                    "data": row['data'],
                    "created_at": row['created_at']
                }
                for row in rows
            ]

            return {"success": True, "data": logs}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/history")
async def get_position_history(inst_id: Optional[str] = None, limit: int = 20):
    """获取历史平仓记录（包含AI复盘总结和决策历史）"""
    
    try:
        from src.ai.data_manager import DataManager
        data_manager = DataManager()

        # 获取当前配置的API_KEY
        api_key = config.API_KEY if config.API_KEY else 'default'

        # 获取历史仓位
        result = position_api.get_positions_history(
                    inst_type='SWAP',
                    inst_id=inst_id,
                    limit=limit  # 获取最近100条历史记录
                )

        # 格式化返回数据（包含复盘总结和决策历史）
        if result['code']!='0':
            return {"success": False, "error": result.get('msg')}
        else:
            closed_positions=result['data']
        formatted_positions = []
        for pos in closed_positions:
            # 获取该仓位的决策历史
            pos_id = str(pos.get('cTime'))
            decisions = []
            if pos_id:
                decisions = data_manager.get_decisions_by_pos_id(pos_id, api_key=api_key)
                existing_review = data_manager.get_position_review_summary(
                                        inst_id=pos['instId'],
                                        pos_side=pos['posSide'],
                                        open_time=pos_id,
                                        api_key=config.API_KEY if config.API_KEY else 'default'
                                    )
            formatted_positions.append({
                "inst_id": pos['instId'],
                "pos_side": pos['posSide'],
                "size": pos['closeTotalPos'],
                "entry_price": pos['openAvgPx'],
                "exit_price": pos['closeAvgPx'],
                "pnl": pos.get('pnl') ,
                "pnl_ratio": float(pos['pnlRatio']) * 100,
                "fee": pos['fee'],
                "leverage": pos['lever'],
                "open_time": pos['cTime'],
                "close_time": pos['uTime'],
                "holding_seconds": (float(pos['uTime'])- float(pos['cTime']))/1000 ,
                "review_summary": existing_review if existing_review else '',
                "decisions": decisions,  # 决策历史
                "decision_count": len(decisions)  # 决策次数
            })

        return {"success": True, "data": formatted_positions}
    except Exception as e:
        logger.error(f"获取历史仓位失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/stats")
async def get_position_stats(inst_id: Optional[str] = None, days: int = 30):
    """获取仓位收益统计"""
    try:
        from src.ai.data_manager import DataManager
        data_manager = DataManager()

        # 获取当前配置的API_KEY
        api_key = config.API_KEY if config.API_KEY else 'default'

        stats = data_manager.get_performance_stats(
            inst_id=inst_id,
            days=days,
            api_key=api_key
        )

        return {"success": True, "data": stats}
    except Exception as e:
        logger.error(f"获取收益统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
async def get_config():
    """获取程序配置（每次重新加载.env文件）"""
    from src.config.settings import reload_config

    # 重新加载全局配置
    config=reload_config()

    # 读取代理配置
    proxy_config = {
        "enabled": False,
        "type": "http",
        "host": "127.0.0.1",
        "port": 7890,
        "username": "",
        "password": ""
    }

    # 读取当前激活的Prompt
    active_prompt = ""

    env_file_path = os.path.join(project_root, '.env')
    if os.path.exists(env_file_path):
        with open(env_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith('#') and '=' in stripped:
                    key, value = stripped.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    if key == 'PROXY_ENABLED':
                        proxy_config['enabled'] = value.lower() == 'true'
                    elif key == 'PROXY_TYPE':
                        proxy_config['type'] = value
                    elif key == 'PROXY_HOST':
                        proxy_config['host'] = value
                    elif key == 'PROXY_PORT':
                        try:
                            proxy_config['port'] = int(value)
                        except ValueError:
                            pass
                    elif key == 'PROXY_USERNAME':
                        proxy_config['username'] = value
                    elif key == 'PROXY_PASSWORD':
                        proxy_config['password'] = value
                    elif key == 'ACTIVE_PROMPT':
                        active_prompt = value

    return {
        "success": True,
        "data": {
            # OKX API配置
            "api_configured": config.is_configured(),
            "okx_api_key": config.API_KEY,
            "okx_secret_key": config.SECRET_KEY,
            "okx_passphrase": config.PASSPHRASE,
            "is_demo": config.IS_DEMO,
            "use_aws": config.USE_AWS,
            "log_level": config.LOG_LEVEL,
            "bot_start_time": config.BOT_START_TIME,

            # AI配置
            "ai_provider": config.AI_PROVIDER,
            "ai_configured": config.is_ai_configured(),

            # 豆包配置
            "doubao_configured": bool(config.DOUBAO_API_KEY and config.DOUBAO_ENDPOINT_ID),
            "doubao_api_key": config.DOUBAO_API_KEY,  # 返回完整key
            "doubao_endpoint_id": config.DOUBAO_ENDPOINT_ID,
            "doubao_model": config.DOUBAO_MODEL,
            "doubao_timeout": config.DOUBAO_TIMEOUT,
            "doubao_models": [
                "doubao-seed-1-6-lite-251015",
                "doubao-seed-1-6-flash-250828",
                "doubao-seed-1-6-thinking-250715",
                "doubao-seed-1-6-251015"
            ],

            # DeepSeek配置
            "deepseek_configured": bool(config.DEEPSEEK_API_KEY),
            "deepseek_api_key": config.DEEPSEEK_API_KEY,  # 返回完整key
            "deepseek_model": config.DEEPSEEK_MODEL,
            "deepseek_timeout": config.DEEPSEEK_TIMEOUT,
            "deepseek_models": [
                "deepseek-chat",
                "deepseek-reasoner"
            ],

            # 通义千问配置
            "qwen_configured": bool(config.QWEN_API_KEY),
            "qwen_api_key": config.QWEN_API_KEY,  # 返回完整key
            "qwen_model": config.QWEN_MODEL,
            "qwen_timeout": config.QWEN_TIMEOUT,
            "qwen_models": [
                "qwen-plus",
                "qwen-turbo",
                "qwen-max",
                "qwen-max-longcontext"
            ],

            # 其他 OpenAI-compatible 配置
            "custom_ai_configured": bool(config.CUSTOM_AI_BASE_URL and config.CUSTOM_AI_MODEL),
            "custom_ai_api_key": config.CUSTOM_AI_API_KEY,
            "custom_ai_base_url": config.CUSTOM_AI_BASE_URL,
            "custom_ai_model": config.CUSTOM_AI_MODEL,
            "custom_ai_timeout": config.CUSTOM_AI_TIMEOUT,

            # 交易配置
            "default_leverage": config.DEFAULT_LEVERAGE,

            # 代理配置
            "proxy": proxy_config,

            # 数据采集器配置
            "collector_symbols": config.COLLECTOR_SYMBOLS,
            "collector_timeframes": config.COLLECTOR_TIMEFRAMES,
            "collector_history_days": config.COLLECTOR_HISTORY_DAYS,
            "collector_status_interval": config.COLLECTOR_STATUS_INTERVAL,
            "collector_data_timeout": config.COLLECTOR_DATA_TIMEOUT,
            "inst_id": config.INST_ID,

            # MySQL数据库配置
            "mysql_host": config.MYSQL_HOST,
            "mysql_port": config.MYSQL_PORT,
            "mysql_user": config.MYSQL_USER,
            "mysql_password": config.MYSQL_PASSWORD,
            "mysql_database": config.MYSQL_DATABASE,
            "decision_interval_minutes":config.INTERVAL_MINUTES,

            # 飞书机器人配置
            "feishu_enabled": config.FEISHU_ENABLED,
            "feishu_webhook_url": config.FEISHU_WEBHOOK_URL,

            # 当前激活的Prompt
            "active_prompt": active_prompt
        }
    }


class ConfigUpdateRequest(BaseModel):
    """配置更新请求模型"""
    # OKX API配置
    okx_api_key: Optional[str] = None
    okx_secret_key: Optional[str] = None
    okx_passphrase: Optional[str] = None
    is_demo: Optional[bool] = None
    use_aws: Optional[bool] = None
    log_level: Optional[str] = None
    bot_start_time: Optional[str] = None

    # AI配置
    ai_provider: Optional[str] = None
    doubao_api_key: Optional[str] = None
    doubao_endpoint_id: Optional[str] = None
    doubao_model: Optional[str] = None
    doubao_timeout: Optional[int] = None
    deepseek_api_key: Optional[str] = None
    deepseek_model: Optional[str] = None
    deepseek_timeout: Optional[int] = None
    qwen_api_key: Optional[str] = None
    qwen_model: Optional[str] = None
    qwen_timeout: Optional[int] = None
    custom_ai_api_key: Optional[str] = None
    custom_ai_base_url: Optional[str] = None
    custom_ai_model: Optional[str] = None
    custom_ai_timeout: Optional[int] = None

    # 交易配置
    default_leverage: Optional[int] = None
    decision_interval_minutes:Optional[float]=None

    # 代理配置
    proxy: Optional[Dict] = None

    # 数据采集器配置
    collector_symbols: Optional[str] = None
    collector_timeframes: Optional[str] = None
    collector_history_days: Optional[int] = None
    collector_status_interval: Optional[int] = None
    collector_data_timeout: Optional[int] = None
    inst_id : Optional[str] = None

    # MySQL数据库配置
    mysql_host: Optional[str] = None
    mysql_port: Optional[int] = None
    mysql_user: Optional[str] = None
    mysql_password: Optional[str] = None
    mysql_database: Optional[str] = None

    # 飞书机器人配置
    feishu_enabled: Optional[bool] = None
    feishu_webhook_url: Optional[str] = None

@app.post("/api/config/update")
async def update_config(request: ConfigUpdateRequest):
    global account_api
    global okx_client
    global position_api
    global market_api
    global config
    """更新配置到.env文件（保持注释）"""
    try:
        env_file_path = os.path.join(project_root, '.env')

        # 读取现有.env文件，保留注释和格式
        env_lines = []
        env_vars = {}

        if os.path.exists(env_file_path):
            with open(env_file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    env_lines.append(line.rstrip('\n'))
                    stripped = line.strip()
                    if stripped and not stripped.startswith('#') and '=' in stripped:
                        key, value = stripped.split('=', 1)
                        env_vars[key.strip()] = value.strip()

        # 更新配置值的辅助函数
        def update_env_var(key: str, value, field_name: str):
            """更新环境变量值，保持在原位置或添加到末尾"""
            new_value = str(value)
            env_vars[key] = new_value
            updated_fields.append(field_name)

            # 在现有行中查找并更新
            found = False
            for i, line in enumerate(env_lines):
                stripped = line.strip()
                if stripped.startswith(f"{key}="):
                    env_lines[i] = f"{key}={new_value}"
                    found = True
                    break

            # 如果没找到，添加到末尾
            if not found:
                env_lines.append(f"{key}={new_value}")

        # 更新配置值
        updated_fields = []

        # OKX API配置
        if request.okx_api_key is not None:
            update_env_var('OKX_API_KEY', request.okx_api_key, 'okx_api_key')
            config.API_KEY = request.okx_api_key

        if request.okx_secret_key is not None:
            update_env_var('OKX_SECRET_KEY', request.okx_secret_key, 'okx_secret_key')
            config.SECRET_KEY = request.okx_secret_key

        if request.okx_passphrase is not None:
            update_env_var('OKX_PASSPHRASE', request.okx_passphrase, 'okx_passphrase')
            config.PASSPHRASE = request.okx_passphrase

        if request.is_demo is not None:
            update_env_var('OKX_IS_DEMO', 'true' if request.is_demo else 'false', 'is_demo')
            config.IS_DEMO = request.is_demo

        if request.use_aws is not None:
            update_env_var('OKX_USE_AWS', 'true' if request.use_aws else 'false', 'use_aws')
            config.USE_AWS = request.use_aws

        if request.log_level is not None:
            update_env_var('LOG_LEVEL', request.log_level, 'log_level')
            config.LOG_LEVEL = request.log_level

        if request.bot_start_time is not None:
            update_env_var('BOT_START_TIME', request.bot_start_time, 'bot_start_time')
            config.BOT_START_TIME = request.bot_start_time

        # AI配置
        if request.ai_provider is not None:
            update_env_var('AI_PROVIDER', request.ai_provider, 'ai_provider')
            config.AI_PROVIDER = request.ai_provider

        if request.doubao_api_key is not None:
            update_env_var('DOUBAO_API_KEY', request.doubao_api_key, 'doubao_api_key')
            config.DOUBAO_API_KEY = request.doubao_api_key

        if request.doubao_endpoint_id is not None:
            update_env_var('DOUBAO_ENDPOINT_ID', request.doubao_endpoint_id, 'doubao_endpoint_id')
            config.DOUBAO_ENDPOINT_ID = request.doubao_endpoint_id

        if request.doubao_model is not None:
            update_env_var('DOUBAO_MODEL', request.doubao_model, 'doubao_model')
            config.DOUBAO_MODEL = request.doubao_model

        if request.doubao_timeout is not None:
            update_env_var('DOUBAO_TIMEOUT', request.doubao_timeout, 'doubao_timeout')
            config.DOUBAO_TIMEOUT = request.doubao_timeout

        if request.deepseek_api_key is not None:
            update_env_var('DEEPSEEK_API_KEY', request.deepseek_api_key, 'deepseek_api_key')
            config.DEEPSEEK_API_KEY = request.deepseek_api_key

        if request.deepseek_model is not None:
            update_env_var('DEEPSEEK_MODEL', request.deepseek_model, 'deepseek_model')
            config.DEEPSEEK_MODEL = request.deepseek_model

        if request.deepseek_timeout is not None:
            update_env_var('DEEPSEEK_TIMEOUT', request.deepseek_timeout, 'deepseek_timeout')
            config.DEEPSEEK_TIMEOUT = request.deepseek_timeout

        if request.qwen_api_key is not None:
            update_env_var('QWEN_API_KEY', request.qwen_api_key, 'qwen_api_key')
            config.QWEN_API_KEY = request.qwen_api_key

        if request.qwen_model is not None:
            update_env_var('QWEN_MODEL', request.qwen_model, 'qwen_model')
            config.QWEN_MODEL = request.qwen_model

        if request.qwen_timeout is not None:
            update_env_var('QWEN_TIMEOUT', request.qwen_timeout, 'qwen_timeout')
            config.QWEN_TIMEOUT = request.qwen_timeout

        if request.custom_ai_api_key is not None:
            update_env_var('CUSTOM_AI_API_KEY', request.custom_ai_api_key, 'custom_ai_api_key')
            config.CUSTOM_AI_API_KEY = request.custom_ai_api_key

        if request.custom_ai_base_url is not None:
            update_env_var('CUSTOM_AI_BASE_URL', request.custom_ai_base_url.rstrip('/'), 'custom_ai_base_url')
            config.CUSTOM_AI_BASE_URL = request.custom_ai_base_url.rstrip('/')

        if request.custom_ai_model is not None:
            update_env_var('CUSTOM_AI_MODEL', request.custom_ai_model, 'custom_ai_model')
            config.CUSTOM_AI_MODEL = request.custom_ai_model

        if request.custom_ai_timeout is not None:
            update_env_var('CUSTOM_AI_TIMEOUT', request.custom_ai_timeout, 'custom_ai_timeout')
            config.CUSTOM_AI_TIMEOUT = request.custom_ai_timeout

        # 交易配置
        if request.default_leverage is not None:
            update_env_var('DEFAULT_LEVERAGE', request.default_leverage, 'default_leverage')
            config.DEFAULT_LEVERAGE = request.default_leverage
        if request.inst_id is not None:
            update_env_var('INST_ID',request.inst_id,'inst_id')
            config.INST_ID = request.inst_id
        if request.decision_interval_minutes is not None:
            update_env_var('INTERVAL_MINUTES', request.decision_interval_minutes, 'decision_interval_minutes')
            config.INTERVAL_MINUTES = request.decision_interval_minutes

        # 代理配置
        if request.proxy is not None:
            update_env_var('PROXY_ENABLED', 'true' if request.proxy.get('enabled', False) else 'false', 'proxy_enabled')
            config.PROXY_ENABLED=request.proxy.get('enabled', False)
            update_env_var('PROXY_TYPE', request.proxy.get('type', 'http'), 'proxy_type')
            config.PROXY_TYPE=request.proxy.get('type', 'http')
            update_env_var('PROXY_HOST', request.proxy.get('host', '127.0.0.1'), 'proxy_host')
            config.PROXY_HOST= request.proxy.get('host', '127.0.0.1')
            update_env_var('PROXY_PORT', str(request.proxy.get('port', 7890)), 'proxy_port')
            config.PROXY_PORT= str(request.proxy.get('port', 7890))
            update_env_var('PROXY_USERNAME', request.proxy.get('username', ''), 'proxy_username')
            config.PROXY_USERNAME=request.proxy.get('username', '')
            update_env_var('PROXY_PASSWORD', request.proxy.get('password', ''), 'proxy_password')
            config.PROXY_PASSWORD= request.proxy.get('password', '')

        # 数据采集器配置
        if request.collector_symbols is not None:
            update_env_var('COLLECTOR_SYMBOLS', request.collector_symbols, 'collector_symbols')
            config.COLLECTOR_SYMBOLS = request.collector_symbols

        if request.collector_timeframes is not None:
            update_env_var('COLLECTOR_TIMEFRAMES', request.collector_timeframes, 'collector_timeframes')
            config.COLLECTOR_TIMEFRAMES = request.collector_timeframes

        if request.collector_history_days is not None:
            update_env_var('COLLECTOR_HISTORY_DAYS', request.collector_history_days, 'collector_history_days')
            config.COLLECTOR_HISTORY_DAYS = request.collector_history_days

        if request.collector_status_interval is not None:
            update_env_var('COLLECTOR_STATUS_INTERVAL', request.collector_status_interval, 'collector_status_interval')
            config.COLLECTOR_STATUS_INTERVAL = request.collector_status_interval

        if request.collector_data_timeout is not None:
            update_env_var('COLLECTOR_DATA_TIMEOUT', request.collector_data_timeout, 'collector_data_timeout')
            config.COLLECTOR_DATA_TIMEOUT = request.collector_data_timeout

        # MySQL数据库配置
        if request.mysql_host is not None:
            update_env_var('MYSQL_HOST', request.mysql_host, 'mysql_host')
            config.MYSQL_HOST = request.mysql_host

        if request.mysql_port is not None:
            update_env_var('MYSQL_PORT', request.mysql_port, 'mysql_port')
            config.MYSQL_PORT = request.mysql_port

        if request.mysql_user is not None:
            update_env_var('MYSQL_USER', request.mysql_user, 'mysql_user')
            config.MYSQL_USER = request.mysql_user

        if request.mysql_password is not None:
            update_env_var('MYSQL_PASSWORD', request.mysql_password, 'mysql_password')
            config.MYSQL_PASSWORD = request.mysql_password

        if request.mysql_database is not None:
            update_env_var('MYSQL_DATABASE', request.mysql_database, 'mysql_database')
            config.MYSQL_DATABASE = request.mysql_database

        # 飞书机器人配置
        if request.feishu_enabled is not None:
            update_env_var('FEISHU_ENABLED', 'true' if request.feishu_enabled else 'false', 'feishu_enabled')
            config.FEISHU_ENABLED = request.feishu_enabled

        if request.feishu_webhook_url is not None:
            update_env_var('FEISHU_WEBHOOK_URL', request.feishu_webhook_url, 'feishu_webhook_url')
            config.FEISHU_WEBHOOK_URL = request.feishu_webhook_url

        # 写回.env文件（保持原有格式和dashboard注释）
        with open(env_file_path, 'w', encoding='utf-8') as f:
            for line in env_lines:
                f.write(line + '\n')
            # 强制刷新缓冲区
            f.flush()
            os.fsync(f.fileno())

        # 重新加载环境变量到当前进程
        from dotenv import load_dotenv
        load_dotenv(env_file_path, override=True)

        # 重新加载全局配置对象（确保DataManager等组件使用最新配置）
        from src.config.settings import reload_config
        config = reload_config()

        okx_client = getClient()
        account_api = AccountAPI(okx_client)
        position_api = PositionAPI(okx_client)
        market_api = MarketAPI(okx_client)

        # 提示：新启动的bot进程会自动读取更新后的.env文件
        logger.info(f"[OK] Config saved to {env_file_path} and reloaded into memory")

        return {
            "success": True,
            "message": f"配置已更新: {', '.join(updated_fields)}",
            "updated_fields": updated_fields,
            "note": "新启动的机器人将使用更新后的配置"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新配置失败: {str(e)}")


@app.get("/api/config/system_prompt")
async def get_system_prompt():
    """获取系统Prompt"""
    try:
        feature_engineer_path = os.path.join(project_root, 'src', 'ai', 'feature_engineer.py')

        if not os.path.exists(feature_engineer_path):
            raise HTTPException(status_code=404, detail="feature_engineer.py文件不存在")

        with open(feature_engineer_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 使用正则提取preset_prompt的内容
        import re
        pattern = r"preset_prompt\s*=\s*'''(.*?)'''"
        match = re.search(pattern, content, re.DOTALL)

        if match:
            prompt = match.group(1)
            return {
                "success": True,
                "data": {
                    "prompt": prompt
                }
            }
        else:
            raise HTTPException(status_code=404, detail="未找到preset_prompt定义")

    except Exception as e:
        logger.error(f"获取系统Prompt失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取系统Prompt失败: {str(e)}")


class SystemPromptRequest(BaseModel):
    """系统Prompt更新请求模型"""
    prompt: str


@app.post("/api/config/system_prompt")
async def update_system_prompt(request: SystemPromptRequest):
    """更新系统Prompt"""
    try:
        feature_engineer_path = os.path.join(project_root, 'src', 'ai', 'feature_engineer.py')

        if not os.path.exists(feature_engineer_path):
            raise HTTPException(status_code=404, detail="feature_engineer.py文件不存在")

        with open(feature_engineer_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 使用正则替换preset_prompt的内容
        import re
        pattern = r"(preset_prompt\s*=\s*''')(.*?)(''')"

        if not re.search(pattern, content, re.DOTALL):
            raise HTTPException(status_code=404, detail="未找到preset_prompt定义")

        # 替换内容
        new_content = re.sub(
            pattern,
            r"\1" + request.prompt + r"\3",
            content,
            flags=re.DOTALL
        )

        # 写回文件
        with open(feature_engineer_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())

        logger.info("[OK] System prompt updated successfully")

        return {
            "success": True,
            "message": "系统Prompt已更新，需要重启交易机器人才能生效"
        }

    except Exception as e:
        logger.error(f"更新系统Prompt失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新系统Prompt失败: {str(e)}")


# ==================== Prompt管理 API ====================

@app.get("/api/prompts")
async def get_prompts():
    """获取所有Prompt列表"""
    try:
        prompts_file = os.path.join(project_root, 'data', 'prompts.json')

        if not os.path.exists(prompts_file):
            return {"success": True, "data": []}

        with open(prompts_file, 'r', encoding='utf-8') as f:
            prompts = json.load(f)

        return {"success": True, "data": prompts}
    except Exception as e:
        logger.error(f"获取Prompt列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取Prompt列表失败: {str(e)}")


@app.get("/api/prompts/{prompt_name}")
async def get_prompt_detail(prompt_name: str):
    """获取单个Prompt详情"""
    try:
        prompts_file = os.path.join(project_root, 'data', 'prompts.json')

        if not os.path.exists(prompts_file):
            raise HTTPException(status_code=404, detail="Prompt不存在")

        with open(prompts_file, 'r', encoding='utf-8') as f:
            prompts = json.load(f)

        # 查找Prompt
        prompt = None
        for p in prompts:
            if p['name'] == prompt_name:
                prompt = p
                break

        if not prompt:
            raise HTTPException(status_code=404, detail="Prompt不存在")

        return {"success": True, "data": prompt}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取Prompt详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取Prompt详情失败: {str(e)}")


class PromptRequest(BaseModel):
    """Prompt请求模型"""
    name: str
    description: Optional[str] = ""
    content: str


@app.post("/api/prompts")
async def create_prompt(request: PromptRequest):
    """创建新Prompt"""
    try:
        prompts_file = os.path.join(project_root, 'data', 'prompts.json')
        os.makedirs(os.path.dirname(prompts_file), exist_ok=True)

        # 加载现有Prompt
        prompts = []
        if os.path.exists(prompts_file):
            with open(prompts_file, 'r', encoding='utf-8') as f:
                prompts = json.load(f)

        # 检查名称是否重复
        if any(p['name'] == request.name for p in prompts):
            raise HTTPException(status_code=400, detail="Prompt名称已存在")

        # 添加新Prompt
        new_prompt = {
            'name': request.name,
            'description': request.description,
            'content': request.content,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        prompts.append(new_prompt)

        # 保存
        with open(prompts_file, 'w', encoding='utf-8') as f:
            json.dump(prompts, f, ensure_ascii=False, indent=2)

        logger.info(f"[OK] Prompt创建成功: {request.name}")

        return {
            "success": True,
            "message": f"Prompt \"{request.name}\" 创建成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建Prompt失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建Prompt失败: {str(e)}")


@app.put("/api/prompts/{prompt_name}")
async def update_prompt(prompt_name: str, request: PromptRequest):
    """更新Prompt"""
    try:
        prompts_file = os.path.join(project_root, 'data', 'prompts.json')

        if not os.path.exists(prompts_file):
            raise HTTPException(status_code=404, detail="Prompt不存在")

        with open(prompts_file, 'r', encoding='utf-8') as f:
            prompts = json.load(f)

        # 查找并更新Prompt
        prompt_found = False
        for p in prompts:
            if p['name'] == prompt_name:
                p['description'] = request.description
                p['content'] = request.content
                p['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                prompt_found = True
                break

        if not prompt_found:
            raise HTTPException(status_code=404, detail="Prompt不存在")

        # 保存
        with open(prompts_file, 'w', encoding='utf-8') as f:
            json.dump(prompts, f, ensure_ascii=False, indent=2)

        logger.info(f"[OK] Prompt更新成功: {prompt_name}")

        return {
            "success": True,
            "message": f"Prompt \"{prompt_name}\" 更新成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新Prompt失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新Prompt失败: {str(e)}")


@app.delete("/api/prompts/{prompt_name}")
async def delete_prompt(prompt_name: str):
    """删除Prompt"""
    try:
        prompts_file = os.path.join(project_root, 'data', 'prompts.json')

        if not os.path.exists(prompts_file):
            raise HTTPException(status_code=404, detail="Prompt不存在")

        with open(prompts_file, 'r', encoding='utf-8') as f:
            prompts = json.load(f)

        # 检查是否是当前使用的Prompt
        env_file_path = os.path.join(project_root, '.env')
        active_prompt = None
        if os.path.exists(env_file_path):
            with open(env_file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith('ACTIVE_PROMPT='):
                        active_prompt = stripped.split('=', 1)[1].strip()
                        break

        if active_prompt == prompt_name:
            raise HTTPException(status_code=400, detail="无法删除正在使用的Prompt，请先切换到其他Prompt")

        # 过滤掉要删除的Prompt
        original_count = len(prompts)
        prompts = [p for p in prompts if p['name'] != prompt_name]

        if len(prompts) == original_count:
            raise HTTPException(status_code=404, detail="Prompt不存在")

        # 保存
        with open(prompts_file, 'w', encoding='utf-8') as f:
            json.dump(prompts, f, ensure_ascii=False, indent=2)

        logger.info(f"[OK] Prompt删除成功: {prompt_name}")

        return {
            "success": True,
            "message": f"Prompt \"{prompt_name}\" 已删除"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除Prompt失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除Prompt失败: {str(e)}")


@app.post("/api/prompts/{prompt_name}/use")
async def use_prompt(prompt_name: str):
    """应用指定的Prompt到交易策略（设置为当前激活的Prompt）"""
    try:
        logger.info(f"[DEBUG] 尝试应用Prompt: {prompt_name}")
        prompts_file = os.path.join(project_root, 'data', 'prompts.json')
        logger.info(f"[DEBUG] Prompts文件路径: {prompts_file}")

        if not os.path.exists(prompts_file):
            logger.error(f"[ERROR] Prompts文件不存在: {prompts_file}")
            raise HTTPException(status_code=404, detail="Prompts配置文件不存在")

        with open(prompts_file, 'r', encoding='utf-8') as f:
            prompts = json.load(f)

        logger.info(f"[DEBUG] 找到 {len(prompts)} 个Prompts")
        logger.info(f"[DEBUG] Prompt名称列表: {[p['name'] for p in prompts]}")

        # 查找Prompt
        prompt = None
        for p in prompts:
            if p['name'] == prompt_name:
                prompt = p
                break

        if not prompt:
            logger.error(f"[ERROR] Prompt '{prompt_name}' 不存在于列表中")
            raise HTTPException(status_code=404, detail=f"Prompt '{prompt_name}' 不存在")

        logger.info(f"[DEBUG] 找到Prompt: {prompt['name']}")

        # 保存active_prompt到.env文件
        env_file_path = os.path.join(project_root, '.env')
        env_lines = []

        if os.path.exists(env_file_path):
            with open(env_file_path, 'r', encoding='utf-8') as f:
                env_lines = [line.rstrip('\n') for line in f]

        # 更新或添加ACTIVE_PROMPT
        found = False
        for i, line in enumerate(env_lines):
            stripped = line.strip()
            if stripped.startswith('ACTIVE_PROMPT='):
                env_lines[i] = f"ACTIVE_PROMPT={prompt_name}"
                found = True
                break

        if not found:
            env_lines.append(f"ACTIVE_PROMPT={prompt_name}")

        # 写回.env文件
        with open(env_file_path, 'w', encoding='utf-8') as f:
            for line in env_lines:
                f.write(line + '\n')
            f.flush()
            os.fsync(f.fileno())

        logger.info(f"[OK] Prompt应用成功: {prompt_name}")

        return {
            "success": True,
            "message": f"已应用Prompt \"{prompt_name}\"，下次启动交易机器人时将使用此Prompt"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"应用Prompt失败: {e}")
        raise HTTPException(status_code=500, detail=f"应用Prompt失败: {str(e)}")


@app.get("/api/stats")
async def get_statistics():
    """获取统计数据"""
    try:
        from src.ai.data_manager import DataManager
        data_manager = DataManager()

        # 获取当前配置的API_KEY
        api_key = config.API_KEY if config.API_KEY else 'default'

        # 从MySQL获取今日AI决策次数
        stats = data_manager.get_ai_conversation_stats(api_key=api_key)
        today_decisions = stats.get('today_decisions', 0)

        # 从SQLite获取今日交易次数（strategy_logs仍在SQLite中）
        today_trades = 0
        try:
            with get_db_connection_ctx() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT COUNT(*) as count
                    FROM strategy_logs
                    WHERE log_type = 'trade' AND log_level = 'success'
                    AND date(created_at) = date('now')
                ''')
                result = cursor.fetchone()
                today_trades = result['count'] if result else 0
        except Exception as e:
            logger.warning(f"获取今日交易次数失败: {e}")

        return {
            "success": True,
            "data": {
                "today_decisions": today_decisions,
                "today_trades": today_trades,
                "signal_distribution": {},
                "avg_confidence": 0.0
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions")
async def get_sessions():
    """获取所有会话ID"""
    try:
        from src.ai.data_manager import DataManager
        data_manager = DataManager()

        # 获取当前配置的API_KEY
        api_key = config.API_KEY if config.API_KEY else 'default'

        # 从MySQL获取会话列表
        sessions = data_manager.get_conversation_sessions(
            limit=20,
            api_key=api_key
        )

        return {"success": True, "data": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/market/ticker")
async def get_market_ticker(inst_id: str = "BTC-USDT-SWAP"):
    """获取市场行情"""
    if not market_api:
        raise HTTPException(status_code=500, detail="API未配置")

    try:
        result = market_api.get_ticker(inst_id)
        if result['code'] == '0':
            return {"success": True, "data": result['data'][0]}
        else:
            raise HTTPException(status_code=500, detail=result.get('msg', '获取行情失败'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/instruments")
async def get_trading_instruments():
    """获取所有交易产品列表（USDT永续合约）"""
    try:
        instruments = get_instruments_cache()
        return {
            "success": True,
            "data": instruments
        }
    except Exception as e:
        logger.error(f"获取交易产品列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/bot/status")
async def get_bot_status():
    """获取机器人运行状态"""
    try:
        is_running = is_bot_running()

        status_info = {
            "success": True,
            "is_running": is_running,
            "status": "运行中" if is_running else "已停止"
        }

        # 如果正在运行，尝试获取PID
        if is_running and os.path.exists(PID_FILE):
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
                status_info["pid"] = pid

        return status_info

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取状态失败: {str(e)}")


@app.post("/api/bot/start")
async def start_trading_bot():
    """启动交易机器人"""
    try:
        result = start_bot()
        if result['success']:
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get('error', '启动失败'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动失败: {str(e)}")


@app.post("/api/bot/stop")
async def stop_trading_bot():
    """停止交易机器人"""
    try:
        result = stop_bot()
        if result['success']:
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get('error', '停止失败'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"停止失败: {str(e)}")


# ==================== 数据采集器控制 API ====================

@app.get("/api/collector/status")
async def get_collector_status_api():
    """获取数据采集器状态"""
    try:
        status = get_collector_status()
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取状态失败: {str(e)}")


@app.post("/api/collector/start")
async def start_data_collector():
    """启动数据采集器"""
    try:
        result = start_collector()
        if result['success']:
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get('error', '启动失败'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动失败: {str(e)}")


@app.post("/api/collector/stop")
async def stop_data_collector():
    """停止数据采集器"""
    try:
        result = stop_collector()
        if result['success']:
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get('error', '停止失败'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"停止失败: {str(e)}")



@app.get("/api/balance/history")
async def get_balance_history(days: int = 30, granularity: str = 'daily'):
    global account_api
    global okx_client
    """
    获取余额历史数据（用于余额收益曲线图）- 使用数据库缓存增量更新

    Args:
        days: 天数（默认30，当使用BOT_START_TIME时会被覆盖，仅作为后备）
        granularity: 数据粒度，'daily'（按天）或 'hourly'（按小时），默认 'daily'
    """
    if not account_api:
        raise HTTPException(status_code=500, detail="API未配置")

    try:
        from src.ai.data_manager import DataManager
        data_manager = DataManager()

        # 获取当前配置的API_KEY（用于区分不同账户的数据）
        api_key = config.API_KEY if config.API_KEY else 'default'

        # 1. 确定查询起始时间：优先使用BOT_START_TIME（精确时间戳），否则使用days参数
        end_time = datetime.now()

        if config.BOT_START_TIME:
            try:
                # 解析BOT_START_TIME作为起始时间（精确到秒）
                bot_start = datetime.strptime(config.BOT_START_TIME, '%Y-%m-%d %H:%M:%S')
                start_time = bot_start
                start_ts = int(bot_start.timestamp() * 1000)  # 精确的毫秒时间戳
                logger.info(f"使用BOT_START_TIME作为起始时间: {config.BOT_START_TIME}（时间戳: {start_ts}）")
            except ValueError:
                # 解析失败，使用days参数
                start_time = end_time - timedelta(days=days)
                start_ts = int(start_time.timestamp() * 1000)
                logger.warning(f"BOT_START_TIME格式错误，使用 {days} 天作为查询范围")
        else:
            # 未配置BOT_START_TIME，使用days参数
            start_time = end_time - timedelta(days=days)
            start_ts = int(start_time.timestamp() * 1000)
            logger.info(f"未配置BOT_START_TIME，使用 {days} 天作为查询范围")

        # 2. 先尝试从数据库获取历史数据（根据粒度选择不同的聚合方法，传递精确时间戳和api_key）
        if granularity == 'hourly':
            cached_data = data_manager.get_hourly_balance_from_bills(ccy='USDT', start_timestamp=start_ts, api_key=api_key)
        else:
            cached_data = data_manager.get_daily_balance_from_bills(ccy='USDT', start_timestamp=start_ts, api_key=api_key)

        # 3. 获取数据库中最新账单的时间戳（传递api_key）
        latest_ts = data_manager.get_latest_bill_timestamp(ccy='USDT', api_key=api_key)

        # 4. 确定需要查询的时间范围
        end_ts = int(end_time.timestamp() * 1000)

        # 如果数据库中有数据，从最新时间戳开始查询；否则查询从start_ts开始
        if latest_ts and latest_ts >= start_ts:
            # 数据库中有数据且在查询范围内，增量查询
            query_start_ts = latest_ts + 1  # 从最新记录的下一毫秒开始
            logger.info(f"数据库中已有账单数据，增量查询从 {datetime.fromtimestamp(latest_ts/1000)} 开始")
        else:
            # 数据库无数据或数据太旧，从start_ts开始查询
            query_start_ts = start_ts
            logger.info(f"数据库无账单数据或数据过期，全量查询从 {start_time} 开始")

        # 5. 分页获取新的账单数据（只查询增量部分）
        new_bills = []
        after = None
        max_pages = 50
        page_count = 0

        while page_count < max_pages:
            params = {
                'ccy': 'USDT',
                'begin': str(query_start_ts),
                'end': str(end_ts),
                'limit': '100'
            }

            if after:
                params['after'] = after
            okx_client=getClient()
            account_api=AccountAPI(okx_client)

            result = account_api.get_bills_archive(**params)

            if result['code'] != '0':
                # API调用失败，但如果数据库有缓存数据，仍然可以返回
                if cached_data:
                    logger.warning(f"API调用失败，使用缓存数据: {result.get('msg')}")
                    break
                else:
                    raise HTTPException(status_code=500, detail=result.get('msg', '获取账单历史失败'))

            bills = result.get('data', [])

            if not bills:
                break

            new_bills.extend(bills)
            page_count += 1

            if len(bills) < 100:
                break

            after = bills[-1].get('billId')
            if not after:
                break

        # 6. 将新获取的账单保存到数据库（传递api_key）
        if new_bills:
            success_count, fail_count = data_manager.save_bills_batch(new_bills, api_key=api_key)
            logger.info(f"[OK] Saved new bills: {success_count} succeeded, {fail_count} failed (total {len(new_bills)})")

            # 重新从数据库获取完整数据（使用精确时间戳和api_key）
            if granularity == 'hourly':
                cached_data = data_manager.get_hourly_balance_from_bills(ccy='USDT', start_timestamp=start_ts, api_key=api_key)
            else:
                cached_data = data_manager.get_daily_balance_from_bills(ccy='USDT', start_timestamp=start_ts, api_key=api_key)
        else:
            logger.info("[OK] No new bills to update")

        # 7. 如果数据库仍然没有数据，返回当前余额
        if not cached_data:
            balance_result = account_api.get_usdt_balance()
            if balance_result['success']:
                current_balance = float(balance_result.get('totalEq', 0))
                return {
                    "success": True,
                    "data": [
                        {
                            "timestamp": end_time.isoformat(),
                            "balance": current_balance
                        }
                    ]
                }
            else:
                raise HTTPException(status_code=500, detail='无法获取当前余额')

        # 8. 填补缺失的日期数据（以每天00:00为准）
        now = datetime.now()

        if granularity == 'daily' and cached_data:
            # 生成完整的日期列表
            start_date = start_time.date()
            end_date = now.date()

            # 将现有数据转换为字典，方便查找
            existing_data = {}
            for item in cached_data:
                date_key = item.get('date')
                if date_key:
                    existing_data[date_key] = item

            # 填补缺失的日期
            filled_data = []
            current_date = start_date
            last_balance = None

            while current_date <= end_date:
                date_str = current_date.strftime('%Y-%m-%d')

                if date_str in existing_data:
                    # 该日期有数据
                    item = existing_data[date_str]
                    last_balance = item['balance']

                    # 统一时间戳为当天00:00
                    day_start = datetime.combine(current_date, datetime.min.time())
                    filled_data.append({
                        'date': date_str,
                        'balance': item['balance'],
                        'timestamp': day_start.isoformat(),
                        'ts': int(day_start.timestamp() * 1000)
                    })
                elif last_balance is not None:
                    # 该日期无数据，使用上一次的余额填补
                    day_start = datetime.combine(current_date, datetime.min.time())
                    filled_data.append({
                        'date': date_str,
                        'balance': last_balance,
                        'timestamp': day_start.isoformat(),
                        'ts': int(day_start.timestamp() * 1000),
                        'filled': True  # 标记为填补数据
                    })

                current_date += timedelta(days=1)

            cached_data = filled_data
            logger.info(f"日期数据填补完成: {len(filled_data)} 天，原始数据 {len(existing_data)} 天")

        # 9. 确保包含当前时间的余额（如果最新数据不是当前时间段，添加当前余额）
        if granularity == 'hourly':
            current_key = now.strftime('%Y-%m-%d %H:00')
            has_current = any(item.get('datetime') == current_key for item in cached_data)
        else:
            current_key = now.strftime('%Y-%m-%d')
            has_current = any(item.get('date') == current_key for item in cached_data)

        if not has_current:
            balance_result = account_api.get_usdt_balance()
            if balance_result['success']:
                current_balance = float(balance_result.get('totalEq', 0))
                if granularity == 'hourly':
                    cached_data.append({
                        'datetime': current_key,
                        'balance': current_balance,
                        'timestamp': now.isoformat(),
                        'ts': int(now.timestamp() * 1000)
                    })
                else:
                    # 以当天00:00为准
                    day_start = datetime.combine(now.date(), datetime.min.time())
                    cached_data.append({
                        'date': current_key,
                        'balance': current_balance,
                        'timestamp': day_start.isoformat(),
                        'ts': int(day_start.timestamp() * 1000)
                    })
                # 重新排序
                cached_data = sorted(cached_data, key=lambda x: x.get('datetime') or x.get('date'))

        # 10. 转换为前端需要的格式
        chart_data = [
            {
                "timestamp": item['timestamp'],
                "balance": round(item['balance'], 2)
            }
            for item in cached_data
        ]

        return {
            "success": True,
            "data": chart_data
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取余额历史失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"获取余额历史失败: {str(e)}")


# ==================== OKX账户管理 API ====================

@app.get("/api/okx/accounts")
async def get_okx_accounts():
    """获取OKX账户列表"""
    try:
        accounts_file = os.path.join(project_root, 'data', 'okx_accounts.json')

        if not os.path.exists(accounts_file):
            return {"success": True, "data": []}

        with open(accounts_file, 'r', encoding='utf-8') as f:
            accounts = json.load(f)

        return {"success": True, "data": accounts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取账户列表失败: {str(e)}")


class OKXAccountRequest(BaseModel):
    """OKX账户请求模型"""
    name: str
    api_key: str
    secret_key: str
    passphrase: str
    is_demo: bool = False
    use_aws: bool = False


@app.post("/api/okx/accounts")
async def add_okx_account(request: OKXAccountRequest):
    """添加OKX账户"""
    try:
        accounts_file = os.path.join(project_root, 'data', 'okx_accounts.json')
        os.makedirs(os.path.dirname(accounts_file), exist_ok=True)

        # 加载现有账户
        accounts = []
        if os.path.exists(accounts_file):
            with open(accounts_file, 'r', encoding='utf-8') as f:
                accounts = json.load(f)

        # 检查名称是否重复
        if any(acc['name'] == request.name for acc in accounts):
            raise HTTPException(status_code=400, detail="账户名称已存在")

        # 验证API
        from src.core.rest_client import OKXRestClient
        from src.api.account import AccountAPI

        # 构建代理配置
        proxy_url = None
        if config.PROXY_ENABLED:
            if config.PROXY_USERNAME and config.PROXY_PASSWORD:
                proxy_url = f'{config.PROXY_TYPE}://{config.PROXY_USERNAME}:{config.PROXY_PASSWORD}@{config.PROXY_HOST}:{config.PROXY_PORT}'
            else:
                proxy_url = f'{config.PROXY_TYPE}://{config.PROXY_HOST}:{config.PROXY_PORT}'

        test_client = OKXRestClient(
            api_key=request.api_key,
            secret_key=request.secret_key,
            passphrase=request.passphrase,
            is_demo=request.is_demo,
            use_proxy=config.PROXY_ENABLED,
            proxy=proxy_url
        )
        test_account_api = AccountAPI(test_client)
        balance_result = test_account_api.get_usdt_balance()

        if not balance_result['success']:
            raise HTTPException(status_code=400, detail=f"API验证失败: {balance_result.get('error', 'Unknown error')}")

        # 添加账户
        account = {
            'name': request.name,
            'api_key': request.api_key,
            'secret_key': request.secret_key,
            'passphrase': request.passphrase,
            'is_demo': request.is_demo,
            'use_aws': request.use_aws,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        accounts.append(account)

        # 保存
        with open(accounts_file, 'w', encoding='utf-8') as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)

        return {
            "success": True,
            "message": f"✅ 账户添加成功，可用余额: {balance_result.get('available', 0)} USDT"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加账户失败: {str(e)}")


@app.put("/api/okx/accounts/{account_name}")
async def update_okx_account(account_name: str, request: OKXAccountRequest):
    """更新OKX账户"""
    try:
        accounts_file = os.path.join(project_root, 'data', 'okx_accounts.json')

        if not os.path.exists(accounts_file):
            raise HTTPException(status_code=404, detail="账户不存在")

        with open(accounts_file, 'r', encoding='utf-8') as f:
            accounts = json.load(f)

        # 查找账户
        account_found = False
        for acc in accounts:
            if acc['name'] == account_name:
                acc['name'] = request.name
                acc['api_key'] = request.api_key
                acc['secret_key'] = request.secret_key
                acc['passphrase'] = request.passphrase
                acc['is_demo'] = request.is_demo
                acc['use_aws'] = request.use_aws
                acc['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                account_found = True
                break

        if not account_found:
            raise HTTPException(status_code=404, detail="账户不存在")

        # 保存
        with open(accounts_file, 'w', encoding='utf-8') as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)

        return {"success": True, "message": "账户更新成功"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新账户失败: {str(e)}")


@app.delete("/api/okx/accounts/{account_name}")
async def delete_okx_account(account_name: str):
    """删除OKX账户"""
    try:
        accounts_file = os.path.join(project_root, 'data', 'okx_accounts.json')

        if not os.path.exists(accounts_file):
            raise HTTPException(status_code=404, detail="账户不存在")

        with open(accounts_file, 'r', encoding='utf-8') as f:
            accounts = json.load(f)

        # 过滤掉要删除的账户
        original_count = len(accounts)
        accounts = [acc for acc in accounts if acc['name'] != account_name]

        if len(accounts) == original_count:
            raise HTTPException(status_code=404, detail="账户不存在")

        # 保存
        with open(accounts_file, 'w', encoding='utf-8') as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)

        return {"success": True, "message": "账户已删除"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除账户失败: {str(e)}")


@app.post("/api/okx/accounts/{account_name}/use")
async def use_okx_account(account_name: str):
    global account_api
    global okx_client
    global position_api
    global market_api
    """使用指定的OKX账户（保存到.env）"""
    try:
        accounts_file = os.path.join(project_root, 'data', 'okx_accounts.json')

        if not os.path.exists(accounts_file):
            raise HTTPException(status_code=404, detail="账户不存在")

        with open(accounts_file, 'r', encoding='utf-8') as f:
            accounts = json.load(f)

        # 查找账户
        account = None
        for acc in accounts:
            if acc['name'] == account_name:
                account = acc
                break

        if not account:
            raise HTTPException(status_code=404, detail="账户不存在")

        # 保存到.env
        env_file_path = os.path.join(project_root, '.env')
        config_data = {
            'OKX_API_KEY': account['api_key'],
            'OKX_SECRET_KEY': account['secret_key'],
            'OKX_PASSPHRASE': account['passphrase'],
            'OKX_IS_DEMO': 'true' if account['is_demo'] else 'false',
            'OKX_USE_AWS': 'true' if account['use_aws'] else 'false'
        }
        config.API_KEY=account['api_key']
        config.SECRET_KEY=account['secret_key']
        config.PASSPHRASE=account['passphrase']
        config.IS_DEMO= True if account['is_demo'] else False
        config.USE_AWS= True if account['use_aws'] else False
        

        # 更新.env文件
        env_lines = []
        env_vars = {}

        if os.path.exists(env_file_path):
            with open(env_file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    env_lines.append(line.rstrip('\n'))
                    stripped = line.strip()
                    if stripped and not stripped.startswith('#') and '=' in stripped:
                        key, value = stripped.split('=', 1)
                        env_vars[key.strip()] = value.strip()

        # 更新配置值
        for key, value in config_data.items():
            found = False
            for i, line in enumerate(env_lines):
                stripped = line.strip()
                if stripped.startswith(f"{key}="):
                    env_lines[i] = f"{key}={value}"
                    found = True
                    break
            if not found:
                env_lines.append(f"{key}={value}")

        # 写回文件
        with open(env_file_path, 'w', encoding='utf-8') as f:
            for line in env_lines:
                f.write(line + '\n')
            f.flush()
            os.fsync(f.fileno())
            
        okx_client = getClient()
        account_api = AccountAPI(okx_client)
        position_api = PositionAPI(okx_client)
        market_api = MarketAPI(okx_client)

        return {
            "success": True,
            "message": f"已切换到账户 \"{account_name}\"，配置已保存到 .env 文件"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"切换账户失败: {str(e)}")
# ==================== 日志查看 ====================

@app.get("/api/logs/bot")
async def get_bot_logs(lines: int = 100, level: Optional[str] = None):
    """
    获取交易机器人日志

    Args:
        lines: 返回最后N行日志（默认100）
        level: 过滤日志级别（可选：INFO, WARNING, ERROR, SUCCESS, DEBUG）
    """
    try:
        # 获取今天的日志文件
        today = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(project_root, 'logs', f'trading_bot_{today}.log')

        if not os.path.exists(log_file):
            # 如果今天的日志不存在，尝试获取最新的日志文件
            logs_dir = os.path.join(project_root, 'logs')
            if os.path.exists(logs_dir):
                bot_logs = [f for f in os.listdir(logs_dir) if f.startswith('trading_bot_') and f.endswith('.log')]
                if bot_logs:
                    bot_logs.sort(reverse=True)  # 按文件名倒序排序（日期最新的在前）
                    log_file = os.path.join(logs_dir, bot_logs[0])
                else:
                    return {
                        "success": True,
                        "data": [],
                        "message": "暂无交易机器人日志"
                    }
            else:
                return {
                    "success": True,
                    "data": [],
                    "message": "日志目录不存在"
                }

        # 读取日志文件
        logs = []
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                all_lines = f.readlines()

                # 解析日志行并过滤级别
                import re
                parsed_logs = []
                for line in all_lines:
                    line = line.strip()
                    if not line:
                        continue

                    # 解析loguru格式的日志: YYYY-MM-DD HH:mm:ss.SSS | LEVEL | module:function:line - message
                    match = re.match(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\s*\|\s*(\w+)\s*\|\s*(.+)', line)
                    if match:
                        timestamp, log_level, rest = match.groups()

                        # 提取消息部分（去掉模块信息）
                        # 格式: module:function:line - message
                        if ' - ' in rest:
                            message = rest.split(' - ', 1)[1]
                        else:
                            message = rest
                            
                        message=f'{timestamp} | {message}'
                        # 如果指定了级别过滤
                        if level and log_level.strip().upper() != level.upper():
                            continue

                        parsed_logs.append({
                            "timestamp": timestamp,
                            "level": log_level.strip(),
                            "message": message
                        })
                    else:
                        # 如果解析失败，将整行作为消息
                        # 如果没有级别过滤或者级别为INFO，则显示
                        if not level or level.upper() == 'INFO':
                            parsed_logs.append({
                                "timestamp": "",
                                "level": "INFO",
                                "message": line
                            })

                # 获取最后N条符合条件的日志
                logs = parsed_logs[-lines:] if len(parsed_logs) > lines else parsed_logs

        except Exception as e:
            logger.error(f"读取日志文件失败: {e}")
            raise HTTPException(status_code=500, detail=f"读取日志文件失败: {str(e)}")

        return {
            "success": True,
            "data": logs,
            "log_file": log_file
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取Bot日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取Bot日志失败: {str(e)}")


@app.get("/api/logs/collector")
async def get_collector_logs(lines: int = 100, level: Optional[str] = None):
    """
    获取数据采集器日志

    Args:
        lines: 返回最后N行日志（默认100）
        level: 过滤日志级别（可选：INFO, WARNING, ERROR, SUCCESS, DEBUG）
    """
    try:
        # 获取今天的日志文件
        today = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(project_root, 'logs', f'data_collector_{today}.log')

        if not os.path.exists(log_file):
            # 如果今天的日志不存在，尝试获取最新的日志文件
            logs_dir = os.path.join(project_root, 'logs')
            if os.path.exists(logs_dir):
                collector_logs = [f for f in os.listdir(logs_dir) if f.startswith('data_collector_') and f.endswith('.log')]
                if collector_logs:
                    collector_logs.sort(reverse=True)  # 按文件名倒序排序（日期最新的在前）
                    log_file = os.path.join(logs_dir, collector_logs[0])
                else:
                    return {
                        "success": True,
                        "data": [],
                        "message": "暂无数据采集器日志"
                    }
            else:
                return {
                    "success": True,
                    "data": [],
                    "message": "日志目录不存在"
                }

        # 读取日志文件
        logs = []
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                all_lines = f.readlines()

                # 解析日志行并过滤级别
                import re
                parsed_logs = []
                for line in all_lines:
                    line = line.strip()
                    if not line:
                        continue

                    # 解析loguru格式的日志: YYYY-MM-DD HH:mm:ss.SSS | LEVEL | module:function:line - message
                    match = re.match(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\s*\|\s*(\w+)\s*\|\s*(.+)', line)
                    if match:
                        timestamp, log_level, rest = match.groups()

                        # 提取消息部分（去掉模块信息）
                        # 格式: module:function:line - message
                        if ' - ' in rest:
                            message = rest.split(' - ', 1)[1]
                        else:
                            message = rest

                        # 如果指定了级别过滤
                        if level and log_level.strip().upper() != level.upper():
                            continue

                        parsed_logs.append({
                            "timestamp": timestamp,
                            "level": log_level.strip(),
                            "message": message
                        })
                    else:
                        # 如果解析失败，将整行作为消息
                        # 如果没有级别过滤或者级别为INFO，则显示
                        if not level or level.upper() == 'INFO':
                            parsed_logs.append({
                                "timestamp": "",
                                "level": "INFO",
                                "message": line
                            })

                # 获取最后N条符合条件的日志
                logs = parsed_logs[-lines:] if len(parsed_logs) > lines else parsed_logs

        except Exception as e:
            logger.error(f"读取日志文件失败: {e}")
            raise HTTPException(status_code=500, detail=f"读取日志文件失败: {str(e)}")

        return {
            "success": True,
            "data": logs,
            "log_file": log_file
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取Collector日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取Collector日志失败: {str(e)}")


# ==================== 环境检测 API ====================

@app.get("/api/environment/check")
async def check_environment():
    """检测环境配置状态（完整版本）"""
    try:
        result = {
            "success": True,
            "data": {
                "okx_connection": {"status": False, "message": ""},
                "mysql_connection": {"status": False, "message": ""},
                "redis_connection": {"status": False, "message": ""},
                "okx_api_configured": {"status": False, "message": ""}
            }
        }

        # 1. 检查OKX网站可访问性（使用代理如果已配置）
        try:
            import requests
            if config.PROXY_ENABLED:
                proxy=f'http://{config.PROXY_USERNAME}:{config.PROXY_PASSWORD}@{config.PROXY_HOST}:{config.PROXY_PORT}' if config.PROXY_USERNAME else f'http://{config.PROXY_HOST}:{config.PROXY_PORT}'
                proxies = {
                    'http': proxy,
                    'https': proxy
                }
            else:
                proxies=None
            response = requests.get("https://www.okx.com", timeout=5, proxies=proxies)
            if response.status_code == 200:
                result["data"]["okx_connection"]["status"] = True
                result["data"]["okx_connection"]["message"] = "连接成功"
            else:
                result["data"]["okx_connection"]["message"] = f"HTTP {response.status_code}"
        except Exception as e:
            result["data"]["okx_connection"]["message"] = str(e)

        # 2. 检查MySQL连接
        try:
            import pymysql
            conn = pymysql.connect(
                host=config.MYSQL_HOST,
                port=config.MYSQL_PORT,
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                database=config.MYSQL_DATABASE,
                connect_timeout=5
            )
            conn.close()
            result["data"]["mysql_connection"]["status"] = True
            result["data"]["mysql_connection"]["message"] = "连接成功"
        except ImportError:
            result["data"]["mysql_connection"]["message"] = "未安装pymysql模块"
        except Exception as e:
            result["data"]["mysql_connection"]["message"] = str(e)

        # 3. 检查Redis连接
        try:
            import redis
            # 从配置中获取Redis连接信息（假设在环境变量中）
            redis_host = os.getenv('REDIS_HOST', 'localhost')
            redis_port = int(os.getenv('REDIS_PORT', '6379'))
            redis_password = os.getenv('REDIS_PASSWORD', None)

            r = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                socket_connect_timeout=5
            )
            r.ping()
            result["data"]["redis_connection"]["status"] = True
            result["data"]["redis_connection"]["message"] = "连接成功"
        except ImportError:
            result["data"]["redis_connection"]["message"] = "未安装redis模块"
        except Exception as e:
            result["data"]["redis_connection"]["message"] = str(e)

        # 4. 检查OKX API是否已配置
        if config.API_KEY and config.SECRET_KEY and config.PASSPHRASE:
            result["data"]["okx_api_configured"]["status"] = True
            result["data"]["okx_api_configured"]["message"] = "已配置"
        else:
            result["data"]["okx_api_configured"]["message"] = "未配置API密钥"

        return result
    except Exception as e:
        logger.error(f"环境检测失败: {e}")
        raise HTTPException(status_code=500, detail=f"环境检测失败: {str(e)}")


@app.get("/api/environment/is_first_visit")
async def is_first_visit():
    """检查是否是首次访问"""
    global _first_visit_flag
    try:
        return {
            "success": True,
            "data": {
                "is_first_visit": _first_visit_flag
            }
        }
    except Exception as e:
        logger.error(f"检查首次访问失败: {e}")
        raise HTTPException(status_code=500, detail=f"检查首次访问失败: {str(e)}")


@app.post("/api/environment/mark_visited")
async def mark_visited():
    """标记已完成首次访问检测"""
    global _first_visit_flag
    try:
        _first_visit_flag = False
        logger.info("[INFO] 已标记首次访问完成")

        return {
            "success": True,
            "message": "已标记为已访问"
        }
    except Exception as e:
        logger.error(f"标记访问状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"标记访问状态失败: {str(e)}")


class ProxyTestRequest(BaseModel):
    """代理测试请求模型"""
    proxy: Dict


@app.post("/api/environment/test_proxy")
async def test_proxy_connection(request: ProxyTestRequest):
    """测试代理连接到OKX"""
    try:
        import requests
        import time

        proxy_config = request.proxy

        if not proxy_config.get('enabled', False):
            raise HTTPException(status_code=400, detail="代理未启用")

        # 构建代理URL
        proxy_type = proxy_config.get('type', 'http')
        host = proxy_config.get('host', '127.0.0.1')
        port = proxy_config.get('port', 7890)
        username = proxy_config.get('username', '')
        password = proxy_config.get('password', '')

        if username and password:
            proxy_url = f"{proxy_type}://{username}:{password}@{host}:{port}"
        else:
            proxy_url = f"{proxy_type}://{host}:{port}"

        proxies = {
            'http': proxy_url,
            'https': proxy_url
        }

        # 测试连接到OKX（超时5秒）
        start_time = time.time()
        response = requests.get("https://www.okx.com", proxies=proxies, timeout=5)
        response_time = int((time.time() - start_time) * 1000)

        if response.status_code == 200:
            return {
                "success": True,
                "data": {
                    "response_time": response_time,
                    "status_code": response.status_code
                }
            }
        else:
            return {
                "success": False,
                "message": f"连接失败，状态码: {response.status_code}"
            }

    except requests.exceptions.ProxyError as e:
        return {
            "success": False,
            "message": f"代理连接失败: {str(e)}"
        }
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "message": "连接超时（5秒）"
        }
    except Exception as e:
        logger.error(f"代理测试失败: {e}")
        return {
            "success": False,
            "message": f"测试失败: {str(e)}"
        }


@app.get("/api/environment/init_env")
async def init_env_file():
    """检查.env文件是否存在，不存在则从.env.example创建"""
    try:
        env_file = os.path.join(project_root, '.env')
        env_example_file = os.path.join(project_root, '.env.example')

        # 检查.env是否存在
        if os.path.exists(env_file):
            return {
                "success": True,
                "data": {
                    "env_exists": True,
                    "created": False,
                    "message": ".env文件已存在"
                }
            }

        # .env不存在，检查.env.example是否存在
        if not os.path.exists(env_example_file):
            logger.error(".env.example文件不存在，无法创建默认.env")
            raise HTTPException(status_code=500, detail=".env.example模板文件不存在")

        # 从.env.example复制创建.env
        import shutil
        shutil.copy(env_example_file, env_file)
        logger.info(f"已从.env.example创建默认.env文件: {env_file}")

        return {
            "success": True,
            "data": {
                "env_exists": False,
                "created": True,
                "message": "已创建默认.env文件，请配置API密钥后重启服务"
            }
        }

    except Exception as e:
        logger.error(f"初始化.env文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"初始化.env文件失败: {str(e)}")


# ==================== MySQL测试连接 API ====================

class MySQLTestRequest(BaseModel):
    """MySQL测试连接请求模型"""
    host: str
    port: int
    user: str
    password: str
    database: str


@app.post("/api/mysql/test")
async def test_mysql_connection(request: MySQLTestRequest):
    """测试MySQL数据库连接"""
    try:
        import pymysql

        # 尝试连接MySQL
        connection = pymysql.connect(
            host=request.host,
            port=request.port,
            user=request.user,
            password=request.password,
            database=request.database,
            connect_timeout=5
        )

        # 获取MySQL版本
        cursor = connection.cursor()
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]

        cursor.close()
        connection.close()

        return {
            "success": True,
            "message": "MySQL连接成功",
            "version": version
        }

    except ImportError:
        return {
            "success": False,
            "error": "pymysql模块未安装，请运行: pip install pymysql"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


class FeishuTestRequest(BaseModel):
    """飞书webhook测试请求模型"""
    webhook_url: str
    test_message: Dict


@app.post("/api/feishu/test")
async def test_feishu_webhook(request: FeishuTestRequest):
    """测试飞书webhook连接"""
    try:
        import requests
        import json

        # 构造测试消息
        payload = {
            "msg_type": "text",
            "content": {
                "text": request.test_message.get("text", "测试消息"),
                "signal": request.test_message.get("signal", "测试"),
                "inst_id": request.test_message.get("inst_id", "BTC-USDT-SWAP")
            }
        }

        # 发送POST请求到飞书webhook
        response = requests.post(
            request.webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )

        # 检查响应
        if response.status_code == 200:
            return {
                "success": True,
                "message": "飞书webhook测试成功",
                "response": response.json() if response.text else {}
            }
        else:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text}"
            }

    except ImportError:
        return {
            "success": False,
            "error": "requests模块未安装，请运行: pip install requests"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# ==================== 启动函数 ====================

def start_server(host: str = "0.0.0.0", port: int = 8888):
    """启动FastAPI服务器（优化并发配置）"""
    import uvicorn
    print(f"""
    ===============================================================

          OKX Quant Trading Web Dashboard

          Access at: http://localhost:{port}

          Features:
          - Real-time account balance monitoring
          - Current positions display
          - AI analysis conversation logs
          - Strategy execution logs
          - Historical decision statistics
          - Program configuration management

    ===============================================================
    """)

    # 启动时获取交易产品列表并下载图标
    print("[INFO] 正在获取交易产品列表...")
    try:
        instruments = fetch_and_cache_instruments()
        print(f"[OK] 成功缓存 {len(instruments)} 个交易产品")
    except Exception as e:
        print(f"[WARN] 获取交易产品失败: {e}")

    # 优化并发配置
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        # 并发优化参数
        limit_concurrency=100,  # 最大并发连接数
        timeout_keep_alive=5,  # Keep-Alive超时（秒）
        # 使用uvloop事件循环（性能更好，需要安装：pip install uvloop）
        # loop="uvloop",  # 如果安装了uvloop可以启用
    )


if __name__ == "__main__":
    start_server(port=1234)
