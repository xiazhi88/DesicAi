"""
数据管理模块
负责缓存实时数据和存储历史数据
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql
from dbutils.pooled_db import PooledDB
import json
import redis
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger
import threading
import time
from functools import wraps
from src.config.settings import config
from src.config.settings import reload_config




class DataManager:
    """数据管理器 - 管理实时和历史数据"""

    def __init__(self):
        """
        初始化数据管理器

        说明：
            - 从配置文件读取MySQL连接参数
            - 初始化MySQL连接池
            - 初始化数据库表结构
        """
        # 不再缓存配置，改为动态读取
        # 这样可以确保配置更新后立即生效
        self.redis_client=None

        # 初始化MySQL连接池
        self._init_mysql_pool()
        self._init_database()


        # 初始化 Redis 连接池（订单簿存储）
        try:
            # 使用连接池，避免连接资源耗尽
            pool = redis.ConnectionPool(
                host='localhost',
                port=6379,
                db=0,
                decode_responses=True,  # 自动解码为字符串
                max_connections=500,     # 最大连接数
                socket_timeout=5,
                socket_connect_timeout=5,
                socket_keepalive=True,  # 保持连接活跃
                health_check_interval=30  # 健康检查间隔（秒）
            )
            self.redis_client = redis.Redis(connection_pool=pool)

            # 测试连接
            self.redis_client.ping()
            logger.info("✓ Redis连接池初始化成功 (max_connections=50)")
        except Exception as e:
            logger.warning(f"⚠️ Redis连接失败: {e}，订单簿功能将不可用")
            self.redis_client = None

        # 内存缓存（最近的数据）
        self.cache = {
            'tickers': {},      # 最新行情
            'klines': {},       # 最近K线
            'positions': {},    # 当前持仓
            'orders': []        # 最近订单
        }

    def _init_mysql_pool(self):
        """
        初始化MySQL连接池（使用DBUtils）

        连接池参数说明：
            - mincached: 启动时创建的最小空闲连接数
            - maxcached: 连接池中最多闲置的连接数
            - maxconnections: 连接池允许的最大连接数
            - blocking: 连接池达到最大连接数时，是否阻塞等待
        """
        try:
            reload_config()
            self.mysql_pool = PooledDB(
                creator=pymysql,
                maxconnections=20,        # 最大连接数(从500降到20)
                mincached=2,              # 初始化时创建的空闲连接数
                maxcached=10,             # 连接池中最多空闲连接数
                blocking=True,            # 达到最大连接数时阻塞等待
                ping=1,                   # 自动检查连接是否可用(1=默认检查)
                host=config.MYSQL_HOST,
                port=config.MYSQL_PORT,
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                database=config.MYSQL_DATABASE,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False
            )
            logger.info(f"✓ MySQL连接池初始化成功 (max_connections=20)")
        except Exception as e:
            logger.error(f"MySQL连接池初始化失败: {e}")
            raise

    def _get_connection(self, readonly: bool = False):
        """
        从连接池获取MySQL数据库连接（线程安全）

        Args:
            readonly: 是否只读连接（仅作为提示，实际权限由用户决定）

        Returns:
            pymysql.Connection

        说明：
            使用连接池复用连接，避免频繁创建/销毁连接导致 'Too many connections' 错误
        """
        try:
            conn = self.mysql_pool.connection()
            return conn
        except Exception as e:
            logger.error(f"从连接池获取MySQL连接失败: {e}")
            raise

    def _init_database(self):
        """初始化MySQL数据库表结构"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 创建K线表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS klines (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    inst_id VARCHAR(50) NOT NULL,
                    bar VARCHAR(10) NOT NULL,
                    timestamp BIGINT NOT NULL,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    volume DOUBLE,
                    is_confirmed TINYINT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_kline (inst_id, bar, timestamp)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # 创建订单表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    order_id VARCHAR(100) UNIQUE,
                    inst_id VARCHAR(50) NOT NULL,
                    side VARCHAR(10) NOT NULL,
                    order_type VARCHAR(20),
                    price DOUBLE,
                    size DOUBLE,
                    status VARCHAR(20),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    filled_at TIMESTAMP NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # 创建交易信号表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS signals (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    inst_id VARCHAR(50) NOT NULL,
                    signal_type VARCHAR(20) NOT NULL,
                    reason TEXT,
                    ai_analysis TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # 创建逐笔成交缓冲表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades_buffer (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(50) NOT NULL,
                    trade_id VARCHAR(100) NOT NULL,
                    timestamp BIGINT NOT NULL,
                    price DOUBLE NOT NULL,
                    size DOUBLE NOT NULL,
                    side VARCHAR(10) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_trade (symbol, trade_id),
                    KEY idx_trades_time (symbol, timestamp DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # 创建订单簿快照表（聚合指标）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS orderbook_snapshot (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(50) NOT NULL,
                    timestamp BIGINT NOT NULL,
                    bid_price_1 DOUBLE,
                    bid_size_1 DOUBLE,
                    ask_price_1 DOUBLE,
                    ask_size_1 DOUBLE,
                    spread_pct DOUBLE,
                    bid_depth_5 DOUBLE,
                    ask_depth_5 DOUBLE,
                    depth_ratio DOUBLE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_snapshot (symbol, timestamp),
                    KEY idx_orderbook_time (symbol, timestamp DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # 创建原始订单簿表（保存Top10档位）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS orderbook_raw (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(50) NOT NULL,
                    timestamp BIGINT NOT NULL,
                    bids TEXT NOT NULL,
                    asks TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_raw (symbol, timestamp),
                    KEY idx_orderbook_raw_time (symbol, timestamp DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # 创建实时订单簿表（维护完整深度数据，支持增量更新）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS orderbook_live (
                    symbol VARCHAR(50) NOT NULL,
                    side VARCHAR(10) NOT NULL,
                    price VARCHAR(50) NOT NULL,
                    size VARCHAR(50) NOT NULL,
                    orders VARCHAR(50) NOT NULL,
                    timestamp BIGINT NOT NULL,
                    seqId BIGINT NOT NULL,
                    PRIMARY KEY (symbol, side, price),
                    KEY idx_orderbook_live_symbol (symbol, side, price),
                    KEY idx_orderbook_live_seqid (symbol, seqId DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # 创建市场压力指标表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS market_pressure (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(50) NOT NULL,
                    timestamp BIGINT NOT NULL,
                    interval_seconds INT NOT NULL,
                    buy_volume DOUBLE NOT NULL,
                    sell_volume DOUBLE NOT NULL,
                    buy_count INT NOT NULL,
                    sell_count INT NOT NULL,
                    large_buy_volume DOUBLE DEFAULT 0,
                    large_sell_volume DOUBLE DEFAULT 0,
                    avg_buy_price DOUBLE,
                    avg_sell_price DOUBLE,
                    pressure_ratio DOUBLE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_pressure (symbol, timestamp, interval_seconds),
                    KEY idx_pressure_time (symbol, timestamp DESC, interval_seconds)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # 创建账单流水表（bills archive）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bills_archive (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    bill_id VARCHAR(100) NOT NULL,
                    api_key VARCHAR(100) NOT NULL,
                    inst_id VARCHAR(50),
                    ccy VARCHAR(20) NOT NULL,
                    bal DOUBLE NOT NULL,
                    bal_chg DOUBLE NOT NULL,
                    type VARCHAR(50),
                    sub_type VARCHAR(50),
                    ts BIGINT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_bill (bill_id, api_key),
                    KEY idx_bills_api_key (api_key),
                    KEY idx_bills_ccy_ts (api_key, ccy, ts DESC),
                    KEY idx_bills_ts (api_key, ts DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # 创建历史仓位表（只保存已平仓记录）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS positions_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    inst_id VARCHAR(50) NOT NULL,
                    pos_side VARCHAR(10) NOT NULL,
                    pos DOUBLE NOT NULL,
                    avg_px DOUBLE NOT NULL,
                    mark_px DOUBLE NOT NULL,
                    upl DOUBLE NOT NULL,
                    upl_ratio DOUBLE NOT NULL,
                    leverage VARCHAR(10),
                    margin DOUBLE,
                    imr DOUBLE,
                    fee DOUBLE,
                    open_time BIGINT,
                    close_time BIGINT NOT NULL,
                    realized_pnl DOUBLE NOT NULL,
                    close_total_pos DOUBLE,
                    review_summary TEXT,
                    api_key VARCHAR(100) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_position (inst_id, pos_side, open_time, api_key),
                    KEY idx_positions_inst_id (inst_id),
                    KEY idx_positions_close_time (close_time DESC),
                    KEY idx_positions_api_key (api_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # 创建AI决策日志表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ai_decision_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    timestamp DATETIME NOT NULL,
                    pos_id VARCHAR(50),
                    inst_id VARCHAR(50) NOT NULL,
                    pos_side VARCHAR(10),
                    action VARCHAR(20) NOT NULL,
                    size FLOAT,
                    confidence INT,
                    adjust_data TEXT,
                    holding_time VARCHAR(50),
                    reason TEXT,
                    api_key VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    tp_layer_count INT,
                    sl_layer_count INT,
                    KEY idx_pos_id (pos_id),
                    KEY idx_timestamp (timestamp),
                    KEY idx_inst_id (inst_id),
                    KEY idx_api_key (api_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # 创建AI对话记录表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ai_conversations (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    session_id VARCHAR(100),
                    inst_id VARCHAR(50),
                    prompt TEXT,
                    response TEXT,
                    `signal` VARCHAR(20),
                    confidence INT,
                    is_executed TINYINT DEFAULT 0,
                    api_key VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    KEY idx_session_id (session_id),
                    KEY idx_inst_id (inst_id),
                    KEY idx_api_key (api_key),
                    KEY idx_created_at (created_at DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            conn.commit()
            logger.info(f"MySQL数据库初始化完成: {config.MYSQL_HOST}:{config.MYSQL_PORT}/{config.MYSQL_DATABASE}")

        except Exception as e:
            logger.error(f"初始化MySQL数据库失败: {e}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    def update_ticker(self, inst_id: str, ticker_data: Dict):
        """更新实时行情到缓存"""
        self.cache['tickers'][inst_id] = {
            'data': ticker_data,
            'updated_at': datetime.now()
        }

    def get_ticker(self, inst_id: str) -> Optional[Dict]:
        """获取最新行情"""
        cached = self.cache['tickers'].get(inst_id)
        if cached:
            # 检查是否过期（超过1分钟）
            if datetime.now() - cached['updated_at'] < timedelta(minutes=1):
                return cached['data']
        return None

    def save_kline(self, inst_id: str, bar: str, kline_data: List):
        """
        保存K线数据到数据库（支持未完结K线的实时更新）

        Args:
            inst_id: 产品ID
            bar: K线周期
            kline_data: K线数据 [timestamp, open, high, low, close, volume, volCcy, volCcyQuote, confirm]
                       confirm: "0"=未完结, "1"=已完结

        说明：
            - 对于同一时间戳的K线，使用智能UPSERT策略：
              1. 如果数据库中不存在该K线，直接插入
              2. 如果已存在且is_confirmed=0，允许更新（未完结K线）
              3. 如果已存在且is_confirmed=1，禁止更新（已完结K线不可覆盖）
            - 未完结的K线会持续更新最新的high/low/close/volume
            - 完结后 is_confirmed=1，数据固定不变
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 提取confirm字段（第9个字段，索引8）
            confirm = kline_data[8] if len(kline_data) > 8 else "0"
            is_confirmed = 1 if confirm == "1" else 0

            # 使用 INSERT ... ON DUPLICATE KEY UPDATE 实现智能UPSERT
            # 仅当数据库中 is_confirmed=0 时才允许更新
            cursor.execute('''
                INSERT INTO klines
                (inst_id, bar, timestamp, open, high, low, close, volume, is_confirmed)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    open = IF(is_confirmed = 0, VALUES(open), open),
                    high = IF(is_confirmed = 0, VALUES(high), high),
                    low = IF(is_confirmed = 0, VALUES(low), low),
                    close = IF(is_confirmed = 0, VALUES(close), close),
                    volume = IF(is_confirmed = 0, VALUES(volume), volume),
                    is_confirmed = IF(is_confirmed = 0, VALUES(is_confirmed), is_confirmed)
            ''', (
                inst_id, bar,
                int(kline_data[0]),
                float(kline_data[1]),
                float(kline_data[2]),
                float(kline_data[3]),
                float(kline_data[4]),
                float(kline_data[5]) if len(kline_data) > 5 else 0,
                is_confirmed
            ))
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"保存K线失败: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def save_klines_batch(self, inst_id: str, bar: str, klines_data: List[List]) -> int:
        """
        批量保存K线数据到数据库（性能优化版本）

        Args:
            inst_id: 产品ID
            bar: K线周期
            klines_data: K线数据列表，每个元素是 [timestamp, open, high, low, close, volume, volCcy, volCcyQuote, confirm]

        Returns:
            成功保存的K线数量

        说明：
            - 使用 executemany 批量插入，比逐条插入快10-100倍
            - 使用单个事务，全部成功才提交
            - 与 save_kline 相同的UPSERT策略
        """
        if not klines_data:
            return 0

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 准备批量数据
            batch_data = []
            for kline_data in klines_data:
                confirm = kline_data[8] if len(kline_data) > 8 else "0"
                is_confirmed = 1 if confirm == "1" else 0

                batch_data.append((
                    inst_id, bar,
                    int(kline_data[0]),
                    float(kline_data[1]),
                    float(kline_data[2]),
                    float(kline_data[3]),
                    float(kline_data[4]),
                    float(kline_data[5]) if len(kline_data) > 5 else 0,
                    is_confirmed
                ))

            # 批量执行
            cursor.executemany('''
                INSERT INTO klines
                (inst_id, bar, timestamp, open, high, low, close, volume, is_confirmed)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    open = IF(is_confirmed = 0, VALUES(open), open),
                    high = IF(is_confirmed = 0, VALUES(high), high),
                    low = IF(is_confirmed = 0, VALUES(low), low),
                    close = IF(is_confirmed = 0, VALUES(close), close),
                    volume = IF(is_confirmed = 0, VALUES(volume), volume),
                    is_confirmed = IF(is_confirmed = 0, VALUES(is_confirmed), is_confirmed)
            ''', batch_data)

            conn.commit()
            return len(batch_data)

        except Exception as e:
            conn.rollback()
            logger.error(f"批量保存K线失败: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def get_recent_klines(self, inst_id: str, bar: str, limit: int = 100,
                          start_time: int = None, end_time: int = None) -> List[Dict]:
        """
        获取最近的K线数据（包含完结状态）

        Args:
            inst_id: 产品ID
            bar: K线周期
            limit: 数量限制
            start_time: 开始时间戳（毫秒，可选），筛选 >= start_time 的K线
            end_time: 结束时间戳（毫秒，可选），筛选 <= end_time 的K线

        Returns:
            K线数据列表，每个K线包含 is_confirmed 字段标识完结状态
            - is_confirmed=True: K线已完结，数据固定
            - is_confirmed=False: K线未完结，仍在实时更新

        说明：
            - 如果提供了 start_time 和/或 end_time，会按时间范围筛选
            - 如果只提供 limit，返回最近的 N 条K线
            - 如果同时提供时间范围和 limit，返回时间范围内最近的 N 条K线
        """
        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()
        try:
            # 构建查询条件
            where_conditions = ["inst_id = %s", "bar = %s"]
            params = [inst_id, bar]

            # 添加时间范围条件
            if start_time is not None:
                where_conditions.append("timestamp >= %s")
                params.append(start_time)

            if end_time is not None:
                where_conditions.append("timestamp <= %s")
                params.append(end_time)

            where_clause = " AND ".join(where_conditions)

            # 添加 limit 参数
            params.append(limit)

            # 使用子查询：先降序取最近N条，再正序返回
            query = f'''
                SELECT timestamp, open, high, low, close, volume, is_confirmed
                FROM (
                    SELECT timestamp, open, high, low, close, volume, is_confirmed
                    FROM klines
                    WHERE {where_clause}
                    ORDER BY timestamp DESC
                    LIMIT %s
                ) AS sub
                ORDER BY timestamp ASC
            '''

            cursor.execute(query, params)

            rows = cursor.fetchall()

            return [
                {
                    'timestamp': row['timestamp'],
                    'open': row['open'],
                    'high': row['high'],
                    'low': row['low'],
                    'close': row['close'],
                    'volume': row['volume'],
                    'is_confirmed': bool(row['is_confirmed']) if 'is_confirmed' in row else True  # 兼容旧数据
                }
                for row in rows
            ]
        finally:
            cursor.close()
            conn.close()

    def get_unconfirmed_klines(self) -> List[Dict]:
        """
        获取所有未完结的K线（用于启动时修复）

        Returns:
            未完结K线列表，包含inst_id, bar, timestamp等信息
        """
        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                SELECT inst_id, bar, timestamp, open, high, low, close, volume
                FROM klines
                WHERE is_confirmed = 0
                ORDER BY inst_id, bar, timestamp
            ''')

            rows = cursor.fetchall()

            return [
                {
                    'inst_id': row['inst_id'],
                    'bar': row['bar'],
                    'timestamp': row['timestamp'],
                    'open': row['open'],
                    'high': row['high'],
                    'low': row['low'],
                    'close': row['close'],
                    'volume': row['volume']
                }
                for row in rows
            ]
        finally:
            cursor.close()
            conn.close()

    def update_kline_confirmed(self, inst_id: str, bar: str, timestamp: int,
                               open_price: float, high: float, low: float,
                               close: float, volume: float):
        """
        更新K线为已完结状态（用于修复未完结的历史K线）

        Args:
            inst_id: 产品ID
            bar: K线周期
            timestamp: K线时间戳
            open_price: 开盘价
            high: 最高价
            low: 最低价
            close: 收盘价
            volume: 成交量
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                UPDATE klines
                SET open = %s, high = %s, low = %s, close = %s, volume = %s, is_confirmed = 1
                WHERE inst_id = %s AND bar = %s AND timestamp = %s
            ''', (open_price, high, low, close, volume, inst_id, bar, timestamp))
            conn.commit()
        except Exception as e:
            logger.error(f"更新K线完结状态失败: {e}")
        finally:
            cursor.close()
            conn.close()

    def save_signal(self, inst_id: str, signal_type: str, reason: str, ai_analysis: str):
        """
        保存AI交易信号

        Args:
            inst_id: 产品ID
            signal_type: 信号类型 (BUY/SELL/HOLD)
            reason: 原因
            ai_analysis: AI分析结果
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO signals (inst_id, signal_type, reason, ai_analysis)
                VALUES (%s, %s, %s, %s)
            ''', (inst_id, signal_type, reason, ai_analysis))

            conn.commit()
            logger.info(f"保存交易信号: {inst_id} - {signal_type}")
        finally:
            cursor.close()
            conn.close()

    def get_recent_signals(self, inst_id: str, limit: int = 10) -> List[Dict]:
        """获取最近的交易信号"""
        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT signal_type, reason, ai_analysis, created_at
                FROM signals
                WHERE inst_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            ''', (inst_id, limit))

            rows = cursor.fetchall()

            return [
                {
                    'signal_type': row['signal_type'],
                    'reason': row['reason'],
                    'ai_analysis': row['ai_analysis'],
                    'created_at': row['created_at']
                }
                for row in rows
            ]
        finally:
            cursor.close()
            conn.close()

    def save_order(self, order_data: Dict):
        """保存订单记录"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                REPLACE INTO orders
                (order_id, inst_id, side, order_type, price, size, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (
                order_data.get('ordId'),
                order_data.get('instId'),
                order_data.get('side'),
                order_data.get('ordType'),
                float(order_data.get('px', 0)),
                float(order_data.get('sz', 0)),
                order_data.get('state')
            ))

            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def get_market_context(self, inst_id: str) -> Dict:
        """
        获取市场上下文（供AI分析使用）

        Returns:
            包含实时行情、历史K线、最近信号的完整上下文
        """
        context = {
            'inst_id': inst_id,
            'timestamp': datetime.now().isoformat(),
            'current_price': None,
            'recent_klines': [],
            'recent_signals': []
        }

        # 获取当前价格
        ticker = self.get_ticker(inst_id)
        if ticker:
            context['current_price'] = ticker.get('last')

        # 获取最近50根K线
        klines = self.get_recent_klines(inst_id, '1H', limit=50)
        context['recent_klines'] = klines[-10:]  # 只返回最近10根给AI

        # 获取最近的信号
        context['recent_signals'] = self.get_recent_signals(inst_id, limit=5)

        return context

    # ========== 新增：逐笔成交数据管理（使用Redis） ==========

    def save_trades_to_redis(self, symbol: str, trades: List[Dict]):
        """
        保存逐笔成交数据到Redis（高性能，无锁竞争）

        Args:
            symbol: 交易对
            trades: 成交列表 [{'trade_id', 'timestamp', 'price', 'size', 'side'}, ...]

        存储结构：
            - Key: trades:{symbol}
            - Type: Sorted Set (按时间戳排序)
            - Value: JSON序列化的成交数据
            - Score: 时间戳（毫秒）
            - 容量: 保留最近1000条

        优势：
            - 无锁竞争，高并发写入
            - 自动按时间戳排序
            - 使用ZADD自动去重（相同trade_id）
            - ZREMRANGEBYRANK自动清理旧数据
        """
        if not self.redis_client:
            logger.warning("Redis未连接，无法保存成交数据")
            return

        if not trades:
            return

        try:
            key = f'trades:{symbol}'
            pipe = self.redis_client.pipeline()

            # 批量添加成交数据（使用时间戳作为score）
            for trade in trades:
                # 使用trade_id作为member的一部分，确保唯一性
                member = json.dumps({
                    'trade_id': trade['trade_id'],
                    'timestamp': trade['timestamp'],
                    'price': trade['price'],
                    'size': trade['size'],
                    'side': trade['side']
                })
                score = trade['timestamp']
                pipe.zadd(key, {member: score})

            # 只保留最近1000条（按score降序，删除最旧的）
            pipe.zremrangebyrank(key, 0, -10001)

            # 设置过期时间（1小时，防止采集器停止后垃圾数据）
            pipe.expire(key, 3600)

            # 执行批量命令
            pipe.execute()

            logger.debug(f"✓ Redis保存成交数据: {symbol}, {len(trades)}笔")

        except Exception as e:
            logger.error(f"保存成交数据到Redis失败: {e}")

    def get_recent_trades_from_redis(self, symbol: str, seconds: int = 60) -> List[Dict]:
        """
        从Redis获取最近N秒的逐笔成交

        Args:
            symbol: 交易对
            seconds: 时间范围（秒）

        Returns:
            成交列表（按时间戳升序）
        """
        if not self.redis_client:
            logger.warning("Redis未连接，无法获取成交数据")
            return []

        try:
            key = f'trades:{symbol}'
            now_ms = int(datetime.now().timestamp() * 1000)
            cutoff_ms = now_ms - (seconds * 1000)

            # 使用ZRANGEBYSCORE按时间范围查询（升序）
            trades_raw = self.redis_client.zrangebyscore(
                key,
                min=cutoff_ms,
                max=now_ms
            )

            # 解析JSON
            trades = []
            for trade_json in trades_raw:
                try:
                    trade = json.loads(trade_json)
                    trades.append(trade)
                except json.JSONDecodeError:
                    logger.warning(f"解析成交数据失败: {trade_json}")
                    continue

            return trades

        except Exception as e:
            logger.error(f"从Redis获取成交数据失败: {e}")
            return []

    def get_latest_trade_timestamp_from_redis(self, symbol: str) -> Optional[int]:
        """
        从Redis获取最新逐笔成交的时间戳（用于超时检测）

        Args:
            symbol: 交易对

        Returns:
            最新成交时间戳（毫秒），如果没有数据则返回None
        """
        if not self.redis_client:
            return None

        try:
            key = f'trades:{symbol}'

            # 获取最新的一条成交（按score降序）
            trades_raw = self.redis_client.zrevrange(key, 0, 0)

            if not trades_raw:
                return None

            # 解析第一条数据
            trade = json.loads(trades_raw[0])
            return trade.get('timestamp')

        except Exception as e:
            logger.error(f"从Redis获取最新成交时间戳失败: {e}")
            return None

    def clear_redis_trades(self, symbol: str):
        """
        清空Redis中指定交易对的成交数据

        Args:
            symbol: 交易对
        """
        if not self.redis_client:
            return

        try:
            key = f'trades:{symbol}'
            self.redis_client.delete(key)
            logger.info(f"✓ 已清空Redis成交数据: {symbol}")
        except Exception as e:
            logger.error(f"清空Redis成交数据失败: {e}")

    # ========== 旧的MySQL成交数据管理（保留用于历史数据存储，按需使用） ==========

    def save_trades_batch(self, symbol: str, trades: List[Dict]):
        """
        批量保存逐笔成交数据到MySQL（已弃用，改用Redis）

        保留此方法用于特殊场景（如历史数据归档）
        日常使用请使用 save_trades_to_redis()

        Args:
            symbol: 交易对
            trades: 成交列表 [{'trade_id', 'timestamp', 'price', 'size', 'side'}, ...]
        """
        if not trades:
            return

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            data = [
                (symbol, t['trade_id'], t['timestamp'], t['price'], t['size'], t['side'])
                for t in trades
            ]

            cursor.executemany('''
                INSERT IGNORE INTO trades_buffer
                (symbol, trade_id, timestamp, price, size, side)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', data)

            conn.commit()
            #logger.debug(f"批量保存 {len(trades)} 笔成交数据")
        except Exception as e:
            conn.rollback()
            logger.error(f"保存成交数据失败: {e}")
            raise  # 重新抛出异常，让装饰器处理重试
        finally:
            cursor.close()
            conn.close()

    def get_recent_trades(self, symbol: str, seconds: int = 60) -> List[Dict]:
        """
        获取最近N秒的逐笔成交（从Redis读取）

        Args:
            symbol: 交易对
            seconds: 时间范围（秒）

        Returns:
            成交列表
        """
        # 优先从Redis读取
        trades = self.get_recent_trades_from_redis(symbol, seconds)
        if trades:
            return trades

        # Redis没有数据，尝试从MySQL读取（兜底）
        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            cutoff = int((datetime.now().timestamp() - seconds) * 1000)

            cursor.execute('''
                SELECT timestamp, price, size, side
                FROM trades_buffer
                WHERE symbol = %s AND timestamp >= %s
                ORDER BY timestamp ASC
            ''', (symbol, cutoff))

            rows = cursor.fetchall()

            return [
                {'timestamp': r['timestamp'], 'price': r['price'], 'size': r['size'], 'side': r['side']}
                for r in rows
            ]
        finally:
            cursor.close()
            conn.close()

    def get_latest_trade_timestamp(self, symbol: str) -> Optional[int]:
        """
        获取最新逐笔成交的时间戳（优先从Redis读取）

        Args:
            symbol: 交易对

        Returns:
            最新成交时间戳（毫秒），如果没有数据则返回None
        """
        # 优先从Redis获取
        timestamp = self.get_latest_trade_timestamp_from_redis(symbol)
        if timestamp:
            return timestamp

        # Redis没有数据，尝试从MySQL获取（兜底）
        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT timestamp
                FROM trades_buffer
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT 1
            ''', (symbol,))

            row = cursor.fetchone()

            return row['timestamp'] if row else None
        finally:
            cursor.close()
            conn.close()

    def cleanup_old_trades(self, hours: int = 1):
        """清理旧的逐笔成交数据"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cutoff = int((datetime.now().timestamp() - hours * 3600) * 1000)

            cursor.execute('DELETE FROM trades_buffer WHERE timestamp < %s', (cutoff,))
            deleted = cursor.rowcount

            conn.commit()

            if deleted > 0:
                logger.info(f"清理 {deleted} 条旧成交数据")
        finally:
            cursor.close()
            conn.close()

    # ========== 新增：订单簿数据管理 ==========

    def save_orderbook_snapshot(self, symbol: str, orderbook: Dict):
        """
        保存订单簿快照

        Args:
            symbol: 交易对
            orderbook: 订单簿数据 {timestamp, bid1_price, bid1_size, ask1_price, ...}
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                REPLACE INTO orderbook_snapshot
                (symbol, timestamp, bid_price_1, bid_size_1, ask_price_1, ask_size_1,
                 spread_pct, bid_depth_5, ask_depth_5, depth_ratio)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                symbol,
                orderbook['timestamp'],
                orderbook.get('bid1_price'),
                orderbook.get('bid1_size'),
                orderbook.get('ask1_price'),
                orderbook.get('ask1_size'),
                orderbook.get('spread_pct'),
                orderbook.get('bid_depth_5'),
                orderbook.get('ask_depth_5'),
                orderbook.get('depth_ratio')
            ))

            conn.commit()
            logger.debug(f"保存订单簿快照: {symbol}")
        except Exception as e:
            conn.rollback()
            logger.error(f"保存订单簿失败: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def get_latest_orderbook(self, symbol: str) -> Optional[Dict]:
        """获取最新订单簿快照"""
        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT timestamp, bid_price_1, bid_size_1, ask_price_1, ask_size_1,
                       spread_pct, bid_depth_5, ask_depth_5, depth_ratio
                FROM orderbook_snapshot
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT 1
            ''', (symbol,))

            row = cursor.fetchone()

            if row:
                return {
                    'timestamp': row['timestamp'],
                    'bid1_price': row['bid_price_1'],
                    'bid1_size': row['bid_size_1'],
                    'ask1_price': row['ask_price_1'],
                    'ask1_size': row['ask_size_1'],
                    'spread_pct': row['spread_pct'],
                    'bid_depth_5': row['bid_depth_5'],
                    'ask_depth_5': row['ask_depth_5'],
                    'depth_ratio': row['depth_ratio']
                }
            return None
        finally:
            cursor.close()
            conn.close()

    def cleanup_old_orderbook(self, hours: int = 24):
        """清理旧的订单簿快照"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cutoff = int((datetime.now().timestamp() - hours * 3600) * 1000)

            cursor.execute('DELETE FROM orderbook_snapshot WHERE timestamp < %s', (cutoff,))
            deleted = cursor.rowcount

            conn.commit()

            if deleted > 0:
                logger.info(f"清理 {deleted} 条旧订单簿快照")
        finally:
            cursor.close()
            conn.close()

    def save_orderbook_raw(self, symbol: str, timestamp: int, bids: List, asks: List):
        """
        保存原始订单簿档位数据（Top50）

        Args:
            symbol: 交易对
            timestamp: 时间戳（毫秒）
            bids: 买盘档位 [[price, size], [price, size], ...]
            asks: 卖盘档位 [[price, size], [price, size], ...]
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 保存Top50档位
            bids_top50 = bids[:50] if len(bids) > 50 else bids
            asks_top50 = asks[:50] if len(asks) > 50 else asks

            cursor.execute('''
                REPLACE INTO orderbook_raw
                (symbol, timestamp, bids, asks)
                VALUES (%s, %s, %s, %s)
            ''', (
                symbol,
                timestamp,
                json.dumps(bids_top50),
                json.dumps(asks_top50)
            ))

            conn.commit()
            logger.debug(f"保存原始订单簿: {symbol}, {len(bids_top50)}买/{len(asks_top50)}卖档位")
        except Exception as e:
            logger.error(f"保存原始订单簿失败: {e}")
        finally:
            cursor.close()
            conn.close()

    def get_latest_orderbook_raw(self, symbol: str) -> Optional[Dict]:
        """
        获取最新的原始订单簿数据

        Args:
            symbol: 交易对

        Returns:
            {'timestamp': int, 'bids': [[price, size], ...], 'asks': [[price, size], ...]}
        """
        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT timestamp, bids, asks
                FROM orderbook_raw
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT 1
            ''', (symbol,))

            row = cursor.fetchone()

            if row:
                return {
                    'timestamp': row['timestamp'],
                    'bids': json.loads(row['bids']),
                    'asks': json.loads(row['asks'])
                }
            return None
        finally:
            cursor.close()
            conn.close()

    def cleanup_old_orderbook_raw(self, hours: int = 24):
        """清理旧的原始订单簿数据"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cutoff = int((datetime.now().timestamp() - hours * 3600) * 1000)

            cursor.execute('DELETE FROM orderbook_raw WHERE timestamp < %s', (cutoff,))
            deleted = cursor.rowcount

            conn.commit()

            if deleted > 0:
                logger.info(f"清理 {deleted} 条旧原始订单簿数据")
        finally:
            cursor.close()
            conn.close()

    # ========== 新增：实时订单簿维护（增量更新）==========

    def save_orderbook_snapshot_full(self, symbol: str, action: str, bids: List, asks: List,
                                     timestamp: int, seqId: int):
        """
        保存订单簿快照（全量数据）

        Args:
            symbol: 交易对
            action: 'snapshot' 表示全量快照
            bids: 买盘数据 [[price, size, _, orders], ...]
            asks: 卖盘数据 [[price, size, _, orders], ...]
            timestamp: 时间戳
            seqId: 序列号
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 清空旧数据
            cursor.execute('DELETE FROM orderbook_live WHERE symbol = %s', (symbol,))

            # 插入买盘数据
            bid_data = [
                (symbol, 'bid', bid[0], bid[1], bid[3] if len(bid) > 3 else '0', timestamp, seqId)
                for bid in bids
            ]

            # 插入卖盘数据
            ask_data = [
                (symbol, 'ask', ask[0], ask[1], ask[3] if len(ask) > 3 else '0', timestamp, seqId)
                for ask in asks
            ]

            # 批量插入
            cursor.executemany('''
                INSERT INTO orderbook_live (symbol, side, price, size, orders, timestamp, seqId)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', bid_data + ask_data)

            conn.commit()
            logger.info(f"✓ 订单簿快照已保存: {symbol}, {len(bids)}买/{len(asks)}卖档位, seqId={seqId}")
        except Exception as e:
            logger.error(f"保存订单簿快照失败: {e}")
        finally:
            cursor.close()
            conn.close()

    def update_orderbook_incremental(self, symbol: str, bids: List, asks: List,
                                     timestamp: int, seqId: int, prevSeqId: int):
        """
        增量更新订单簿

        Args:
            symbol: 交易对
            bids: 买盘增量数据 [[price, size, _, orders], ...]
            asks: 卖盘增量数据 [[price, size, _, orders], ...]
            timestamp: 时间戳
            seqId: 当前序列号
            prevSeqId: 上一个序列号

        更新规则：
        1. 如果 size == '0'，删除该价格档位
        2. 如果 size != '0' 且价格已存在，更新该档位
        3. 如果 size != '0' 且价格不存在，插入新档位
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 处理买盘增量数据
            for bid in bids:
                price = bid[0]
                size = bid[1]
                orders = bid[3] if len(bid) > 3 else '0'

                if size == '0':
                    # 删除档位
                    cursor.execute('''
                        DELETE FROM orderbook_live
                        WHERE symbol = %s AND side = 'bid' AND price = %s
                    ''', (symbol, price))
                else:
                    # 插入或更新档位 (使用 INSERT ... ON DUPLICATE KEY UPDATE)
                    cursor.execute('''
                        INSERT INTO orderbook_live (symbol, side, price, size, orders, timestamp, seqId)
                        VALUES (%s, 'bid', %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            size=VALUES(size),
                            orders=VALUES(orders),
                            timestamp=VALUES(timestamp),
                            seqId=VALUES(seqId)
                    ''', (symbol, price, size, orders, timestamp, seqId))

            # 处理卖盘增量数据
            for ask in asks:
                price = ask[0]
                size = ask[1]
                orders = ask[3] if len(ask) > 3 else '0'

                if size == '0':
                    # 删除档位
                    cursor.execute('''
                        DELETE FROM orderbook_live
                        WHERE symbol = %s AND side = 'ask' AND price = %s
                    ''', (symbol, price))
                else:
                    # 插入或更新档位
                    cursor.execute('''
                        INSERT INTO orderbook_live (symbol, side, price, size, orders, timestamp, seqId)
                        VALUES (%s, 'ask', %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            size=VALUES(size),
                            orders=VALUES(orders),
                            timestamp=VALUES(timestamp),
                            seqId=VALUES(seqId)
                    ''', (symbol, price, size, orders, timestamp, seqId))

            conn.commit()
            logger.debug(f"✓ 订单簿增量更新: {symbol}, {len(bids)}买/{len(asks)}卖, seqId={seqId}")
        except Exception as e:
            logger.error(f"订单簿增量更新失败: {e}")
        finally:
            cursor.close()
            conn.close()

    def get_orderbook_live(self, symbol: str, depth: int = 400) -> Optional[Dict]:
        """
        获取实时订单簿数据

        Args:
            symbol: 交易对
            depth: 深度档位数（默认400档，可指定如5/25/50等）

        Returns:
            {
                'symbol': str,
                'bids': [[price, size, orders], ...],  # 按价格降序
                'asks': [[price, size, orders], ...],  # 按价格升序
                'timestamp': int,
                'seqId': int
            }
        """
        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            # 获取买盘（价格降序）
            cursor.execute('''
                SELECT price, size, orders, timestamp, seqId
                FROM orderbook_live
                WHERE symbol = %s AND side = 'bid'
                ORDER BY CAST(price AS DECIMAL(20,8)) DESC
                LIMIT %s
            ''', (symbol, depth))
            bids_rows = cursor.fetchall()

            # 获取卖盘（价格升序）
            cursor.execute('''
                SELECT price, size, orders, timestamp, seqId
                FROM orderbook_live
                WHERE symbol = %s AND side = 'ask'
                ORDER BY CAST(price AS DECIMAL(20,8)) ASC
                LIMIT %s
            ''', (symbol, depth))
            asks_rows = cursor.fetchall()

            if not bids_rows and not asks_rows:
                return None

            # 提取最新的时间戳和seqId
            timestamp = bids_rows[0]['timestamp'] if bids_rows else (asks_rows[0]['timestamp'] if asks_rows else 0)
            seqId = bids_rows[0]['seqId'] if bids_rows else (asks_rows[0]['seqId'] if asks_rows else -1)

            return {
                'symbol': symbol,
                'bids': [[row['price'], row['size'], row['orders']] for row in bids_rows],
                'asks': [[row['price'], row['size'], row['orders']] for row in asks_rows],
                'timestamp': timestamp,
                'seqId': seqId
            }
        except Exception as e:
            logger.error(f"获取实时订单簿失败: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def calculate_orderbook_checksum(self, symbol: str) -> Optional[int]:
        """
        计算订单簿校验和（CRC32）

        按照OKX规则：
        1. 取前25档bids和asks
        2. 交替拼接：bid[价格:数量]:ask[价格:数量]:bid[价格:数量]:ask[价格:数量]...
        3. 计算CRC32（32位有符号整型）

        Returns:
            校验和（int32）
        """
        try:
            orderbook = self.get_orderbook_live(symbol, depth=25)
            if not orderbook:
                return None

            bids = orderbook['bids']
            asks = orderbook['asks']

            # 构建校验字符串
            checksum_parts = []
            max_len = max(len(bids), len(asks))

            for i in range(max_len):
                if i < len(bids):
                    checksum_parts.append(f"{bids[i][0]}:{bids[i][1]}")
                if i < len(asks):
                    checksum_parts.append(f"{asks[i][0]}:{asks[i][1]}")

            checksum_str = ':'.join(checksum_parts)

            # 计算CRC32
            import zlib
            crc = zlib.crc32(checksum_str.encode('utf-8'))

            # 转换为有符号32位整数
            if crc >= 0x80000000:
                crc = crc - 0x100000000

            return crc
        except Exception as e:
            logger.error(f"计算订单簿校验和失败: {e}")
            return None

    # ========== Redis订单簿管理（实时高性能）==========

    def clear_redis_orderbook(self, symbol: str):
        """
        清空Redis中指定交易对的订单簿数据

        Args:
            symbol: 交易对

        用途：
            - 采集器启动时清空旧数据
            - 避免旧数据与新数据混合
        """
        if not self.redis_client:
            return

        try:
            pipe = self.redis_client.pipeline()
            pipe.delete(f'orderbook:{symbol}:bids')
            pipe.delete(f'orderbook:{symbol}:asks')
            pipe.delete(f'orderbook:{symbol}:meta')
            pipe.execute()
            logger.info(f"✓ 已清空Redis订单簿: {symbol}")
        except Exception as e:
            logger.error(f"清空Redis订单簿失败: {e}")

    def save_orderbook_to_redis(self, symbol: str, action: str, bids: List, asks: List,
                                 timestamp: int, seqId: int):
        """
        保存订单簿到Redis（快照或增量）

        Args:
            symbol: 交易对
            action: 'snapshot' 或 'update'
            bids: 买盘数据 [[price, size, _, orders], ...]
            asks: 卖盘数据 [[price, size, _, orders], ...]
            timestamp: 时间戳
            seqId: 序列号

        存储格式：
            - Value: "price_str:size_str:orders" (保留原始字符串格式用于checksum)
            - Score: price (float，用于排序)
        """
        if not self.redis_client:
            return

        try:
            pipe = self.redis_client.pipeline()

            if action == 'snapshot':
                # 快照：清空旧数据
                pipe.delete(f'orderbook:{symbol}:bids')
                pipe.delete(f'orderbook:{symbol}:asks')

                # 批量写入买盘（value存储原始字符串，score用于排序）
                for bid in bids:
                    price_str = bid[0]  # 保留原始字符串
                    price_float = float(price_str)
                    size = bid[1]
                    orders = bid[3] if len(bid) > 3 else '0'
                    pipe.zadd(f'orderbook:{symbol}:bids', {f'{price_str}:{size}:{orders}': price_float})

                # 批量写入卖盘
                for ask in asks:
                    price_str = ask[0]
                    price_float = float(price_str)
                    size = ask[1]
                    orders = ask[3] if len(ask) > 3 else '0'
                    pipe.zadd(f'orderbook:{symbol}:asks', {f'{price_str}:{size}:{orders}': price_float})

            elif action == 'update':
                # 增量更新：处理买盘
                for bid in bids:
                    price_str = bid[0]
                    price_float = float(price_str)
                    size = bid[1]
                    orders = bid[3] if len(bid) > 3 else '0'

                    if size == '0':
                        # 删除该价格档位
                        pipe.zremrangebyscore(f'orderbook:{symbol}:bids', price_float, price_float)
                    else:
                        # 更新档位（先删除旧值，再添加新值）
                        pipe.zremrangebyscore(f'orderbook:{symbol}:bids', price_float, price_float)
                        pipe.zadd(f'orderbook:{symbol}:bids', {f'{price_str}:{size}:{orders}': price_float})

                # 增量更新：处理卖盘
                for ask in asks:
                    price_str = ask[0]
                    price_float = float(price_str)
                    size = ask[1]
                    orders = ask[3] if len(ask) > 3 else '0'

                    if size == '0':
                        # 删除该价格档位
                        pipe.zremrangebyscore(f'orderbook:{symbol}:asks', price_float, price_float)
                    else:
                        # 更新档位
                        pipe.zremrangebyscore(f'orderbook:{symbol}:asks', price_float, price_float)
                        pipe.zadd(f'orderbook:{symbol}:asks', {f'{price_str}:{size}:{orders}': price_float})

            # 保存元数据
            pipe.hset(f'orderbook:{symbol}:meta', mapping={
                'timestamp': timestamp,
                'seqId': seqId,
                'updated_at': int(datetime.now().timestamp() * 1000)
            })

            # 设置过期时间（5分钟，防止采集器崩溃后垃圾数据）
            pipe.expire(f'orderbook:{symbol}:bids', 300)
            pipe.expire(f'orderbook:{symbol}:asks', 300)
            pipe.expire(f'orderbook:{symbol}:meta', 300)

            # 执行所有命令
            pipe.execute()

            if action == 'snapshot':
                logger.debug(f"✓ Redis订单簿快照: {symbol}, {len(bids)}买/{len(asks)}卖, seqId={seqId}")
            else:
                logger.debug(f"✓ Redis订单簿增量: {symbol}, {len(bids)}买/{len(asks)}卖, seqId={seqId}")

        except Exception as e:
            logger.error(f"保存订单簿到Redis失败: {e}")

    def get_orderbook_from_redis(self, symbol: str, depth: int = 400) -> Optional[Dict]:
        """
        从Redis获取实时订单簿

        Args:
            symbol: 交易对
            depth: 深度档位数（默认400档）

        Returns:
            {
                'symbol': str,
                'bids': [[price, size, orders], ...],  # 按价格降序
                'asks': [[price, size, orders], ...],  # 按价格升序
                'timestamp': int,
                'seqId': int
            }
        """
        if not self.redis_client:
            logger.warning("Redis未连接，无法获取订单簿")
            return None

        try:
            # 获取买盘（价格降序）
            bids_raw = self.redis_client.zrevrange(
                f'orderbook:{symbol}:bids',
                0, depth - 1,
                withscores=True
            )

            # 获取卖盘（价格升序）
            asks_raw = self.redis_client.zrange(
                f'orderbook:{symbol}:asks',
                0, depth - 1,
                withscores=True
            )

            if not bids_raw and not asks_raw:
                return None

            # 解析数据：value格式为"price_str:size:orders"
            bids = []
            for value, score in bids_raw:
                parts = value.split(':')
                price_str = parts[0]  # 原始价格字符串（用于checksum）
                size = parts[1]
                orders = parts[2] if len(parts) > 2 else '0'
                bids.append([price_str, size, orders])

            asks = []
            for value, score in asks_raw:
                parts = value.split(':')
                price_str = parts[0]
                size = parts[1]
                orders = parts[2] if len(parts) > 2 else '0'
                asks.append([price_str, size, orders])

            # 获取元数据
            meta = self.redis_client.hgetall(f'orderbook:{symbol}:meta')
            timestamp = int(meta.get('timestamp', 0)) if meta else 0
            seqId = int(meta.get('seqId', -1)) if meta else -1

            return {
                'symbol': symbol,
                'bids': bids,
                'asks': asks,
                'timestamp': timestamp,
                'seqId': seqId
            }

        except Exception as e:
            logger.error(f"从Redis获取订单簿失败: {e}")
            return None

    def calculate_orderbook_checksum_redis(self, symbol: str) -> Optional[int]:
        """
        计算Redis订单簿校验和（CRC32）

        按照OKX规则：
        1. 取前25档bids和asks
        2. 交替拼接：bid[价格:数量]:ask[价格:数量]:...
        3. 计算CRC32（32位有符号整型）

        Returns:
            校验和（int32）
        """
        try:
            orderbook = self.get_orderbook_from_redis(symbol, depth=25)
            if not orderbook:
                return None

            bids = orderbook['bids']
            asks = orderbook['asks']

            # 构建校验字符串
            checksum_parts = []
            max_len = max(len(bids), len(asks))

            for i in range(max_len):
                if i < len(bids):
                    checksum_parts.append(f"{bids[i][0]}:{bids[i][1]}")
                if i < len(asks):
                    checksum_parts.append(f"{asks[i][0]}:{asks[i][1]}")

            checksum_str = ':'.join(checksum_parts)

            # 计算CRC32
            import zlib
            crc = zlib.crc32(checksum_str.encode('utf-8'))

            # 转换为有符号32位整数
            if crc >= 0x80000000:
                crc = crc - 0x100000000

            return crc
        except Exception as e:
            logger.error(f"计算订单簿校验和失败: {e}")
            return None

    # ========== 新增：市场压力指标管理 ==========

    def save_market_pressure(self, symbol: str, interval_seconds: int, pressure: Dict):
        """
        保存市场压力指标

        Args:
            symbol: 交易对
            interval_seconds: 时间间隔（60/300/900）
            pressure: 压力数据 {buy_volume, sell_volume, ...}
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                REPLACE INTO market_pressure
                (symbol, timestamp, interval_seconds, buy_volume, sell_volume,
                 buy_count, sell_count, large_buy_volume, large_sell_volume,
                 avg_buy_price, avg_sell_price, pressure_ratio)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                symbol,
                pressure['timestamp'],
                interval_seconds,
                pressure['buy_volume'],
                pressure['sell_volume'],
                pressure['buy_count'],
                pressure['sell_count'],
                pressure.get('large_buy_volume', 0),
                pressure.get('large_sell_volume', 0),
                pressure.get('avg_buy_price'),
                pressure.get('avg_sell_price'),
                pressure.get('pressure_ratio')
            ))

            conn.commit()
            logger.debug(f"保存市场压力指标: {symbol} {interval_seconds}s")
        except Exception as e:
            conn.rollback()
            logger.error(f"保存市场压力失败: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def get_market_pressure(self, symbol: str, interval_seconds: int, limit: int = 10) -> List[Dict]:
        """
        获取市场压力指标

        Args:
            symbol: 交易对
            interval_seconds: 时间间隔
            limit: 返回数量

        Returns:
            压力指标列表
        """
        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT timestamp, buy_volume, sell_volume, buy_count, sell_count,
                       large_buy_volume, large_sell_volume, avg_buy_price, avg_sell_price,
                       pressure_ratio
                FROM market_pressure
                WHERE symbol = %s AND interval_seconds = %s
                ORDER BY timestamp DESC
                LIMIT %s
            ''', (symbol, interval_seconds, limit))

            rows = cursor.fetchall()

            return [
                {
                    'timestamp': r['timestamp'],
                    'buy_volume': r['buy_volume'],
                    'sell_volume': r['sell_volume'],
                    'buy_count': r['buy_count'],
                    'sell_count': r['sell_count'],
                    'large_buy_volume': r['large_buy_volume'],
                    'large_sell_volume': r['large_sell_volume'],
                    'avg_buy_price': r['avg_buy_price'],
                    'avg_sell_price': r['avg_sell_price'],
                    'pressure_ratio': r['pressure_ratio']
                }
                for r in rows
            ]
        finally:
            cursor.close()
            conn.close()

    def get_latest_pressure(self, symbol: str, interval_seconds: int) -> Optional[Dict]:
        """获取最新的市场压力指标"""
        pressures = self.get_market_pressure(symbol, interval_seconds, limit=1)
        return pressures[0] if pressures else None

    # ========== 新增：历史仓位管理 ==========

    def save_closed_position(self, position: Dict, close_time: int, realized_pnl: float) -> bool:
        """
        保存已平仓记录到数据库（单条）

        Args:
            position: 持仓数据（包含 instId, posSide, pos, avgPx, markPx, upl, uplRatio等字段）
            close_time: 平仓时间（毫秒时间戳）
            realized_pnl: 已实现盈亏

        Returns:
            是否保存成功

        说明：
            - 使用 REPLACE INTO 避免重复
            - 只保存已平仓记录，当前持仓只在内存中维护
            - 注意：批量保存请使用 save_closed_positions_batch 方法以提高性能
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 提取字段
            inst_id = position.get('instId', '')
            pos_side = position.get('posSide', '')
            pos_size = float(position.get('pos', 0))
            avg_px = float(position.get('avgPx', 0))
            mark_px = float(position.get('markPx', 0)) if position.get('markPx') else float(position.get('last', 0))
            upl = float(position.get('upl', 0))
            upl_ratio = float(position.get('uplRatio', 0))
            leverage = position.get('lever', '')
            margin = float(position.get('margin', 0)) if position.get('margin') else None
            imr = float(position.get('imr', 0)) if position.get('imr') else None
            fee = float(position.get('fee', 0)) if position.get('fee') else 0.0
            open_time = int(position.get('cTime', 0)) if position.get('cTime') else None
            close_total_pos = float(position.get('closeTotalPos', 0)) if position.get('closeTotalPos') else None

            cursor.execute('''
                REPLACE INTO positions_history
                (inst_id, pos_side, pos, avg_px, mark_px, upl, upl_ratio, leverage,
                 margin, imr, fee, open_time, close_time, realized_pnl, close_total_pos, api_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'default')
            ''', (
                inst_id, pos_side, pos_size, avg_px, mark_px, upl, upl_ratio,
                leverage, margin, imr, fee, open_time, close_time, realized_pnl, close_total_pos
            ))

            conn.commit()
            return True

        except Exception as e:
            logger.error(f"保存已平仓记录失败: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            conn.close()

    def save_closed_positions_batch(self, positions_data: List[tuple], api_key: str = None) -> tuple:
        """
        批量保存已平仓记录到数据库（性能优化版本）

        Args:
            positions_data: 持仓数据列表，每个元素是一个元组：
                (inst_id, pos_side, pos_size, avg_px, mark_px, upl, upl_ratio,
                 leverage, margin, imr, fee, open_time, close_time, realized_pnl, close_total_pos)
            api_key: OKX API KEY（用于区分不同账户）

        Returns:
            (成功数量, 总数量)

        说明：
            - 使用单个连接和事务批量插入，比逐条插入快10-100倍
            - 使用 executemany 批量执行
            - 通过 open_time 匹配：存在则更新，不存在则插入
            - 全部成功才提交，失败则全部回滚
        """
        if not positions_data:
            return (0, 0)

        if not api_key:
            api_key = 'default'

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 为每条数据添加api_key
            positions_with_api_key = [
                (*pos_data, api_key) for pos_data in positions_data
            ]

            cursor.executemany('''
                INSERT INTO positions_history
                (inst_id, pos_side, pos, avg_px, mark_px, upl, upl_ratio, leverage,
                 margin, imr, fee, open_time, close_time, realized_pnl, close_total_pos, api_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    pos = VALUES(pos),
                    avg_px = VALUES(avg_px),
                    mark_px = VALUES(mark_px),
                    upl = VALUES(upl),
                    upl_ratio = VALUES(upl_ratio),
                    leverage = VALUES(leverage),
                    margin = VALUES(margin),
                    imr = VALUES(imr),
                    fee = VALUES(fee),
                    close_time = VALUES(close_time),
                    realized_pnl = VALUES(realized_pnl),
                    close_total_pos = VALUES(close_total_pos)
            ''', positions_with_api_key)

            conn.commit()
            return (len(positions_data), len(positions_data))

        except Exception as e:
            logger.error(f"批量保存已平仓记录失败: {e}")
            conn.rollback()
            return (0, len(positions_data))
        finally:
            cursor.close()
            conn.close()

    def get_recent_closed_positions(self, inst_id: str = None, limit: int = 10, api_key: str = None) -> List[Dict]:
        """
        获取最近已平仓的持仓记录

        Args:
            inst_id: 产品ID（可选，不指定则返回所有产品）
            limit: 返回数量
            api_key: OKX API KEY（用于区分不同账户）

        Returns:
            已平仓持仓列表，每个包含完整的持仓信息

        说明:
            - 按 close_time 降序排列
            - 计算持仓时长（秒）
        """
        if not api_key:
            api_key = 'default'

        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            if inst_id:
                cursor.execute('''
                    SELECT inst_id, pos_side, pos, avg_px, mark_px, upl, upl_ratio,
                           leverage, margin, imr, fee, open_time,
                           close_time, realized_pnl, close_total_pos, review_summary
                    FROM positions_history
                    WHERE inst_id = %s AND api_key = %s
                    ORDER BY close_time DESC
                    LIMIT %s
                ''', (inst_id, api_key, limit))
            else:
                cursor.execute('''
                    SELECT inst_id, pos_side, pos, avg_px, mark_px, upl, upl_ratio,
                           leverage, margin, imr, fee, open_time,
                           close_time, realized_pnl, close_total_pos, review_summary
                    FROM positions_history
                    WHERE api_key = %s
                    ORDER BY close_time DESC
                    LIMIT %s
                ''', (api_key, limit))

            rows = cursor.fetchall()

            positions = []
            for row in rows:
                # 计算持仓时长（秒）
                open_time = row['open_time']
                close_time = row['close_time']
                holding_duration = 0
                if open_time and close_time:
                    holding_duration = (close_time - open_time) / 1000

                positions.append({
                    'inst_id': row['inst_id'],
                    'pos_side': row['pos_side'],
                    'pos': row['pos'],
                    'avg_px': row['avg_px'],
                    'mark_px': row['mark_px'],
                    'upl': row['upl'],
                    'upl_ratio': row['upl_ratio'],
                    'leverage': row['leverage'],
                    'margin': row['margin'],
                    'imr': row['imr'],
                    'fee': row['fee'],
                    'open_time': row['open_time'],
                    'close_time': row['close_time'],
                    'realized_pnl': row['realized_pnl'],
                    'close_total_pos': row.get('close_total_pos'),  # 平仓数量
                    'review_summary': row.get('review_summary'),  # 添加复盘总结
                    'holding_duration_seconds': holding_duration
                })

            return positions

        except Exception as e:
            logger.error(f"获取已平仓持仓失败: {e}")
            return []
        finally:
            cursor.close()
            conn.close()

    def get_performance_stats(self, inst_id: str = None, days: int = 30, api_key: str = None) -> Dict:
        """
        计算历史收益统计

        Args:
            inst_id: 产品ID（可选）
            days: 统计天数
            api_key: OKX API KEY（用于区分不同账户）

        Returns:
            统计数据字典，包含:
            - total_trades: 总交易次数
            - winning_trades: 盈利交易次数
            - losing_trades: 亏损交易次数
            - win_rate: 胜率（%）
            - total_pnl: 总盈亏（USDT）
            - total_fee: 总手续费（USDT）
            - net_pnl: 净盈亏（扣除手续费）
            - avg_pnl: 平均盈亏
            - avg_winning_pnl: 平均盈利
            - avg_losing_pnl: 平均亏损
            - max_winning: 最大单笔盈利
            - max_losing: 最大单笔亏损
            - avg_holding_seconds: 平均持仓时长（秒）
        """
        if not api_key:
            api_key = 'default'

        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            cutoff_time = int((datetime.now().timestamp() - days * 86400) * 1000)

            # 基础查询（移除 is_closed 条件，因为所有记录都是已平仓）
            if inst_id:
                cursor.execute('''
                    SELECT realized_pnl, fee, open_time, close_time
                    FROM positions_history
                    WHERE inst_id = %s AND close_time >= %s AND api_key = %s
                    ORDER BY close_time DESC
                ''', (inst_id, cutoff_time, api_key))
            else:
                cursor.execute('''
                    SELECT realized_pnl, fee, open_time, close_time
                    FROM positions_history
                    WHERE close_time >= %s AND api_key = %s
                    ORDER BY close_time DESC
                ''', (cutoff_time, api_key))

            rows = cursor.fetchall()

            if not rows:
                return {
                    'total_trades': 0,
                    'winning_trades': 0,
                    'losing_trades': 0,
                    'win_rate': 0.0,
                    'total_pnl': 0.0,
                    'total_fee': 0.0,
                    'net_pnl': 0.0,
                    'avg_pnl': 0.0,
                    'avg_winning_pnl': 0.0,
                    'avg_losing_pnl': 0.0,
                    'max_winning': 0.0,
                    'max_losing': 0.0,
                    'avg_holding_seconds': 0
                }

            # 统计计算
            total_trades = len(rows)
            winning_trades = 0
            losing_trades = 0
            total_pnl = 0.0
            total_fee = 0.0
            winning_pnls = []
            losing_pnls = []
            holding_durations = []

            for row in rows:
                pnl = row['realized_pnl'] if row['realized_pnl'] is not None else 0.0
                fee = row['fee'] if row['fee'] is not None else 0.0
                open_time = row['open_time']
                close_time = row['close_time']

                total_pnl += pnl
                total_fee += fee

                if pnl > 0:
                    winning_trades += 1
                    winning_pnls.append(pnl)
                elif pnl < 0:
                    losing_trades += 1
                    losing_pnls.append(pnl)

                # 持仓时长
                if open_time and close_time:
                    holding_durations.append((close_time - open_time) / 1000)

            # 计算统计指标
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
            net_pnl = total_pnl - total_fee
            avg_pnl = total_pnl / total_trades if total_trades > 0 else 0.0
            avg_winning_pnl = sum(winning_pnls) / len(winning_pnls) if winning_pnls else 0.0
            avg_losing_pnl = sum(losing_pnls) / len(losing_pnls) if losing_pnls else 0.0
            max_winning = max(winning_pnls) if winning_pnls else 0.0
            max_losing = min(losing_pnls) if losing_pnls else 0.0
            avg_holding_seconds = sum(holding_durations) / len(holding_durations) if holding_durations else 0

            return {
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': losing_trades,
                'win_rate': win_rate,
                'total_pnl': total_pnl,
                'total_fee': total_fee,
                'net_pnl': net_pnl,
                'avg_pnl': avg_pnl,
                'avg_winning_pnl': avg_winning_pnl,
                'avg_losing_pnl': avg_losing_pnl,
                'max_winning': max_winning,
                'max_losing': max_losing,
                'avg_holding_seconds': int(avg_holding_seconds)
            }

        except Exception as e:
            logger.error(f"计算收益统计失败: {e}")
            return {}
        finally:
            cursor.close()
            conn.close()

        # 注意：不关闭连接（复用单例只读连接）

    # ========== 新增：K线最后更新时间管理（使用Redis） ==========

    def save_tick_features(self, symbol: str, features: Dict):
        """
        保存Tick特征聚合数据到Redis

        Args:
            symbol: 交易对
            features: 特征字典，包含:
                - timestamp: 时间戳（毫秒）
                - vwap: 成交量加权平均价
                - volume_imbalance: 买卖量失衡比率
                - price_range: 价格波动范围
                - tick_count: Tick数量
                - large_trade_ratio: 大单比例
                - buy_volume: 买入成交量
                - sell_volume: 卖出成交量
                - total_volume: 总成交量
                - max_price: 最高价
                - min_price: 最低价
                - avg_volume: 平均成交量

        存储格式：
            - Key: tick_features:{symbol}
            - Type: Hash
            - 过期时间: 5分钟（防止垃圾数据）
        """
        if not self.redis_client:
            logger.warning("Redis未连接，无法保存Tick特征")
            return

        try:
            key = f"tick_features:{symbol}"

            # 将特征数据保存为Hash
            self.redis_client.hset(key, mapping={
                'timestamp': features['timestamp'],
                'vwap': features['vwap'],
                'volume_imbalance': features['volume_imbalance'],
                'price_range': features['price_range'],
                'tick_count': features['tick_count'],
                'large_trade_ratio': features['large_trade_ratio'],
                'buy_volume': features['buy_volume'],
                'sell_volume': features['sell_volume'],
                'total_volume': features['total_volume'],
                'max_price': features['max_price'],
                'min_price': features['min_price'],
                'avg_volume': features['avg_volume']
            })

            # 设置过期时间（5分钟）
            self.redis_client.expire(key, 300)

            logger.debug(f"✓ 保存Tick特征到Redis: {symbol}, VWAP={features['vwap']:.2f}")

        except Exception as e:
            logger.error(f"保存Tick特征到Redis失败: {e}")

    def get_tick_features(self, symbol: str) -> Optional[Dict]:
        """
        从Redis获取Tick特征聚合数据

        Args:
            symbol: 交易对

        Returns:
            特征字典，如果不存在则返回None
        """
        if not self.redis_client:
            logger.warning("Redis未连接，无法获取Tick特征")
            return None

        try:
            key = f"tick_features:{symbol}"

            # 获取所有字段
            data = self.redis_client.hgetall(key)

            if not data:
                return None

            # 转换数据类型
            return {
                'timestamp': int(data.get('timestamp', 0)),
                'vwap': float(data.get('vwap', 0)),
                'volume_imbalance': float(data.get('volume_imbalance', 0)),
                'price_range': float(data.get('price_range', 0)),
                'tick_count': int(data.get('tick_count', 0)),
                'large_trade_ratio': float(data.get('large_trade_ratio', 0)),
                'buy_volume': float(data.get('buy_volume', 0)),
                'sell_volume': float(data.get('sell_volume', 0)),
                'total_volume': float(data.get('total_volume', 0)),
                'max_price': float(data.get('max_price', 0)),
                'min_price': float(data.get('min_price', 0)),
                'avg_volume': float(data.get('avg_volume', 0))
            }

        except Exception as e:
            logger.error(f"从Redis获取Tick特征失败: {e}")
            return None

    def update_kline_last_update(self, symbol: str, timeframe: str):
        """
        记录K线数据的最后更新时间（实际接收到数据的时间）

        Args:
            symbol: 交易对
            timeframe: K线周期

        存储在Redis中：kline_updates:{symbol}:{timeframe} -> timestamp_ms
        """
        if not self.redis_client:
            return

        try:
            key = f"kline_updates:{symbol}:{timeframe}"
            now_ms = int(datetime.now().timestamp() * 1000)
            self.redis_client.set(key, now_ms)
            # 设置过期时间（10分钟），防止垃圾数据
            self.redis_client.expire(key, 600)
        except Exception as e:
            logger.error(f"更新K线最后更新时间失败: {e}")

    def get_kline_last_update(self, symbol: str, timeframe: str) -> Optional[int]:
        """
        获取K线数据的最后更新时间（毫秒时间戳）

        Args:
            symbol: 交易对
            timeframe: K线周期

        Returns:
            最后更新时间戳（毫秒），如果没有数据则返回None
        """
        if not self.redis_client:
            logger.warning("Redis未连接，无法获取K线最后更新时间")
            return None

        try:
            key = f"kline_updates:{symbol}:{timeframe}"
            value = self.redis_client.get(key)
            if value:
                return int(value)
            return None
        except Exception as e:
            logger.error(f"获取K线最后更新时间失败: {e}")
            return None

    # ========== 账单流水管理 ==========

    def save_bills_batch(self, bills: List[Dict], api_key: str = None) -> tuple:
        """
        批量保存账单流水（使用 INSERT IGNORE 避免重复）

        Args:
            bills: 账单列表，每个账单包含 billId, instId, ccy, bal, balChg, type, subType, ts 等字段
            api_key: API密钥（用于区分不同账户），如果为None则使用'default'

        Returns:
            (成功数, 失败数) 元组
        """
        if not bills:
            return (0, 0)

        # 如果未提供api_key，使用'default'
        if not api_key:
            api_key = 'default'

        conn = self._get_connection()
        cursor = conn.cursor()

        success_count = 0
        fail_count = 0

        try:
            for bill in bills:
                try:
                    cursor.execute('''
                        INSERT IGNORE INTO bills_archive
                        (bill_id, api_key, inst_id, ccy, bal, bal_chg, type, sub_type, ts)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (
                        bill.get('billId'),
                        api_key,
                        bill.get('instId'),
                        bill.get('ccy'),
                        float(bill.get('bal', 0)),
                        float(bill.get('balChg', 0)),
                        bill.get('type'),
                        bill.get('subType'),
                        int(bill.get('ts', 0))
                    ))
                    if cursor.rowcount > 0:
                        success_count += 1
                except Exception as e:
                    fail_count += 1
                    logger.debug(f"保存单条账单失败 (billId={bill.get('billId')}): {e}")

            conn.commit()
            return (success_count, fail_count)

        except Exception as e:
            logger.error(f"批量保存账单流水失败: {e}")
            conn.rollback()
            return (0, len(bills))
        finally:
            cursor.close()
            conn.close()

    def get_latest_bill_timestamp(self, ccy: str = 'USDT', api_key: str = None) -> Optional[int]:
        """
        获取数据库中最新账单的时间戳（用于增量查询）

        Args:
            ccy: 币种（默认USDT）
            api_key: API密钥（用于区分不同账户），如果为None则使用'default'

        Returns:
            最新账单时间戳（毫秒），如果没有数据则返回None
        """
        # 如果未提供api_key，使用'default'
        if not api_key:
            api_key = 'default'

        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT MAX(ts)
                FROM bills_archive
                WHERE ccy = %s AND api_key = %s
            ''', (ccy, api_key))

            row = cursor.fetchone()
            if row and row['MAX(ts)']:
                return int(row['MAX(ts)'])
            return None

        except Exception as e:
            logger.error(f"获取最新账单时间戳失败: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def get_daily_balance_from_bills(self, ccy: str = 'USDT', start_timestamp: int = None, api_key: str = None) -> List[Dict]:
        """
        从账单流水中聚合每日余额数据

        Args:
            ccy: 币种（默认USDT）
            start_timestamp: 起始时间戳（毫秒），如果为None则查询所有数据
            api_key: API密钥（用于区分不同账户），如果为None则使用'default'

        Returns:
            每日余额列表 [{'date': 'YYYY-MM-DD', 'balance': float, 'timestamp': str, 'ts': int}, ...]
        """
        # 如果未提供api_key，使用'default'
        if not api_key:
            api_key = 'default'

        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            # 查询指定时间范围内的所有账单，按时间正序
            if start_timestamp:
                cursor.execute('''
                    SELECT ts, bal, bal_chg
                    FROM bills_archive
                    WHERE ccy = %s AND api_key = %s AND ts >= %s
                    ORDER BY ts ASC
                ''', (ccy, api_key, start_timestamp))
            else:
                cursor.execute('''
                    SELECT ts, bal, bal_chg
                    FROM bills_archive
                    WHERE ccy = %s AND api_key = %s
                    ORDER BY ts ASC
                ''', (ccy, api_key))

            rows = cursor.fetchall()

            if not rows:
                return []

            # 结果列表（包含期初余额点和每日最后余额点）
            result = []

            # 计算起始余额（第一笔交易前的余额）
            first_ts = rows[0]['ts']
            first_bal = rows[0]['bal']
            first_bal_chg = rows[0]['bal_chg']
            start_balance = first_bal - first_bal_chg  # 交易前余额 = 交易后余额 - 变动金额

            # 记录是否添加了初始余额点
            added_initial_balance = False
            initial_date = None

            # 如果指定了起始时间，添加起始时间的初始余额作为第一个数据点
            if start_timestamp and first_ts > start_timestamp:
                start_date = datetime.fromtimestamp(start_timestamp / 1000).strftime('%Y-%m-%d')
                result.append({
                    'date': start_date,
                    'balance': start_balance,
                    'timestamp': datetime.fromtimestamp(start_timestamp / 1000).isoformat(),
                    'ts': start_timestamp
                })
                added_initial_balance = True
                initial_date = start_date

            # 按日期聚合，保存每日最后一条余额
            daily_balances = {}

            for row in rows:
                ts = row['ts']
                balance = row['bal']

                # 转换为日期
                date = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d')

                # 保存每日最后的余额（取时间戳最大的）
                if date not in daily_balances or ts > daily_balances[date]['ts']:
                    daily_balances[date] = {
                        'date': date,
                        'balance': balance,
                        'timestamp': datetime.fromtimestamp(ts / 1000).isoformat(),
                        'ts': ts
                    }

            # 按日期排序后添加到结果（跳过已添加初始余额的日期）
            sorted_data = sorted(daily_balances.values(), key=lambda x: x['date'])
            if added_initial_balance and initial_date:
                # 跳过与初始余额同一天的数据（因为我们已经有初始余额了）
                sorted_data = [item for item in sorted_data if item['date'] != initial_date]
            result.extend(sorted_data)

            return result

        except Exception as e:
            logger.error(f"从账单流水聚合每日余额失败: {e}")
            return []
        finally:
            cursor.close()
            conn.close()

    def get_hourly_balance_from_bills(self, ccy: str = 'USDT', start_timestamp: int = None, api_key: str = None) -> List[Dict]:
        """
        从账单流水中聚合每小时余额数据

        Args:
            ccy: 币种（默认USDT）
            start_timestamp: 起始时间戳（毫秒），如果为None则查询所有数据
            api_key: API密钥（用于区分不同账户），如果为None则使用'default'

        Returns:
            每小时余额列表 [{'datetime': 'YYYY-MM-DD HH:00', 'balance': float, 'timestamp': str, 'ts': int}, ...]
        """
        # 如果未提供api_key，使用'default'
        if not api_key:
            api_key = 'default'

        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            # 查询指定时间范围内的所有账单，按时间正序
            if start_timestamp:
                cursor.execute('''
                    SELECT ts, bal, bal_chg
                    FROM bills_archive
                    WHERE ccy = %s AND api_key = %s AND ts >= %s
                    ORDER BY ts ASC
                ''', (ccy, api_key, start_timestamp))
            else:
                cursor.execute('''
                    SELECT ts, bal, bal_chg
                    FROM bills_archive
                    WHERE ccy = %s AND api_key = %s
                    ORDER BY ts ASC
                ''', (ccy, api_key))

            rows = cursor.fetchall()

            if not rows:
                return []

            # 结果列表（包含期初余额点和每小时最后余额点）
            result = []

            # 计算起始余额（第一笔交易前的余额）
            first_ts = rows[0]['ts']
            first_bal = rows[0]['bal']
            first_bal_chg = rows[0]['bal_chg']
            start_balance = first_bal - first_bal_chg  # 交易前余额 = 交易后余额 - 变动金额

            # 记录是否添加了初始余额点
            added_initial_balance = False
            initial_hour = None

            # 如果指定了起始时间，添加起始时间的初始余额作为第一个数据点
            if start_timestamp and first_ts > start_timestamp:
                hour_key_start = datetime.fromtimestamp(start_timestamp / 1000).strftime('%Y-%m-%d %H:00')
                result.append({
                    'datetime': hour_key_start,
                    'balance': start_balance,
                    'timestamp': datetime.fromtimestamp(start_timestamp / 1000).isoformat(),
                    'ts': start_timestamp
                })
                added_initial_balance = True
                initial_hour = hour_key_start

            # 按小时聚合，保存每小时最后一条余额
            hourly_balances = {}

            for row in rows:
                ts = row['ts']
                balance = row['bal']

                # 转换为小时（格式：YYYY-MM-DD HH:00）
                dt = datetime.fromtimestamp(ts / 1000)
                hour_key = dt.strftime('%Y-%m-%d %H:00')

                # 保存每小时最后的余额（取时间戳最大的）
                if hour_key not in hourly_balances or ts > hourly_balances[hour_key]['ts']:
                    hourly_balances[hour_key] = {
                        'datetime': hour_key,
                        'balance': balance,
                        'timestamp': datetime.fromtimestamp(ts / 1000).isoformat(),
                        'ts': ts
                    }

            # 按时间排序后添加到结果（跳过已添加初始余额的小时）
            sorted_data = sorted(hourly_balances.values(), key=lambda x: x['datetime'])
            if added_initial_balance and initial_hour:
                # 跳过与初始余额同一小时的数据（因为我们已经有初始余额了）
                sorted_data = [item for item in sorted_data if item['datetime'] != initial_hour]
            result.extend(sorted_data)

            return result

        except Exception as e:
            logger.error(f"从账单流水聚合每小时余额失败: {e}")
            return []
        finally:
            cursor.close()
            conn.close()

    def close(self):
        """
        关闭数据管理器，释放资源

        - 关闭Redis连接
        - 关闭MySQL连接池
        """
        try:
            # 关闭Redis连接
            if self.redis_client:
                self.redis_client.close()
                logger.debug("✓ Redis连接已关闭")

            # 关闭MySQL连接池
            if hasattr(self, 'mysql_pool') and self.mysql_pool:
                self.mysql_pool.close()
                logger.debug("✓ MySQL连接池已关闭")
        except Exception as e:
            logger.error(f"关闭数据管理器时出错: {e}")

    def insert_ai_decision(self, decision_data: dict, api_key: str = None) -> int:
        """
        插入AI决策记录到数据库（新版：使用adjust_data）

        Args:
            decision_data: 决策数据字典，必须包含:
                - timestamp: 时间戳
                - pos_id: 仓位ID
                - inst_id: 产品ID
                - pos_side: 仓位方向
                - action: 操作类型
                - size: 数量
                - confidence: 置信度
                - adjust_data: 止盈止损数据（JSON格式）
                - holding_time: 持仓时间
                - reason: 决策理由
            api_key: API密钥（用于多账户隔离）

        Returns:
            插入的记录ID
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 提取 adjust_data 并序列化
            adjust_data = decision_data.get('adjust_data')
            adjust_data_json = None
            tp_layer_count = 0
            sl_layer_count = 0

            if adjust_data:
                # 序列化为 JSON 字符串
                adjust_data_json = json.dumps(adjust_data, ensure_ascii=False)

                # 统计层数
                tp_layer_count = len(adjust_data.get('take_profit', []))
                sl_layer_count = len(adjust_data.get('stop_loss', []))

            sql = """
            INSERT INTO ai_decision_log
            (timestamp, pos_id, inst_id, pos_side, action, size, confidence,
             adjust_data, tp_layer_count, sl_layer_count,
             holding_time, reason, api_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql, (
                decision_data.get('timestamp'),
                decision_data.get('pos_id'),
                decision_data.get('inst_id'),
                decision_data.get('pos_side'),
                decision_data.get('action'),
                decision_data.get('size'),
                decision_data.get('confidence'),
                adjust_data_json,
                tp_layer_count,
                sl_layer_count,
                decision_data.get('holding_time'),
                decision_data.get('reason'),
                api_key
            ))
            conn.commit()
            record_id = cursor.lastrowid
            logger.debug(
                f"✓ AI决策已插入: ID={record_id}, "
                f"止盈{tp_layer_count}层, 止损{sl_layer_count}层"
            )
            return record_id
        except Exception as e:
            conn.rollback()
            logger.error(f"插入AI决策失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return 0
        finally:
            cursor.close()
            conn.close()

    def get_decisions_by_pos_id(self, pos_id: str, api_key: str = None) -> list:
        """
        根据pos_id查询该仓位的所有AI决策历史（新版：反序列化adjust_data）

        Args:
            pos_id: 仓位ID
            api_key: API密钥

        Returns:
            决策历史列表，adjust_data已从JSON反序列化为dict
        """
        if not pos_id:
            return []

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            sql = """
            SELECT
                id, timestamp, pos_id, inst_id, pos_side, action, size, confidence,
                adjust_data, tp_layer_count, sl_layer_count,
                holding_time, reason, api_key, created_at
            FROM ai_decision_log
            WHERE pos_id = %s AND api_key = %s
            ORDER BY timestamp ASC
            """
            cursor.execute(sql, (pos_id, api_key))
            rows = cursor.fetchall()

            if not rows:
                return []

            # 反序列化 adjust_data
            decisions = []
            for row in rows:
                decision = {
                    'id': row['id'],
                    'timestamp': row['timestamp'],
                    'pos_id': row['pos_id'],
                    'inst_id': row['inst_id'],
                    'pos_side': row['pos_side'],
                    'action': row['action'],
                    'size': row['size'],
                    'confidence': row['confidence'],
                    'adjust_data': json.loads(row['adjust_data']) if row['adjust_data'] else None,
                    'tp_layer_count': row['tp_layer_count'],
                    'sl_layer_count': row['sl_layer_count'],
                    'holding_time': row['holding_time'],
                    'reason': row['reason'],
                    'api_key': row['api_key'],
                    'created_at': row['created_at']
                }
                decisions.append(decision)

            return decisions

        except Exception as e:
            logger.error(f"查询决策历史失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return []
        finally:
            cursor.close()
            conn.close()

    def update_position_review_summary(self, inst_id: str, pos_side: str, open_time: int,
                                       review_summary: str, api_key: str = None) -> bool:
        """
        更新仓位的复盘总结

        Args:
            inst_id: 产品ID
            pos_side: 仓位方向
            open_time: 开仓时间（用于匹配记录）
            review_summary: 复盘总结
            api_key: API密钥

        Returns:
            是否更新成功
        """
        if not api_key:
            api_key = 'default'

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            sql = """
            UPDATE positions_history
            SET review_summary = %s
            WHERE inst_id = %s AND pos_side = %s AND open_time = %s AND api_key = %s
            """
            cursor.execute(sql, (review_summary, inst_id, pos_side, open_time, api_key))
            conn.commit()

            if cursor.rowcount > 0:
                logger.debug(f"✓ 复盘总结已更新: {inst_id} {pos_side} open_time={open_time}")
                return True
            else:
                logger.warning(f"⚠️ 未找到匹配的仓位记录: {inst_id} {pos_side} open_time={open_time}")
                return False

        except Exception as e:
            logger.error(f"更新复盘总结失败: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            conn.close()

    def get_position_review_summary(self, inst_id: str, pos_side: str, open_time: int,
                                    api_key: str = None) -> Optional[str]:
        """
        获取仓位的复盘总结（检查是否已存在）

        Args:
            inst_id: 产品ID
            pos_side: 仓位方向
            open_time: 开仓时间
            api_key: API密钥

        Returns:
            复盘总结（如果存在），否则返回None
        """
        if not api_key:
            api_key = 'default'

        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            sql = """
            SELECT review_summary
            FROM positions_history
            WHERE inst_id = %s AND pos_side = %s AND open_time = %s AND api_key = %s
            """
            cursor.execute(sql, (inst_id, pos_side, open_time, api_key))
            row = cursor.fetchone()

            if row and row['review_summary']:
                return row['review_summary']
            return None

        except Exception as e:
            logger.error(f"获取复盘总结失败: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def save_conversation(
        self,
        session_id: str,
        inst_id: str,
        prompt: str,
        response: Optional[str],
        analysis: Dict,
        is_executed: bool = False,
        api_key: str = None
    ) -> int:
        """
        保存AI对话记录到MySQL

        Args:
            session_id: 会话ID
            inst_id: 交易对
            prompt: AI提示词
            response: AI响应
            analysis: 分析结果
            is_executed: 是否已执行
            api_key: API密钥

        Returns:
            记录ID，失败返回-1
        """
        if not api_key:
            api_key = 'default'

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO ai_conversations
                (session_id, inst_id, prompt, response, `signal`, confidence, is_executed, api_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                session_id,
                inst_id,
                prompt,
                response,
                analysis.get('signal', 'UNKNOWN'),
                analysis.get('confidence', 0),
                is_executed,
                api_key
            ))

            record_id = cursor.lastrowid
            conn.commit()

            logger.debug(f"✓ AI对话记录已保存: ID={record_id}")
            return record_id

        except Exception as e:
            logger.error(f"保存AI对话失败: {e}")
            conn.rollback()
            return -1
        finally:
            cursor.close()
            conn.close()

    def update_conversation_executed(self, conversation_id: int, is_executed: bool, api_key: str = None) -> bool:
        """
        更新会话的执行状态

        Args:
            conversation_id: 会话记录ID
            is_executed: 是否已执行（True=成功，False=失败）
            api_key: API密钥

        Returns:
            是否更新成功
        """
        if not api_key:
            api_key = 'default'

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                UPDATE ai_conversations
                SET is_executed = %s
                WHERE id = %s AND api_key = %s
            ''', (is_executed, conversation_id, api_key))

            conn.commit()

            if cursor.rowcount > 0:
                logger.debug(f"✓ 更新会话执行状态: ID={conversation_id}, is_executed={is_executed}")
                return True
            else:
                logger.warning(f"⚠️ 未找到会话记录: ID={conversation_id}")
                return False

        except Exception as e:
            logger.error(f"更新会话执行状态失败: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            conn.close()

    def get_conversations(self, limit: int = 50, session_id: Optional[str] = None, api_key: str = None) -> List[Dict]:
        """
        获取AI对话记录列表（不含大文本字段）

        Args:
            limit: 返回数量
            session_id: 会话ID（可选）
            api_key: API密钥

        Returns:
            对话记录列表
        """
        if not api_key:
            api_key = 'default'

        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            if session_id:
                cursor.execute('''
                    SELECT id, session_id, inst_id, `signal`, confidence,
                           is_executed, created_at
                    FROM ai_conversations
                    WHERE session_id = %s AND api_key = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                ''', (session_id, api_key, limit))
            else:
                cursor.execute('''
                    SELECT id, session_id, inst_id, `signal`, confidence,
                           is_executed, created_at
                    FROM ai_conversations
                    WHERE api_key = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                ''', (api_key, limit))

            rows = cursor.fetchall()

            conversations = [
                {
                    "id": row['id'],
                    "session_id": row['session_id'],
                    "inst_id": row['inst_id'],
                    "signal": row['signal'],
                    "confidence": row['confidence'],
                    "is_executed": bool(row['is_executed']),
                    "created_at": row['created_at'].strftime('%Y-%m-%d %H:%M:%S') if row['created_at'] else None,
                    "prompt": None,
                    "response": None
                }
                for row in rows
            ]

            return conversations

        except Exception as e:
            logger.error(f"获取对话记录失败: {e}")
            return []
        finally:
            cursor.close()
            conn.close()

    def get_conversation_detail(self, conv_id: int, api_key: str = None) -> Optional[Dict]:
        """
        获取单条对话的完整详情（包含prompt和response）

        Args:
            conv_id: 对话ID
            api_key: API密钥

        Returns:
            对话详情字典，如果不存在则返回None
        """
        if not api_key:
            api_key = 'default'

        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT id, session_id, inst_id, prompt, response, `signal`, confidence,
                       is_executed, created_at
                FROM ai_conversations
                WHERE id = %s AND api_key = %s
            ''', (conv_id, api_key))

            row = cursor.fetchone()
            if not row:
                return None

            conversation = {
                "id": row['id'],
                "session_id": row['session_id'],
                "inst_id": row['inst_id'],
                "prompt": row['prompt'],
                "response": row['response'],
                "signal": row['signal'],
                "confidence": row['confidence'],
                "is_executed": bool(row['is_executed']),
                "created_at": row['created_at'].strftime('%Y-%m-%d %H:%M:%S') if row['created_at'] else None
            }

            return conversation

        except Exception as e:
            logger.error(f"获取对话详情失败: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def get_ai_conversation_stats(self, api_key: str = None) -> Dict:
        """
        获取AI对话统计数据

        Args:
            api_key: API密钥

        Returns:
            统计数据字典，包含今日决策次数
        """
        if not api_key:
            api_key = 'default'

        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            # 获取今日AI决策次数
            cursor.execute('''
                SELECT COUNT(*) as count
                FROM ai_conversations
                WHERE DATE(created_at) = CURDATE() AND api_key = %s
            ''', (api_key,))

            result = cursor.fetchone()
            today_decisions = result['count'] if result else 0

            return {
                'today_decisions': today_decisions
            }

        except Exception as e:
            logger.error(f"获取对话统计失败: {e}")
            return {'today_decisions': 0}
        finally:
            cursor.close()
            conn.close()

    def get_conversation_sessions(self, limit: int = 20, api_key: str = None) -> List[Dict]:
        """
        获取对话会话列表

        Args:
            limit: 返回数量
            api_key: API密钥

        Returns:
            会话列表
        """
        if not api_key:
            api_key = 'default'

        conn = self._get_connection(readonly=True)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT session_id,
                       MIN(created_at) as first_seen,
                       MAX(created_at) as last_seen,
                       COUNT(*) as decision_count
                FROM ai_conversations
                WHERE api_key = %s
                GROUP BY session_id
                ORDER BY last_seen DESC
                LIMIT %s
            ''', (api_key, limit))

            rows = cursor.fetchall()

            sessions = [
                {
                    "session_id": row['session_id'],
                    "first_seen": row['first_seen'].strftime('%Y-%m-%d %H:%M:%S') if row['first_seen'] else None,
                    "last_seen": row['last_seen'].strftime('%Y-%m-%d %H:%M:%S') if row['last_seen'] else None,
                    "decision_count": row['decision_count']
                }
                for row in rows
            ]

            return sessions

        except Exception as e:
            logger.error(f"获取会话列表失败: {e}")
            return []
        finally:
            cursor.close()
            conn.close()

    def __del__(self):
        """析构函数，确保资源被释放"""
        self.close()


# 使用示例
if __name__ == '__main__':
    # 创建数据管理器
    dm = DataManager()

    # 模拟更新行情
    dm.update_ticker('BTC-USDT', {'last': '50000', 'vol24h': '1000'})

    # 保存K线
    dm.save_kline('BTC-USDT', '1H', [
        1700000000000, 50000, 51000, 49000, 50500, 100
    ])

    # 保存信号
    dm.save_signal(
        'BTC-USDT',
        'BUY',
        '价格突破阻力位',
        'AI分析：当前价格突破50000关键阻力位，建议建仓'
    )

    # 获取市场上下文
    context = dm.get_market_context('BTC-USDT')
    print(json.dumps(context, indent=2, ensure_ascii=False))
