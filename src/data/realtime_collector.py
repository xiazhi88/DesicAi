"""
WebSocket实时数据采集器
负责采集订单簿和逐笔成交数据
"""
import asyncio
import json
import time
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional
from loguru import logger

try:
    import websockets
except ImportError:
    logger.warning("websockets库未安装，请运行: pip install websockets")
    websockets = None


class RealtimeDataCollector:
    """实时数据采集器：订单簿 + 逐笔成交 + K线"""

    def __init__(self, data_manager, market_api=None, symbols: List[str] = None, timeframes: List[str] = None, history_days: int = 30):
        """
        初始化实时数据采集器

        Args:
            data_manager: DataManager实例
            market_api: MarketAPI实例（用于获取历史数据）
            symbols: 交易对列表，默认 ['BTC-USDT-SWAP']
            timeframes: K线周期列表，默认 ['1m', '5m', '15m']
            history_days: 历史数据天数，默认30天
        """
        self.db = data_manager
        self.market_api = market_api
        self.symbols = symbols or ['BTC-USDT-SWAP']
        self.timeframes = timeframes or ['1m', '5m', '15m']
        self.history_days = history_days

        # 内存缓冲（用于实时特征计算）
        self.trades_buffer = {sym: deque(maxlen=1000) for sym in self.symbols}
        self.orderbook_cache = {sym: None for sym in self.symbols}
        self.kline_cache = {}  # {symbol: {timeframe: kline_data}}
        for sym in self.symbols:
            self.kline_cache[sym] = {tf: None for tf in self.timeframes}

        # WebSocket配置（OKX频道）
        self.ws_url = "wss://ws.okx.com:8443/ws/v5/public"  # 公共频道：trades + orderbook
        self.ws_business_url = "wss://ws.okx.com:8443/ws/v5/business"  # business频道：K线

        # 运行状态
        self.running = False

        # 统计
        self.stats = {
            'trades_received': 0,
            'orderbook_updates': 0,
            'klines_received': 0,
            'history_klines_loaded': 0,
            'last_update': None
        }

    async def start(self):
        """启动实时数据采集"""
        if websockets is None:
            logger.error("websockets库未安装，无法启动实时数据采集")
            return

        self.running = True

        logger.info(f"🚀 启动实时数据采集: {self.symbols}")
        logger.info(f"📊 K线周期: {self.timeframes}")

        tasks = []

        # 1. 历史数据初始化（后台任务，不阻塞实时采集）
        if self.market_api:
            logger.info("📚 历史数据初始化将在后台运行...")
            tasks.append(self.init_history_data_background())
        else:
            logger.warning("⚠️ 未提供MarketAPI，跳过历史数据初始化")

        # 2. WebSocket 连接（异步运行）
        tasks.append(self.maintain_public_connection())
        tasks.append(self.maintain_business_connection())

        # 3. 后台任务
        tasks.append(self.aggregate_pressure_loop())
        tasks.append(self.snapshot_orderbook_loop())
        tasks.append(self.cleanup_old_data_loop())
        tasks.append(self.stats_report_loop())

        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"实时数据采集错误: {e}")
            self.running = False

    async def init_history_data_background(self):
        """后台初始化历史数据（不阻塞实时采集）"""
        try:
            # 等待2秒，让实时采集先启动
            await asyncio.sleep(2)
            await self.init_history_data()
        except Exception as e:
            logger.error(f"后台历史数据初始化失败: {e}")
            import traceback
            traceback.print_exc()

    async def init_history_data(self):
        """初始化历史数据（智能检测并补齐所有缺失的日期）"""
        from datetime import datetime, timedelta

        logger.info(f"📚 开始初始化历史数据（最近 {self.history_days} 天）...")
        total_loaded = 0

        for symbol in self.symbols:
            for timeframe in self.timeframes:
                try:
                    logger.info(f"  检查 {symbol} {timeframe}...")

                    # 获取需要补齐的时间区间列表
                    missing_ranges = await self._detect_missing_ranges(symbol, timeframe)

                    if not missing_ranges:
                        logger.info(f"  {symbol} {timeframe}: ✓ 数据完整，无需补齐")
                        continue

                    logger.info(f"  {symbol} {timeframe}: 发现 {len(missing_ranges)} 个缺失区间")

                    # 补齐每个缺失区间
                    for i, (start_ts, end_ts) in enumerate(missing_ranges, 1):
                        start_date = datetime.fromtimestamp(start_ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
                        end_date = datetime.fromtimestamp(end_ts / 1000).strftime('%Y-%m-%d %H:%M:%S')

                        logger.info(f"    补齐区间 {i}/{len(missing_ranges)}: {start_date} 至 {end_date}")
                        print(start_ts,end_ts)
                        count = await self._fetch_and_save_klines(
                            symbol, timeframe,
                            after=str(end_ts),  # OKX API: after是结束时间
                            before=str(start_ts)  # before是开始时间
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

    async def _detect_missing_ranges(self, symbol: str, timeframe: str) -> List[tuple]:
        """
        检测缺失的时间区间

        Args:
            symbol: 交易对
            timeframe: K线周期

        Returns:
            缺失区间列表 [(start_ts, end_ts), ...]
        """
        from datetime import datetime, timedelta

        # 计算应该有数据的时间范围
        now = datetime.now()
        end_time = now
        start_time = now - timedelta(days=self.history_days)

        # 获取数据库中的所有K线数据（只取timestamp）
        existing_klines = self.db.get_recent_klines(
            symbol, timeframe,
            limit=self.history_days * 1440  # 最多30天 * 1440分钟
        )

        if not existing_klines:
            # 完全没有数据，返回整个时间范围
            return [(int(start_time.timestamp() * 1000), int(end_time.timestamp() * 1000))]

        # 将现有数据的时间戳转换为集合（按天）
        existing_timestamps = {kline['timestamp'] for kline in existing_klines}

        # 根据K线周期计算间隔（毫秒）
        interval_map = {
            '1m': 60 * 1000,
            '3m': 3 * 60 * 1000,
            '5m': 5 * 60 * 1000,
            '15m': 15 * 60 * 1000,
            '30m': 30 * 60 * 1000,
            '1H': 60 * 60 * 1000,
            '2H': 2 * 60 * 60 * 1000,
            '4H': 4 * 60 * 60 * 1000,
            '1D': 24 * 60 * 60 * 1000,
        }

        interval_ms = interval_map.get(timeframe, 60 * 1000)

        # 检测缺失区间（按天检测，避免过于细粒度）
        missing_ranges = []
        current_time = int(start_time.timestamp() * 1000)
        end_time_ms = int(end_time.timestamp() * 1000)

        # 按天检测（而不是按每根K线）
        day_ms = 24 * 60 * 60 * 1000
        range_start = None

        check_time = current_time
        while check_time < end_time_ms:
            # 检查这一天是否有任何K线数据
            day_start = check_time
            day_end = check_time + day_ms

            # 检查这个时间范围内是否有数据
            has_data = any(
                day_start <= ts < day_end
                for ts in existing_timestamps
            )

            if not has_data:
                # 这一天没有数据
                if range_start is None:
                    # 开始一个新的缺失区间
                    range_start = day_start
            else:
                # 这一天有数据
                if range_start is not None:
                    # 结束当前缺失区间
                    missing_ranges.append((range_start, day_start))
                    range_start = None

            check_time += day_ms

        # 处理最后一个区间
        if range_start is not None:
            missing_ranges.append((range_start, end_time_ms))

        return missing_ranges

    async def _fetch_and_save_klines(self, symbol: str, timeframe: str, after: str = None, before: str = None) -> int:
        """
        获取并保存K线数据（分页处理）

        Args:
            symbol: 交易对
            timeframe: K线周期
            after: 起始时间戳
            before: 结束时间戳

        Returns:
            保存的K线数量
        """
        count = 0
        current_after = after

        # OKX history-candles 每次最多返回100根K线，需要分页
        max_iterations = 1000  # 防止无限循环
        iteration = 0

        while iteration < max_iterations:
            try:
                # 调用REST API获取历史K线
                result = self.market_api.get_history_candles(
                    inst_id=symbol,
                    bar=timeframe,
                    after=current_after,
                    before=before,
                    limit='100'
                )
                print(symbol,timeframe,current_after,before)

                if result['code'] != '0':
                    logger.error(f"获取K线失败: {result.get('msg')}")
                    break

                klines = result.get('data', [])
                if not klines:
                    break

                # 批量保存K线
                for kline in klines:
                    self.db.save_kline(
                        inst_id=symbol,
                        bar=timeframe,
                        kline_data=kline
                    )
                    count += 1

                # OKX返回的K线是从新到旧排序
                # after参数应该是最旧的那根K线的时间戳
                oldest_ts = klines[-1][0]

                # 如果最旧的时间戳等于current_after，说明没有更多数据了
                if current_after and oldest_ts == current_after:
                    break

                # 更新after参数，继续获取更早的数据
                current_after = oldest_ts

                # 如果返回的数量少于100，说明已经到头了
                if len(klines) < 100:
                    break

                iteration += 1

                # 避免请求过快（OKX限速 20次/2秒）
                await asyncio.sleep(0.05)

            except Exception as e:
                logger.error(f"获取K线数据异常: {e}")
                break

        return count

    async def maintain_public_connection(self):
        """维护公共频道WebSocket连接（trades + orderbook）"""
        reconnect_count = 0
        max_reconnects = 100

        while self.running and reconnect_count < max_reconnects:
            try:
                logger.info(f"公共频道连接尝试 {reconnect_count + 1}/{max_reconnects}")

                async with websockets.connect(
                    self.ws_url,
                    ping_interval=None,  # 禁用协议层ping
                    close_timeout=10,
                    open_timeout=30
                ) as ws:
                    logger.info("✓ 公共频道连接已建立")

                    # 构建订阅参数
                    args = []
                    for symbol in self.symbols:
                        args.append({"channel": "trades", "instId": symbol})
                        args.append({"channel": "books5", "instId": symbol})

                    # 发送订阅消息
                    subscribe_msg = {"op": "subscribe", "args": args}
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info(f"✓ 订阅公共频道: {len(self.symbols)}个交易对 (trades + orderbook)")

                    # 重置重连计数
                    if reconnect_count > 0:
                        logger.success("公共频道重连成功")
                    reconnect_count = 0

                    # 心跳保活
                    last_ping_time = time.time()
                    ping_interval = 20  # 20秒（小于OKX的30秒要求）

                    while self.running:
                        try:
                            # 使用1秒超时接收消息，避免阻塞心跳
                            message = await asyncio.wait_for(ws.recv(), timeout=1.0)

                            # 处理 pong 响应
                            if message == 'pong':
                                logger.debug("公共频道收到pong")
                                continue

                            # 解析 JSON 消息
                            data = json.loads(message)

                            # 跳过订阅确认消息
                            if data.get('event') == 'subscribe':
                                logger.debug(f"订阅成功: {data}")
                                continue

                            # 处理数据
                            if 'data' in data and 'arg' in data:
                                channel = data['arg'].get('channel', '')
                                inst_id = data['arg'].get('instId', '')

                                if channel == 'trades':
                                    for trade in data['data']:
                                        await self.process_trade(inst_id, trade)
                                elif channel == 'books5':
                                    for book in data['data']:
                                        await self.process_orderbook(inst_id, book)

                        except asyncio.TimeoutError:
                            # 超时后检查是否需要发送 ping
                            if time.time() - last_ping_time > ping_interval:
                                await ws.send('ping')
                                logger.debug("公共频道发送ping")
                                last_ping_time = time.time()

                        except Exception as e:
                            logger.error(f"公共频道消息处理错误: {e}")
                            import traceback
                            logger.error(f"  堆栈跟踪:\n{traceback.format_exc()}")

            except websockets.exceptions.ConnectionClosed as e:
                reconnect_count += 1
                logger.warning(f"公共频道连接断开，5秒后重连... [{reconnect_count}/{max_reconnects}]")
                logger.error(f"  错误: {type(e).__name__} - {e}")

                # 只在首次错误或每10次重连时显示详细堆栈
                if reconnect_count == 1 or reconnect_count % 10 == 0:
                    import traceback
                    logger.error(f"  堆栈跟踪:\n{traceback.format_exc()}")

                await asyncio.sleep(5)

            except Exception as e:
                reconnect_count += 1
                logger.error(f"公共频道错误: {type(e).__name__} - {e}")

                # 只在首次错误或每10次重连时显示详细堆栈
                if reconnect_count == 1 or reconnect_count % 10 == 0:
                    import traceback
                    logger.error(f"  堆栈跟踪:\n{traceback.format_exc()}")

                await asyncio.sleep(5)

        if reconnect_count >= max_reconnects:
            logger.error(f"公共频道重连次数已达上限 ({max_reconnects})，停止重连")


    async def maintain_business_connection(self):
        """维护business频道WebSocket连接（K线）"""
        reconnect_count = 0
        max_reconnects = 100

        while self.running and reconnect_count < max_reconnects:
            try:
                logger.info(f"business频道连接尝试 {reconnect_count + 1}/{max_reconnects}")

                async with websockets.connect(
                    self.ws_business_url,
                    ping_interval=None,  # 禁用协议层ping
                    close_timeout=10,
                    open_timeout=30
                ) as ws:
                    logger.info("✓ business频道连接已建立")

                    # 构建订阅参数
                    args = []
                    for symbol in self.symbols:
                        for timeframe in self.timeframes:
                            channel = f"candle{timeframe}"
                            args.append({"channel": channel, "instId": symbol})

                    # 发送订阅消息
                    subscribe_msg = {"op": "subscribe", "args": args}
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info(f"✓ 订阅business频道: {len(self.symbols)}个交易对 × {len(self.timeframes)}个周期")

                    # 重置重连计数
                    if reconnect_count > 0:
                        logger.success("business频道重连成功")
                    reconnect_count = 0

                    # 心跳保活
                    last_ping_time = time.time()
                    ping_interval = 20  # 20秒（小于OKX的30秒要求）

                    while self.running:
                        try:
                            # 使用1秒超时接收消息，避免阻塞心跳
                            message = await asyncio.wait_for(ws.recv(), timeout=1.0)

                            # 处理 pong 响应
                            if message == 'pong':
                                logger.debug("business频道收到pong")
                                continue

                            # 解析 JSON 消息
                            data = json.loads(message)

                            # 跳过订阅确认消息
                            if data.get('event') == 'subscribe':
                                logger.debug(f"订阅成功: {data}")
                                continue

                            # 处理K线数据
                            if 'data' in data and 'arg' in data:
                                channel = data['arg'].get('channel', '')
                                inst_id = data['arg'].get('instId', '')

                                # 从channel中提取timeframe
                                if channel.startswith('candle'):
                                    timeframe = channel.replace('candle', '')
                                    for kline in data['data']:
                                        await self.process_kline(inst_id, timeframe, kline)

                        except asyncio.TimeoutError:
                            # 超时后检查是否需要发送 ping
                            if time.time() - last_ping_time > ping_interval:
                                await ws.send('ping')
                                logger.debug("business频道发送ping")
                                last_ping_time = time.time()

                        except Exception as e:
                            logger.error(f"business频道消息处理错误: {e}")
                            import traceback
                            logger.error(f"  堆栈跟踪:\n{traceback.format_exc()}")

            except websockets.exceptions.ConnectionClosed as e:
                reconnect_count += 1
                logger.warning(f"business频道连接断开，5秒后重连... [{reconnect_count}/{max_reconnects}]")
                logger.error(f"  错误: {type(e).__name__} - {e}")

                # 只在首次错误或每10次重连时显示详细堆栈
                if reconnect_count == 1 or reconnect_count % 10 == 0:
                    import traceback
                    logger.error(f"  堆栈跟踪:\n{traceback.format_exc()}")

                await asyncio.sleep(5)

            except Exception as e:
                reconnect_count += 1
                logger.error(f"business频道错误: {type(e).__name__} - {e}")

                # 只在首次错误或每10次重连时显示详细堆栈
                if reconnect_count == 1 or reconnect_count % 10 == 0:
                    import traceback
                    logger.error(f"  堆栈跟踪:\n{traceback.format_exc()}")

                await asyncio.sleep(5)

        if reconnect_count >= max_reconnects:
            logger.error(f"business频道重连次数已达上限 ({max_reconnects})，停止重连")


    async def process_kline(self, symbol: str, timeframe: str, kline: List):
        """
        处理K线数据

        OKX K线数据格式:
        [
            "1597026383085",  // 时间戳
            "3.721",          // 开盘价
            "3.743",          // 最高价
            "3.677",          // 最低价
            "3.708",          // 收盘价
            "8422410",        // 成交量(张)
            "22698348.04828491",  // 成交量(计价货币)
            "22698348.04828491",  // 成交量(计价货币，重复)
            "0"               // 0=未完成 1=已完成
        ]
        """
        try:
            # 只保存已完成的K线 (confirm=1)
            # 但为了实时性，也可以保存未完成的K线，用 INSERT OR REPLACE
            confirm = kline[8] if len(kline) > 8 else "0"

            # 解析K线数据
            kline_data = {
                'timestamp': int(kline[0]),
                'open': float(kline[1]),
                'high': float(kline[2]),
                'low': float(kline[3]),
                'close': float(kline[4]),
                'volume': float(kline[5]),
                'confirm': confirm
            }

            # 缓存到内存（用于实时特征计算）
            self.kline_cache[symbol][timeframe] = kline_data

            # 保存到数据库（实时更新，未完成的K线也保存）
            self.db.save_kline(
                inst_id=symbol,
                bar=timeframe,
                kline_data=kline  # 传入原始数据
            )

            self.stats['klines_received'] += 1

            # 只在K线完成时记录
            if confirm == "1":
                logger.debug(f"✓ K线完成: {symbol} {timeframe} @{kline_data['close']:.2f}")

        except Exception as e:
            logger.error(f"处理K线数据失败: {e}, 原始数据: {kline}")


    async def process_trade(self, symbol: str, trade: Dict):
        """
        处理单笔成交

        OKX成交数据格式:
        {
            "instId": "BTC-USDT-SWAP",
            "tradeId": "123456",
            "px": "50000.0",
            "sz": "1.5",
            "side": "buy",
            "ts": "1609459200000"
        }
        """
        try:
            # 解析数据
            trade_data = {
                'trade_id': trade['tradeId'],
                'timestamp': int(trade['ts']),
                'price': float(trade['px']),
                'size': float(trade['sz']),
                'side': trade['side']  # 'buy' or 'sell'
            }

            # 写入内存缓冲
            self.trades_buffer[symbol].append(trade_data)
            self.stats['trades_received'] += 1
            self.stats['last_update'] = datetime.now()

            # 批量写入数据库（每100条）
            if len(self.trades_buffer[symbol]) >= 100:
                await self.flush_trades_to_db(symbol)

        except Exception as e:
            logger.error(f"处理成交数据失败: {e}, 原始数据: {trade}")

    async def flush_trades_to_db(self, symbol: str):
        """批量写入逐笔成交到数据库"""
        trades = list(self.trades_buffer[symbol])
        if not trades:
            return

        try:
            self.db.save_trades_batch(symbol, trades)
            #logger.debug(f"✓ 批量保存成交: {symbol} {len(trades)}笔")
        except Exception as e:
            logger.error(f"写入成交数据失败: {e}")

    async def process_orderbook(self, symbol: str, book: Dict):
        """
        处理订单簿数据

        OKX订单簿格式:
        {
            "asks": [["50100", "10", "0", "1"], ...],
            "bids": [["50099", "15", "0", "2"], ...],
            "ts": "1609459200000",
            "checksum": 123456
        }
        """
        try:
            bids = book.get('bids', [])
            asks = book.get('asks', [])
            timestamp = int(book['ts'])

            if not bids or not asks:
                return

            # 计算特征
            bid1_price = float(bids[0][0])
            bid1_size = float(bids[0][1])
            ask1_price = float(asks[0][0])
            ask1_size = float(asks[0][1])

            mid_price = (bid1_price + ask1_price) / 2
            spread_pct = (ask1_price - bid1_price) / mid_price * 100

            # 计算5档深度
            bid_depth_5 = sum(float(b[1]) for b in bids[:5]) if len(bids) >= 5 else 0
            ask_depth_5 = sum(float(a[1]) for a in asks[:5]) if len(asks) >= 5 else 0
            depth_ratio = bid_depth_5 / ask_depth_5 if ask_depth_5 > 0 else 0

            # 缓存到内存
            self.orderbook_cache[symbol] = {
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

            self.stats['orderbook_updates'] += 1

        except Exception as e:
            logger.error(f"处理订单簿失败: {e}, 原始数据: {book}")

    async def snapshot_orderbook_loop(self):
        """定期快照订单簿（每分钟1次）"""
        while self.running:
            try:
                await asyncio.sleep(60)

                for symbol in self.symbols:
                    book = self.orderbook_cache.get(symbol)
                    if not book:
                        continue

                    self.db.save_orderbook_snapshot(symbol, book)

                logger.debug("✓ 订单簿快照已保存")

            except Exception as e:
                logger.error(f"订单簿快照错误: {e}")

    async def aggregate_pressure_loop(self):
        """聚合市场压力指标（每分钟1次）"""
        while self.running:
            try:
                await asyncio.sleep(60)

                for symbol in self.symbols:
                    # 1分钟、5分钟、15分钟压力
                    for interval_sec in [60, 300, 900]:
                        pressure = self.calculate_pressure(symbol, interval_sec)
                        if pressure:
                            self.db.save_market_pressure(symbol, interval_sec, pressure)

                logger.debug("✓ 市场压力指标已更新")

            except Exception as e:
                logger.error(f"压力聚合错误: {e}")

    def calculate_pressure(self, symbol: str, interval_seconds: int) -> Optional[Dict]:
        """
        计算市场买卖压力

        Args:
            symbol: 交易对
            interval_seconds: 时间间隔（秒）

        Returns:
            压力指标字典
        """
        try:
            # 从数据库查询最近的成交
            trades = self.db.get_recent_trades(symbol, interval_seconds)

            if not trades:
                return None

            buy_volume = 0
            sell_volume = 0
            buy_count = 0
            sell_count = 0
            large_buy_volume = 0  # >10万U
            large_sell_volume = 0
            buy_prices = []
            sell_prices = []

            for trade in trades:
                price = trade['price']
                size = trade['size']
                side = trade['side']
                value = price * size

                if side == 'buy':
                    buy_volume += size
                    buy_count += 1
                    buy_prices.append(price)
                    if value > 100000:
                        large_buy_volume += size
                else:
                    sell_volume += size
                    sell_count += 1
                    sell_prices.append(price)
                    if value > 100000:
                        large_sell_volume += size

            avg_buy_price = sum(buy_prices) / len(buy_prices) if buy_prices else 0
            avg_sell_price = sum(sell_prices) / len(sell_prices) if sell_prices else 0
            pressure_ratio = buy_volume / sell_volume if sell_volume > 0 else 0

            return {
                'timestamp': int(datetime.now().timestamp() * 1000),
                'buy_volume': buy_volume,
                'sell_volume': sell_volume,
                'buy_count': buy_count,
                'sell_count': sell_count,
                'large_buy_volume': large_buy_volume,
                'large_sell_volume': large_sell_volume,
                'avg_buy_price': avg_buy_price,
                'avg_sell_price': avg_sell_price,
                'pressure_ratio': pressure_ratio
            }

        except Exception as e:
            logger.error(f"计算压力指标失败: {e}")
            return None

    async def cleanup_old_data_loop(self):
        """清理旧数据（每小时1次）"""
        while self.running:
            try:
                await asyncio.sleep(3600)

                # 只保留最近1小时的逐笔成交
                self.db.cleanup_old_trades(hours=1)

                # 只保留最近24小时的订单簿快照
                self.db.cleanup_old_orderbook(hours=24)

                logger.info("✓ 旧数据已清理")

            except Exception as e:
                logger.error(f"清理数据错误: {e}")

    async def stats_report_loop(self):
        """统计报告（每5分钟）"""
        while self.running:
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

    def stop(self):
        """停止数据采集"""
        logger.info("⏹️  停止实时数据采集")
        self.running = False
        # WebSocket连接会通过async context manager自动关闭

    def get_latest_orderbook(self, symbol: str) -> Optional[Dict]:
        """获取最新订单簿（内存）"""
        return self.orderbook_cache.get(symbol)

    def get_latest_kline(self, symbol: str, timeframe: str) -> Optional[Dict]:
        """获取最新K线（内存）"""
        if symbol in self.kline_cache:
            return self.kline_cache[symbol].get(timeframe)
        return None

    def get_recent_trades_from_memory(self, symbol: str, limit: int = 100) -> List[Dict]:
        """从内存缓冲获取最近成交"""
        buffer = self.trades_buffer.get(symbol, deque())
        return list(buffer)[-limit:]


# 测试代码
async def test_collector():
    """测试实时数据采集器"""
    from src.ai.data_manager import DataManager

    # 创建数据管理器
    dm = DataManager()

    # 创建采集器
    collector = RealtimeDataCollector(dm, symbols=['BTC-USDT-SWAP'])

    try:
        # 运行5分钟后停止
        await asyncio.wait_for(collector.start(), timeout=300)
    except asyncio.TimeoutError:
        logger.info("测试完成，停止采集")
        collector.stop()


if __name__ == '__main__':
    asyncio.run(test_collector())
