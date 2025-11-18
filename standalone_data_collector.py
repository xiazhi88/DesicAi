"""
独立实时数据采集器
可以单独运行，持续采集K线、逐笔成交、订单簿数据
与交易系统解耦，确保数据库始终保持最新数据
"""
import sys
import os
import signal
import asyncio
import argparse
import json
from tqdm import tqdm
import time
from datetime import datetime
from loguru import logger
from concurrent.futures import ThreadPoolExecutor
from collections import deque

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from src.config.settings import config
from src.core.rest_client import OKXRestClient
from src.api.market import MarketAPI
from src.api.public import PublicAPI
from src.ai.data_manager import DataManager
from src.data.okx_websocket import OkxWebSocket


class StandaloneDataCollector:
    """独立数据采集器"""

    # K线周期到毫秒的映射（类常量）
    BAR_TO_MS = {
        '1m': 60 * 1000,
        '5m': 5 * 60 * 1000,
        '15m': 15 * 60 * 1000,
        '30m': 30 * 60 * 1000,
        '1H': 60 * 60 * 1000,
        '4H': 4 * 60 * 60 * 1000,
        '1D': 24 * 60 * 60 * 1000
    }

    # 不同周期的历史数据天数（类常量）
    HISTORY_DAYS_BY_TIMEFRAME = {
        '1m': 3,      # 1分钟K线：3天
        '5m': 7,      # 5分钟K线：7天
        '15m': 15,    # 15分钟K线：15天
        '30m': 30,    # 30分钟K线：30天
        '1H': 30,     # 1小时K线：30天
        '4H': 30,     # 4小时K线：30天
        '1D': 30      # 日线：30天
    }

    def __init__(
        self,
        symbols: list = None,
        timeframes: list = None,
        history_days: int = 30,
        data_timeout_seconds: int = 120
    ):
        """
        初始化独立数据采集器

        Args:
            symbols: 交易对列表（默认BTC-USDT-SWAP）
            timeframes: K线周期列表（默认1m, 5m, 15m）
            history_days: 初始化历史数据天数
            data_timeout_seconds: 数据更新超时时间（秒），超过此时间未收到数据则重启（默认120秒）
        """
        self.symbols = symbols or ['BTC-USDT-SWAP']
        self.timeframes = timeframes or ['1m', '5m', '15m']
        self.history_days = history_days
        self.data_timeout_seconds = data_timeout_seconds

        # 运行状态
        self.is_running = False
        self.need_restart = False  # 标记是否需要重启

        # 时间同步（本地时间 - 服务器时间的差值，单位：毫秒）
        self.time_offset_ms = 0

        # WebSocket 连接
        self.ws_public: OkxWebSocket = None
        self.ws_business: OkxWebSocket = None

        # 线程池（减小工作线程数，避免MySQL连接数过多）
        self.executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="ws")
        self.executor_redis = ThreadPoolExecutor(max_workers=500, thread_name_prefix="ws")
        # 2个WebSocket连接线程 + 数据处理线程（避免Too many connections）

        # 内存缓冲
        self.trades_buffer = {sym: deque(maxlen=1000) for sym in self.symbols}

        # 订单簿初始化标记
        self.orderbook_initialized = {sym: False for sym in self.symbols}

        self.kline_cache = {}
        for sym in self.symbols:
            self.kline_cache[sym] = {tf: None for tf in self.timeframes}

        # 统计
        self.stats = {
            'trades_received': 0,
            'orderbook_updates': 0,
            'klines_received': 0,
            'history_klines_loaded': 0,
            'last_update': None
        }

        # 初始化数据管理器
        logger.info("📊 初始化数据管理器...")
        self.data_manager = DataManager()

        # 初始化REST API（用于获取历史数据）
        if not config.is_configured():
            logger.warning("⚠️ 未配置API密钥，将无法获取历史数据")
            self.market_api = None
            self.public_api = None
        else:
            client = OKXRestClient(
                api_key=config.API_KEY,
                secret_key=config.SECRET_KEY,
                passphrase=config.PASSPHRASE,
                is_demo=config.IS_DEMO,
                use_proxy=config.PROXY_ENABLED,
            proxy=f'http://{config.PROXY_USERNAME}:{config.PROXY_PASSWORD}@{config.PROXY_HOST}:{config.PROXY_PORT}' if config.PROXY_USERNAME else f'http://{config.PROXY_HOST}:{config.PROXY_PORT}'
            )
            self.market_api = MarketAPI(client)
            self.public_api = PublicAPI(client)

        # 设置信号处理（优雅退出）
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.success("✓ 独立数据采集器初始化完成")
        logger.info(f"  交易对: {', '.join(self.symbols)}")
        logger.info(f"  K线周期: {', '.join(self.timeframes)}")
        logger.info(f"  历史数据策略:")
        for tf in self.timeframes:
            days = self.HISTORY_DAYS_BY_TIMEFRAME.get(tf, self.history_days)
            logger.info(f"    {tf}: {days}天")
        logger.info(f"  数据超时检测: {self.data_timeout_seconds}秒")

    def _signal_handler(self, signum, frame):
        """处理退出信号"""
        logger.info(f"\n收到退出信号 ({signum})，正在停止数据采集...")
        self.stop()
        sys.exit(0)

    async def _sync_server_time(self):
        """
        同步服务器时间，计算本地时间与服务器时间的差值

        使用多次采样取平均值以提高准确性
        """
        try:
            if not self.public_api:
                logger.warning("⚠️ 未配置API，无法同步服务器时间")
                return

            logger.info("⏰ 正在同步服务器时间...")
            samples = []

            # 采样3次取平均值
            for i in range(3):
                local_before = int(time.time() * 1000)
                result = self.public_api.get_system_time()
                local_after = int(time.time() * 1000)

                if result.get('code') == '0' and result.get('data'):
                    server_time = int(result['data'][0]['ts'])
                    # 估算网络延迟（往返时间的一半）
                    network_delay = (local_after - local_before) // 2
                    # 本地时间（减去网络延迟）- 服务器时间
                    local_time_adjusted = local_before + network_delay
                    offset = local_time_adjusted - server_time
                    samples.append(offset)

                    logger.debug(f"  采样 {i+1}: 本地={local_time_adjusted}, 服务器={server_time}, 差值={offset}ms")

                if i < 2:
                    await asyncio.sleep(0.5)

            if samples:
                # 取中位数（比平均数更鲁棒）
                samples.sort()
                self.time_offset_ms = samples[len(samples) // 2]

                logger.success(
                    f"✓ 时间同步完成: 本地时间比服务器时间 "
                    f"{'快' if self.time_offset_ms > 0 else '慢'} {abs(self.time_offset_ms)}ms"
                )
            else:
                logger.warning("⚠️ 时间同步失败，将使用本地时间")

        except Exception as e:
            logger.error(f"时间同步异常: {e}")
            import traceback
            traceback.print_exc()

    def get_corrected_time_ms(self) -> int:
        """
        获取校正后的当前时间（毫秒）

        Returns:
            校正后的时间戳（毫秒）= 本地时间 - 时间差
        """
        local_time_ms = int(time.time() * 1000)
        return local_time_ms - self.time_offset_ms

    # ==================== 公共频道回调 ====================

    def on_public_open(self, ws):
        """公共频道连接打开回调"""
        # 构建订阅参数
        args = []
        for symbol in self.symbols:
            args.append({"channel": "books", "instId": symbol})  # 400档订单簿（增量推送）

        # 发送订阅
        self.ws_public.subscribe(args)
        logger.info(f"✓ 订阅公共频道: {len(self.symbols)}个交易对 (trades + 400档orderbook)")

    def on_public_message(self, message: str):
        """公共频道消息回调"""
        try:
            # 解析 JSON 消息
            data = json.loads(message)

            # 跳过订阅确认消息
            if data.get('event') == 'subscribe':
                logger.debug(f"订阅成功: {data}")
                return

            # 处理数据（提交到线程池，避免阻塞WebSocket接收）
            if 'data' in data and 'arg' in data:
                channel = data['arg'].get('channel', '')
                inst_id = data['arg'].get('instId', '')
                action = data.get('action', '')  # snapshot / update

                if channel == 'books':
                    # 处理订单簿数据（快照或增量）
                    for book in data['data']:
                        # 提交到线程池异步处理
                        self._process_orderbook(inst_id, action, book)

        except Exception as e:
            logger.error(f"公共频道消息处理错误: {e}")

    def on_public_close(self, ws, close_code, close_msg):
        """公共频道连接关闭回调"""
        if self.is_running:
            logger.warning(f"公共频道连接关闭，5秒后重连...")
            time.sleep(5)
            # 重新提交到线程池
            if self.ws_public:
                self.executor.submit(self.ws_public.connect)

    def on_public_error(self, ws, error):
        """公共频道错误回调"""
        logger.error(f"公共频道错误: {error}")

    # ==================== Business频道回调 ====================

    def on_business_open(self, ws):
        """Business频道连接打开回调"""
        # 构建订阅参数
        args = []
        for symbol in self.symbols:
            for timeframe in self.timeframes:
                channel = f"candle{timeframe}"
                args.append({"channel": channel, "instId": symbol})
            args.append(
                {
                    "channel": "trades-all",
                    "instId": symbol
                })
        # 发送订阅
        self.ws_business.subscribe(args)
        logger.info(f"✓ 订阅business频道: {len(self.symbols)}个交易对 × {len(self.timeframes)}个周期")

    def on_business_message(self, message: str):
        """Business频道消息回调"""
        try:
            # 解析 JSON 消息
            data = json.loads(message)

            # 跳过订阅确认消息
            if data.get('event') == 'subscribe':
                logger.debug(f"订阅成功: {data}")
                return

            # 处理K线数据（提交到线程池，避免阻塞WebSocket接收）
            if 'data' in data and 'arg' in data:
                channel = data['arg'].get('channel', '')
                inst_id = data['arg'].get('instId', '')
                if channel =='trades-all':
                    self.executor_redis.submit(self._process_trade, inst_id, data['data'])

                # 从channel中提取timeframe
                if channel.startswith('candle'):
                    timeframe = channel.replace('candle', '')
                    for kline in data['data']:
                        # 提交到线程池异步处理
                        self.executor.submit(self._process_kline, inst_id, timeframe, kline)
            
        except Exception as e:
            logger.error(f"business频道消息处理错误: {e}")

    def on_business_close(self, ws, close_code, close_msg):
        """Business频道连接关闭回调"""
        if self.is_running:
            logger.warning(f"business频道连接关闭，5秒后重连...")
            time.sleep(5)
            # 重新提交到线程池
            if self.ws_business:
                self.executor.submit(self.ws_business.connect)

    def on_business_error(self, ws, error):
        """Business频道错误回调"""
        logger.error(f"business频道错误: {error}")

    # ==================== 数据处理方法 ====================

    def _process_trade(self, symbol: str, trades: dict):
        """处理逐笔成交（仅保存到Redis，AI分析时再聚合）"""
        try:
            trade_data = [{
                'trade_id': trade['tradeId'],
                'timestamp': int(trade['ts']),
                'price': float(trade['px']),
                'size': float(trade['sz']),
                'side': trade['side']
            } for trade in trades]

            # 更新统计
            self.stats['trades_received'] += len(trade_data)
            self.stats['last_update'] = datetime.now()

            # 实时写入Redis（供AI分析时查询和聚合）
            self.data_manager.save_trades_to_redis(symbol, trade_data)

        except Exception as e:
            logger.error(f"处理成交数据失败: {e}")

    def _process_orderbook(self, symbol: str, action: str, book: dict):
        """
        处理订单簿数据（快照 + 增量更新）

        Args:
            symbol: 交易对
            action: 'snapshot' (全量快照) 或 'update' (增量更新)
            book: 订单簿数据 {bids, asks, ts, checksum, seqId, prevSeqId}

        OKX订单簿更新机制：
        1. 首次推送：action='snapshot', prevSeqId=-1
           - 清空数据库，保存全量400档数据
        2. 后续推送：action='update'
           - 增量更新：size='0'删除，size!='0'更新或插入
        3. 校验：使用checksum验证数据完整性
        """
        try:
            bids = book.get('bids', [])
            asks = book.get('asks', [])
            timestamp = int(book['ts'])
            checksum = book.get('checksum')
            seqId = book.get('seqId', -1)
            prevSeqId = book.get('prevSeqId', -1)

            if not bids and not asks:
                # 空更新（维持心跳），prevSeqId == seqId
                logger.debug(f"订单簿心跳: {symbol}, seqId={seqId}")
                return

            # 检查序列号异常（序列重置）
            if action == 'update' and seqId < prevSeqId:
                logger.warning(f"⚠️  订单簿序列重置: {symbol}, prevSeqId={prevSeqId} -> seqId={seqId}")
                # 序列重置后，后续消息会遵循正常排序

            # 处理快照或增量
            if action == 'snapshot':
                # 全量快照 - 保存到Redis
                self.data_manager.save_orderbook_to_redis(
                    symbol=symbol,
                    action=action,
                    bids=bids,
                    asks=asks,
                    timestamp=timestamp,
                    seqId=seqId
                )
                self.orderbook_initialized[symbol] = True

            elif action == 'update':
                # 增量更新 - 保存到Redis
                if not self.orderbook_initialized.get(symbol, False):
                    logger.warning(f"⚠️  订单簿未初始化，跳过增量更新: {symbol}")
                    return

                self.data_manager.save_orderbook_to_redis(
                    symbol=symbol,
                    action=action,
                    bids=bids,
                    asks=asks,
                    timestamp=timestamp,
                    seqId=seqId
                )



            # 定期保存聚合指标到SQLite（用于快速查询和历史分析）
            if self.stats['orderbook_updates'] % 10 == 0:
                self._save_orderbook_snapshot_metrics(symbol)

        except Exception as e:
            logger.error(f"处理订单簿失败: {e}")
            import traceback
            traceback.print_exc()

    def _save_orderbook_snapshot_metrics(self, symbol: str):
        """
        保存订单簿聚合指标（定期快照，用于快速查询）

        从orderbook_live提取前5档数据，计算聚合指标
        """
        try:
            orderbook = self.data_manager.get_orderbook_live(symbol, depth=5)
            if not orderbook or not orderbook['bids'] or not orderbook['asks']:
                return

            bids = orderbook['bids']
            asks = orderbook['asks']
            timestamp = orderbook['timestamp']

            # 提取第1档
            bid1_price = float(bids[0][0])
            bid1_size = float(bids[0][1])
            ask1_price = float(asks[0][0])
            ask1_size = float(asks[0][1])

            # 计算中间价和价差
            mid_price = (bid1_price + ask1_price) / 2
            spread_pct = (ask1_price - bid1_price) / mid_price * 100

            # 计算5档深度
            bid_depth_5 = sum(float(b[1]) for b in bids[:5])
            ask_depth_5 = sum(float(a[1]) for a in asks[:5])
            depth_ratio = bid_depth_5 / ask_depth_5 if ask_depth_5 > 0 else 0

            # 保存聚合指标
            snapshot_data = {
                'timestamp': timestamp,
                'bid1_price': bid1_price,
                'bid1_size': bid1_size,
                'ask1_price': ask1_price,
                'ask1_size': ask1_size,
                'spread_pct': spread_pct,
                'bid_depth_5': bid_depth_5,
                'ask_depth_5': ask_depth_5,
                'depth_ratio': depth_ratio
            }

            self.data_manager.save_orderbook_snapshot(symbol, snapshot_data)

        except Exception as e:
            logger.error(f"保存订单簿指标失败: {e}")

    def _process_kline(self, symbol: str, timeframe: str, kline: list):
        """
        处理K线数据（包括未完结K线的实时更新）

        重要逻辑：
        1. confirm="0" (未完结): 持续UPDATE数据库的high/low/close/volume，is_confirmed=0
        2. confirm="1" (完结): 最后一次UPDATE，包括最终价格和is_confirmed=1

        OKX WebSocket推送频率：
        - 未完结K线：价格变化时推送（高频）
        - 完结K线：周期结束时推送一次（confirm="1"）

        数据库操作：
        - 使用 INSERT OR REPLACE 实现UPSERT
        - 同一时间戳的K线会不断被更新，直到完结
        """
        try:
            confirm = kline[8] if len(kline) > 8 else "0"
            is_confirmed = (confirm == "1")

            # 解析K线数据
            kline_data = {
                'timestamp': int(kline[0]),
                'open': float(kline[1]),
                'high': float(kline[2]),
                'low': float(kline[3]),
                'close': float(kline[4]),
                'volume': float(kline[5]),
                'confirm': confirm,
                'is_confirmed': is_confirmed
            }

            # 缓存到内存（始终保持最新状态）
            self.kline_cache[symbol][timeframe] = kline_data

            # 保存/更新到数据库（每次推送都会更新）
            # - 未完结时：不断UPDATE最新的价格数据
            # - 完结时：最后一次UPDATE并标记is_confirmed=1
            self.data_manager.save_kline(
                inst_id=symbol,
                bar=timeframe,
                kline_data=kline
            )

            # 记录K线数据的最后更新时间（实际接收到数据的时间）
            self.data_manager.update_kline_last_update(symbol, timeframe)

            self.stats['klines_received'] += 1

            # 日志输出（区分完结和未完结）
            if is_confirmed:
                logger.debug(
                    f"✓ K线完结: {symbol} {timeframe} | "
                    f"O={kline_data['open']:.2f} H={kline_data['high']:.2f} "
                    f"L={kline_data['low']:.2f} C={kline_data['close']:.2f} "
                    f"V={kline_data['volume']:.2f}"
                )
            else:
                # 未完结K线也输出，但频率较低（避免刷屏）
                if self.stats['klines_received'] % 10 == 0:  # 每10次更新输出1次
                    logger.debug(
                        f"⟳ K线更新: {symbol} {timeframe} | "
                        f"当前价={kline_data['close']:.2f} "
                        f"H={kline_data['high']:.2f} L={kline_data['low']:.2f} "
                        f"[未完结-持续更新中]"
                    )

        except Exception as e:
            logger.error(f"处理K线数据失败: {e}")

    async def start(self):
        """启动数据采集"""
        if self.is_running:
            logger.warning("⚠️ 数据采集器已在运行中")
            return

        logger.info("=" * 60)
        logger.info("🚀 启动独立数据采集器")
        logger.info(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)

        self.is_running = True

        try:
            # 0. 同步服务器时间（重要！用于校正数据更新时间）
            await self._sync_server_time()

            # 1. 清空Redis中的旧数据（重要！）
            logger.info("🧹 清空Redis旧数据...")
            for symbol in self.symbols:
                # 清空订单簿
                self.data_manager.clear_redis_orderbook(symbol)
                # 清空逐笔成交
                self.data_manager.clear_redis_trades(symbol)
            logger.success("✓ Redis数据已清空（订单簿 + 逐笔成交）")

            # 1. 修复未完结的历史K线（重要！）
            if self.market_api:
                logger.info("🔧 检查并修复未完结的历史K线...")
                asyncio.create_task(self._fix_unconfirmed_klines())
            else:
                logger.warning("⚠️ 未提供MarketAPI，跳过未完结K线修复")

            # 2. 历史数据初始化（后台任务）
            if self.market_api:
                logger.info("📚 历史数据初始化将在后台运行...")
                asyncio.create_task(self._init_history_data())
            else:
                logger.warning("⚠️ 未提供MarketAPI，跳过历史数据初始化")

            # 3. 创建 WebSocket 连接
            self.ws_public = OkxWebSocket(
                url="wss://ws.okx.com:8443/ws/v5/public",
                name="公共频道",
                on_open=self.on_public_open,
                on_message=self.on_public_message,
                on_close=self.on_public_close,
                on_error=self.on_public_error,
                ping_interval=10,
                proxy=f'http://{config.PROXY_USERNAME}:{config.PROXY_PASSWORD}@{config.PROXY_HOST}:{config.PROXY_PORT}' if config.PROXY_USERNAME else f'http://{config.PROXY_HOST}:{config.PROXY_PORT}' ,
                use_proxy=config.PROXY_ENABLED,
            )

            self.ws_business = OkxWebSocket(
                url="wss://ws.okx.com:8443/ws/v5/business",
                name="business频道",
                on_open=self.on_business_open,
                on_message=self.on_business_message,
                on_close=self.on_business_close,
                on_error=self.on_business_error,
                ping_interval=20,
                proxy=f'http://{config.PROXY_USERNAME}:{config.PROXY_PASSWORD}@{config.PROXY_HOST}:{config.PROXY_PORT}' if config.PROXY_USERNAME else f'http://{config.PROXY_HOST}:{config.PROXY_PORT}' ,
                use_proxy=config.PROXY_ENABLED,
            )

            # 4. 在线程池中启动 WebSocket 连接
            logger.info("🔌 启动 WebSocket 连接...")
            future_public = self.executor.submit(self.ws_public.connect)
            future_business = self.executor.submit(self.ws_business.connect)

            logger.success("✓ WebSocket连接已在线程池中启动")
            logger.info("📡 正在接收实时数据...")
            logger.info("💡 按 Ctrl+C 停止采集")

            # 5. 启动后台任务
            tasks = [
                #asyncio.create_task(self._aggregate_pressure_loop()),
                asyncio.create_task(self._snapshot_orderbook_loop()),
                asyncio.create_task(self._cleanup_old_data_loop()),
                asyncio.create_task(self._stats_report_loop()),
                asyncio.create_task(self._monitor_status())
            ]

            # 保存到实例变量，以便在需要重启时可以取消
            self.background_tasks = tasks

            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                logger.info("后台任务被取消")
                # 取消所有未完成的任务
                for task in tasks:
                    if not task.done():
                        task.cancel()
                # 等待所有任务完成取消
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"❌ 数据采集异常: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.stop()

    # ==================== 后台任务 ====================

    async def _fix_unconfirmed_klines(self):
        """
        修复未完结的历史K线（启动时执行）

        当采集器停止后再启动时，数据库中可能存在 is_confirmed=0 但实际已完结的K线
        此方法会：
        1. 查找所有未完结的K线
        2. 判断是否已过期（当前时间 > K线时间 + 周期）
        3. 调用REST API获取正确的历史数据
        4. 更新数据库，设置 is_confirmed=1
        """
        try:
            await asyncio.sleep(1)  # 等待1秒确保数据库初始化完成

            # 获取所有未完结的K线
            unconfirmed = self.data_manager.get_unconfirmed_klines()

            if not unconfirmed:
                logger.info("✓ 数据库中无未完结K线，无需修复")
                return

            logger.info(f"🔍 发现 {len(unconfirmed)} 根未完结K线，开始修复...")

            now_ms = int(time.time() * 1000)
            fixed_count = 0
            skipped_count = 0

            # 按交易对和周期分组
            from collections import defaultdict
            groups = defaultdict(list)
            for kline in unconfirmed:
                key = (kline['inst_id'], kline['bar'])
                groups[key].append(kline)

            # 逐个处理
            for (inst_id, bar), klines in groups.items():
                logger.info(f"  处理 {inst_id} {bar}: {len(klines)} 根K线")

                bar_duration_ms = self.BAR_TO_MS.get(bar, 60000)  # 默认1分钟

                for kline in klines:
                    timestamp = kline['timestamp']
                    kline_end_time = timestamp + bar_duration_ms

                    # 判断是否已过期（已完结）
                    if now_ms <= kline_end_time:
                        # 未过期，跳过（可能是刚启动时的最新K线）
                        skipped_count += 1
                        continue

                    # 已过期，需要修复
                    try:
                        # 调用REST API获取该时间点的历史K线
                        # 注意：OKX API的 after/before 参数是按时间倒序的
                        result = self.market_api.get_history_candles(
                            inst_id=inst_id,
                            bar=bar,
                            after=str(timestamp),
                            limit='1'
                        )

                        if result['code'] != '0':
                            logger.warning(f"    ⚠️  API调用失败: {result.get('msg')}")
                            continue

                        candles = result.get('data', [])
                        if not candles:
                            logger.warning(f"    ⚠️  未找到历史数据: timestamp={timestamp}")
                            continue

                        # 找到匹配时间戳的K线
                        matched = None
                        for candle in candles:
                            if int(candle[0]) == timestamp:
                                matched = candle
                                break

                        if not matched:
                            # 如果没有精确匹配，使用最接近的
                            matched = candles[0]

                        # 更新数据库
                        self.data_manager.update_kline_confirmed(
                            inst_id=inst_id,
                            bar=bar,
                            timestamp=timestamp,
                            open_price=float(matched[1]),
                            high=float(matched[2]),
                            low=float(matched[3]),
                            close=float(matched[4]),
                            volume=float(matched[5])
                        )

                        fixed_count += 1

                        # 避免请求过快
                        await asyncio.sleep(0.1)

                    except Exception as e:
                        logger.error(f"    ❌ 修复K线失败: {e}")

            logger.success(
                f"✓ K线修复完成: 修复 {fixed_count} 根, 跳过 {skipped_count} 根（未过期）"
            )

        except Exception as e:
            logger.error(f"❌ 修复未完结K线异常: {e}")
            import traceback
            traceback.print_exc()

    async def _init_history_data(self):
        """初始化历史数据（后台运行）"""
        from datetime import timedelta

        try:
            await asyncio.sleep(2)  # 等待2秒让实时采集先启动
            logger.info(f"📚 开始初始化历史数据（不同周期使用不同天数）...")
            total_loaded = 0

            for symbol in self.symbols:
                for timeframe in self.timeframes:
                    try:
                        # 获取该timeframe对应的历史天数
                        history_days = self.HISTORY_DAYS_BY_TIMEFRAME.get(timeframe, self.history_days)
                        logger.info(f"  检查 {symbol} {timeframe} (最近{history_days}天)...")

                        # 获取需要补齐的时间区间列表
                        missing_ranges = await self._detect_missing_ranges(symbol, timeframe)

                        if not missing_ranges:
                            logger.info(f"  {symbol} {timeframe}: ✓ 数据完整，无需补齐")
                            continue

                        # 计算缺失的K线总数
                        bar_interval_ms = self.BAR_TO_MS.get(timeframe, 60 * 1000)
                        total_missing_bars = sum(
                            (end_ts - start_ts) // bar_interval_ms
                            for start_ts, end_ts in missing_ranges
                        )

                        logger.info(
                            f"  {symbol} {timeframe}: 发现 {len(missing_ranges)} 个缺失区间，"
                            f"共缺失 {total_missing_bars} 根K线"
                        )

                        # 补齐每个缺失区间
                        for i, (start_ts, end_ts) in enumerate(missing_ranges, 1):
                            start_date = datetime.fromtimestamp(start_ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
                            end_date = datetime.fromtimestamp(end_ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
                            bars_in_range = (end_ts - start_ts) // bar_interval_ms

                            logger.info(
                                f"    补齐区间 {i}/{len(missing_ranges)}: "
                                f"{start_date} 至 {end_date} (约 {bars_in_range} 根K线)"
                            )
                            bl=tqdm(total=bars_in_range,desc=f'下载K线数据【{timeframe}】')

                            # OKX API: after=T返回<T的数据, before=T返回>T的数据
                            # K线时间戳是周期开始时间，所以需要让before参数往前移一点
                            # 使用 before=start_ts-1 来包含 start_ts 那根K线
                            count = await self._fetch_and_save_klines(
                                symbol, timeframe,
                                after=str(end_ts),
                                before=str(start_ts - 1) if start_ts > 0 else None,
                                bl=bl
                            )

                            total_loaded += count
                            logger.success(f"      ✓ 加载 {count} 根K线")

                            # 避免请求过快
                            await asyncio.sleep(0.5)

                    except Exception as e:
                        logger.error(f"  ❌ {symbol} {timeframe} 历史数据加载失败: {e}")
                        import traceback
                        traceback.print_exc()

            self.stats['history_klines_loaded'] = total_loaded
            logger.success(f"✓ 历史数据初始化完成，共加载 {total_loaded} 根K线")

        except Exception as e:
            logger.error(f"后台历史数据初始化失败: {e}")
            import traceback
            traceback.print_exc()

    async def _detect_missing_ranges(self, symbol: str, timeframe: str) -> list:
        """
        检测缺失的K线时间区间（按timeframe周期检测）

        Args:
            symbol: 交易对
            timeframe: K线周期

        Returns:
            缺失区间列表 [(start_ts, end_ts), ...]
        """
        from datetime import timedelta

        bar_interval_ms = self.BAR_TO_MS.get(timeframe, 60 * 1000)

        # 根据timeframe获取对应的历史天数
        history_days = self.HISTORY_DAYS_BY_TIMEFRAME.get(timeframe, self.history_days)

        # 计算应该有数据的时间范围
        now = datetime.now()
        end_time = now
        start_time = now - timedelta(days=history_days)

        # 对齐到K线周期的起始点（向下取整）
        start_time_ms = int(start_time.timestamp() * 1000)
        start_time_ms = (start_time_ms // bar_interval_ms) * bar_interval_ms

        end_time_ms = int(end_time.timestamp() * 1000)
        end_time_ms = (end_time_ms // bar_interval_ms) * bar_interval_ms

        # 获取数据库中的所有K线数据
        # 计算需要查询的数量（按timeframe计算）
        total_bars = (end_time_ms - start_time_ms) // bar_interval_ms
        limit = min(total_bars + 100, 50000)  # 最多查询5万根K线

        existing_klines = self.data_manager.get_recent_klines(
            symbol, timeframe,
            limit=int(limit)
        )

        if not existing_klines:
            # 数据库中无数据，整个时间范围都缺失
            return [(start_time_ms, end_time_ms)]

        # 将现有数据的时间戳转换为集合
        existing_timestamps = {kline['timestamp'] for kline in existing_klines}

        # 生成应该存在的所有K线时间戳
        expected_timestamps = []
        current_ts = start_time_ms
        while current_ts <= end_time_ms:
            expected_timestamps.append(current_ts)
            current_ts += bar_interval_ms

        # 找出缺失的时间戳
        missing_timestamps = [ts for ts in expected_timestamps if ts not in existing_timestamps]

        if not missing_timestamps:
            # 没有缺失数据
            return []

        # 将连续缺失的时间戳合并为区间
        missing_ranges = []
        range_start = missing_timestamps[0]
        range_end = missing_timestamps[0]

        for i in range(1, len(missing_timestamps)):
            if missing_timestamps[i] == range_end + bar_interval_ms:
                # 连续缺失，扩展当前区间
                range_end = missing_timestamps[i]
            else:
                # 不连续，保存当前区间，开始新区间
                missing_ranges.append((range_start, range_end + bar_interval_ms))
                range_start = missing_timestamps[i]
                range_end = missing_timestamps[i]

        # 保存最后一个区间
        missing_ranges.append((range_start, range_end + bar_interval_ms))

        return missing_ranges

    async def _fetch_and_save_klines(self, symbol: str, timeframe: str, after: str = None, before: str = None,bl:tqdm = None) -> int:
        """获取并保存K线数据（批量优化版本）"""
        count = 0
        current_after = after
        max_iterations = 1000

        for iteration in range(max_iterations):
            try:
                result = self.market_api.get_history_candles(
                    inst_id=symbol,
                    bar=timeframe,
                    after=current_after,
                    before=before,
                    limit='100'
                )

                if result['code'] != '0':
                    logger.error(f"获取K线失败: {result.get('msg')}")
                    break

                klines = result.get('data', [])
                if not klines:
                    break

                # 批量保存K线（一次性保存所有K线，大幅提升效率）
                saved = self.data_manager.save_klines_batch(
                    inst_id=symbol,
                    bar=timeframe,
                    klines_data=klines
                )
                count += saved

                # 更新进度条
                if bl:
                    bl.update(saved)

                # 获取最旧的时间戳
                oldest_ts = klines[-1][0]

                if current_after and oldest_ts == current_after:
                    break

                current_after = oldest_ts

                if len(klines) < 100:
                    break


            except Exception as e:
                logger.error(f"获取K线数据异常: {e}")
                break

        return count

    async def _aggregate_pressure_loop(self):
        """聚合市场压力指标（每分钟1次）"""
        while self.is_running:
            try:
                await asyncio.sleep(60)

                for symbol in self.symbols:
                    for interval_sec in [60, 300, 900]:
                        trades = self.data_manager.get_recent_trades(symbol, interval_sec)
                        if trades:
                            # 计算压力指标
                            buy_volume = sum(t['size'] for t in trades if t['side'] == 'buy')
                            sell_volume = sum(t['size'] for t in trades if t['side'] == 'sell')
                            buy_count = sum(1 for t in trades if t['side'] == 'buy')
                            sell_count = sum(1 for t in trades if t['side'] == 'sell')
                            pressure_ratio = buy_volume / sell_volume if sell_volume > 0 else 0

                            pressure = {
                                'timestamp': int(time.time() * 1000),
                                'buy_volume': buy_volume,
                                'sell_volume': sell_volume,
                                'buy_count': buy_count,
                                'sell_count': sell_count,
                                'pressure_ratio': pressure_ratio
                            }
                            self.data_manager.save_market_pressure(symbol, interval_sec, pressure)

                logger.debug("✓ 市场压力指标已更新")

            except Exception as e:
                logger.error(f"压力聚合错误: {e}")

    async def _snapshot_orderbook_loop(self):
        """定期从数据库查询订单簿并保存快照指标（每分钟1次）"""
        while self.is_running:
            try:
                await asyncio.sleep(60)

                for symbol in self.symbols:
                    # 从数据库获取最新订单簿
                    self._save_orderbook_snapshot_metrics(symbol)

                logger.debug("✓ 订单簿快照已保存")

            except Exception as e:
                logger.error(f"订单簿快照错误: {e}")

    async def _cleanup_old_data_loop(self):
        """清理旧数据（每小时1次）"""
        while self.is_running:
            try:
                await asyncio.sleep(3600)

                # 只保留最近1小时的逐笔成交
                self.data_manager.cleanup_old_trades(hours=1)

                # 只保留最近24小时的订单簿快照
                self.data_manager.cleanup_old_orderbook(hours=24)

                # 只保留最近24小时的原始订单簿数据
                self.data_manager.cleanup_old_orderbook_raw(hours=24)

                logger.info("✓ 旧数据已清理")

            except Exception as e:
                logger.error(f"清理数据错误: {e}")

    async def _stats_report_loop(self):
        """统计报告（每5分钟）"""
        while self.is_running:
            try:
                await asyncio.sleep(300)

                logger.info(
                    f"📊 采集统计 | "
                    f"历史K线: {self.stats['history_klines_loaded']}根 | "
                    f"实时成交: {self.stats['trades_received']}笔 | "
                    f"订单簿: {self.stats['orderbook_updates']}次 | "
                    f"实时K线: {self.stats['klines_received']}根 | "
                    f"最后更新: {self.stats['last_update'].strftime('%H:%M:%S') if self.stats['last_update'] else 'N/A'}"
                )

            except Exception as e:
                logger.error(f"统计报告错误: {e}")

    async def _monitor_status(self):
        """监控运行状态并定期打印统计信息，检测数据更新超时"""
        status_interval = 30  # 每60秒打印一次状态
        await asyncio.sleep(30)
        while self.is_running:
            try:
                await asyncio.sleep(status_interval)

                # 获取当前时间戳（毫秒，使用校正后的服务器时间）
                now_ms = self.get_corrected_time_ms()
                timeout_threshold_ms = self.data_timeout_seconds * 1000

                # 检查各个数据源的更新超时
                timeout_detected = False
                timeout_details = []

                for symbol in self.symbols:
                    # 1. 检查K线数据超时（仍然使用Redis，因为有多个timeframe）
                    for timeframe in self.timeframes:
                        last_update = self.data_manager.get_kline_last_update(symbol, timeframe)
                        if last_update:
                            time_since_update_ms = now_ms - last_update
                            if time_since_update_ms > timeout_threshold_ms:
                                timeout_detected = True
                                timeout_details.append(
                                    f"K线{timeframe}: {time_since_update_ms/1000:.0f}秒"
                                )

                    # 2. 检查逐笔成交数据超时（从数据库获取最新timestamp）
                    last_trade_ts = self.data_manager.get_latest_trade_timestamp_from_redis(symbol)
                    if last_trade_ts:
                        time_since_trades_ms = now_ms - last_trade_ts
                        if time_since_trades_ms > timeout_threshold_ms:
                            timeout_detected = True
                            timeout_details.append(
                                f"逐笔成交: {time_since_trades_ms/1000:.0f}秒"
                            )

                    # 3. 检查订单簿数据超时（从Redis获取最新timestamp）
                    orderbook_redis = self.data_manager.get_orderbook_from_redis(symbol, depth=1)
                    if orderbook_redis and orderbook_redis.get('timestamp'):
                        time_since_orderbook_ms = now_ms - orderbook_redis['timestamp']
                        if time_since_orderbook_ms > timeout_threshold_ms:
                            timeout_detected = True
                            timeout_details.append(
                                f"订单簿: {time_since_orderbook_ms/1000:.0f}秒"
                            )



                # 如果检测到任何数据源超时，触发重启
                if timeout_detected:
                    logger.error(
                        f"❌ 数据更新超时检测到！以下数据源超过 {self.data_timeout_seconds}秒 未更新："
                    )
                    for detail in timeout_details:
                        logger.error(f"   - {detail}")
                    logger.warning("🔄 WebSocket可能已断开或数据流中断，准备重启采集器...")

                    # 设置重启标志并停止当前运行
                    self.need_restart = True
                    self.stop()
                    logger.warning("⚠️ 采集器已停止，将在5秒后自动重启...")
                    return  # 退出监控循环

                # 检查整体数据更新（向后兼容，使用stats['last_update']）
                if self.stats['last_update']:
                    time_since_update = (datetime.now() - self.stats['last_update']).total_seconds()

                    if time_since_update > self.data_timeout_seconds * 0.7:
                        # 提前警告（超过70%阈值时）
                        logger.warning(
                            f"⚠️ 数据更新延迟警告: 已经 {time_since_update:.0f} 秒未收到新数据 "
                            f"(阈值: {self.data_timeout_seconds}秒)"
                        )

                # 打印状态
                logger.info("\n" + "=" * 60)
                logger.info(f"📊 数据采集状态 [{datetime.now().strftime('%H:%M:%S')}]")
                logger.info("=" * 60)

                # 统计每个交易对的数据量
                for symbol in self.symbols:
                    logger.info(f"\n交易对: {symbol}")

                    # K线数据及最后更新时间
                    for tf in self.timeframes:
                        klines = self.data_manager.get_recent_klines(symbol, tf, limit=1)
                        last_update = self.data_manager.get_kline_last_update(symbol, tf)
                        if klines:
                            latest = klines[0]
                            update_status = ""
                            if last_update:
                                seconds_ago = (now_ms - last_update) / 1000
                                update_status = f", 更新于{seconds_ago:.0f}秒前"
                            logger.info(
                                f"  {tf} K线: 最新价格 {latest['close']:.2f}, "
                                f"时间 {datetime.fromtimestamp(latest['timestamp']/1000).strftime('%H:%M:%S')}"
                                f"{update_status}"
                            )

                    # 逐笔成交及最后更新时间（从Redis获取）
                    last_trade_ts = self.data_manager.get_latest_trade_timestamp_from_redis(symbol)
                    if last_trade_ts:
                        seconds_ago = (now_ms - last_trade_ts) / 1000
                        logger.info(f"  逐笔成交: 更新于{seconds_ago:.0f}秒前 (Redis缓存)")

                    # 订单簿及最后更新时间（从Redis获取）
                    orderbook_redis = self.data_manager.get_orderbook_from_redis(symbol, depth=1)
                    if orderbook_redis and orderbook_redis.get('timestamp'):
                        # 计算更新时间
                        seconds_ago = (now_ms - orderbook_redis['timestamp']) / 1000
                        update_status = f", 更新于{seconds_ago:.0f}秒前"

                        # 获取价格信息
                        bids = orderbook_redis.get('bids', [])
                        asks = orderbook_redis.get('asks', [])

                        if bids and asks:
                            bid1_price = float(bids[0][0])
                            ask1_price = float(asks[0][0])
                            spread_pct = (ask1_price - bid1_price) / bid1_price * 100

                            logger.info(
                                f"  订单簿: 价差={spread_pct:.8f}%, "
                                f"买1={bid1_price:.2f}, "
                                f"卖1={ask1_price:.2f}"
                                f"{update_status}"
                            )

                logger.info("\n✓ 数据采集正常运行中...")

                # 显示最后更新时间
                if self.stats['last_update']:
                    logger.info(
                        f"📡 最后数据更新: {self.stats['last_update'].strftime('%H:%M:%S')} "
                        f"({(datetime.now() - self.stats['last_update']).total_seconds():.0f}秒前)"
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"状态监控异常: {e}")

    def stop(self):
        """停止数据采集"""
        if not self.is_running:
            return

        logger.info("\n" + "=" * 60)
        logger.info("🛑 停止数据采集器")
        logger.info("=" * 60)

        self.is_running = False

        # 取消所有后台任务（立即停止所有循环，不等待sleep结束）
        if hasattr(self, 'background_tasks') and self.background_tasks:
            logger.info("正在取消所有后台任务...")
            for task in self.background_tasks:
                if not task.done():
                    task.cancel()
            logger.info(f"✓ 已取消 {len(self.background_tasks)} 个后台任务")

        # 关闭 WebSocket 连接
        if self.ws_public:
            self.ws_public.close()
        if self.ws_business:
            self.ws_business.close()

        # 关闭线程池
        logger.info("正在关闭线程池...")
        self.executor.shutdown(wait=True, cancel_futures=True)

        logger.success("✓ 数据采集器已停止")
        logger.info(f"停止时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    def get_stats(self) -> dict:
        """获取采集统计信息"""
        stats = {
            'is_running': self.is_running,
            'symbols': self.symbols,
            'timeframes': self.timeframes,
            'stats': self.stats,
            'data': {}
        }

        for symbol in self.symbols:
            symbol_stats = {
                'klines': {},
                'trades': {},
                'orderbook': {}
            }

            # K线统计
            for tf in self.timeframes:
                klines = self.data_manager.get_recent_klines(symbol, tf, limit=100)
                symbol_stats['klines'][tf] = len(klines) if klines else 0

            # 成交统计
            for interval, key in [(60, '1m'), (300, '5m'), (900, '15m')]:
                pressure = self.data_manager.get_latest_pressure(symbol, interval)
                symbol_stats['trades'][key] = pressure is not None

            # 订单簿统计
            orderbook = self.data_manager.get_latest_orderbook(symbol)
            symbol_stats['orderbook']['available'] = orderbook is not None

            stats['data'][symbol] = symbol_stats

        return stats


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='OKX独立实时数据采集器 - 持续采集K线、逐笔成交、订单簿数据'
    )

    parser.add_argument(
        '--symbols',
        type=str,
        default='BTC-USDT-SWAP',
        help='交易对列表，逗号分隔（默认: BTC-USDT-SWAP）'
    )

    parser.add_argument(
        '--timeframes',
        type=str,
        default='1m,5m,15m',
        help='K线周期列表，逗号分隔（默认: 1m,5m,15m）'
    )

    parser.add_argument(
        '--history-days',
        type=int,
        default=30,
        help='初始化历史数据天数（默认: 30）'
    )

    parser.add_argument(
        '--data-timeout',
        type=int,
        default=120,
        help='数据更新超时时间（秒），超过此时间未收到数据则重启（默认: 120）'
    )

    parser.add_argument(
        '--max-restarts',
        type=int,
        default=9999,
        help='最大自动重启次数（默认: 10），超过此次数后程序退出'
    )

    parser.add_argument(
        '--status-interval',
        type=int,
        default=60,
        help='状态打印间隔（秒，默认: 60）'
    )

    args = parser.parse_args()

    # 解析参数
    symbols = [s.strip() for s in args.symbols.split(',')]
    timeframes = [tf.strip() for tf in args.timeframes.split(',')]

    # 重启计数器
    restart_count = 0
    max_restarts = args.max_restarts

    # 自动重启循环
    while restart_count <= max_restarts:
        if restart_count > 0:
            logger.warning(f"🔄 第 {restart_count} 次重启 (最多 {max_restarts} 次)")
            await asyncio.sleep(5)  # 等待5秒后重启

        # 创建并启动采集器
        collector = StandaloneDataCollector(
            symbols=symbols,
            timeframes=timeframes,
            history_days=args.history_days,
            data_timeout_seconds=args.data_timeout
        )

        try:
            await collector.start()

            # 正常结束，检查是否需要重启
            if collector.need_restart:
                restart_count += 1
                logger.info(f"✓ 准备重启采集器... (已重启 {restart_count}/{max_restarts} 次)")
                continue
            else:
                # 用户正常退出，不重启
                logger.info("✓ 采集器正常退出")
                break

        except KeyboardInterrupt:
            logger.info("\n⚠️ 用户中断，停止采集器...")
            collector.stop()
            break

        except Exception as e:
            logger.error(f"❌ 程序异常: {e}")
            import traceback
            traceback.print_exc()

            # 异常退出也尝试重启
            restart_count += 1
            if restart_count <= max_restarts:
                logger.warning(f"🔄 因异常准备重启... (已重启 {restart_count}/{max_restarts} 次)")
                collector.stop()
                continue
            else:
                logger.error(f"❌ 已达到最大重启次数 ({max_restarts})，程序退出")
                collector.stop()
                break

        finally:
            # 确保资源清理
            if collector.is_running:
                collector.stop()

    if restart_count > max_restarts:
        logger.critical(f"⚠️ 采集器重启次数超过限制 ({max_restarts})，程序终止")
        sys.exit(1)


if __name__ == '__main__':

    # 配置日志格式
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO"
    )

    # 添加文件日志
    logger.add(
        "logs/data_collector_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="7 days",
        level="INFO"
    )

    logger.info("=" * 60)
    logger.info("OKX 独立实时数据采集器 v2.0")
    logger.info("使用 OkxWebSocket 类 + ThreadPoolExecutor")
    logger.info("=" * 60)
    logger.info(f"collector PID: {os.getpid()}")
    COLLECTOR_PID_FILE = os.path.join(project_root, "data", "collector.pid")

    with open(COLLECTOR_PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

    # 运行
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n程序退出")
    except Exception as e:
        logger.error(f"启动失败: {e}")
        import traceback
        traceback.print_exc()
