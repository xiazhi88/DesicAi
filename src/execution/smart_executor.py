"""
智能订单执行器
优先使用挂单（Post-Only）降低手续费，超时后改用市价单
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import asyncio
import math
from typing import Dict, Optional, Tuple
from loguru import logger

from src.api.trade import TradeAPI
from src.api.position import PositionAPI
from src.api.market import MarketAPI
from src.api.public import PublicAPI
from src.utils.fee_calculator import FeeCalculator
from src.utils.instrument_cache import get_cache
from src.execution.order_monitor import OrderMonitor


class SmartOrderExecutor:
    """智能订单执行器 - 优先挂单策略"""

    def __init__(
        self,
        trade_api: TradeAPI,
        position_api: PositionAPI,
        market_api: MarketAPI,
        fee_calculator: FeeCalculator = None,
        public_api: PublicAPI = None,
        data_manager = None
    ):
        """
        初始化智能订单执行器

        Args:
            trade_api: 交易API
            position_api: 持仓API
            market_api: 行情API
            fee_calculator: 手续费计算器
            public_api: 公共API（用于获取交易对信息）
            data_manager: 数据管理器（用于从Redis获取行情）
        """
        self.trade_api = trade_api
        self.position_api = position_api
        self.market_api = market_api
        self.public_api = public_api or PublicAPI(market_api.client)
        self.fee_calc = fee_calculator or FeeCalculator()
        self.instrument_cache = get_cache()
        self.data_manager = data_manager

        # 初始化订单监控器
        self.order_monitor = OrderMonitor(
            trade_api=trade_api,
            position_api=position_api,
            market_api=market_api
        )

        # 配置参数
        self.post_only_timeout = 20  # 挂单等待时间（秒）
        self.order_check_interval = 0.5  # 订单检查间隔（秒）

    def calculate_order_price(
        self,
        inst_id: str,
        side: str,
        offset_ticks: int = 1
    ) -> Optional[float]:
        """
        计算挂单价格（略优于盘口价）- 优先使用Redis订单簿

        Args:
            inst_id: 交易对
            side: 方向 'buy' or 'sell'
            offset_ticks: 偏移档位（1=挂在买1/卖1）

        Returns:
            挂单价格
        """
        try:
            # 优先从Redis获取订单簿（快速、实时）
            if self.data_manager:
                orderbook = self.data_manager.get_orderbook_from_redis(inst_id, depth=1)
                if orderbook and orderbook.get('bids') and orderbook.get('asks'):
                    if side == 'buy':
                        # 买入：挂在买1价
                        bid_price = float(orderbook['bids'][0][0])
                        return bid_price
                    else:
                        # 卖出：挂在卖1价
                        ask_price = float(orderbook['asks'][0][0])
                        return ask_price

            # Redis数据不可用，降级到REST API
            logger.debug(f"Redis订单簿不可用，使用REST API获取价格")
            ticker_result = self.market_api.get_ticker(inst_id)

            if ticker_result['code'] != '0':
                logger.error(f"获取行情失败: {ticker_result.get('msg')}")
                return None

            ticker = ticker_result['data'][0]

            if side == 'buy':
                # 买入：挂在买1价
                bid_price = float(ticker['bidPx'])
                return bid_price
            else:
                # 卖出：挂在卖1价
                ask_price = float(ticker['askPx'])
                return ask_price

        except Exception as e:
            logger.error(f"计算挂单价格失败: {e}")
            return None

    def place_limit_order(
        self,
        inst_id: str,
        side: str,
        size: str,
        price: float,
        pos_side: str = 'net',
        td_mode: str = 'cross'
    ) -> Optional[Dict]:
        """
        下限价单（Post-Only）

        Args:
            inst_id: 交易对
            side: 方向 buy/sell
            size: 数量
            price: 价格
            pos_side: 持仓方向 long/short/net
            td_mode: 交易模式 cross/isolated

        Returns:
            订单结果
        """
        try:
            result = self.trade_api.place_order(
                inst_id=inst_id,
                td_mode=td_mode,
                side=side,
                ord_type='post_only',  # Post-Only确保挂单
                sz=size,
                px=str(price),
                pos_side=pos_side
            )

            if result['code'] == '0':
                order_id = result['data'][0]['ordId']
                logger.info(f"✓ 挂单成功: 订单ID={order_id}, 价格={price}, 数量={size}")
                return {
                    'success': True,
                    'order_id': order_id,
                    'price': price,
                    'size': size,
                    'is_maker': True
                }
            else:
                logger.warning(f"挂单失败: {result.get('msg')}")
                return None

        except Exception as e:
            logger.error(f"下限价单失败: {e}")
            return None

    def place_market_order(
        self,
        inst_id: str,
        side: str,
        size: str,
        pos_side: str = 'net',
        td_mode: str = 'cross'
    ) -> Optional[Dict]:
        """
        下市价单（Taker）

        Args:
            inst_id: 交易对
            side: 方向
            size: 数量
            pos_side: 持仓方向
            td_mode: 交易模式

        Returns:
            订单结果
        """
        try:
            result = self.trade_api.place_order(
                inst_id=inst_id,
                td_mode=td_mode,
                side=side,
                ord_type='market',
                sz=size,
                pos_side=pos_side
            )

            if result['code'] == '0':
                order_id = result['data'][0]['ordId']
                logger.info(f"✓ 市价单成功: 订单ID={order_id}, 数量={size}")
                return {
                    'success': True,
                    'order_id': order_id,
                    'size': size,
                    'is_maker': False
                }
            else:
                logger.error(f"市价单失败: {result.get('msg')}")
                return None

        except Exception as e:
            logger.error(f"下市价单失败: {e}")
            return None

    def check_order_status(self, inst_id: str, order_id: str) -> Optional[str]:
        """
        检查订单状态

        Args:
            inst_id: 交易对
            order_id: 订单ID

        Returns:
            订单状态: 'filled' / 'partially_filled' / 'pending' / 'cancelled'
        """
        try:
            result = self.trade_api.get_order(inst_id=inst_id, ord_id=order_id)

            if result['code'] != '0':
                return None

            order = result['data'][0]
            state = order['state']

            # OKX订单状态: live(未完成) partially_filled(部分成交) filled(完全成交) canceled(已撤销)
            return state

        except Exception as e:
            logger.error(f"查询订单状态失败: {e}")
            return None

    def cancel_order(self, inst_id: str, order_id: str) -> bool:
        """
        撤销订单

        Args:
            inst_id: 交易对
            order_id: 订单ID

        Returns:
            是否成功
        """
        try:
            result = self.trade_api.cancel_order(inst_id=inst_id, ord_id=order_id)
            print(result)
            if result['code'] == '0':
                logger.info(f"✓ 撤单成功: {order_id}")
                return True
            elif result['code'] == '1' and ' the order has been filled, canceled or does not exist' in  result['data'][0]['sMsg']:
                logger.info(f"✓ 撤单失败: 订单已成交或者已取消 或者订单不存在")
                return False
            else:
                logger.warning(f"撤单失败: {result.get('msg')}")
                return False

        except Exception as e:
            logger.error(f"撤单异常: {e}")
            return False

    async def _set_stop_orders_after_filled(
        self,
        inst_id: str,
        order_id: str,
        pos_side: str,
        size: str,
        stop_loss_rate: float = None,
        take_profit_rate: float = None,
        stop_loss_price: float = None,
        take_profit_price: float = None,
        leverage: int = 20,
        td_mode: str = 'cross'
    ):
        """
        订单成交后设置止盈止损

        Args:
            inst_id: 交易对
            order_id: 订单ID
            pos_side: 持仓方向 'long' or 'short'
            size: 持仓数量
            stop_loss_rate: 止损比例（基于盈亏）
            take_profit_rate: 止盈比例（基于盈亏）
            stop_loss_price: 止损具体价格（优先使用）
            take_profit_price: 止盈具体价格（优先使用）
            leverage: 杠杆倍数
            td_mode: 交易模式
        """
        try:
            # 获取订单成交均价
            result = self.trade_api.get_order(inst_id=inst_id, ord_id=order_id)

            if result['code'] != '0':
                logger.error(f"获取订单信息失败: {result.get('msg')}")
                return False

            order = result['data'][0]
            avgPx = order.get('avgPx', '0')
            avg_price = float(avgPx if avgPx != '' else 0)

            if avg_price == 0:
                logger.error(f"无法获取成交均价")
                return False

            logger.info(f"📊 订单成交均价: {avg_price:.2f}")

            # 优先使用具体价格，如果没有则使用比率计算
            if stop_loss_price and take_profit_price:
                # 使用AI提供的具体价格
                stop_loss_px = stop_loss_price
                take_profit_px = take_profit_price

                logger.info(
                    f"📐 使用指定的止盈止损价格:\n"
                    f"  开仓价: {avg_price:.2f}\n"
                    f"  止损价: {stop_loss_px:.2f}\n"
                    f"  止盈价: {take_profit_px:.2f}"
                )
            else:
                # 使用比率计算价格
                # 将盈亏比例转换为价格变动比例
                # 公式：价格变动率 = 盈亏率 / 杠杆
                price_stop_loss_rate = stop_loss_rate / leverage
                price_take_profit_rate = take_profit_rate / leverage

                logger.info(
                    f"📐 杠杆调整:\n"
                    f"  杠杆: {leverage}x\n"
                    f"  期望止损(盈亏): {stop_loss_rate*100:.1f}% → 价格变动: {price_stop_loss_rate*100:.2f}%\n"
                    f"  期望止盈(盈亏): {take_profit_rate*100:.1f}% → 价格变动: {price_take_profit_rate*100:.2f}%"
                )

                # 计算止盈止损价格
                if pos_side == 'long':
                    # 多头：止损价 < 开仓价 < 止盈价
                    stop_loss_px = avg_price * (1 - price_stop_loss_rate)
                    take_profit_px = avg_price * (1 + price_take_profit_rate)
                else:
                    # 空头：止盈价 < 开仓价 < 止损价
                    stop_loss_px = avg_price * (1 + price_stop_loss_rate)
                    take_profit_px = avg_price * (1 - price_take_profit_rate)

                logger.info(
                    f"📐 计算止盈止损价格:\n"
                    f"  开仓价: {avg_price:.2f}\n"
                    f"  止损价: {stop_loss_px:.2f} (价格变动 {price_stop_loss_rate*100:.2f}% = 盈亏 {stop_loss_rate*100:.1f}%)\n"
                    f"  止盈价: {take_profit_px:.2f} (价格变动 {price_take_profit_rate*100:.2f}% = 盈亏 {take_profit_rate*100:.1f}%)"
                )

            # 确定平仓方向
            close_side = 'sell' if pos_side == 'long' else 'buy'

            # 使用 OCO 订单（止盈止损联动）
            result = self.trade_api.place_algo_order(
                inst_id=inst_id,
                td_mode=td_mode,
                side=close_side,
                ord_type='oco',  # OCO订单（One-Cancels-the-Other）
                sz=size,
                posSide=pos_side,
                # 止损参数
                slTriggerPx=str(stop_loss_px),  # 止损触发价
                slOrdPx='-1',  # 止损委托价（-1=市价）
                # 止盈参数
                tpTriggerPx=str(take_profit_px),  # 止盈触发价
                tpOrdPx=str(take_profit_px),  # 止盈委托价（限价）
            )

            if result['code'] == '0':
                algo_id = result['data'][0]['algoId']
                logger.success(
                    f"✅ 止盈止损设置成功！\n"
                    f"  策略订单ID: {algo_id}\n"
                    f"  止损: {stop_loss_px:.2f}\n"
                    f"  止盈: {take_profit_px:.2f}"
                )
                return True
            else:
                logger.error(f"❌ 止盈止损设置失败: {result.get('msg')}")
                return False

        except Exception as e:
            logger.error(f"❌ 设置止盈止损异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def smart_open_position(
        self,
        inst_id: str,
        side: str,
        size: str,
        leverage: int = 20,
        td_mode: str = 'cross',
        timeout: int = None,
        stop_loss_rate: float = None,
        take_profit_rate: float = None,
        stop_loss_price: float = None,  # 新增：止损具体价格
        take_profit_price: float = None,  # 新增：止盈具体价格
        auto_stop_orders: bool = True
    ) -> Dict:
        """
        智能开仓：优先挂单，超时改市价，成交后自动设置止盈止损

        Args:
            inst_id: 交易对
            side: 方向 'buy'(开多) or 'sell'(开空)
            size: 数量（张数）
            leverage: 杠杆倍数
            td_mode: 交易模式
            timeout: 挂单超时时间（秒），默认使用配置
            stop_loss_rate: 止损比例（如 0.01 = 1%）（与stop_loss_price二选一）
            take_profit_rate: 止盈比例（如 0.03 = 3%）（与take_profit_price二选一）
            stop_loss_price: 止损具体价格（优先使用）
            take_profit_price: 止盈具体价格（优先使用）
            auto_stop_orders: 是否自动设置止盈止损（默认True）

        Returns:
            执行结果
        """
        timeout = timeout or self.post_only_timeout
        pos_side = 'long' if side == 'buy' else 'short'

        logger.info(f"🎯 智能开仓: {inst_id} {side.upper()} {size}张 {leverage}x")

        # 步骤1: 计算挂单价格
        limit_price = self.calculate_order_price(inst_id, side)

        if not limit_price:
            logger.warning("⚠️ 无法获取盘口价格，直接使用市价单")
            market_order = await asyncio.to_thread(
                self.place_market_order, inst_id, side, size, pos_side, td_mode
            )
            if not market_order:
                return {'success': False, 'error': '市价单失败'}

            # 市价单成交后设置止盈止损
            if auto_stop_orders and (stop_loss_rate or stop_loss_price) and (take_profit_rate or take_profit_price):
                await self._set_stop_orders_after_filled(
                    inst_id=inst_id,
                    order_id=market_order['order_id'],
                    pos_side=pos_side,
                    size=size,
                    stop_loss_rate=stop_loss_rate,
                    take_profit_rate=take_profit_rate,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    leverage=leverage,
                    td_mode=td_mode
                )

            return market_order

        # 步骤2: 尝试挂单
        logger.info(f"📝 尝试挂单: 价格={limit_price:.2f}")
        limit_order = self.place_limit_order(inst_id, side, size, limit_price, pos_side, td_mode)

        if not limit_order:
            logger.warning("⚠️ 挂单失败，改用市价单")
            market_order = await asyncio.to_thread(
                self.place_market_order, inst_id, side, size, pos_side, td_mode
            )
            if not market_order:
                return {'success': False, 'error': '市价单失败'}

            # 市价单成交后设置止盈止损
            if auto_stop_orders and (stop_loss_rate or stop_loss_price) and (take_profit_rate or take_profit_price):
                await self._set_stop_orders_after_filled(
                    inst_id=inst_id,
                    order_id=market_order['order_id'],
                    pos_side=pos_side,
                    size=size,
                    stop_loss_rate=stop_loss_rate,
                    take_profit_rate=take_profit_rate,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    leverage=leverage,
                    td_mode=td_mode
                )

            return market_order

        order_id = limit_order['order_id']

        # 步骤3: 等待挂单成交（短时间）
        start_time = time.time()
        while time.time() - start_time < timeout:
            await asyncio.sleep(self.order_check_interval)

            status = self.check_order_status(inst_id, order_id)

            if status == 'filled':
                logger.success(f"✅ 挂单成交！订单ID={order_id}")
                limit_order['filled'] = True

                # 挂单成交后设置止盈止损
                if auto_stop_orders and (stop_loss_rate or stop_loss_price) and (take_profit_rate or take_profit_price):
                    await self._set_stop_orders_after_filled(
                        inst_id=inst_id,
                        order_id=order_id,
                        pos_side=pos_side,
                        size=size,
                        stop_loss_rate=stop_loss_rate,
                        take_profit_rate=take_profit_rate,
                        stop_loss_price=stop_loss_price,
                        take_profit_price=take_profit_price,
                        leverage=leverage,
                        td_mode=td_mode
                    )

                return limit_order

            elif status in ['canceled', 'failed']:
                logger.warning(f"⚠️ 订单已取消或失败: {status}")
                break

        # 步骤4: 超时未成交，撤单改市价
        logger.warning(f"⏰ 挂单超时（{timeout}秒未成交），撤单改市价")

        # 撤销挂单
        self.cancel_order(inst_id, order_id)
        await asyncio.sleep(0.5)  # 等待撤单完成

        # 下市价单
        logger.info("📢 执行市价单")
        market_order = self.place_market_order(inst_id, side, size, pos_side, td_mode)

        if not market_order:
            return {'success': False, 'error': '市价单失败'}

        # 市价单成交后设置止盈止损
        if auto_stop_orders and (stop_loss_rate or stop_loss_price) and (take_profit_rate or take_profit_price):
            await self._set_stop_orders_after_filled(
                inst_id=inst_id,
                order_id=market_order['order_id'],
                pos_side=pos_side,
                size=size,
                stop_loss_rate=stop_loss_rate,
                take_profit_rate=take_profit_rate,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                leverage=leverage,
                td_mode=td_mode
            )

        return market_order

    async def smart_close_position(
        self,
        inst_id: str,
        pos_side: str,
        size: str,
        td_mode: str = 'cross',
        force_market: bool = False,
        timeout: int = None
    ) -> Dict:
        """
        智能平仓：紧急情况用市价，否则尝试挂单

        Args:
            inst_id: 交易对
            pos_side: 持仓方向 'long' or 'short'
            size: 平仓数量
            td_mode: 交易模式
            force_market: 是否强制市价（止损时使用）
            timeout: 挂单超时时间（秒），默认使用配置

        Returns:
            执行结果
        """
        timeout = timeout or self.post_only_timeout

        # 平多仓 = sell，平空仓 = buy
        side = 'sell' if pos_side == 'long' else 'buy'

        logger.info(f"🔄 智能平仓: {inst_id} 平{pos_side} {size}张 (side={side}, posSide={pos_side})")

        # 紧急平仓（止损）直接用市价
        if force_market:
            logger.warning("🚨 紧急平仓，使用市价单")
            return await asyncio.to_thread(
                self.place_market_order, inst_id, side, size, pos_side, td_mode
            ) or {'success': False, 'error': '市价单失败'}

        # 正常平仓尝试挂单
        # 计算挂单价格
        limit_price = self.calculate_order_price(inst_id, side)

        if not limit_price:
            logger.warning("⚠️ 无法获取盘口价格，直接使用市价单")
            return await asyncio.to_thread(
                self.place_market_order, inst_id, side, size, pos_side, td_mode
            ) or {'success': False, 'error': '市价单失败'}

        # 尝试挂单
        logger.info(f"📝 尝试挂单平仓: 价格={limit_price:.2f}")
        limit_order = self.place_limit_order(inst_id, side, size, limit_price, pos_side, td_mode)

        if not limit_order:
            logger.warning("⚠️ 挂单失败，改用市价单")
            return await asyncio.to_thread(
                self.place_market_order, inst_id, side, size, pos_side, td_mode
            ) or {'success': False, 'error': '市价单失败'}

        order_id = limit_order['order_id']

        # 等待挂单成交（短时间）
        start_time = time.time()
        while time.time() - start_time < timeout:
            await asyncio.sleep(self.order_check_interval)

            status = self.check_order_status(inst_id, order_id)

            if status == 'filled':
                logger.success(f"✅ 平仓挂单成交！订单ID={order_id}")
                limit_order['filled'] = True
                return limit_order

            elif status in ['canceled', 'failed']:
                logger.warning(f"⚠️ 订单已取消或失败: {status}")
                break

        # 超时未成交，撤单改市价
        logger.warning(f"⏰ 平仓挂单超时（{timeout}秒未成交），撤单改市价")

        # 撤销挂单
        self.cancel_order(inst_id, order_id)
        await asyncio.sleep(0.5)  # 等待撤单完成

        # 下市价单
        logger.info("📢 执行市价平仓")
        market_order = self.place_market_order(inst_id, side, size, pos_side, td_mode)

        if market_order:
            return market_order
        else:
            return {'success': False, 'error': '市价单失败'}

    def calculate_position_size(
        self,
        inst_id: str,
        principal: float,
        leverage: int,
        current_price: float = None
    ) -> Dict:
        """
        计算开仓张数（支持小数张数）

        Args:
            inst_id: 交易对
            principal: 本金（USDT）
            leverage: 杠杆
            current_price: 当前价格（可选，不提供则自动获取）

        Returns:
            dict: {
                'success': bool,
                'size': str,              # 张数（字符串，可能是小数）
                'size_float': float,      # 张数（浮点数）
                'contract_value': float,  # 每张合约价值
                'total_value': float,     # 总价值
                'margin': float,          # 需要的保证金
                'error': str              # 错误信息（如果失败）
            }
        """
        try:
            # 从缓存获取产品信息（优先使用缓存，避免频繁调用API）
            cache_result = self.instrument_cache.get_instrument_info(
                inst_id=inst_id,
                inst_type='SWAP',
                market_api=self.public_api
            )

            if not cache_result['success']:
                logger.error(f"获取产品信息失败: {cache_result.get('error')}")
                return {
                    'success': False,
                    'size': '1',
                    'size_float': 1.0,
                    'error': f"获取产品信息失败: {cache_result.get('error')}"
                }

            inst_info = cache_result['data']

            # 记录是否来自缓存
            from_cache = cache_result.get('from_cache', False)
            if from_cache:
                logger.debug(f"使用缓存的 {inst_id} 信息")
            else:
                logger.info(f"已更新 {inst_id} 缓存")

            # ctVal: 合约面值，如 BTC-USDT-SWAP 为 0.01 (每张0.01个BTC)
            ct_val = float(inst_info.get('ctVal', 0))
            # minSz: 最小下单数量，如 0.01
            min_sz = float(inst_info.get('minSz', 1))
            # lotSz: 下单数量精度，如 0.01
            lot_sz = float(inst_info.get('lotSz', 1))

            if ct_val == 0:
                logger.error(f"{inst_id} 合约面值为0")
                return {
                    'success': False,
                    'size': '1',
                    'size_float': 1.0,
                    'error': '合约面值为0'
                }

            # 获取当前价格（如果没有提供）
            if current_price is None:
                ticker_result = self.market_api.get_ticker(inst_id=inst_id)
                if ticker_result.get('code') != '0':
                    logger.error(f"获取价格失败: {ticker_result.get('msg')}")
                    return {
                        'success': False,
                        'size': '1',
                        'size_float': 1.0,
                        'error': '获取价格失败'
                    }
                ticker_data = ticker_result.get('data', [])
                if not ticker_data:
                    return {
                        'success': False,
                        'size': '1',
                        'size_float': 1.0,
                        'error': '无法获取价格'
                    }
                current_price = float(ticker_data[0].get('last', 0))

            if current_price == 0:
                return {
                    'success': False,
                    'size': '1',
                    'size_float': 1.0,
                    'error': '价格为0'
                }

            # 计算张数
            # 每张合约价值 = 合约面值(BTC) * 价格(USDT)
            contract_value = ct_val * current_price

            # 考虑杠杆后，实际可开张数 = (USDT金额 * 杠杆) / 每张合约价值
            size_raw = (principal * leverage) / contract_value

            # 按照 lotSz 精度向下取整
            # 例如 lotSz=0.01, size_raw=0.1765 -> size=0.17
            size = math.floor(size_raw / lot_sz) * lot_sz

            # 计算精度（小数位数）
            if lot_sz >= 1:
                precision = 0
            else:
                # 去除尾部的0，然后计算小数位数
                precision = len(str(lot_sz).rstrip('0').split('.')[-1])

            # 保留指定精度
            size = round(size, precision)

            # 检查是否满足最小下单数量
            if size < min_sz:
                required_margin = min_sz * contract_value / leverage
                logger.warning(
                    f"金额不足。最小下单 {min_sz} 张，需要保证金 {required_margin:.2f} USDT "
                    f"（{leverage}倍杠杆，每张价值 {contract_value:.2f} USDT）"
                )
                return {
                    'success': False,
                    'size': str(min_sz),
                    'size_float': min_sz,
                    'error': f'金额不足，最小下单 {min_sz} 张，需要保证金 {required_margin:.2f} USDT'
                }

            # 实际总价值
            total_value = size * contract_value

            # 需要的保证金
            margin_needed = total_value / leverage

            logger.info(
                f"计算仓位: {size}张 (本金 {principal:.2f} USDT × {leverage}x = "
                f"{total_value:.2f} USDT, 需保证金 {margin_needed:.2f} USDT)"
            )

            return {
                'success': True,
                'size': str(size),
                'size_float': size,
                'contract_value': contract_value,
                'total_value': total_value,
                'margin': margin_needed,
                'ct_val': ct_val,
                'min_sz': min_sz,
                'lot_sz': lot_sz
            }

        except Exception as e:
            logger.error(f"计算张数失败: {e}")
            import traceback
            logger.debug(f"错误堆栈: {traceback.format_exc()}")
            return {
                'success': False,
                'size': '1',
                'size_float': 1.0,
                'error': str(e)
            }


# 测试代码
if __name__ == '__main__':
    from src.core.rest_client import OKXRestClient
    from src.config.settings import config

    # 初始化客户端
    client = OKXRestClient(
        api_key=config.API_KEY,
        secret_key=config.SECRET_KEY,
        passphrase=config.PASSPHRASE,
        is_demo=True
    )

    # 初始化APIs
    trade_api = TradeAPI(client)
    position_api = PositionAPI(client)
    market_api = MarketAPI(client)

    # 创建执行器
    executor = SmartOrderExecutor(trade_api, position_api, market_api)

    # 测试智能开仓
    async def test():
        result = await executor.smart_open_position(
            inst_id='BTC-USDT-SWAP',
            side='buy',
            size='1',
            leverage=20
        )
        print(result)

    asyncio.run(test())
