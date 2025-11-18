"""
BTC-USDT-SWAP 增强版短线交易（方案A：原始数据+技术指标混合）
提供原始K线、订单簿数据给AI，充分发挥AI的分析能力
同时保留技术指标作为辅助参考
"""
import sys
import os,re

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import argparse
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
import json
from datetime import datetime, timedelta
from loguru import logger

from src.config.settings import config
from src.core.rest_client import OKXRestClient
from src.api.trade import TradeAPI
from src.api.position import PositionAPI
from src.api.market import MarketAPI
from src.api.public import PublicAPI
from src.api.account import AccountAPI
from src.ai.data_manager import DataManager
from src.ai.feature_engineer import FeatureEngineer
from src.execution.smart_executor import SmartOrderExecutor
from src.utils.fee_calculator import FeeCalculator
class BTCEnhancedBotRaw:
    """BTC-USDT-SWAP 增强版交易机器人（方案A：原始数据）"""

    def __init__(self, auto_execute: bool = False):
        """
        初始化增强版交易机器人（原始数据版本）

        Args:
            auto_execute: 是否自动执行交易
            use_realtime: 是否启用实时数据采集
        """
        self.inst_id = config.INST_ID  # 从配置文件读取交易标的
        self.auto_execute = auto_execute
        self.executor_=ThreadPoolExecutor()

        # 从配置文件读取交易参数
        self.leverage = config.DEFAULT_LEVERAGE  # 从.env读取杠杆

        # 数据新鲜度阈值（秒）
        self.data_freshness_threshold = 300  # 5分钟，数据超过此时间视为滞后

        # 生成会话ID
        import uuid
        self.session_id = str(uuid.uuid4())[:8]

        # 内存缓存：历史AI决策（只保留最近10个AI response + 时间戳）
        # 决策历史文件路径
        self.ai_decision_history_file = os.path.join(project_root, 'data', 'ai_decision_history.json')
        self.ai_decision_history = []  # [{"content": ai_response_str, "timestamp": "2025-10-27 16:30:45"}, ...]
        self.current_conversation_id = None  # 当前会话的数据库ID

        # 从文件加载历史决策
        self._load_decision_history()
        logger.info(f"✓ 已加载 {len(self.ai_decision_history)} 条历史AI决策")

        # 账户余额缓存（提高交易执行效率）
        self.cached_balance = 0.0  # 缓存的USDT余额
        self.balance_last_update = None  # 余额最后更新时间
        self.balance_update_thread = None  # 余额更新线程
        self.stop_balance_thread = False  # 停止余额更新线程的标志

        # 仓位缓存（提高交易执行效率）
        self.cached_positions = []  # 缓存的持仓列表
        self.positions_last_update = None  # 最后更新时间
        self.position_update_thread = None  # 后台更新线程
        self.stop_position_thread = False  # 停止标志

        # 止盈止损订单缓存
        self.cached_stop_orders = {}  # {pos_side: {'stop_loss': {...}, 'take_profit': {...}}}
        self.stop_orders_last_update = None
        self.stop_order_update_thread = None
        self.stop_stop_order_thread = False

        # 合约信息缓存
        self.instrument_info = None

        # 历史仓位缓存（供AI分析使用）
        self.cached_historical_positions = []  # 最近10笔已平仓位
        self.cached_performance_stats = {}  # 30天收益统计
        self.history_last_update = None
        self.position_history_thread = None
        self.stop_history_thread = False

        # 资金费率缓存（供AI分析使用）
        self.cached_funding_rate = None  # 最新资金费率数据
        self.funding_rate_last_update = None
        self.funding_rate_update_thread = None
        self.stop_funding_rate_thread = False

        # 市场数据缓存（持仓量、交易量、主动买卖）
        self.cached_taker_volume = None  # 主动买卖数据
        self.cached_open_interest = None  # 持仓量和交易量数据
        self.market_data_last_update = None
        self.market_data_update_thread = None
        self.stop_market_data_thread = False

        # 机器人启动时间（用于AI理解长期运行任务）
        if config.BOT_START_TIME:
            try:
                # 尝试解析.env中配置的启动时间
                self.bot_start_time = datetime.strptime(config.BOT_START_TIME, '%Y-%m-%d %H:%M:%S')
                logger.info(f"✓ 从配置加载机器人启动时间: {self.bot_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            except ValueError as e:
                # 解析失败，使用当前时间
                logger.warning(f"⚠️ 启动时间格式错误: {config.BOT_START_TIME}，使用当前时间")
                self.bot_start_time = datetime.now()
        else:
            # 未配置启动时间，使用当前时间
            self.bot_start_time = datetime.now()
            logger.info(f"✓ 机器人启动时间（未配置，使用当前）: {self.bot_start_time.strftime('%Y-%m-%d %H:%M:%S')}")

        # 初始化API
        if not config.is_configured():
            raise ValueError("请先在.env文件中配置API密钥")

        self.client = OKXRestClient(
            api_key=config.API_KEY,
            secret_key=config.SECRET_KEY,
            passphrase=config.PASSPHRASE,
            is_demo=config.IS_DEMO,
            use_proxy=config.PROXY_ENABLED,
            proxy=f'http://{config.PROXY_USERNAME}:{config.PROXY_PASSWORD}@{config.PROXY_HOST}:{config.PROXY_PORT}' if config.PROXY_USERNAME else f'http://{config.PROXY_HOST}:{config.PROXY_PORT}' 
        )

        self.trade_api = TradeAPI(self.client)
        self.position_api = PositionAPI(self.client)
        self.market_api = MarketAPI(self.client)
        self.account_api = AccountAPI(self.client)
        self.public_api= PublicAPI(self.client)

        # 初始化数据管理器
        self.data_manager = DataManager()

        # 初始化特征工程师
        self.feature_engineer = FeatureEngineer(self.data_manager)

        # 初始化手续费计算器
        self.fee_calculator = FeeCalculator()

        # 初始化智能订单执行器（传入data_manager用于Redis访问）
        self.executor = SmartOrderExecutor(
            self.trade_api,
            self.position_api,
            self.market_api,
            self.fee_calculator,
            self.public_api,
            self.data_manager
        )

        # 设置杠杆（只在启动时设置一次）
        logger.info(f"⚙️ 设置杠杆 {self.leverage}x...")
        try:
            # 为多空双向设置杠杆
            for pos_side in ['long', 'short']:
                lever_result = self.position_api.set_leverage(
                    inst_id=self.inst_id,
                    lever=str(self.leverage),
                    mgn_mode='cross',
                    pos_side=pos_side
                )
                if lever_result['code'] == '0':
                    logger.success(f"✓ 杠杆设置成功: {pos_side} {self.leverage}x")
                else:
                    logger.warning(f"⚠️ 杠杆设置失败（可能已设置）: {pos_side} - {lever_result.get('msg')}")
        except Exception as e:
            logger.warning(f"⚠️ 杠杆设置异常: {e}")

        # 初始化实时数据采集器（可选）
        self.realtime_collector = None
        self.collector_task = None


        # 豆包AI（如果配置）
        self.doubao_client = None
        self.deepseek_client = None
        self.qwen_client = None
        self.ai_client = None  # 统一AI客户端接口

        # 根据配置选择AI提供商
        if config.AI_PROVIDER == 'doubao' and config.DOUBAO_API_KEY:
            try:
                from src.ai.doubao_client import DoubaoClient
                self.doubao_client = DoubaoClient(
                    api_key=config.DOUBAO_API_KEY,
                    endpoint_id=config.DOUBAO_ENDPOINT_ID,
                    model=config.DOUBAO_MODEL,
                    default_timeout=config.DOUBAO_TIMEOUT
                )
                self.ai_client = self.doubao_client
                logger.info(f"✓ 豆包AI已启用 (模型: {config.DOUBAO_MODEL}, 超时: {config.DOUBAO_TIMEOUT}秒)")
            except Exception as e:
                logger.warning(f"豆包AI初始化失败: {e}")

        elif config.AI_PROVIDER == 'deepseek' and config.DEEPSEEK_API_KEY:
            try:
                from src.ai.deepseek_client import DeepSeekClient
                self.deepseek_client = DeepSeekClient(
                    api_key=config.DEEPSEEK_API_KEY,
                    model=config.DEEPSEEK_MODEL,
                    default_timeout=config.DEEPSEEK_TIMEOUT
                )
                self.ai_client = self.deepseek_client
                logger.info(f"✓ DeepSeek AI已启用 (模型: {config.DEEPSEEK_MODEL}, 超时: {config.DEEPSEEK_TIMEOUT}秒)")
            except Exception as e:
                logger.warning(f"DeepSeek AI初始化失败: {e}")

        elif config.AI_PROVIDER == 'qwen' and config.QWEN_API_KEY:
            try:
                from src.ai.qwen_client import QwenClient
                self.qwen_client = QwenClient(
                    api_key=config.QWEN_API_KEY,
                    model=config.QWEN_MODEL,
                    default_timeout=config.QWEN_TIMEOUT
                )
                self.ai_client = self.qwen_client
                logger.info(f"✓ 通义千问AI已启用 (模型: {config.QWEN_MODEL}, 超时: {config.QWEN_TIMEOUT}秒)")
            except Exception as e:
                logger.warning(f"通义千问AI初始化失败: {e}")

        else:
            logger.warning(f"⚠️ 未配置AI或配置无效 (AI_PROVIDER={config.AI_PROVIDER})")

        # 获取并缓存合约信息
        try:
            from src.utils.instrument_cache import InstrumentCache
            instrument_cache = InstrumentCache()
            cache_result = instrument_cache.get_instrument_info(
                inst_id=self.inst_id,
                inst_type='SWAP',
                market_api=self.public_api
            )
            if cache_result['success']:
                self.instrument_info = cache_result['data']
                logger.info(f"✓ 合约信息已缓存: ctVal={self.instrument_info.get('ctVal')}, minSz={self.instrument_info.get('minSz')}")
            else:
                logger.warning(f"⚠️ 获取合约信息失败: {cache_result.get('error')}")
        except Exception as e:
            logger.warning(f"⚠️ 合约信息缓存失败: {e}")

        # 显示配置信息
        logger.info(f"✓ 增强版交易机器人初始化完成（方案A：原始数据）")
        logger.info(f"  自动执行: {auto_execute}")
        logger.info(f"  杠杆倍数: {self.leverage}x")
        logger.info(f"  数据新鲜度阈值: {self.data_freshness_threshold}秒")

        # 飞书通知配置
        self.feishu_enabled = config.FEISHU_ENABLED
        self.feishu_webhook_url = config.FEISHU_WEBHOOK_URL if config.FEISHU_ENABLED else None
        if self.feishu_enabled and self.feishu_webhook_url:
            logger.info(f"  飞书通知: 已启用")
        else:
            logger.info(f"  飞书通知: 未启用")
    def send_feishu_content(self,posId):
        """
        发送飞书通知（仓位信息）- 后台线程异步执行

        Args:
            position: 仓位信息
        """
        # 检查飞书通知是否启用
        if not self.feishu_enabled or not self.feishu_webhook_url:
            return
        try:
            position=None
            while not position:
                for posData in self.cached_historical_positions:
                    if str(posData['open_time']) == str(posId):
                        position=posData
                        break
            signal_text=f'{"多仓" if position["pos_side"] =="long" else "空仓"} 已平仓【{int(position["leverage"])}x】'
            content_parts = [f"【{signal_text}】" ]
            content_parts.append(f"▸ 交易对: {position['inst_id']}")
            if position.get('avg_px'):
                content_parts.append(f"▸ 开仓价格: { position.get('avg_px'):.2f} USDT")
            if position.get('mark_px'):
                content_parts.append(f"▸ 平仓价格: { position.get('mark_px'):.2f} USDT")
            content_parts.append(f"▸ 开仓数量: {position['pos']} 张")
            upl_ratio=round(position['upl_ratio']*100,2)
            open_time=datetime.fromtimestamp(position['open_time'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
            close_time=datetime.fromtimestamp(position['close_time'] / 1000).strftime('%Y-%m-%d %H:%M:%S')

            content_parts.append(f"  实现收益：${position['upl']}({upl_ratio}%)")
            content_parts.append(f"  开仓时间：{open_time}")
            content_parts.append(f"  平仓时间：{close_time}")
            
            content = "\n".join(content_parts)

            signal=f'{"多仓" if position["posSide"] =="long" else "空仓"} 已平仓',
            payload={
    "msg_type": "post",
    "content": {
        "post": {
            "zh_cn": {
                "title": f"AI平仓通知 【{signal}】 （{self.inst_id}）",
                "content": [
                    [{
                        "tag": "text",
                        "text":content
                    }]
                ]
            }
        }
    }
}

            # 发送POST请求（设置30秒超时）
            response = requests.post(
                self.feishu_webhook_url,
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                logger.info(f"✅ 飞书通知已发送: {signal_text}")
            else:
                logger.warning(f"⚠️ 飞书通知发送失败: HTTP {response.status_code}")
        except requests.exceptions.Timeout:
            logger.warning("⚠️ 飞书通知发送超时（30秒）")
        except Exception as e:
            logger.error(f"❌ 飞书通知发送异常: {e}")
    def send_feishu_notification(self, current_price: float = None):
        """
        发送飞书通知（开仓/调整止盈止损决策）- 后台线程异步执行

        Args:
            current_price: 当前价格
        """
        # 检查飞书通知是否启用
        if not self.feishu_enabled or not self.feishu_webhook_url:
            return

        signal = self.analysis.get('signal')

        # 只有开仓和调整止盈止损时才发送通知
        if signal not in ['OPEN_LONG', 'OPEN_SHORT', 'ADJUST_STOP']:
            return

        # 在后台线程中发送，避免阻塞主线程
        def send_task():
            try:
                confidence = self.analysis.get('confidence', 0)
                reason = self.analysis.get('reason', '无理由说明')
                adjust_data = self.analysis.get('adjust_data', {})

                # 根据信号类型格式化内容
                if signal == 'OPEN_LONG':
                    signal_text = " 开多仓"
                    size = self.analysis.get('size', 0)

                    # 从adjust_data提取止盈止损信息
                    tp_layers = adjust_data.get('take_profit', [])
                    sl_layers = adjust_data.get('stop_loss', [])

                    content_parts = [f"【{signal_text}】"]
                    content_parts.append(f"▸ 交易对: {self.inst_id}")
                    if current_price:
                        content_parts.append(f"▸ 开仓价格: {current_price:.2f} USDT")
                    content_parts.append(f"▸ 开仓数量: {size} 张")

                    # 显示止盈层级
                    if tp_layers:
                        content_parts.append(f"▸ 止盈（{len(tp_layers)}层）:")
                        for i, layer in enumerate(tp_layers, 1):
                            content_parts.append(f"  #{i}: {layer['size']}张 @ {layer['price']:.2f}")

                    # 显示止损层级
                    if sl_layers:
                        content_parts.append(f"▸ 止损（{len(sl_layers)}层）:")
                        for i, layer in enumerate(sl_layers, 1):
                            content_parts.append(f"  #{i}: {layer['size']}张 @ {layer['price']:.2f}")

                    content_parts.append(f"▸ 置信度: {confidence}%")
                    content_parts.append(f"▸ 杠杆倍数: {self.leverage}x")
                    content_parts.append(f"▸ 决策理由: {reason[:100]}...")

                    content = "\n".join(content_parts)

                elif signal == 'OPEN_SHORT':
                    signal_text = " 开空仓"
                    size = self.analysis.get('size', 0)

                    # 从adjust_data提取止盈止损信息
                    tp_layers = adjust_data.get('take_profit', [])
                    sl_layers = adjust_data.get('stop_loss', [])

                    content_parts = [f"【{signal_text}】"]
                    content_parts.append(f"▸ 交易对: {self.inst_id}")
                    if current_price:
                        content_parts.append(f"▸ 开仓价格: {current_price:.2f} USDT")
                    content_parts.append(f"▸ 开仓数量: {size} 张")

                    # 显示止盈层级
                    if tp_layers:
                        content_parts.append(f"▸ 止盈（{len(tp_layers)}层）:")
                        for i, layer in enumerate(tp_layers, 1):
                            content_parts.append(f"  #{i}: {layer['size']}张 @ {layer['price']:.2f}")

                    # 显示止损层级
                    if sl_layers:
                        content_parts.append(f"▸ 止损（{len(sl_layers)}层）:")
                        for i, layer in enumerate(sl_layers, 1):
                            content_parts.append(f"  #{i}: {layer['size']}张 @ {layer['price']:.2f}")

                    content_parts.append(f"▸ 置信度: {confidence}%")
                    content_parts.append(f"▸ 杠杆倍数: {self.leverage}x")
                    content_parts.append(f"▸ 决策理由: {reason[:100]}...")

                    content = "\n".join(content_parts)

                elif signal == 'ADJUST_STOP':
                    signal_text = " 调整止盈止损"

                    # 从adjust_data提取止盈止损信息
                    tp_layers = adjust_data.get('take_profit', [])
                    sl_layers = adjust_data.get('stop_loss', [])

                    content_parts = [f"【{signal_text}】"]
                    content_parts.append(f"▸ 交易对: {self.inst_id}")
                    if current_price:
                        content_parts.append(f"▸ 当前价格: {current_price:.2f} USDT")

                    # 显示止盈层级
                    if tp_layers:
                        content_parts.append(f"▸ 新止盈（{len(tp_layers)}层）:")
                        for i, layer in enumerate(tp_layers, 1):
                            content_parts.append(f"  #{i}: {layer['size']}张 @ {layer['price']:.2f}")

                    # 显示止损层级
                    if sl_layers:
                        content_parts.append(f"▸ 新止损（{len(sl_layers)}层）:")
                        for i, layer in enumerate(sl_layers, 1):
                            content_parts.append(f"  #{i}: {layer['size']}张 @ {layer['price']:.2f}")

                    content_parts.append(f"▸ 置信度: {confidence}%")
                    content_parts.append(f"▸ 决策理由: {reason[:100]}...")

                    content = "\n".join(content_parts)

                else:
                    return

                # 构造飞书消息格式

                payload={
    "msg_type": "post",
    "content": {
        "post": {
            "zh_cn": {
                "title": f"AI交易通知 【{signal}】 （{self.inst_id}）",
                "content": [
                    [{
                        "tag": "text",
                        "text":content
                    }]
                ]
            }
        }
    }
}

                # 发送POST请求（设置30秒超时）
                response = requests.post(
                    self.feishu_webhook_url,
                    json=payload,
                    timeout=30
                )

                if response.status_code == 200:
                    logger.info(f"✅ 飞书通知已发送: {signal_text}")
                else:
                    logger.warning(f"⚠️ 飞书通知发送失败: HTTP {response.status_code}")

            except requests.exceptions.Timeout:
                logger.warning("⚠️ 飞书通知发送超时（30秒）")
            except Exception as e:
                logger.error(f"❌ 飞书通知发送异常: {e}")

        # 启动后台线程发送（daemon=True，不阻塞主程序退出）
        notification_thread = threading.Thread(
            target=send_task,
            daemon=True,
            name="feishu-notification"
        )
        notification_thread.start()

    def _load_decision_history(self):
        """
        从本地JSON文件加载历史AI决策

        文件格式: [{"content": "...", "timestamp": "2025-10-27 16:30:45"}, ...]
        """
        try:
            # 确保data目录存在
            data_dir = os.path.dirname(self.ai_decision_history_file)
            if not os.path.exists(data_dir):
                os.makedirs(data_dir, exist_ok=True)
                logger.info(f"✓ 创建数据目录: {data_dir}")

            # 如果文件不存在，创建空文件
            if not os.path.exists(self.ai_decision_history_file):
                with open(self.ai_decision_history_file, 'w', encoding='utf-8') as f:
                    json.dump([], f, ensure_ascii=False, indent=2)
                logger.info(f"✓ 创建历史决策文件: {self.ai_decision_history_file}")
                return

            # 读取文件
            with open(self.ai_decision_history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 验证数据格式
            if isinstance(data, list):
                # 只保留最近10条（防止文件过大）
                self.ai_decision_history = data[-10:]
                logger.debug(f"✓ 从文件加载了 {len(self.ai_decision_history)} 条历史决策")
            else:
                logger.warning(f"⚠️ 历史决策文件格式错误，期望list，得到{type(data)}")
                self.ai_decision_history = []

        except json.JSONDecodeError as e:
            logger.error(f"❌ 历史决策文件JSON解析失败: {e}")
            self.ai_decision_history = []
        except Exception as e:
            logger.error(f"❌ 加载历史决策失败: {e}")
            self.ai_decision_history = []

    def _save_decision_history(self):
        """
        保存历史AI决策到本地JSON文件（后台线程执行，避免阻塞）

        只保留最近10条决策
        """
        def save_task():
            try:
                # 确保data目录存在
                data_dir = os.path.dirname(self.ai_decision_history_file)
                if not os.path.exists(data_dir):
                    os.makedirs(data_dir, exist_ok=True)

                # 只保留最近10条
                history_to_save = self.ai_decision_history[-10:] if len(self.ai_decision_history) > 10 else self.ai_decision_history

                # 写入文件
                with open(self.ai_decision_history_file, 'w', encoding='utf-8') as f:
                    json.dump(history_to_save, f, ensure_ascii=False, indent=2)

                logger.debug(f"✓ 历史决策已保存到文件: {len(history_to_save)} 条")

            except Exception as e:
                logger.error(f"❌ 保存历史决策失败: {e}")

        # 启动后台线程保存（daemon=True，不阻塞主程序退出）
        save_thread = threading.Thread(
            target=save_task,
            daemon=True,
            name="save-decision-history"
        )
        save_thread.start()

    async def start_realtime_collector(self):
        """启动实时数据采集（后台任务）"""
        if self.realtime_collector:
            logger.info("🚀 启动实时数据采集...")
            self.collector_task = asyncio.create_task(self.realtime_collector.start())
            await asyncio.sleep(3)  # 等待数据积累
            logger.success("✓ 实时数据采集已启动")

    def update_balance_cache(self):
        """
        后台线程：定期更新账户余额缓存
        每30秒更新一次，避免交易时实时调用API影响效率
        """
        while not self.stop_balance_thread:
            try:
                # 获取最新余额
                balance = self.account_api.get_usdt_balance()
                if balance['success']:
                    # 直接更新，无需锁（float读写基本是原子性的）
                    self.cached_balance = balance.get('availEq', 0)
                    self.balance_last_update = datetime.now()
                    logger.debug(f"✓ 余额缓存已更新: {self.cached_balance:.2f} USDT")
                else:
                    logger.warning(f"⚠️ 余额更新失败: {balance}")
            except Exception as e:
                logger.error(f"❌ 余额更新异常: {e}")

            # 每30秒更新一次
            time.sleep(30)

    def start_balance_update_thread(self):
        """启动余额更新后台线程"""
        # 立即获取一次余额
        try:
            balance = self.account_api.get_usdt_balance()
            if balance['success']:
                self.cached_balance = balance.get('availEq', 0)
                self.balance_last_update = datetime.now()
                logger.info(f"💰 初始余额: {self.cached_balance:.2f} USDT")
        except Exception as e:
            logger.error(f"❌ 初始余额获取失败: {e}")

        # 启动后台线程
        self.balance_update_thread = threading.Thread(
            target=self.update_balance_cache,
            daemon=True,
            name="balance-updater"
        )
        self.balance_update_thread.start()
        logger.info("✓ 余额更新线程已启动（每30秒更新）")

    def get_cached_balance(self) -> float:
        """
        获取缓存的账户余额（无锁访问，提高交易效率）

        Returns:
            缓存的USDT余额
        """
        # 检查缓存是否过期（仅提示，不影响使用）
        if self.balance_last_update:
            age_seconds = (datetime.now() - self.balance_last_update).total_seconds()
            if age_seconds > 60:
                logger.warning(f"⚠️ 余额缓存已过期 ({age_seconds:.0f}秒)，可能不准确")
        else:
            logger.warning("⚠️ 余额缓存未初始化")

        return self.cached_balance

    def stop_balance_update_thread(self):
        """停止余额更新线程"""
        if self.balance_update_thread and self.balance_update_thread.is_alive():
            self.stop_balance_thread = True
            self.balance_update_thread.join(timeout=5)
            logger.info("✓ 余额更新线程已停止")

    def update_position_cache(self):
        """
        后台线程：定期更新仓位缓存，关联AI决策历史
        每20秒更新一次
        """
        while not self.stop_position_thread:
            try:
                result = self.position_api.get_contract_positions(inst_type='SWAP', inst_id=self.inst_id)
                if result['code'] == '0':
                    positions = result.get('data', [])

                    # 关联决策历史
                    enriched_positions = []
                    for pos in positions:
                        if float(pos.get('pos', 0)) == 0:
                            continue

                        pos_id = str(pos.get('cTime'))

                        # 从数据库查询决策历史
                        decisions = self.data_manager.get_decisions_by_pos_id(
                            pos_id,
                            api_key=config.API_KEY if config.API_KEY else 'default'
                        )

                        # 合并仓位和决策历史
                        enriched_pos = {
                            **pos,
                            'decisions': decisions,
                            'decision_count': len(decisions),
                            'last_decision': decisions[-1] if decisions else None,
                            'open_reason': decisions[0]['reason'] if decisions else None,
                            'adjustments': [d for d in decisions if d.get('action') == 'ADJUST_STOP']
                        }
                        enriched_positions.append(enriched_pos)
                    for lastPosition  in  self.cached_positions:
                        if str(lastPosition['cTime']) not in [str(position['cTime']) for position in enriched_positions]:
                            #发送飞书平仓通知
                            self.send_feishu_content(str(lastPosition['cTime']))
                    self.cached_positions = enriched_positions
                    self.positions_last_update = datetime.now()
                    logger.debug(f"✓ 仓位缓存已更新: {len(self.cached_positions)}个持仓")
                else:
                    logger.warning(f"⚠️ 仓位更新失败: {result.get('msg')}")
            except Exception as e:
                logger.error(f"❌ 仓位更新异常: {e}")

            time.sleep(20)

    def start_position_update_thread(self):
        """启动仓位更新后台线程"""
        # 立即获取一次仓位
        try:
            result = self.position_api.get_contract_positions(inst_type='SWAP', inst_id=self.inst_id)
            if result['code'] == '0':
                positions = result.get('data', [])
                self.cached_positions = [p for p in positions if float(p.get('pos', 0)) != 0]
                self.positions_last_update = datetime.now()
                logger.info(f"📊 初始仓位: {len(self.cached_positions)}个持仓")
        except Exception as e:
            logger.error(f"❌ 初始仓位获取失败: {e}")

        # 启动后台线程
        self.position_update_thread = threading.Thread(
            target=self.update_position_cache,
            daemon=True,
            name="position-updater"
        )
        self.position_update_thread.start()
        logger.info("✓ 仓位更新线程已启动（每20秒更新）")

    def get_cached_positions(self) -> list:
        """
        获取缓存的仓位数据（无锁访问，提高交易效率）

        Returns:
            缓存的持仓列表
        """
        # 检查缓存是否过期
        if self.positions_last_update:
            age_seconds = (datetime.now() - self.positions_last_update).total_seconds()
            if age_seconds > 60:
                logger.warning(f"⚠️ 仓位缓存已过期 ({age_seconds:.0f}秒)，可能不准确")
        else:
            logger.warning("⚠️ 仓位缓存未初始化")

        return self.cached_positions

    def stop_position_update_thread(self):
        """停止仓位更新线程"""
        if self.position_update_thread and self.position_update_thread.is_alive():
            self.stop_position_thread = True
            self.position_update_thread.join(timeout=5)
            logger.info("✓ 仓位更新线程已停止")

    def update_stop_order_cache(self):
        """
        后台线程：定期更新止盈止损委托订单缓存

        止盈：从普通挂单获取（limit orders，挂单模式）
        止损：从算法订单获取（conditional orders，条件单）

        每20秒更新一次
        """
        while not self.stop_stop_order_thread:
            try:
                # 1. 获取普通挂单（止盈用限价单）
                limit_orders = []
                try:
                    limit_result = self.trade_api.get_orders_pending(
                        inst_id=self.inst_id,
                        ord_type='limit',
                        state='live'  # 未成交订单
                    )
                    if limit_result['code'] == '0':
                        limit_orders = limit_result.get('data', [])
                        logger.debug(f"✓ 获取到 {len(limit_orders)} 个限价单（止盈）")
                except Exception as e:
                    logger.warning(f"⚠️ 获取限价单失败: {e}")

                # 2. 获取算法订单（止损用条件单）
                algo_orders = []
                try:
                    algo_result = self.trade_api.get_algo_order_list(
                        ord_type='conditional',
                        inst_id=self.inst_id
                    )
                    if algo_result['code'] == '0':
                        algo_orders = algo_result.get('data', [])
                        logger.debug(f"✓ 获取到 {len(algo_orders)} 个条件单（止损）")
                except Exception as e:
                    logger.warning(f"⚠️ 获取条件单失败: {e}")

                # 3. 合并解析
                self.cached_stop_orders = self._parse_stop_orders(limit_orders, algo_orders)
                self.stop_orders_last_update = datetime.now()
                logger.debug(f"✓ 止盈止损订单缓存已更新: {len(self.cached_stop_orders)}个方向")

            except Exception as e:
                logger.error(f"❌ 止盈止损订单更新异常: {e}")

            time.sleep(20)

    def _parse_stop_orders(self, limit_orders: list, algo_orders: list) -> dict:
        """
        解析止盈止损订单（分别处理限价单和条件单）

        Args:
            limit_orders: 普通限价单列表（止盈）
            algo_orders: 算法条件单列表（止损）

        Returns:
            解析后的字典 {pos_side: {'stop_loss': [...], 'take_profit': [...]}}
            每个列表包含多层信息：[{'order_id': ..., 'price': ..., 'size': ...}, ...]
        """
        parsed = {}

        # 1. 解析限价单（止盈）
        for order in limit_orders:
            pos_side = order.get('posSide')
            if not pos_side:
                continue

            if pos_side not in parsed:
                parsed[pos_side] = {'take_profit': [], 'stop_loss': []}

            # 限价单字段：ordId, px (价格), sz (数量), side
            order_info = {
                'order_id': order.get('ordId'),
                'price': float(order.get('px', 0)),
                'size': float(order.get('sz', 0)),
                'order_type': 'limit'
            }

            # 根据side判断是止盈订单
            # 多头止盈：卖出平仓，空头止盈：买入平仓
            side = order.get('side')
            if (pos_side == 'long' and side == 'sell') or (pos_side == 'short' and side == 'buy'):
                parsed[pos_side]['take_profit'].append(order_info)

        # 2. 解析算法订单（止损）
        for order in algo_orders:
            pos_side = order.get('posSide')
            if not pos_side:
                continue

            if pos_side not in parsed:
                parsed[pos_side] = {'take_profit': [], 'stop_loss': []}

            # 条件单字段：algoId, slTriggerPx (止损触发价), sz
            sl_trigger_px = order.get('slTriggerPx')
            if sl_trigger_px:
                order_info = {
                    'order_id': order.get('algoId'),
                    'price': float(sl_trigger_px),
                    'size': float(order.get('sz', 0)),
                    'order_type': 'conditional'
                }
                parsed[pos_side]['stop_loss'].append(order_info)

        # 3. 按价格排序（止盈：从高到低，止损：从低到高）
        for pos_side, orders in parsed.items():
            # 止盈排序（多头从高到低，空头从低到高）
            if orders['take_profit']:
                reverse = (pos_side == 'long')
                orders['take_profit'].sort(key=lambda x: x['price'], reverse=reverse)

            # 止损排序（多头从高到低，空头从低到高）
            if orders['stop_loss']:
                reverse = (pos_side == 'long')
                orders['stop_loss'].sort(key=lambda x: x['price'], reverse=reverse)

        return parsed

    def start_stop_order_update_thread(self):
        """启动止盈止损订单更新后台线程"""
        # 立即获取一次订单
        try:
            # 1. 获取普通挂单（止盈）
            limit_orders = []
            try:
                limit_result = self.trade_api.get_orders_pending(
                    inst_id=self.inst_id,
                    ord_type='limit',
                    state='live'
                )
                if limit_result['code'] == '0':
                    limit_orders = limit_result.get('data', [])
            except Exception as e:
                logger.warning(f"⚠️ 初始获取限价单失败: {e}")

            # 2. 获取算法订单（止损）
            algo_orders = []
            try:
                algo_result = self.trade_api.get_algo_order_list(
                    ord_type='conditional',
                    inst_id=self.inst_id
                )
                if algo_result['code'] == '0':
                    algo_orders = algo_result.get('data', [])
            except Exception as e:
                logger.warning(f"⚠️ 初始获取条件单失败: {e}")

            # 3. 合并解析
            self.cached_stop_orders = self._parse_stop_orders(limit_orders, algo_orders)
            self.stop_orders_last_update = datetime.now()

            # 统计层数
            total_tp_layers = sum(len(orders.get('take_profit', [])) for orders in self.cached_stop_orders.values())
            total_sl_layers = sum(len(orders.get('stop_loss', [])) for orders in self.cached_stop_orders.values())

            logger.info(
                f"📋 初始止盈止损订单: {len(self.cached_stop_orders)}个方向, "
                f"止盈{total_tp_layers}层, 止损{total_sl_layers}层"
            )
        except Exception as e:
            logger.error(f"❌ 初始止盈止损订单获取失败: {e}")

        # 启动后台线程
        self.stop_order_update_thread = threading.Thread(
            target=self.update_stop_order_cache,
            daemon=True,
            name="stop-order-updater"
        )
        self.stop_order_update_thread.start()
        logger.info("✓ 止盈止损订单更新线程已启动（每20秒更新）")

    def get_cached_stop_orders(self) -> dict:
        """
        获取缓存的止盈止损订单数据

        Returns:
            缓存的订单字典
        """
        # 检查缓存是否过期
        if self.stop_orders_last_update:
            age_seconds = (datetime.now() - self.stop_orders_last_update).total_seconds()
            if age_seconds > 60:
                logger.warning(f"⚠️ 止盈止损订单缓存已过期 ({age_seconds:.0f}秒)，可能不准确")
        else:
            logger.warning("⚠️ 止盈止损订单缓存未初始化")

        return self.cached_stop_orders

    def stop_stop_order_update_thread(self):
        """停止止盈止损订单更新线程"""
        if self.stop_order_update_thread and self.stop_order_update_thread.is_alive():
            self.stop_stop_order_thread = True
            self.stop_order_update_thread.join(timeout=5)
            logger.info("✓ 止盈止损订单更新线程已停止")

    def update_position_history_cache(self):
        """
        后台线程：定期更新历史仓位缓存

        流程：
        1. 通过API获取OKX历史持仓记录（已平仓）
        2. 保存到数据库（INSERT OR REPLACE，避免重复）
        3. 更新内存缓存（历史仓位 + 统计数据）

        每30秒执行一次
        """
        while not self.stop_history_thread:
            try:
                # 1. 获取OKX历史持仓记录（已平仓）
                history_result = self.position_api.get_positions_history(
                    inst_type='SWAP',
                    inst_id=self.inst_id,
                    limit='100'  # 获取最近100条历史记录
                )

                # 2. 保存OKX历史持仓到数据库（批量优化）
                if history_result and history_result.get('code') == '0' and history_result.get('data'):
                    okx_history = history_result['data']

                    # 准备批量数据
                    batch_data = []
                    for pos in okx_history:
                        try:
                            inst_id = pos.get('instId', '')
                            pos_side = pos.get('posSide', '')

                            # 使用uTime作为平仓时间（OKX返回的实际平仓时间）
                            close_time = int(pos.get('uTime', 0))
                            realized_pnl = float(pos.get('realizedPnl', 0)) if pos.get('realizedPnl') else float(pos.get('pnl', 0))

                            # 提取字段
                            pos_size = float(pos.get('closePosSize', 0))
                            avg_px = float(pos.get('openAvgPx', 0))
                            mark_px = float(pos.get('closeAvgPx', 0))
                            upl = realized_pnl
                            upl_ratio = float(pos.get('pnlRatio', 0))
                            leverage = pos.get('lever', '20')
                            margin = float(pos.get('margin', 0)) if pos.get('margin') else None
                            imr = float(pos.get('imr', 0)) if pos.get('imr') else None
                            fee = float(pos.get('fee', 0)) if pos.get('fee') else 0.0
                            open_time = int(pos.get('cTime', 0)) if pos.get('cTime') else None
                            close_total_pos = float(pos.get('closeTotalPos', 0)) if pos.get('closeTotalPos') else None

                            # 添加到批量数据
                            batch_data.append((
                                inst_id, pos_side, pos_size, avg_px, mark_px, upl, upl_ratio,
                                leverage, margin, imr, fee, open_time, close_time, realized_pnl, close_total_pos
                            ))

                        except Exception as e:
                            logger.debug(f"处理OKX历史持仓失败: {e}")
                            continue

                    # 批量保存（一次性提交）
                    if batch_data:
                        success_count, total_count = self.data_manager.save_closed_positions_batch(
                            batch_data,
                            api_key=config.API_KEY if config.API_KEY else 'default'
                        )
                        logger.debug(f"✓ 批量保存历史持仓: {success_count}/{total_count} 条")

                        # 复盘逻辑：检查每个仓位是否需要生成复盘总结
                        if self.ai_client:
                            for pos in okx_history:
                                try:
                                    inst_id = pos.get('instId', '')
                                    pos_side = pos.get('posSide', '')
                                    open_time = int(pos.get('cTime', 0)) if pos.get('cTime') else None
                                    close_time = int(pos.get('uTime', 0))

                                    if not open_time:
                                        continue
                                        
                                    if pos['type']!='2':
                                        continue
                                        

                                    # 检查是否已有复盘总结
                                    existing_review = self.data_manager.get_position_review_summary(
                                        inst_id=inst_id,
                                        pos_side=pos_side,
                                        open_time=open_time,
                                        api_key=config.API_KEY if config.API_KEY else 'default'
                                    )

                                    if existing_review:
                                        logger.debug(f"⏩ 仓位已有复盘总结，跳过: {inst_id} {pos_side} open_time={open_time}")
                                        continue

                                    # 准备仓位数据
                                    position_data = {
                                        'inst_id': inst_id,
                                        'pos_side': pos_side,
                                        'pos':  float(pos.get('closeTotalPos', 0)) if pos.get('closeTotalPos') else None,
                                        'avg_px': float(pos.get('openAvgPx', 0)),
                                        'mark_px': float(pos.get('closeAvgPx', 0)),
                                        'upl': float(pos.get('realizedPnl', 0)) if pos.get('realizedPnl') else float(pos.get('pnl', 0)),
                                        'upl_ratio': float(pos.get('pnlRatio', 0)),
                                        'fee': float(pos.get('fee', 0)) if pos.get('fee') else 0.0,
                                        'open_time': open_time,
                                        'close_time': close_time,
                                        'realized_pnl': float(pos.get('realizedPnl', 0)) if pos.get('realizedPnl') else float(pos.get('pnl', 0)),
                                        'holding_duration_seconds': (close_time - open_time) / 1000 if open_time and close_time else 0
                                    }

                                    # 获取决策历史
                                    pos_id = str(open_time)
                                    decisions = self.data_manager.get_decisions_by_pos_id(
                                        pos_id,
                                        api_key=config.API_KEY if config.API_KEY else 'default'
                                    )

                                    # 获取平仓前的5分钟K线数据
                                    # 使用 end_time 参数获取平仓时间之前的K线
                                    klines = self.data_manager.get_recent_klines(
                                        inst_id=inst_id,
                                        bar='5m',
                                        limit=80,  # 获取20根K线
                                        end_time=close_time+60*30*1000  # 只获取平仓时间之前的K线
                                    )
                                    
                                    if not decisions:
                                        continue

                                    # 生成复盘总结（同步调用）
                                    logger.info(f"📝 正在为仓位生成复盘: {inst_id} {pos_side} open_time={open_time}")
                                    review_summary = self.generate_position_review(
                                        position_data=position_data,
                                        decisions=decisions,
                                        klines=klines  # 已经是平仓前的K线
                                    )

                                    # 保存复盘总结到数据库
                                    if review_summary and not review_summary.startswith('复盘生成失败') and not review_summary.startswith('复盘生成异常'):
                                        success = self.data_manager.update_position_review_summary(
                                            inst_id=inst_id,
                                            pos_side=pos_side,
                                            open_time=open_time,
                                            review_summary=review_summary,
                                            api_key=config.API_KEY if config.API_KEY else 'default'
                                        )
                                        if success:
                                            logger.success(f"✅ 复盘总结已保存: {inst_id} {pos_side}")
                                        else:
                                            logger.warning(f"⚠️ 复盘总结保存失败: {inst_id} {pos_side}")

                                except Exception as e:
                                    logger.error(f"❌ 生成仓位复盘失败: {e}")
                                    import traceback
                                    logger.debug(traceback.format_exc())
                                    continue


                # 3. 更新缓存的历史仓位和统计数据（从数据库查询并关联决策）
                historical_positions = self.data_manager.get_recent_closed_positions(
                    inst_id=self.inst_id,
                    limit=10,
                    api_key=config.API_KEY if config.API_KEY else 'default'
                )

                # 关联决策历史
                for pos in historical_positions:
                    pos_id = str(pos.get('open_time')  )
                    if pos_id:
                        decisions = self.data_manager.get_decisions_by_pos_id(
                            pos_id,
                            api_key=config.API_KEY if config.API_KEY else 'default'
                        )
                        pos['decisions'] = decisions
                        pos['decision_count'] = len(decisions)
                        pos['open_reason'] = decisions[0]['reason'] if decisions else None
                        pos['adjustments'] = [d for d in decisions if d.get('action') == 'ADJUST_STOP']

                self.cached_historical_positions = historical_positions
                self.cached_performance_stats = self.data_manager.get_performance_stats(
                    inst_id=self.inst_id,
                    days=30,
                    api_key=config.API_KEY if config.API_KEY else 'default'
                )
                self.history_last_update = datetime.now()

                logger.debug(
                    f"✓ 历史仓位缓存已更新: {len(self.cached_historical_positions)}笔历史, "
                    f"30天胜率: {self.cached_performance_stats.get('win_rate', 0):.1f}%"
                )

            except Exception as e:
                logger.error(f"❌ 历史仓位更新异常: {e}")
                import traceback
                logger.debug(traceback.format_exc())

            time.sleep(30)

    def start_position_history_thread(self):
        """启动历史仓位更新后台线程"""
        # 立即执行一次更新（通过API获取OKX历史持仓）
        try:
            # 获取OKX历史持仓记录（已平仓）
            history_result = self.position_api.get_positions_history(
                inst_type='SWAP',
                inst_id=self.inst_id,
                limit='100'
            )

            # 导入OKX历史持仓到数据库（批量优化）
            if history_result and history_result.get('code') == '0' and history_result.get('data'):
                okx_history = history_result['data']
                logger.info(f"📊 从OKX获取到 {len(okx_history)} 条历史持仓记录")

                # 准备批量数据
                batch_data = []
                for pos in okx_history:
                    try:
                        inst_id = pos.get('instId', '')
                        pos_side = pos.get('posSide', '')

                        # 使用uTime作为平仓时间（OKX返回的实际平仓时间）
                        close_time = int(pos.get('uTime', 0))
                        if close_time == 0:
                            close_time = int(pos.get('cTime', 0))

                        realized_pnl = float(pos.get('realizedPnl', 0)) if pos.get('realizedPnl') else float(pos.get('pnl', 0))

                        # 提取字段
                        pos_size = float(pos.get('closePosSize', 0))
                        avg_px = float(pos.get('openAvgPx', 0))
                        mark_px = float(pos.get('closeAvgPx', 0))
                        upl = realized_pnl
                        upl_ratio = float(pos.get('pnlRatio', 0))
                        leverage = pos.get('lever', '20')
                        margin = float(pos.get('margin', 0)) if pos.get('margin') else None
                        imr = float(pos.get('imr', 0)) if pos.get('imr') else None
                        fee = float(pos.get('fee', 0)) if pos.get('fee') else 0.0
                        open_time = int(pos.get('cTime', 0)) if pos.get('cTime') else None
                        close_total_pos = float(pos.get('closeTotalPos', 0)) if pos.get('closeTotalPos') else None

                        # 添加到批量数据
                        batch_data.append((
                            inst_id, pos_side, pos_size, avg_px, mark_px, upl, upl_ratio,
                            leverage, margin, imr, fee, open_time, close_time, realized_pnl, close_total_pos
                        ))

                    except Exception as e:
                        logger.debug(f"导入历史持仓失败: {e}")
                        continue

                # 批量保存（一次性提交）
                if batch_data:
                    success_count, total_count = self.data_manager.save_closed_positions_batch(
                        batch_data,
                        api_key=config.API_KEY if config.API_KEY else 'default'
                    )
                    logger.info(f"✓ 成功导入 {success_count}/{total_count} 条OKX历史持仓")

                    # 复盘逻辑：检查每个仓位是否需要生成复盘总结
                    if self.ai_client:
                        logger.info(f"📝 检查历史仓位复盘需求...")
                        review_count = 0
                        for pos in okx_history:
                            try:
                                inst_id = pos.get('instId', '')
                                pos_side = pos.get('posSide', '')
                                open_time = int(pos.get('cTime', 0)) if pos.get('cTime') else None
                                close_time = int(pos.get('uTime', 0))
                                if close_time == 0:
                                    close_time = int(pos.get('cTime', 0))

                                if not open_time:
                                    continue
                                if pos['type']!='2':
                                    continue

                                # 检查是否已有复盘总结
                                existing_review = self.data_manager.get_position_review_summary(
                                    inst_id=inst_id,
                                    pos_side=pos_side,
                                    open_time=open_time,
                                    api_key=config.API_KEY if config.API_KEY else 'default'
                                )

                                if existing_review:
                                    continue

                                # 限制初始化时只复盘最近5笔
                                if review_count >= 5:
                                    logger.info(f"⏸️ 初始化时已复盘5笔，剩余将由后台线程处理")
                                    break

                                # 准备仓位数据
                                position_data = {
                                    'inst_id': inst_id,
                                    'pos_side': pos_side,
                                    'pos': float(pos.get('closePosSize', 0)),
                                    'avg_px': float(pos.get('openAvgPx', 0)),
                                    'mark_px': float(pos.get('closeAvgPx', 0)),
                                    'upl': float(pos.get('realizedPnl', 0)) if pos.get('realizedPnl') else float(pos.get('pnl', 0)),
                                    'upl_ratio': float(pos.get('pnlRatio', 0)),
                                    'fee': float(pos.get('fee', 0)) if pos.get('fee') else 0.0,
                                    'open_time': open_time,
                                    'close_time': close_time,
                                    'realized_pnl': float(pos.get('realizedPnl', 0)) if pos.get('realizedPnl') else float(pos.get('pnl', 0)),
                                    'holding_duration_seconds': (close_time - open_time) / 1000 if open_time and close_time else 0
                                }

                                # 获取决策历史
                                pos_id = str(open_time)
                                decisions = self.data_manager.get_decisions_by_pos_id(
                                    pos_id,
                                    api_key=config.API_KEY if config.API_KEY else 'default'
                                )

                                # 获取平仓前的5分钟K线数据
                                klines = self.data_manager.get_recent_klines(
                                    inst_id=inst_id,
                                    bar='5m',
                                    limit=80,
                                    end_time=close_time+60*30*1000   # 只获取平仓时间之前的K线
                                )
                                if not decisions:
                                    continue

                                # 生成复盘总结（同步调用）
                                logger.info(f"📝 正在为仓位生成复盘: {inst_id} {pos_side} open_time={open_time}")
                                review_summary = self.generate_position_review(
                                    position_data=position_data,
                                    decisions=decisions,
                                    klines=klines  # 已经是平仓前的K线
                                )

                                # 保存复盘总结到数据库
                                if review_summary and not review_summary.startswith('复盘生成失败') and not review_summary.startswith('复盘生成异常'):
                                    success = self.data_manager.update_position_review_summary(
                                        inst_id=inst_id,
                                        pos_side=pos_side,
                                        open_time=open_time,
                                        review_summary=review_summary,
                                        api_key=config.API_KEY if config.API_KEY else 'default'
                                    )
                                    if success:
                                        logger.success(f"✅ 复盘总结已保存: {inst_id} {pos_side}")
                                        review_count += 1

                            except Exception as e:
                                logger.error(f"❌ 生成仓位复盘失败: {e}")
                                import traceback
                                logger.debug(traceback.format_exc())
                                continue


            # 从数据库加载历史数据到缓存并关联决策
            historical_positions = self.data_manager.get_recent_closed_positions(
                inst_id=self.inst_id,
                limit=10,
                api_key=config.API_KEY if config.API_KEY else 'default'
            )

            # 关联决策历史
            for pos in historical_positions:
                pos_id = str(pos.get('open_time') ) 
                if pos_id:
                    decisions = self.data_manager.get_decisions_by_pos_id(
                        pos_id,
                        api_key=config.API_KEY if config.API_KEY else 'default'
                    )
                    pos['decisions'] = decisions
                    pos['decision_count'] = len(decisions)
                    pos['open_reason'] = decisions[0]['reason'] if decisions else None
                    pos['adjustments'] = [d for d in decisions if d.get('action') == 'ADJUST_STOP']

            self.cached_historical_positions = historical_positions
            self.cached_performance_stats = self.data_manager.get_performance_stats(
                inst_id=self.inst_id,
                days=30,
                api_key=config.API_KEY if config.API_KEY else 'default'
            )
            self.history_last_update = datetime.now()
            logger.info(
                f"📊 初始历史仓位: {len(self.cached_historical_positions)}笔历史, "
                f"30天总交易: {self.cached_performance_stats.get('total_trades', 0)}笔"
            )
        except Exception as e:
            logger.error(f"❌ 初始历史仓位获取失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())

        # 启动后台线程
        self.position_history_thread = threading.Thread(
            target=self.update_position_history_cache,
            daemon=True,
            name="position-history-updater"
        )
        self.position_history_thread.start()
        logger.info("✓ 历史仓位更新线程已启动（每30秒更新）")

    def stop_position_history_thread(self):
        """停止历史仓位更新线程"""
        if self.position_history_thread and self.position_history_thread.is_alive():
            self.stop_history_thread = True
            self.position_history_thread.join(timeout=5)
            logger.info("✓ 历史仓位更新线程已停止")

    def update_funding_rate_cache(self):
        """
        后台线程：定期更新资金费率缓存
        每20秒更新一次
        """
        while not self.stop_funding_rate_thread:
            try:
                result = self.public_api.get_funding_rate(inst_id=self.inst_id)
                if result['code'] == '0' and result.get('data'):
                    self.cached_funding_rate = result['data'][0]
                    self.funding_rate_last_update = datetime.now()

                    # 提取资金费率信息
                    funding_rate = float(self.cached_funding_rate.get('fundingRate', 0))
                    next_funding_time = self.cached_funding_rate.get('nextFundingTime', '')

                    logger.debug(f"✓ 资金费率缓存已更新: {funding_rate*100:.4f}% (下次: {next_funding_time})")
                else:
                    logger.warning(f"⚠️ 资金费率更新失败: {result.get('msg')}")
            except Exception as e:
                logger.error(f"❌ 资金费率更新异常: {e}")

            time.sleep(20)

    def start_funding_rate_update_thread(self):
        """启动资金费率更新后台线程"""
        # 立即获取一次资金费率
        try:
            result = self.public_api.get_funding_rate(inst_id=self.inst_id)
            if result['code'] == '0' and result.get('data'):
                self.cached_funding_rate = result['data'][0]
                self.funding_rate_last_update = datetime.now()

                funding_rate = float(self.cached_funding_rate.get('fundingRate', 0))
                next_funding_time = self.cached_funding_rate.get('nextFundingTime', '')
                logger.info(f"💸 初始资金费率: {funding_rate*100:.4f}% (下次: {next_funding_time})")
        except Exception as e:
            logger.error(f"❌ 初始资金费率获取失败: {e}")

        # 启动后台线程
        self.funding_rate_update_thread = threading.Thread(
            target=self.update_funding_rate_cache,
            daemon=True,
            name="funding-rate-updater"
        )
        self.funding_rate_update_thread.start()
        logger.info("✓ 资金费率更新线程已启动（每20秒更新）")

    def get_cached_funding_rate(self) -> dict:
        """
        获取缓存的资金费率数据（无锁访问，提高AI分析效率）

        Returns:
            缓存的资金费率字典
        """
        # 检查缓存是否过期
        if self.funding_rate_last_update:
            age_seconds = (datetime.now() - self.funding_rate_last_update).total_seconds()
            if age_seconds > 60:
                logger.warning(f"⚠️ 资金费率缓存已过期 ({age_seconds:.0f}秒)，可能不准确")
        else:
            logger.warning("⚠️ 资金费率缓存未初始化")

        return self.cached_funding_rate if self.cached_funding_rate else {}

    def stop_funding_rate_update_thread(self):
        """停止资金费率更新线程"""
        if self.funding_rate_update_thread and self.funding_rate_update_thread.is_alive():
            self.stop_funding_rate_thread = True
            self.funding_rate_update_thread.join(timeout=5)
            logger.info("✓ 资金费率更新线程已停止")

    def update_market_data_cache(self):
        """
        后台线程：定期更新市场数据缓存（持仓量、交易量、主动买卖）
        每30秒更新一次
        """
        while not self.stop_market_data_thread:
            try:
                # 1. 获取主动买卖数据（15分钟周期）
                taker_result = self.trade_api.taker_volume_contract(
                    inst_id=self.inst_id,
                    period='15m',
                    unit=2,  # USDT
                    limit=24  # 最近24个数据点（6小时）
                )
                if taker_result.get('code') == '0' and taker_result.get('data'):
                    self.cached_taker_volume = taker_result['data']
                    logger.debug(f"✓ 主动买卖数据已更新: {len(self.cached_taker_volume)}条")
                else:
                    logger.warning(f"⚠️ 主动买卖数据更新失败: {taker_result.get('msg')}")

                # 2. 获取持仓量和交易量数据（1小时周期）
                oi_result = self.trade_api.open_interest_volume(
                    inst_id=self.inst_id,
                    period='1H',
                    begin=None,
                    end=None
                )
                if oi_result.get('code') == '0' and oi_result.get('data'):
                    self.cached_open_interest = oi_result['data']
                    logger.debug(f"✓ 持仓量数据已更新: {len(self.cached_open_interest)}条")
                else:
                    logger.warning(f"⚠️ 持仓量数据更新失败: {oi_result.get('msg')}")

                # 更新时间戳
                self.market_data_last_update = datetime.now()

            except Exception as e:
                logger.error(f"❌ 市场数据更新异常: {e}")
                import traceback
                logger.debug(traceback.format_exc())

            time.sleep(30)

    def start_market_data_update_thread(self):
        """启动市场数据更新后台线程"""
        # 立即获取一次数据
        try:
            # 主动买卖数据
            taker_result = self.trade_api.taker_volume_contract(
                inst_id=self.inst_id,
                period='15m',
                unit=2,
                limit=24
            )
            if taker_result.get('code') == '0' and taker_result.get('data'):
                self.cached_taker_volume = taker_result['data']
                logger.info(f"📊 初始主动买卖数据: {len(self.cached_taker_volume)}条")

            # 持仓量数据
            oi_result = self.trade_api.open_interest_volume(
                inst_id=self.inst_id,
                period='1H'
            )
            if oi_result.get('code') == '0' and oi_result.get('data'):
                self.cached_open_interest = oi_result['data']
                logger.info(f"📊 初始持仓量数据: {len(self.cached_open_interest)}条")

            self.market_data_last_update = datetime.now()

        except Exception as e:
            logger.error(f"❌ 初始市场数据获取失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())

        # 启动后台线程
        self.market_data_update_thread = threading.Thread(
            target=self.update_market_data_cache,
            daemon=True,
            name="market-data-updater"
        )
        self.market_data_update_thread.start()
        logger.info("✓ 市场数据更新线程已启动（每30秒更新）")

    def get_cached_market_data(self) -> tuple:
        """
        获取缓存的市场数据（无锁访问，提高AI分析效率）

        Returns:
            (cached_taker_volume, cached_open_interest): 主动买卖数据和持仓量数据
        """
        # 检查缓存是否过期
        if self.market_data_last_update:
            age_seconds = (datetime.now() - self.market_data_last_update).total_seconds()
            if age_seconds > 60:
                logger.warning(f"⚠️ 市场数据缓存已过期 ({age_seconds:.0f}秒)，可能不准确")
        else:
            logger.warning("⚠️ 市场数据缓存未初始化")

        return self.cached_taker_volume, self.cached_open_interest

    def stop_market_data_update_thread(self):
        """停止市场数据更新线程"""
        if self.market_data_update_thread and self.market_data_update_thread.is_alive():
            self.stop_market_data_thread = True
            self.market_data_update_thread.join(timeout=5)
            logger.info("✓ 市场数据更新线程已停止")

    def generate_position_review(self, position_data: dict, decisions: list, klines: list) -> str:
        """
        调用AI生成仓位复盘总结（同步版本）

        Args:
            position_data: 仓位数据字典，包含 inst_id, pos_side, pos, avg_px, mark_px, upl,
                          upl_ratio, fee, open_time, close_time, realized_pnl, holding_duration_seconds
            decisions: AI决策历史列表
            klines: 平仓前的5分钟K线数据列表

        Returns:
            AI生成的复盘总结文本
        """
        if not self.ai_client:
            logger.warning("⚠️ AI客户端未初始化，无法生成复盘")
            return "AI客户端未配置，无法生成复盘总结"

        try:
            # 格式化仓位信息表格
            pos_side_display = "Long" if position_data['pos_side'] == 'long' else "Short"
            entry_price = position_data['avg_px']
            exit_price = position_data['mark_px']
            pnl = position_data['realized_pnl']
            pnl_pct = position_data['upl_ratio'] * 100
            fee = position_data.get('fee', 0)
            holding_seconds = position_data.get('holding_duration_seconds', 0)
            holding_hours = holding_seconds / 3600
            close_time_str = datetime.fromtimestamp(position_data['close_time'] / 1000).strftime('%m-%d %H:%M')

            position_table = f"""| # | Side | Size | Entry | Exit | PnL | P&L% | Fee | Hold Time | Close Time |
|---|------|------|-------|------|-----|------|-----|-----------|------------|
| 1 | {pos_side_display} | {position_data['pos']} | {entry_price:.2f} | {exit_price:.2f} | {pnl:.2f} | {pnl_pct:.2f}% | {fee:.2f} | {holding_hours:.1f}h | {close_time_str} |"""

            # 格式化AI决策历史
            decisions_text = ""
            if decisions:
                decisions_text = "\n**仓位的AI决策历史:**\n"
                for idx, decision in enumerate(decisions, 1):
                    timestamp_str = decision['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(decision['timestamp'], datetime) else str(decision['timestamp'])
                    action = decision['action']
                    confidence = decision.get('confidence', 0)
                    reason = decision.get('reason', '')

                    decisions_text += f"  {idx}. [{timestamp_str}] {action} (信心: {confidence}%)\n"

                    # 如果是 ADJUST_STOP，显示调整信息
                    if action == 'ADJUST_STOP' and decision.get('adjust_data'):
                        adjust_data = decision['adjust_data']
                        tp_layers = adjust_data.get('take_profit', [])
                        sl_layers = adjust_data.get('stop_loss', [])

                        tp_price = tp_layers[0]['price'] if tp_layers else 0
                        sl_price = sl_layers[0]['price'] if sl_layers else 0

                        decisions_text += f"     调整: 止盈{len(tp_layers)}层, 止损{len(sl_layers)}层, 止盈价: {tp_price:.2f}, 止损价: {sl_price:.2f}\n"

                    # 显示理由（如果有）
                    if reason and action in ['OPEN_LONG', 'OPEN_SHORT', 'ADJUST_STOP']:
                        # 只显示前200字符
                        reason_short = reason[:200] + '...' if len(reason) > 200 else reason
                        decisions_text += f"     理由: {reason_short}\n"
            else:
                logger.info('仓位不存在历史决策，不复盘')
                return '复盘生成失败:仓位不存在历史决策，不复盘'
            # 格式化K线数据
            klines_text = "\n### K线数据 (平仓前最近15根5分钟K线)\n"
            klines_text += "```\n"
            klines_text += "Time    | Open    | High    | Low     | Close   | Volume  | Status\n"
            klines_text += "--------|---------|---------|---------|---------|---------|------\n"

            for kline in klines[-15:]:  # 只取最后15根
                timestamp = kline.get('timestamp', 0)
                time_str = datetime.fromtimestamp(timestamp / 1000).strftime('%H:%M')
                open_price = kline.get('open', 0)
                high = kline.get('high', 0)
                low = kline.get('low', 0)
                close = kline.get('close', 0)
                volume = kline.get('volume', 0)
                is_confirmed = kline.get('is_confirmed', True)
                status = '✓' if is_confirmed else '⟳'

                klines_text += f"{time_str} | {open_price:.2f} | {high:.2f} | {low:.2f} | {close:.2f} | {volume:.2f} | {status}\n"

            klines_text += "```\n自主识别形态"

            # 构建完整的prompt
            prompt = f"""这是上一次交易的仓位情况和市场行情，请复盘分析评价一下这个仓位的整个操作。

{position_table}

{decisions_text}

{klines_text}


请给出简洁专业的复盘总结（300-500字）。"""

            # 调用AI
            logger.info(f"🤖 正在生成仓位复盘总结...")
            result = self.ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": "你是一位专业的加密货币交易分析师，擅长复盘分析交易记录。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                use_json_mode=False,
                stream=False,
                timeout=60
            )

            if result.get('success'):
                review_summary = result.get('data', '')['choices'][0]['message']['content']
                logger.success(f"✓ 复盘总结生成成功（{len(review_summary)}字符）")
                return review_summary
            else:
                error_msg = result.get('error', 'unknown')
                logger.warning(f"⚠️ AI复盘生成失败: {error_msg}")
                return f"复盘生成失败: {error_msg}"

        except Exception as e:
            logger.error(f"❌ 生成复盘总结异常: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return f"复盘生成异常: {str(e)}"

    def get_cached_historical_data(self) -> tuple:
        """
        获取缓存的历史仓位数据（无锁访问，提高AI分析效率）

        Returns:
            (cached_historical_positions, cached_performance_stats): 历史仓位列表和统计数据
        """
        # 导入datetime（确保在方法内可用）
        from datetime import datetime as dt

        # 检查缓存是否过期
        if self.history_last_update:
            age_seconds = (dt.now() - self.history_last_update).total_seconds()
            if age_seconds > 60:
                logger.warning(f"⚠️ 历史仓位缓存已过期 ({age_seconds:.0f}秒)，可能不准确")
        else:
            logger.warning("⚠️ 历史仓位缓存未初始化")

        return self.cached_historical_positions, self.cached_performance_stats

    def get_current_positions(self) -> dict:
        """
        获取当前持仓信息

        Returns:
            持仓信息字典
        """
        try:
            result = self.position_api.get_contract_positions(inst_type='SWAP', inst_id=self.inst_id)

            if result['code'] != '0':
                logger.warning(f"获取持仓失败: {result.get('msg')}")
                return {'positions': []}

            positions = result.get('data', [])

            # 过滤掉数量为0的持仓
            active_positions = [p for p in positions if float(p.get('pos', 0)) != 0]

            return {'positions': active_positions}

        except Exception as e:
            logger.error(f"获取持仓异常: {e}")
            return {'positions': []}

    def check_data_freshness(self, features: dict, timeframe: str) -> tuple[bool, str]:
        """
        检查数据新鲜度，避免基于滞后数据做决策

        Args:
            features: 特征数据
            timeframe: K线周期

        Returns:
            (is_fresh, reason)
        """
        now_ms = int(datetime.now().timestamp() * 1000)
        threshold_ms = self.data_freshness_threshold * 1000

        issues = []

        # 1. 检查K线数据新鲜度（使用实际更新时间，而非K线timestamp）
        kline_last_update = self.data_manager.get_kline_last_update(self.inst_id, timeframe)
        if kline_last_update:
            lag_seconds = (now_ms - kline_last_update) / 1000

            if lag_seconds > self.data_freshness_threshold:
                issues.append(f"K线数据滞后 {lag_seconds:.0f} 秒")
                logger.warning(
                    f"⚠️ K线数据滞后: {lag_seconds:.0f}秒 > 阈值 {self.data_freshness_threshold}秒 "
                    f"(最后更新: {datetime.fromtimestamp(kline_last_update/1000).strftime('%H:%M:%S')})"
                )
        else:
            issues.append("K线数据无更新记录")
            logger.warning("⚠️ K线数据无更新记录（可能是采集器未启动或Redis连接失败）")

        # 2. 检查订单簿新鲜度
        orderbook_raw = features.get('orderbook_raw', {})
        if orderbook_raw and orderbook_raw.get('timestamp'):
            orderbook_ts = orderbook_raw['timestamp']
            lag_seconds = (now_ms - orderbook_ts) / 1000

            if lag_seconds > self.data_freshness_threshold:
                issues.append(f"订单簿数据滞后 {lag_seconds:.0f} 秒")
                logger.warning(
                    f"⚠️ 订单簿数据滞后: {lag_seconds:.0f}秒 > 阈值 {self.data_freshness_threshold}秒"
                )

        # 3. 检查成交压力数据新鲜度
        trades = features.get('trades', {})
        if trades:
            # 检查最新的压力数据（1分钟）
            pressure_1m = self.data_manager.get_latest_pressure(self.inst_id, 60)
            if pressure_1m:
                pressure_ts = pressure_1m['timestamp']
                lag_seconds = (now_ms - pressure_ts) / 1000

                if lag_seconds > self.data_freshness_threshold:
                    issues.append(f"成交压力数据滞后 {lag_seconds:.0f} 秒")
                    logger.warning(
                        f"⚠️ 成交压力数据滞后: {lag_seconds:.0f}秒 > 阈值 {self.data_freshness_threshold}秒"
                    )

        # 汇总结果
        if issues:
            reason = "数据滞后: " + ", ".join(issues)
            return False, reason

        return True, "数据新鲜度检查通过"


    async def run_analysis(self) :
        """
        运行一次增强分析（双周期版本：5m + 4H）

        Returns:
            分析结果
        """
        logger.info("=" * 60)
        logger.info(f" {self.inst_id}")
        logger.info(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)

        # 提取双周期特征
        features = self.feature_engineer.extract_dual_timeframe_features(self.inst_id)

        if not features or not features.get('short_term'):
            logger.error("❌ 特征提取失败，数据不足")
            return {'signal': 'HOLD', 'reason': '数据不足', 'success': False}

        # 检查数据新鲜度（使用5m数据）
        is_fresh, freshness_reason = self.check_data_freshness(features, '5m')
        if not is_fresh:
            logger.error(f"❌ {freshness_reason}")
            logger.warning("⚠️ 数据滞后过多，跳过本次分析以避免错误决策")
            return {'signal': 'HOLD', 'reason': freshness_reason, 'success': False}

        logger.info(f"✓ {freshness_reason}")

        # 显示特征摘要
        self.display_dual_timeframe_features(features)

        # 获取当前持仓信息（从缓存）
        position_info = {'positions': self.get_cached_positions()}

        # AI分析
        if self.ai_client:
            analysis = await self.ai_analysis(features, position_info)
        else:
            # 回退到规则分析
            analysis = self.rule_based_analysis(features)

        # 显示分析结果
        
        self.display_analysis(analysis)

        
    def run_conversation(self):
        # 处理交易信号
        signal = self.analysis.get('signal')

        if self.auto_execute and signal not in ['HOLD', 'HOLD_LONG', 'HOLD_SHORT']:
            # 执行交易（开仓/平仓/调整止盈止损）
            #self.execute_trade(analysis)
            try:
                asyncio.run(self.execute_trade())
            except Exception as e:
                logger.error(f'execute_trade 执行错误:{e}')
        else:
            # HOLD信号或非自动执行模式：保存对话记录但不交易
            if signal in ['HOLD', 'HOLD_LONG', 'HOLD_SHORT']:
                logger.info(f"💤 信号为 {signal}，不执行交易")
            elif not self.auto_execute:
                logger.info(f"📋 信号为 {signal}，但未开启自动执行")
            
        return self.analysis
    def display_dual_timeframe_features(self, features: dict):
        """显示双周期特征摘要"""
        short_term = features.get('short_term', {})
        long_term = features.get('long_term', {})
        kline_raw_5m = features.get('kline_raw_5m', [])
        orderbook_raw = features.get('orderbook_raw', {})

        logger.info("\n📈 短期指标（5分钟）:")
        logger.info(f"  价格: {short_term.get('current_price', 0):.2f} USDT")

        # 短期指标（取列表第一个元素）
        ema_20_short = short_term.get('ema_20', [0])
        rsi_7_short = short_term.get('rsi_7', [0])
        rsi_14_short = short_term.get('rsi_14', [0])

        logger.info(f"  EMA20: {ema_20_short[0] if ema_20_short else 0:.2f}")
        logger.info(f"  RSI(7/14): {rsi_7_short[0] if rsi_7_short else 0:.2f} / {rsi_14_short[0] if rsi_14_short else 0:.2f}")

        # MACD柱状图（取列表第一个元素）
        macd_hist_short = short_term.get('macd_histogram', [0])
        logger.info(f"  MACD柱状图: {macd_hist_short[0] if macd_hist_short else 0:.2f}")

        if kline_raw_5m:
            logger.info(f"\n📊 原始K线: 最近{len(kline_raw_5m)}根（供AI分析形态）")

        logger.info("\n📊 长期趋势（4小时）:")

        # 长期指标（取列表第一个元素）
        ema_20_long = long_term.get('ema_20', [0])
        atr_3_long = long_term.get('atr_3', [0])
        volume_ratio = long_term.get('volume_ratio', 1.0)  # 这是单个值
        macd_hist_long = long_term.get('macd_histogram', [0])
        rsi_14_long = long_term.get('rsi_14', [0])

        logger.info(f"  EMA20: {ema_20_long[0] if ema_20_long else 0:.2f}")
        logger.info(f"  ATR(3): {atr_3_long[0] if atr_3_long else 0:.2f}")
        logger.info(f"  成交量比: {volume_ratio:.2f} ({'放量' if volume_ratio > 1.2 else '缩量' if volume_ratio < 0.8 else '正常'})")
        logger.info(f"  MACD柱状图: {macd_hist_long[0] if macd_hist_long else 0:.2f}")
        logger.info(f"  RSI(14): {rsi_14_long[0] if rsi_14_long else 0:.2f}")

        if orderbook_raw and orderbook_raw.get('bids'):
            logger.info(f"\n📖 订单簿: {len(orderbook_raw.get('bids', []))}档买盘/{len(orderbook_raw.get('asks', []))}档卖盘")

    def _aggregate_tick_features_realtime(self) -> dict:
        """
        实时聚合 tick 特征（从 Redis 获取 60 秒成交数据并聚合）

        Returns:
            tick_features 字典，包含 VWAP、买卖量失衡、价格波动等指标
        """
        try:
            import time
            ts = time.time()

            # 从 Redis 获取过去 60 秒的 tick 数据
            ticks = self.data_manager.get_recent_trades_from_redis(self.inst_id, seconds=60)

            if not ticks or len(ticks) == 0:
                logger.debug(f"⚠️ {self.inst_id}: 过去60秒无tick数据，返回空特征")
                return {}

            # 提取价格和成交量
            prices = [t['price'] for t in ticks]
            volumes = [t['size'] for t in ticks]

            # 1. 计算VWAP（成交量加权平均价）
            total_volume = sum(volumes)
            vwap = sum(p * v for p, v in zip(prices, volumes)) / total_volume if total_volume > 0 else 0

            # 2. 计算买卖量失衡
            buy_volume = sum(t['size'] for t in ticks if t['side'] == 'buy')
            sell_volume = sum(t['size'] for t in ticks if t['side'] == 'sell')
            volume_imbalance = buy_volume / sell_volume if sell_volume > 0 else (float('inf') if buy_volume > 0 else 0)

            # 3. 计算价格波动范围
            max_price = max(prices)
            min_price = min(prices)
            price_range = max_price - min_price

            # 4. Tick数量
            tick_count = len(ticks)

            # 5. 大单比例（定义大单为成交量大于平均成交量的2倍）
            avg_volume = total_volume / tick_count if tick_count > 0 else 0
            large_trade_threshold = avg_volume * 2
            large_trades = sum(1 for t in ticks if t['size'] > large_trade_threshold)
            large_trade_ratio = large_trades / tick_count if tick_count > 0 else 0

            # 使用最新tick的timestamp
            latest_timestamp = max(t['timestamp'] for t in ticks)

            # 构建特征向量
            features = {
                'timestamp': latest_timestamp,
                'vwap': vwap,
                'volume_imbalance': volume_imbalance,
                'price_range': price_range,
                'tick_count': tick_count,
                'large_trade_ratio': large_trade_ratio,
                'buy_volume': buy_volume,
                'sell_volume': sell_volume,
                'total_volume': total_volume,
                'max_price': max_price,
                'min_price': min_price,
                'avg_volume': avg_volume
            }

            logger.debug(
                f"✓ 实时聚合Tick特征: {self.inst_id} | "
                f"VWAP={vwap:.2f}, 买卖失衡={volume_imbalance:.2f}, "
                f"价格范围={price_range:.2f}, Tick数={tick_count}, "
                f"大单比例={large_trade_ratio:.2%}, "
                f"耗时={time.time()-ts:.3f}s"
            )

            return features

        except Exception as e:
            logger.error(f"实时聚合Tick特征失败: {e}")
            import traceback
            traceback.print_exc()
            return {}

    async def ai_analysis(self, features: dict, position_info: dict = None) -> dict:
        """AI分析（流式优化版：当捕捉到reason字段时立即启动决策，不等reason完整输出）"""
        try:
            # 获取缓存的历史仓位数据
            cached_historical_positions, cached_performance_stats = self.get_cached_historical_data()

            # 获取缓存的市场数据
            cached_taker_volume, cached_open_interest = self.get_cached_market_data()

            # 实时获取并聚合 tick 特征数据（从 60 秒成交数据）
            tick_features = self._aggregate_tick_features_realtime()

            # 计算机器人已运行时长（分钟）
            bot_runtime_minutes = int((datetime.now() - self.bot_start_time).total_seconds() / 60)

            # 生成当前的Prompt（传递余额、合约信息、止盈止损订单、历史决策、历史仓位、资金费率、市场数据、tick特征、运行时长）
            # 返回(system_prompt, user_prompt)元组
            system_prompt, user_prompt = self.feature_engineer.generate_ai_prompt_with_raw(
                self.inst_id,
                features,
                position_info,
                self.leverage,
                available_balance=self.get_cached_balance(),
                instrument_info=self.instrument_info,
                stop_orders=self.get_cached_stop_orders(),
                ai_decision_history=self.ai_decision_history,  # ✅ 传递历史决策
                historical_positions=cached_historical_positions,  # ✅ 传递缓存的历史仓位
                performance_stats=cached_performance_stats,  # ✅ 传递缓存的统计数据
                funding_rate=self.get_cached_funding_rate(),  # ✅ 传递缓存的资金费率
                taker_volume=cached_taker_volume,  # ✅ 传递主动买卖数据
                open_interest=cached_open_interest,  # ✅ 传递持仓量数据
                tick_features=tick_features,  # ✅ 传递 tick 特征聚合数据
                bot_runtime_minutes=bot_runtime_minutes  # ✅ 传递运行时长
            )

            # 构建messages（system + user，历史决策已在user_prompt中）
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # 显示历史决策信息
            if self.ai_decision_history:
                logger.info(f"📜 使用历史决策上下文: {len(self.ai_decision_history)} 个决策")

            # 调用AI (流式JSON模式)
            ai_provider = config.AI_PROVIDER
            logger.info(f"🤖 正在调用 {ai_provider.upper()} AI分析（流式模式）...")
            result = self.ai_client.chat_completion(
                messages=messages,
                temperature=0.5,
                use_json_mode=True,
                stream=True,  # ⚡ 启用流式输出
                timeout=None,
                session_id=self.session_id
            )

            # 检查响应成功性
            if not result.get('success'):
                error_type = result.get('error', 'unknown')
                logger.warning(f"⚠️ AI调用失败: {error_type}")
                return {
                    'signal': 'HOLD',
                    'confidence': 0,
                    'reason': f'AI调用失败: {error_type}',
                    'success': False,
                    'timeout': True
                }

            # === 流式解析JSON ===
            streaming_buffer = ""
            early_decision_triggered = False
            early_decision_data = None

            for chunk in result.get('stream', []):
                # 提取增量内容
                try:
                    delta_content = chunk['choices'][0]['delta'].get('content', '')
                    if delta_content:
                        streaming_buffer += delta_content
                        print(delta_content,end='')
                        # 尝试提取早期决策（当检测到reason字段时）
                        if not early_decision_triggered:
                            early_decision = self._try_parse_early_decision(streaming_buffer)
                            if early_decision:
                                early_decision_triggered = True
                                early_decision_data = early_decision
                                self.analysis = self.parse_ai_json_response(early_decision, features)
                                # 启动后台线程保存
                                self.executor_.submit(self.run_conversation)

                except (KeyError, IndexError) as e:
                    logger.debug(f'ai响应提取错误:{str(e)}')
                    continue

            # 流式输出完成，解析完整响应

            try:
                # 清理可能的markdown标记
                clean_buffer = streaming_buffer.replace('```json', '').replace('```', '').strip()
                complete_response = json.loads(clean_buffer)

                logger.success(f"✓ 完整JSON解析成功: {complete_response.get('signal')}")

            except json.JSONDecodeError as e:
                logger.error(f"❌ 完整JSON解析失败: {e}")
                logger.debug(f"原始响应: {streaming_buffer[:500]}")
                # 如果完整解析失败，但早期决策已提取，仍可继续
                if not early_decision_triggered:
                    return {
                        'signal': 'HOLD',
                        'confidence': 0,
                        'reason': 'JSON解析失败',
                        'success': False
                    }
                else:
                    # 使用早期决策数据
                    complete_response = early_decision_data
                    complete_response['reason'] = '[流式解析失败，使用早期决策]'

            # 解析AI响应（JSON格式）
            self.analysis = self.parse_ai_json_response(complete_response, features)

            # 保存完整决策到历史（包含完整reason）
            

            # 添加元数据
            self.analysis['_user_prompt'] = user_prompt
            self.analysis['_ai_content'] = streaming_buffer
            self.analysis['_features'] = features
            self.analysis['_early_execution'] = early_decision_triggered  # 标记是否使用了早期执行
            self._save_conversation_async(self.analysis,False)

            return self.analysis

        except Exception as e:
            logger.error(f"AI分析失败: {e}，回退到规则分析")
            import traceback
            logger.debug(f"错误堆栈: {traceback.format_exc()}")
            return {
                'signal': 'HOLD',
                'confidence': 0,
                'reason': 'AI分析失败',
                'success': False,
                'timeout': True
            }

    def _try_parse_early_decision(self, buffer: str) -> dict:
        """
        尝试从缓冲区中提取早期决策数据（不包含完整reason）

        检测逻辑：
        1. 如果缓冲区中出现 "reason" 字段（但可能未完整）
        2. 提取除reason之外的所有必要字段
        3. 构建不包含reason的JSON用于早期决策

        Returns:
            不包含完整reason的决策JSON，如果无法解析则返回None
        """
        # 检查是否包含"reason"关键字
        if '"reason"' not in buffer:
            return None

        try:
            # 清理markdown标记
            clean_buffer = buffer.replace('```json', '').replace('```', '').strip()

            # 找到"reason"字段的位置
            reason_match = re.search(r'"reason"\s*:\s*"', clean_buffer)
            if not reason_match:
                return None

            reason_start = reason_match.start()

            # 提取"reason"之前的所有内容
            before_reason = clean_buffer[:reason_start].rstrip(',\n\r\t ')

            # 尝试闭合JSON（添加}）
            if not before_reason.endswith('}'):
                before_reason += '}'

            # 尝试解析
            try:
                early_json = json.loads(before_reason)

                # 验证必需字段是否存在
                required_fields = ['signal', 'confidence']
                if all(field in early_json for field in required_fields):
                    logger.debug(f"✓ 早期决策JSON解析成功: {early_json.get('signal')}")
                    return early_json
                else:
                    return None

            except json.JSONDecodeError:
                # 方法2：手动提取关键字段（regex fallback）
                early_json = {}

                # 提取signal
                signal_match = re.search(r'"signal"\s*:\s*"([^"]+)"', before_reason)
                if signal_match:
                    early_json['signal'] = signal_match.group(1)

                # 提取confidence
                conf_match = re.search(r'"confidence"\s*:\s*(\d+)', before_reason)
                if conf_match:
                    early_json['confidence'] = int(conf_match.group(1))

                # 提取size（如果有）
                size_match = re.search(r'"size"\s*:\s*([\d.]+)', before_reason)
                if size_match:
                    early_json['size'] = float(size_match.group(1))

                # 提取stop_loss_rate
                sl_match = re.search(r'"stop_loss_rate"\s*:\s*([\d.]+)', before_reason)
                if sl_match:
                    early_json['stop_loss_rate'] = float(sl_match.group(1))

                # 提取take_profit_rate
                tp_match = re.search(r'"take_profit_rate"\s*:\s*([\d.]+)', before_reason)
                if tp_match:
                    early_json['take_profit_rate'] = float(tp_match.group(1))

                # 提取holding_time
                ht_match = re.search(r'"holding_time"\s*:\s*"([^"]+)"', before_reason)
                if ht_match:
                    early_json['holding_time'] = ht_match.group(1)

                # 提取adjust_type（如果有）
                at_match = re.search(r'"adjust_type"\s*:\s*"([^"]+)"', before_reason)
                if at_match:
                    early_json['adjust_type'] = at_match.group(1)

                # 提取new_stop_loss_price（如果有）
                nsl_match = re.search(r'"new_stop_loss_price"\s*:\s*([\d.]+)', before_reason)
                if nsl_match:
                    early_json['new_stop_loss_price'] = float(nsl_match.group(1))

                # 提取new_take_profit_price（如果有）
                ntp_match = re.search(r'"new_take_profit_price"\s*:\s*([\d.]+)', before_reason)
                if ntp_match:
                    early_json['new_take_profit_price'] = float(ntp_match.group(1))

                # 验证必需字段
                if 'signal' in early_json and 'confidence' in early_json:
                    logger.debug(f"✓ 早期决策手动提取成功: {early_json.get('signal')}")
                    return early_json
                else:
                    return None

        except Exception as e:
            logger.debug(f"早期决策提取失败: {e}")
            return None

    def _save_complete_decision(self, response: dict):
        """
        保存完整的决策JSON到历史（包含完整reason）

        Args:
            response: 完整的AI响应JSON
        """
        try:
            # 保存到历史决策（内存）
            self.ai_decision_history.append({
                'content': json.dumps(response, ensure_ascii=False),
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })

            # 只保留最近10条
            if len(self.ai_decision_history) > 10:
                self.ai_decision_history.pop(0)

            # 保存到文件
            self._save_decision_history()

            logger.info(f"💾 完整决策已保存到本地（reason: {len(response.get('reason', ''))} 字符）")

        except Exception as e:
            logger.error(f"❌ 保存完整决策失败: {e}")

    def parse_ai_json_response(self, response: dict, features: dict) -> dict:
        """解析AI的JSON响应（统一使用adjust_data）"""
        try:
            # 直接从JSON提取字段
            signal = response.get('signal', 'HOLD')

            analysis = {
                'signal': signal,
                'confidence': int(response.get('confidence', 50)),
                'reason': response.get('reason', ''),
                'holding_time': response.get('holding_time', '1小时'),
                'risk_warning': response.get('risk_warning', ''),
                'success': True,
                'features': features
            }

            # 开仓信号需要提取size
            if signal in ['OPEN_LONG', 'OPEN_SHORT']:
                size = response.get('size')
                if size:
                    analysis['size'] = float(size)
                    logger.info(f"✓ AI建议开仓数量: {size}张")
                else:
                    logger.warning("⚠️ AI未提供size参数，将在执行时计算")

            # 提取adjust_data（开仓和调整止盈止损都需要）
            if signal in ['OPEN_LONG', 'OPEN_SHORT', 'ADJUST_STOP']:
                adjust_data = response.get('adjust_data')
                if adjust_data:
                    analysis['adjust_data'] = adjust_data

                    tp_count = len(adjust_data.get('take_profit', []))
                    sl_count = len(adjust_data.get('stop_loss', []))
                    logger.info(f"✓ AI提供止盈止损: {tp_count}层止盈, {sl_count}层止损")
                else:
                    if signal in ['OPEN_LONG', 'OPEN_SHORT']:
                        logger.warning("⚠️ AI未提供adjust_data，开仓后将无止盈止损保护")
                    elif signal == 'ADJUST_STOP':
                        logger.error("❌ ADJUST_STOP信号缺少adjust_data字段")

            logger.info(f"✓ JSON解析成功: {signal} (置信度 {analysis['confidence']}%)")
            return analysis

        except Exception as e:
            logger.error(f"JSON响应解析失败: {e}")
            return self.rule_based_analysis(features)

    def rule_based_analysis(self, features: dict) -> dict:
        """基于规则的分析（AI不可用时，返回adjust_data格式）"""
        score = features.get('score', {})
        kline = features.get('kline', {})
        trades = features.get('trades', {})

        signal = score.get('signal', 'HOLD')
        total_score = score.get('total_score', 0)

        # 转换信号格式
        if signal == 'STRONG_BUY' or signal == 'BUY':
            signal = 'OPEN_LONG'
            confidence = min(85, 50 + abs(total_score))
        elif signal == 'STRONG_SELL' or signal == 'SELL':
            signal = 'OPEN_SHORT'
            confidence = min(85, 50 + abs(total_score))
        else:
            signal = 'HOLD'
            confidence = 50

        analysis = {
            'signal': signal,
            'confidence': confidence,
            'reason': f"规则分析：综合评分{total_score}分，压力趋势{trades.get('pressure_trend', 'N/A')}",
            'holding_time': '1小时',
            'success': True,
            'features': features
        }

        # 为开仓信号构建adjust_data
        if signal in ['OPEN_LONG', 'OPEN_SHORT']:
            current_price = features.get('short_term', {}).get('current_price', 0)

            if current_price > 0:
                # 根据ATR调整止损止盈
                atr_pct = kline.get('atr_pct', 1.0)
                volatility_factor = min(2.0, max(0.5, atr_pct / 1.0))

                # 计算价格百分比（账户盈亏比例 / 杠杆 = 价格变动比例）
                base_stop_loss_rate = 0.10  # 10%账户亏损
                base_take_profit_rate = 0.30  # 30%账户盈利

                stop_loss_rate = max(0.05, min(0.20, base_stop_loss_rate * volatility_factor))
                take_profit_rate = max(0.10, min(0.50, base_take_profit_rate * volatility_factor))

                # 转换为价格变动比例
                price_sl_pct = stop_loss_rate / self.leverage
                price_tp_pct = take_profit_rate / self.leverage

                # 计算止盈止损价格
                if signal == 'OPEN_LONG':
                    stop_loss_price = current_price * (1 - price_sl_pct)
                    take_profit_price = current_price * (1 + price_tp_pct)
                else:  # OPEN_SHORT
                    stop_loss_price = current_price * (1 + price_sl_pct)
                    take_profit_price = current_price * (1 - price_tp_pct)

                # 注意：size 将在 execute_open 中填充
                analysis['adjust_data'] = {
                    'take_profit': [{'size': None, 'price': round(take_profit_price, 2)}],
                    'stop_loss': [{'size': None, 'price': round(stop_loss_price, 2)}]
                }

                logger.info(f"✓ 规则分析生成单层止盈止损: TP={take_profit_price:.2f}, SL={stop_loss_price:.2f}")

        return analysis

    def display_analysis(self, analysis: dict):
        """显示分析结果"""
        signal = analysis.get('signal', 'HOLD')
        confidence = analysis.get('confidence', 0)
        reason = analysis.get('reason', '')
        risk_warning = analysis.get('risk_warning', '')

        # 信号图标
        signal_icons = {
            'OPEN_LONG': '🟢',
            'OPEN_SHORT': '🔴',
            'CLOSE_LONG': '🟡',
            'CLOSE_SHORT': '🟠',
            'ADJUST_STOP': '🔧',
            'HOLD': '⚪'
        }

        icon = signal_icons.get(signal, '❓')

        logger.info(f"\n{icon} 交易信号: {signal}")
        logger.info(f"置信度: {confidence}%")
        logger.info(f"理由: {reason[:200]}...")

        if risk_warning:
            logger.warning(f"⚠️ 风险提示: {risk_warning}")

        # 显示adjust_data
        adjust_data = analysis.get('adjust_data')
        if adjust_data:
            take_profit = adjust_data.get('take_profit', [])
            stop_loss = adjust_data.get('stop_loss', [])

            logger.info(f"\n📐 止盈止损计划:")

            if take_profit:
                logger.info(f"  止盈（{len(take_profit)}层）:")
                for i, layer in enumerate(take_profit, 1):
                    size_str = f"{layer['size']:.4f}" if layer.get('size') else "待定"
                    logger.info(f"    #{i}: {size_str}张 @ {layer['price']:.2f}")

            if stop_loss:
                logger.info(f"  止损（{len(stop_loss)}层）:")
                for i, layer in enumerate(stop_loss, 1):
                    size_str = f"{layer['size']:.4f}" if layer.get('size') else "待定"
                    logger.info(f"    #{i}: {size_str}张 @ {layer['price']:.2f}")

            logger.info(f"  预期持仓: {analysis.get('holding_time', 'N/A')}")

    def validate_adjust_data(self, adjust_data: dict, total_size: float) -> tuple[bool, str]:
        """
        校验adjust_data的合法性

        Returns:
            (is_valid, error_message)
        """
        take_profit = adjust_data.get('take_profit', [])
        stop_loss = adjust_data.get('stop_loss', [])

        # 1. 至少有一个
        if not take_profit and not stop_loss:
            return False, "adjust_data至少需要包含take_profit或stop_loss"

        # 2. 校验size总和
        if take_profit:
            tp_total = sum(layer.get('size', 0) for layer in take_profit)
            if abs(tp_total - total_size) > 0.001:
                return False, f"止盈size总和({tp_total:.4f}) ≠ 持仓({total_size:.4f})"

        if stop_loss:
            sl_total = sum(layer.get('size', 0) for layer in stop_loss)
            if abs(sl_total - total_size) > 0.001:
                return False, f"止损size总和({sl_total:.4f}) ≠ 持仓({total_size:.4f})"

        # 3. 校验字段完整性
        for layer in take_profit + stop_loss:
            if 'size' not in layer or 'price' not in layer:
                return False, "每层必须包含size和price字段"
            if layer.get('size', 0) <= 0 or layer.get('price', 0) <= 0:
                return False, "size和price必须大于0"

        return True, ""

    def _display_adjust_data(self, adjust_data: dict, current_price: float):
        """显示adjust_data摘要"""
        take_profit = adjust_data.get('take_profit', [])
        stop_loss = adjust_data.get('stop_loss', [])

        logger.info("\n📐 止盈止损设置:")
        logger.info(f"  当前价格: {current_price:.2f}")

        if take_profit:
            logger.info(f"  止盈（{len(take_profit)}层）:")
            for i, layer in enumerate(take_profit, 1):
                pct = ((layer['price'] - current_price) / current_price) * 100
                logger.info(f"    #{i}: {layer['size']:.4f}张 @ {layer['price']:.2f} ({pct:+.2f}%)")

        if stop_loss:
            logger.info(f"  止损（{len(stop_loss)}层）:")
            for i, layer in enumerate(stop_loss, 1):
                pct = ((layer['price'] - current_price) / current_price) * 100
                logger.info(f"    #{i}: {layer['size']:.4f}张 @ {layer['price']:.2f} ({pct:+.2f}%)")

    async def _apply_adjust_data(self, pos_side: str, adjust_data: dict):
        """
        应用adjust_data到指定仓位

        流程：
        1. 撤销所有现有订单
        2. 创建止盈订单（限价挂单）
        3. 创建止损订单（策略委托）
        """
        take_profit_layers = adjust_data.get('take_profit', [])
        stop_loss_layers = adjust_data.get('stop_loss', [])

        # 1. 撤销所有现有订单
        logger.info("  撤销现有订单...")
        await self._cancel_all_position_orders(pos_side)

        # 2. 创建止盈订单（限价挂单，maker手续费）
        if take_profit_layers:
            logger.info(f"  创建{len(take_profit_layers)}层止盈...")
            await self._create_take_profit_layers(pos_side, take_profit_layers)

        # 3. 创建止损订单（策略委托，保证执行）
        if stop_loss_layers:
            logger.info(f"  创建{len(stop_loss_layers)}层止损...")
            await self._create_stop_loss_layers(pos_side, stop_loss_layers)

        logger.success(
            f"✅ 止盈止损已设置：\n"
            f"  止盈：{len(take_profit_layers)}层（限价单）\n"
            f"  止损：{len(stop_loss_layers)}层（策略单）"
        )

    async def _cancel_all_position_orders(self, pos_side: str):
        """撤销指定仓位的所有订单（限价单+策略单）"""

        # 1. 撤销普通限价单
        try:
            pending_orders = self.trade_api.get_orders_pending(
                inst_id=self.inst_id,
                state='live'
            )

            if pending_orders['code'] == '0':
                for order in pending_orders['data']:
                    if order.get('posSide') == pos_side:
                        self.trade_api.cancel_order(
                            inst_id=self.inst_id,
                            ord_id=order['ordId']
                        )
                        logger.debug(f"  ✓ 撤销限价单: {order['ordId']}")
        except Exception as e:
            logger.error(f"  ❌ 撤销限价单异常: {e}")

        # 2. 撤销策略委托单
        try:
            algo_orders = self.trade_api.get_algo_order_list(
                ord_type='conditional',
                inst_id=self.inst_id
            )

            if algo_orders['code'] == '0':
                for order in algo_orders['data']:
                    if order.get('posSide') == pos_side:
                        self.trade_api.cancel_algo_order([{
                            'algoId': order['algoId'],
                            'instId': self.inst_id
                        }])
                        logger.debug(f"  ✓ 撤销策略单: {order['algoId']}")
        except Exception as e:
            logger.error(f"  ❌ 撤销策略单异常: {e}")

    async def _create_take_profit_layers(self, pos_side: str, layers: list):
        """创建分层止盈订单（限价挂单）"""
        close_side = 'sell' if pos_side == 'long' else 'buy'

        for i, layer in enumerate(layers):
            size = layer['size']
            price = layer['price']

            result = self.trade_api.place_order(
                inst_id=self.inst_id,
                td_mode='cross',
                side=close_side,
                ord_type='limit',
                px=str(price),
                sz=str(size),
                posSide=pos_side,
                reduce_only=True
            )

            if result['code'] == '0':
                ord_id = result['data'][0]['ordId']
                logger.debug(f"  ✓ 止盈#{i+1}: {size:.4f}张 @ {price:.2f} (ID: {ord_id})")
            else:
                logger.error(f"  ❌ 止盈#{i+1}失败: {result.get('msg')}")

    async def _create_stop_loss_layers(self, pos_side: str, layers: list):
        """创建分层止损订单（策略委托）"""
        close_side = 'sell' if pos_side == 'long' else 'buy'

        for i, layer in enumerate(layers):
            size = layer['size']
            trigger_price = layer['price']

            result = self.trade_api.place_algo_order(
                inst_id=self.inst_id,
                td_mode='cross',
                side=close_side,
                ord_type='conditional',
                sz=str(size),
                posSide=pos_side,
                slTriggerPx=str(trigger_price),
                slOrdPx='-1'
            )

            if result['code'] == '0':
                algo_id = result['data'][0]['algoId']
                logger.debug(f"  ✓ 止损#{i+1}: {size:.4f}张 @ {trigger_price:.2f} (ID: {algo_id})")
            else:
                logger.error(f"  ❌ 止损#{i+1}失败: {result.get('msg')}")

    async def execute_trade(self ):
        """执行交易（支持开仓/调整止盈止损）"""
        signal = self.analysis.get('signal')
        confidence = self.analysis.get('confidence', 0)

 

        

        # ⚡ 先执行交易（提高效率），再保存对话记录
        current_price = self.analysis['features'].get('short_term', {}).get('current_price', 0)

        # 处理调整止盈止损信号
        if signal == 'ADJUST_STOP':
            await self.execute_adjust_stop(signal, confidence, current_price)
            return

        # 处理开仓信号
        if signal in ['OPEN_LONG', 'OPEN_SHORT']:
            await self.execute_open(signal, confidence, current_price)
            return

    def _save_conversation_async(self, analysis: dict, is_executed: bool = False):
        """
        异步保存AI对话记录（不阻塞主线程）

        Args:
            analysis: AI分析结果（包含临时存储的user_prompt等信息）
            is_executed: 是否已执行交易
        """
        # 提取临时存储的信息
        user_prompt = analysis.get('_user_prompt')  # 现在只保存user部分
        ai_content = analysis.get('_ai_content')
        features = analysis.get('_features')

        if not user_prompt or not ai_content:
            # 规则分析没有这些字段，跳过保存
            return

        # 在后台线程中保存（避免阻塞主线程）
        def save_task():
            try:
                # 如果是更新执行状态（is_executed=True）且已有conversation_id，则更新
                if is_executed and self.current_conversation_id:
                    # 更新现有记录的执行状态
                    success = self.data_manager.update_conversation_executed(
                        conversation_id=self.current_conversation_id,
                        is_executed=True,
                        api_key=config.API_KEY if config.API_KEY else 'default'
                    )
                    if success:
                        logger.debug(f"✓ 对话记录执行状态已更新: ID={self.current_conversation_id}")
                    else:
                        logger.warning(f"⚠️ 对话记录执行状态更新失败: ID={self.current_conversation_id}")
                    return

                # 否则创建新记录
                conv_id = self.data_manager.save_conversation(
                    session_id=self.session_id,
                    inst_id=self.inst_id,
                    prompt=user_prompt,  # 使用user_prompt
                    response=ai_content,
                    analysis=analysis,
                    is_executed=is_executed,
                    api_key=config.API_KEY if config.API_KEY else 'default'
                )

                # 记录会话ID
                self.current_conversation_id = conv_id

                # 更新内存缓存的AI决策历史（只保留最近10个response + 时间戳）
                # 去掉reason字段以减少token消耗
                from datetime import datetime
                beijing_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                # 解析ai_content，移除reason字段
                try:
                    import json
                    ai_dict = json.loads(ai_content)
                    # 移除reason字段（如果存在）
                    ai_dict.pop('reason', None)
                    # 移除risk_warning字段（也是冗长的文本）
                    ai_dict.pop('risk_warning', None)
                    # 重新序列化为JSON
                    ai_content_compact = json.dumps(ai_dict, ensure_ascii=False)
                except:
                    # 如果解析失败，使用原始内容
                    ai_content_compact = ai_content

                self.ai_decision_history.append({
                    "content": ai_content_compact,
                    "timestamp": beijing_time
                })

                # 保持最多10个历史决策
                if len(self.ai_decision_history) > 10:
                    self.ai_decision_history = self.ai_decision_history[-10:]

                logger.debug(f"✓ 对话记录已保存: ID={conv_id}, 历史决策数: {len(self.ai_decision_history)}")

                # 🔄 保存历史决策到文件（在同一个后台线程中执行）
                try:
                    # 确保data目录存在
                    data_dir = os.path.dirname(self.ai_decision_history_file)
                    if not os.path.exists(data_dir):
                        os.makedirs(data_dir, exist_ok=True)

                    # 只保留最近10条
                    history_to_save = self.ai_decision_history[-10:] if len(self.ai_decision_history) > 10 else self.ai_decision_history

                    # 写入文件
                    with open(self.ai_decision_history_file, 'w', encoding='utf-8') as f:
                        json.dump(history_to_save, f, ensure_ascii=False, indent=2)

                    logger.debug(f"✓ 历史决策已同步到文件: {len(history_to_save)} 条")

                except Exception as file_error:
                    logger.error(f"❌ 保存历史决策文件失败: {file_error}")

            except Exception as e:
                logger.error(f"❌ 保存对话记录失败: {e}")

        # 启动后台线程保存
        save_thread = threading.Thread(target=save_task, daemon=True, name="save-conversation")
        save_thread.start()

    async def execute_adjust_stop(self, signal: str, confidence: int, current_price: float):
        """执行调整止盈止损（统一使用adjust_data）"""

        adjust_data = self.analysis.get('adjust_data')
        if not adjust_data:
            logger.error("❌ ADJUST_STOP信号缺少adjust_data字段")
            return

        logger.info(f"🔧 执行止盈止损调整")

        # 获取当前持仓
        positions = self.get_cached_positions()
        if not positions:
            logger.warning("⚠️ 无持仓，跳过调整")
            return

        for pos in positions:
            pos_side = pos.get('posSide')
            total_size = abs(float(pos.get('pos', 0)))

            logger.info(f"  调整{pos_side}仓位（{total_size}张）")

            # 校验adjust_data
            is_valid, error_msg = self.validate_adjust_data(adjust_data, total_size)
            if not is_valid:
                logger.error(f"❌ adjust_data校验失败: {error_msg}")
                continue

            # 显示调整详情
            self._display_adjust_data(adjust_data, current_price)

            # 应用新的止盈止损
            await self._apply_adjust_data(pos_side, adjust_data)

            # 保存调整决策到数据库
            await self._save_adjust_decision_to_db(pos, pos_side)

        # 保存对话记录
        ts = time.time()
        while True:
            if time.time() - ts > 20:
                break
            if self.analysis.get('reason'):
                break

        self.send_feishu_notification(current_price)
        time.sleep(5)
        self._save_conversation_async(self.analysis, is_executed=True)

    async def _adjust_take_profit_layers(
        self, pos_side: str, td_mode: str, current_orders: list, new_layers: list
    ):
        """
        调整止盈层级（限价单）

        Args:
            pos_side: 仓位方向 (long/short)
            td_mode: 保证金模式 (cross/isolated)
            current_orders: 当前止盈订单列表
            new_layers: 新的止盈层级配置 [{'size': float, 'price': float}, ...]
        """
        try:
            # 1. 取消所有旧的止盈限价单
            if current_orders:
                logger.info(f"    取消旧的止盈订单 ({len(current_orders)}层)...")
                cancel_tasks = []
                for order in current_orders:
                    order_id = order.get('order_id')
                    if order_id:
                        cancel_tasks.append(self._cancel_limit_order(order_id))

                # 并发取消
                if cancel_tasks:
                    await asyncio.gather(*cancel_tasks, return_exceptions=True)
                    logger.info(f"    ✓ 已取消 {len(cancel_tasks)} 个旧止盈订单")

            # 2. 下新的多层止盈限价单
            logger.info(f"    下新的止盈订单 ({len(new_layers)}层)...")
            close_side = 'sell' if pos_side == 'long' else 'buy'

            place_tasks = []
            for i, layer in enumerate(new_layers, 1):
                size = layer['size']
                price = layer['price']

                # 创建限价单任务
                task = self._place_limit_order(
                    inst_id=self.inst_id,
                    td_mode=td_mode,
                    side=close_side,
                    pos_side=pos_side,
                    size=str(size),
                    price=str(price)
                )
                place_tasks.append(task)
                logger.debug(f"      Layer {i}: {size:.4f}张 @ {price:.2f}")

            # 并发下单
            if place_tasks:
                results = await asyncio.gather(*place_tasks, return_exceptions=True)
                success_count = sum(1 for r in results if isinstance(r, dict) and r.get('success'))
                logger.success(f"    ✅ 止盈订单下单完成: {success_count}/{len(new_layers)}层成功")

        except Exception as e:
            logger.error(f"    ❌ 调整止盈失败: {e}")
            import traceback
            traceback.print_exc()

    async def _adjust_stop_loss_layers(
        self, pos_side: str, td_mode: str, current_orders: list, new_layers: list
    ):
        """
        调整止损层级（条件单）

        Args:
            pos_side: 仓位方向 (long/short)
            td_mode: 保证金模式 (cross/isolated)
            current_orders: 当前止损订单列表
            new_layers: 新的止损层级配置 [{'size': float, 'price': float}, ...]
        """
        try:
            # 1. 取消所有旧的止损条件单
            if current_orders:
                logger.info(f"    取消旧的止损订单 ({len(current_orders)}层)...")
                cancel_tasks = []
                for order in current_orders:
                    algo_id = order.get('order_id')
                    if algo_id:
                        cancel_tasks.append(self._cancel_algo_order(algo_id))

                # 并发取消
                if cancel_tasks:
                    await asyncio.gather(*cancel_tasks, return_exceptions=True)
                    logger.info(f"    ✓ 已取消 {len(cancel_tasks)} 个旧止损订单")

            # 2. 下新的多层止损条件单
            logger.info(f"    下新的止损订单 ({len(new_layers)}层)...")
            close_side = 'sell' if pos_side == 'long' else 'buy'

            place_tasks = []
            for i, layer in enumerate(new_layers, 1):
                size = layer['size']
                trigger_price = layer['price']

                # 创建条件单任务
                task = self._place_conditional_order(
                    inst_id=self.inst_id,
                    td_mode=td_mode,
                    side=close_side,
                    pos_side=pos_side,
                    size=str(size),
                    trigger_price=str(trigger_price)
                )
                place_tasks.append(task)
                logger.debug(f"      Layer {i}: {size:.4f}张 @ {trigger_price:.2f}")

            # 并发下单
            if place_tasks:
                results = await asyncio.gather(*place_tasks, return_exceptions=True)
                success_count = sum(1 for r in results if isinstance(r, dict) and r.get('success'))
                logger.success(f"    ✅ 止损订单下单完成: {success_count}/{len(new_layers)}层成功")

        except Exception as e:
            logger.error(f"    ❌ 调整止损失败: {e}")
            import traceback
            traceback.print_exc()

    async def _place_limit_order(
        self, inst_id: str, td_mode: str, side: str, pos_side: str, size: str, price: str
    ) -> dict:
        """
        下限价单（止盈用）

        Returns:
            {'success': bool, 'order_id': str, 'error': str}
        """
        try:
            result = self.trade_api.place_order(
                inst_id=inst_id,
                td_mode=td_mode,
                side=side,
                pos_side=pos_side,
                ord_type='limit',
                sz=size,
                px=price
            )

            if result['code'] == '0':
                order_id = result['data'][0]['ordId']
                return {'success': True, 'order_id': order_id}
            else:
                return {'success': False, 'error': result.get('msg', 'Unknown error')}

        except Exception as e:
            return {'success': False, 'error': str(e)}

    async def _place_conditional_order(
        self, inst_id: str, td_mode: str, side: str, pos_side: str, size: str, trigger_price: str
    ) -> dict:
        """
        下条件单（止损用）

        Returns:
            {'success': bool, 'order_id': str, 'error': str}
        """
        try:
            result = self.trade_api.place_algo_order(
                inst_id=inst_id,
                td_mode=td_mode,
                side=side,
                pos_side=pos_side,
                ord_type='conditional',
                sz=size,
                slTriggerPx=trigger_price,
                slOrdPx='-1'  # 市价成交
            )

            if result['code'] == '0':
                algo_id = result['data'][0]['algoId']
                return {'success': True, 'order_id': algo_id}
            else:
                return {'success': False, 'error': result.get('msg', 'Unknown error')}

        except Exception as e:
            return {'success': False, 'error': str(e)}

    async def _cancel_limit_order(self, order_id: str):
        """取消限价单"""
        try:
            result = self.trade_api.cancel_order(
                inst_id=self.inst_id,
                ord_id=order_id
            )
            if result['code'] == '0':
                logger.debug(f"      ✓ 取消限价单成功: {order_id}")
            else:
                logger.warning(f"      ⚠️ 取消限价单失败: {result.get('msg')}")
        except Exception as e:
            logger.error(f"      ❌ 取消限价单异常: {e}")

    async def _amend_by_recreate(self, pos: dict, pos_side: str, sl_price: float, tp_price: float):
        """
        通过撤销重建方式修改止盈止损（回退方案）

        Args:
            pos: 持仓信息
            pos_side: 仓位方向
            sl_price: 止损价格
            tp_price: 止盈价格
        """
        try:
            pos_size = abs(float(pos.get('pos', 0)))
            td_mode = pos.get('mgnMode', 'cross')

            # 获取当前订单
            current_orders = self.cached_stop_orders.get(pos_side, {})
            sl_order = current_orders.get('stop_loss')
            tp_order = current_orders.get('take_profit')

            # 取消旧订单
            if sl_order and sl_order.get('algo_id'):
                await self._cancel_algo_order(sl_order['algo_id'])
            if tp_order and tp_order.get('algo_id') and tp_order.get('algo_id') != sl_order.get('algo_id'):
                await self._cancel_algo_order(tp_order['algo_id'])

            # 下新的OCO订单
            close_side = 'sell' if pos_side == 'long' else 'buy'

            result = self.trade_api.place_algo_order(
                inst_id=self.inst_id,
                td_mode=td_mode,
                side=close_side,
                ord_type='oco',
                sz=str(pos_size),
                posSide=pos_side,
                slTriggerPx=str(sl_price),
                slOrdPx='-1',  # 市价
                tpTriggerPx=str(tp_price),
                tpOrdPx=str(tp_price),  # 限价
            )

            if result['code'] == '0':
                algo_id = result['data'][0]['algoId']
                logger.success(
                    f"✅ 止盈止损已调整（撤销重建）！\n"
                    f"  新策略订单ID: {algo_id}\n"
                    f"  止损: {sl_price:.2f}\n"
                    f"  止盈: {tp_price:.2f}"
                )
            else:
                logger.error(f"❌ 撤销重建失败: {result.get('msg')}")

        except Exception as e:
            logger.error(f"❌ 撤销重建异常: {e}")
            import traceback
            traceback.print_exc()

    async def _create_new_oco_order(self, pos: dict, pos_side: str, sl_price: float, tp_price: float):
        """
        创建新的止盈止损OCO订单

        Args:
            pos: 持仓信息
            pos_side: 仓位方向
            sl_price: 止损价格
            tp_price: 止盈价格
        """
        try:
            pos_size = abs(float(pos.get('pos', 0)))
            td_mode = pos.get('mgnMode', 'cross')
            close_side = 'sell' if pos_side == 'long' else 'buy'

            logger.info(f"  创建新的OCO订单: 止损={sl_price:.2f}, 止盈={tp_price:.2f}")

            result = self.trade_api.place_algo_order(
                inst_id=self.inst_id,
                td_mode=td_mode,
                side=close_side,
                ord_type='oco',
                sz=str(pos_size),
                posSide=pos_side,
                slTriggerPx=str(sl_price),
                slOrdPx='-1',  # 市价
                tpTriggerPx=str(tp_price),
                tpOrdPx=str(tp_price),  # 限价
            )

            if result['code'] == '0':
                algo_id = result['data'][0]['algoId']
                logger.success(
                    f"✅ OCO订单已创建！\n"
                    f"  策略订单ID: {algo_id}\n"
                    f"  止损: {sl_price:.2f}\n"
                    f"  止盈: {tp_price:.2f}"
                )
            else:
                logger.error(f"❌ 创建OCO订单失败: {result.get('msg')}")

        except Exception as e:
            logger.error(f"❌ 创建OCO订单异常: {e}")
            import traceback
            traceback.print_exc()

    async def _cancel_algo_order(self, algo_id: str):
        """取消算法订单"""
        try:
            # cancel_algo_order 需要传入列表格式
            result = self.trade_api.cancel_algo_order([{
                'algoId': algo_id,
                'instId': self.inst_id
            }])
            if result['code'] == '0':
                logger.debug(f"✓ 取消订单成功: {algo_id}")
            else:
                logger.warning(f"⚠️ 取消订单失败: {result.get('msg')}")
        except Exception as e:
            logger.error(f"❌ 取消订单异常: {e}")

    async def _save_decision_to_db_async(self, signal: str):
        """
        异步保存AI决策到数据库（查询posId后保存）

        Args:
            signal: 交易信号 (OPEN_LONG/OPEN_SHORT)
        """
        def save_task():
            try:
                # 1. 等待仓位创建（0.5秒）
                time.sleep(0.5)

                # 2. 查询posId
                pos_side = 'long' if signal == 'OPEN_LONG' else 'short'
                positions = self.position_api.get_contract_positions(
                    inst_type='SWAP',
                    inst_id=self.inst_id
                )

                pos_id = None
                if positions['code'] == '0':
                    for pos in positions['data']:
                        if pos.get('posSide') == pos_side and float(pos.get('pos', 0)) != 0:
                            pos_id = str(pos.get('cTime'))
                            break

                if not pos_id:
                    logger.warning(f"⚠️ 未找到 {pos_side} 仓位的posId，跳过决策保存")
                    return

                # 3. 准备决策数据（新版：使用adjust_data）
                decision_data = {
                    'timestamp': datetime.now(),
                    'pos_id': pos_id,
                    'inst_id': self.inst_id,
                    'pos_side': pos_side,
                    'action': signal,
                    'size': self.analysis.get('size'),
                    'confidence': self.analysis.get('confidence'),
                    'adjust_data': self.analysis.get('adjust_data'),  # 新字段
                    'holding_time': self.analysis.get('holding_time'),
                    'reason': self.analysis.get('reason', '')
                }

                # 4. 保存到数据库
                record_id = self.data_manager.insert_ai_decision(
                    decision_data,
                    api_key=config.API_KEY if config.API_KEY else 'default'
                )

                if record_id:
                    # 提取adjust_data信息用于日志
                    adjust_data = decision_data.get('adjust_data')
                    tp_count = len(adjust_data.get('take_profit', [])) if adjust_data else 0
                    sl_count = len(adjust_data.get('stop_loss', [])) if adjust_data else 0

                    logger.info(
                        f"✓ AI决策已保存到数据库: ID={record_id}, posId={pos_id}, "
                        f"止盈{tp_count}层, 止损{sl_count}层"
                    )

            except Exception as e:
                logger.error(f"❌ 保存AI决策到数据库失败: {e}")
                import traceback
                logger.debug(traceback.format_exc())

        # 在后台线程执行
        thread = threading.Thread(target=save_task, daemon=True, name="save-decision-db")
        thread.start()

    async def _save_adjust_decision_to_db(self, pos: dict, pos_side: str):
        """保存调整止盈止损决策到数据库（新版：使用adjust_data）"""
        def save_task():
            try:
                pos_id = str(pos.get('cTime'))
                if not pos_id:
                    logger.warning("⚠️ posId不存在，跳过决策保存")
                    return

                # 准备决策数据
                # 保存对话记录
                ts = time.time()
                while True:
                    if time.time() - ts > 20:
                        break
                    if self.analysis.get('reason'):
                        break
                decision_data = {
                    'timestamp': datetime.now(),
                    'pos_id': pos_id,
                    'inst_id': self.inst_id,
                    'pos_side': pos_side,
                    'action': 'ADJUST_STOP',
                    'confidence': self.analysis.get('confidence'),
                    'adjust_data': self.analysis.get('adjust_data'),  # 新字段
                    'reason': self.analysis.get('reason', '')
                }

                # 保存到数据库
                record_id = self.data_manager.insert_ai_decision(
                    decision_data,
                    api_key=config.API_KEY if config.API_KEY else 'default'
                )

                if record_id:
                    # 提取adjust_data信息用于日志
                    adjust_data = decision_data.get('adjust_data')
                    tp_count = len(adjust_data.get('take_profit', [])) if adjust_data else 0
                    sl_count = len(adjust_data.get('stop_loss', [])) if adjust_data else 0

                    logger.info(
                        f"✓ 调整决策已保存: ID={record_id}, posId={pos_id}, "
                        f"止盈{tp_count}层, 止损{sl_count}层"
                    )

            except Exception as e:
                logger.error(f"❌ 保存调整决策失败: {e}")
                import traceback
                logger.debug(traceback.format_exc())

        thread = threading.Thread(target=save_task, daemon=True, name="save-adjust-decision")
        thread.start()

    async def execute_close(self, signal: str, confidence: int, current_price: float, analysis: dict):
        """执行平仓"""
        # 获取当前持仓
        position_info = self.get_current_positions()
        positions = position_info.get('positions', [])

        if not positions:
            logger.warning("⚠️ 无持仓，跳过平仓")
            return

        # 确定要平仓的方向
        target_pos_side = 'long' if signal == 'CLOSE_LONG' else 'short'

        # 查找对应方向的持仓
        target_position = None
        for pos in positions:
            if pos.get('posSide') == target_pos_side:
                target_position = pos
                break

        if not target_position:
            logger.warning(f"⚠️ 无{target_pos_side}持仓，跳过平仓")
            return

        # 获取持仓数量
        pos_size = abs(float(target_position.get('pos', 0)))
        avg_price = float(target_position.get('avgPx', 0))
        unrealized_pnl = float(target_position.get('upl', 0))

        logger.info(f"🔄 执行平仓: {signal}")
        logger.info(f"  持仓数量: {pos_size}张")
        logger.info(f"  开仓均价: {avg_price:.2f}")
        logger.info(f"  当前价格: {current_price:.2f}")
        logger.info(f"  未实现盈亏: {unrealized_pnl:.2f} USDT")

        # 平仓：多头平仓用sell，空头平仓用buy
        side = 'sell' if target_pos_side == 'long' else 'buy'

        # 调用智能平仓（注意：size必须是字符串）
        result = await self.executor.smart_close_position(
            inst_id=self.inst_id,
            pos_side=target_pos_side,
            size=str(pos_size)  # 转换为字符串
        )

        if result.get('success'):
            logger.success(f"✅ 平仓成功！盈亏: {unrealized_pnl:.2f} USDT")
            self.last_trade_time = datetime.now()

            # ⚡ 交易成功后，在后台异步保存对话记录
            self._save_conversation_async(analysis, is_executed=True)

        else:
            logger.error(f"❌ 平仓失败: {result.get('error')}")

            # ⚡ 交易失败，也保存对话记录
            self._save_conversation_async(analysis, is_executed=False)

    async def execute_open(self, signal: str, confidence: int, current_price: float):
        """执行开仓（统一使用adjust_data）"""

        # 1. 计算开仓数量
        usdt_balance = self.get_cached_balance()
        logger.info(f"💰 可用余额（缓存）: {usdt_balance:.2f} USDT")

        # 检查AI是否提供了size
        ai_size = self.analysis.get('size')

        if ai_size:
            size = ai_size
            logger.info(f"✓ 使用AI建议的开仓数量: {size}张")

            # 验证size的合法性
            if self.instrument_info:
                min_sz = float(self.instrument_info.get('minSz', 0.01))
                if size < min_sz:
                    logger.warning(f"⚠️ AI建议的size {size} < 最小下单量 {min_sz}，调整为 {min_sz}")
                    size = min_sz
        else:
            # AI未提供size，使用简单计算：10%可用余额
            logger.info(f"AI未提供size,不执行交易")
            return

        # 2. 检查adjust_data并补充size
        adjust_data = self.analysis.get('adjust_data')

        if adjust_data:
            # 补充size（AI可能只提供了price）
            for layer in adjust_data.get('take_profit', []):
                if layer.get('size') is None:
                    layer['size'] = size

            for layer in adjust_data.get('stop_loss', []):
                if layer.get('size') is None:
                    layer['size'] = size

            # 校验adjust_data
            is_valid, error_msg = self.validate_adjust_data(adjust_data, size)
            if not is_valid:
                logger.error(f"❌ adjust_data校验失败: {error_msg}")
                return

            logger.info(f"✓ adjust_data校验通过")
            self._display_adjust_data(adjust_data, current_price)
        else:
            logger.warning("⚠️ AI未提供adjust_data，开仓后无止盈止损保护")

        # 3. 执行开仓
        side = 'buy' if signal == 'OPEN_LONG' else 'sell'
        logger.info(f"🚀 执行开仓: {side.upper()} {size}张")

        # 调用智能开仓（不使用executor的自动止盈止损）
        result = await self.executor.smart_open_position(
            inst_id=self.inst_id,
            side=side,
            size=str(size),
            leverage=self.leverage,
            auto_stop_orders=False  # 不使用executor的自动止盈止损
        )

        if result.get('success'):
            logger.success(f"✅ 开仓成功！")
            self.last_trade_time = datetime.now()

            # 4. 如果有adjust_data，设置止盈止损
            if adjust_data:
                logger.info("🎯 正在设置止盈止损...")
                await asyncio.sleep(30)  # 等待仓位创建

                # 获取仓位信息
                positions = self.get_cached_positions()
                target_pos = None
                for pos in positions:
                    if pos.get('posSide') == ('long' if side == 'buy' else 'short'):
                        target_pos = pos
                        break

                if target_pos:
                    pos_side = target_pos.get('posSide')
                    await self._apply_adjust_data(pos_side, adjust_data)
                else:
                    logger.error("❌ 未找到新开仓位，无法设置止盈止损")

            # 5. 保存决策到数据库
            await self._save_decision_to_db_async(signal)

            # 6. 保存对话记录
            ts = time.time()
            while True:
                if time.time() - ts > 15:
                    self._save_conversation_async(self.analysis, is_executed=True)
                    self.send_feishu_notification(current_price)
                    break
                if self.analysis.get('reason'):
                    self._save_conversation_async(self.analysis, is_executed=True)
                    self.send_feishu_notification(current_price)
                    break

        else:
            logger.error(f"❌ 开仓失败: {result.get('error')}")

            # 保存对话记录
            ts = time.time()
            while True:
                if time.time() - ts > 15:
                    self._save_conversation_async(self.analysis, is_executed=False)
                    self.send_feishu_notification(current_price)
                    break
                if self.analysis.get('reason'):
                    self._save_conversation_async(self.analysis, is_executed=False)
                    self.send_feishu_notification(current_price)
                    break

    async def run_continuous(self, interval_seconds: float = 60):
        """持续监控模式（双周期版本）"""
        logger.info(f"  检查间隔: {interval_seconds}秒")
        

        # ⚡ 启动所有后台线程
        self.start_balance_update_thread()
        self.start_position_update_thread()
        self.start_stop_order_update_thread()
        self.start_position_history_thread()  # ✅ 启动历史仓位更新线程
        self.start_funding_rate_update_thread()  # ✅ 启动资金费率更新线程
        self.start_market_data_update_thread()  # ✅ 启动市场数据更新线程



        iteration = 0
        await asyncio.sleep(5)

        try:
            while True:
                iteration += 1
                logger.info(f"\n{'='*60}")
                logger.info(f"第 {iteration} 次扫描")
                logger.info(f"{'='*60}")

                await self.run_analysis()

                logger.info(f"\n⏳ 等待 {interval_seconds} 秒...")
                await asyncio.sleep(interval_seconds)

        except KeyboardInterrupt:
            logger.info("\n⚠️ 用户中断")
        except Exception as e:
            logger.error(f"❌ 监控错误: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 停止所有后台线程
            self.stop_balance_update_thread()
            self.stop_position_update_thread()
            self.stop_stop_order_update_thread()
            self.stop_position_history_thread()  # ✅ 停止历史仓位更新线程
            self.stop_funding_rate_update_thread()  # ✅ 停止资金费率更新线程
            self.stop_market_data_update_thread()  # ✅ 停止市场数据更新线程

            # 停止实时采集
            if self.realtime_collector:
                self.realtime_collector.stop()
                if self.collector_task:
                    self.collector_task.cancel()
                logger.info("✓ 实时数据采集已停止")

            # 关闭订单监控器
            if hasattr(self.executor, 'order_monitor'):
                self.executor.order_monitor.shutdown()
                logger.info("✓ 订单监控器已关闭")


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='BTC-USDT-SWAP 增强版短线交易机器人（双周期版本：5m+4H）')

    parser.add_argument('--once', action='store_true', help='运行一次分析后退出')
    parser.add_argument('--continuous', action='store_true', help='持续监控模式')
    parser.add_argument('--interval', type=float, default=60, help='检查间隔（秒）')
    parser.add_argument('--auto-execute', action='store_true', help='自动执行交易')
    #parser.add_argument('--no-realtime', action='store_true', help='禁用实时数据采集')

    args = parser.parse_args()

    # 创建机器人
    bot = BTCEnhancedBotRaw(
        auto_execute=args.auto_execute,
    )

    # 单次分析
    if args.once:
        await bot.run_analysis()
        return

    # 持续监控
    if args.continuous:
        await bot.run_continuous(args.interval)
        return

    # 默认：显示帮助
    parser.print_help()
    logger.info("\n💡 使用示例：")
    logger.info("  1. 单次分析: python examples/enhanced_trading.py --once")
    logger.info("  2. 持续监控: python examples/enhanced_trading.py --continuous --interval 60")
    logger.info("  3. 自动交易: python examples/enhanced_trading.py --continuous --auto-execute")


if __name__ == '__main__':
    PID_FILE = os.path.join(project_root, "data", "bot.pid")
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    # 配置日志输出 
    logger.remove()  # 移除默认处理器
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO"
    )

    # 添加文件日志
    logger.add(
        "logs/trading_bot_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="7 days",
        level="INFO"
    )
    logger.info(f"Bot PID: {os.getpid()}")

    asyncio.run(main())
