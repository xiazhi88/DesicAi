"""
订单监控和止盈止损管理
负责监控订单成交状态，自动设置止盈止损
"""
import asyncio
import time
from typing import Dict, Optional
from loguru import logger
from concurrent.futures import ThreadPoolExecutor

from src.api.trade import TradeAPI
from src.api.position import PositionAPI
from src.api.market import MarketAPI


class OrderMonitor:
    """订单监控器 - 监控订单成交并自动设置止盈止损"""

    def __init__(
        self,
        trade_api: TradeAPI,
        position_api: PositionAPI,
        market_api: MarketAPI
    ):
        """
        初始化订单监控器

        Args:
            trade_api: 交易API
            position_api: 持仓API
            market_api: 行情API
        """
        self.trade_api = trade_api
        self.position_api = position_api
        self.market_api = market_api

        # 监控配置
        self.check_interval = 5  # 检查间隔（秒）- 增加到5秒，减少API调用频率
        self.max_check_time = 300  # 最大监控时间（秒）

        # 运行中的监控任务
        self.monitoring_tasks = {}

        # 线程池执行器（用于执行同步API调用，避免阻塞事件循环）
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="order_monitor")

    async def monitor_and_set_stop_orders(
        self,
        inst_id: str,
        order_id: str,
        side: str,
        size: str,
        stop_loss_rate: float = None,
        take_profit_rate: float = None,
        stop_loss_price: float = None,  # 新增：止损具体价格
        take_profit_price: float = None,  # 新增：止盈具体价格
        leverage: int = 20,
        td_mode: str = 'cross'
    ):
        """
        监控订单成交并自动设置止盈止损

        Args:
            inst_id: 交易对
            order_id: 订单ID
            side: 开仓方向 'buy'(开多) or 'sell'(开空)
            size: 开仓数量
            stop_loss_rate: 止损比例（基于盈亏，如 0.1 = 10%盈亏）（与stop_loss_price二选一）
            take_profit_rate: 止盈比例（基于盈亏，如 0.3 = 30%盈亏）（与take_profit_price二选一）
            stop_loss_price: 止损具体价格（优先使用）
            take_profit_price: 止盈具体价格（优先使用）
            leverage: 杠杆倍数（用于计算实际价格变动）
            td_mode: 交易模式
        """
        pos_side = 'long' if side == 'buy' else 'short'

        # 根据是否提供了具体价格来显示日志
        if stop_loss_price and take_profit_price:
            logger.info(
                f"🔍 启动订单监控: {inst_id} {order_id} | "
                f"杠杆{leverage}x | 止损价{stop_loss_price:.2f} 止盈价{take_profit_price:.2f}"
            )
        else:
            logger.info(
                f"🔍 启动订单监控: {inst_id} {order_id} | "
                f"杠杆{leverage}x | 止损{stop_loss_rate*100:.1f}% 止盈{take_profit_rate*100:.1f}%"
            )

        start_time = time.time()

        try:
            # 循环检查订单状态
            while time.time() - start_time < self.max_check_time:
                await asyncio.sleep(self.check_interval)

                # 查询订单状态
                status, avg_price = await self._check_order_filled(inst_id, order_id)

                if status == 'filled':
                    logger.success(f"✅ 订单已成交！订单ID={order_id} 成交价={avg_price:.2f}")

                    # 设置止盈止损
                    await self._set_stop_orders(
                        inst_id=inst_id,
                        pos_side=pos_side,
                        size=size,
                        entry_price=avg_price,
                        stop_loss_rate=stop_loss_rate,
                        take_profit_rate=take_profit_rate,
                        stop_loss_price=stop_loss_price,  # 传递具体价格
                        take_profit_price=take_profit_price,  # 传递具体价格
                        leverage=leverage,
                        td_mode=td_mode
                    )

                    # 监控完成
                    break

                elif status in ['canceled', 'failed']:
                    logger.warning(f"⚠️ 订单未成交: {status}，停止监控")
                    break

                elif status == 'live':
                    logger.debug(f"⏳ 订单未成交，继续监控... (已等待 {int(time.time() - start_time)}秒)")

            else:
                # 超时
                logger.warning(f"⏰ 订单监控超时 ({self.max_check_time}秒)，停止监控")

        except Exception as e:
            logger.error(f"❌ 订单监控异常: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # 清理任务记录
            if order_id in self.monitoring_tasks:
                del self.monitoring_tasks[order_id]

    async def _check_order_filled(self, inst_id: str, order_id: str) -> tuple:
        """
        检查订单是否成交

        Args:
            inst_id: 交易对
            order_id: 订单ID

        Returns:
            (status, avg_price): 订单状态和成交均价
        """
        try:
            # 使用线程池执行器运行同步API调用，避免阻塞事件循环
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.executor,
                lambda: self.trade_api.get_order(inst_id=inst_id, ord_id=order_id)
            )

            if result['code'] != '0':
                logger.error(f"查询订单失败: {result.get('msg')}")
                return 'unknown', 0

            orders = result.get('data', [])
            if not orders:
                return 'unknown', 0

            order = orders[0]
            state = order.get('state', '')  # filled, live, canceled, partially_filled
            avgPx=order.get('avgPx', '0')
            avg_px = float(avgPx if  avgPx!='' else 0)

            return state, avg_px

        except Exception as e:
            logger.error(f"查询订单状态异常: {e}")
            return 'unknown', 0

    async def _set_stop_orders(
        self,
        inst_id: str,
        pos_side: str,
        size: str,
        entry_price: float,
        stop_loss_rate: float = None,
        take_profit_rate: float = None,
        stop_loss_price: float = None,  # 新增：止损具体价格
        take_profit_price: float = None,  # 新增：止盈具体价格
        leverage: int = 20,
        td_mode: str = 'cross'
    ):
        """
        设置止盈止损订单

        Args:
            inst_id: 交易对
            pos_side: 持仓方向 'long' or 'short'
            size: 持仓数量
            entry_price: 开仓均价
            stop_loss_rate: 止损比例（基于盈亏，如 0.1 = 10%盈亏）（与stop_loss_price二选一）
            take_profit_rate: 止盈比例（基于盈亏，如 0.3 = 30%盈亏）（与take_profit_price二选一）
            stop_loss_price: 止损具体价格（优先使用）
            take_profit_price: 止盈具体价格（优先使用）
            leverage: 杠杆倍数
            td_mode: 交易模式
        """
        try:
            # 优先使用具体价格，如果没有则使用比率计算
            if stop_loss_price and take_profit_price:
                # 使用AI提供的具体价格
                stop_loss_px = stop_loss_price
                take_profit_px = take_profit_price

                logger.info(
                    f"📐 使用AI提供的具体止盈止损价格:\n"
                    f"  开仓价: {entry_price:.2f}\n"
                    f"  止损价: {stop_loss_px:.2f}\n"
                    f"  止盈价: {take_profit_px:.2f}"
                )
            else:
                # 使用比率计算价格
                # 将盈亏比例转换为价格变动比例
                # 公式：价格变动率 = 盈亏率 / 杠杆
                # 例如：10%盈亏 / 20倍杠杆 = 0.5%价格变动
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
                    stop_loss_px = entry_price * (1 - price_stop_loss_rate)
                    take_profit_px = entry_price * (1 + price_take_profit_rate)
                else:
                    # 空头：止盈价 < 开仓价 < 止损价
                    stop_loss_px = entry_price * (1 + price_stop_loss_rate)
                    take_profit_px = entry_price * (1 - price_take_profit_rate)

                logger.info(
                    f"📐 计算止盈止损价格:\n"
                    f"  开仓价: {entry_price:.2f}\n"
                    f"  止损价: {stop_loss_px:.2f} (价格变动 {price_stop_loss_rate*100:.2f}% = 盈亏 {stop_loss_rate*100:.1f}%)\n"
                    f"  止盈价: {take_profit_px:.2f} (价格变动 {price_take_profit_rate*100:.2f}% = 盈亏 {take_profit_rate*100:.1f}%)"
                )

            # 确定平仓方向
            close_side = 'sell' if pos_side == 'long' else 'buy'

            # 使用 OCO 订单（止盈止损联动）
            # OKX支持 oco 订单类型，同时设置止盈和止损
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.executor,
                lambda: self.trade_api.place_algo_order(
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

    def start_monitoring(
        self,
        inst_id: str,
        order_id: str,
        side: str,
        size: str,
        stop_loss_rate: float = None,
        take_profit_rate: float = None,
        stop_loss_price: float = None,  # 新增：止损具体价格
        take_profit_price: float = None,  # 新增：止盈具体价格
        leverage: int = 20,
        td_mode: str = 'cross'
    ):
        """
        启动订单监控（创建后台任务）

        Args:
            inst_id: 交易对
            order_id: 订单ID
            side: 开仓方向
            size: 开仓数量
            stop_loss_rate: 止损比例（基于盈亏）（与stop_loss_price二选一）
            take_profit_rate: 止盈比例（基于盈亏）（与take_profit_price二选一）
            stop_loss_price: 止损具体价格（优先使用）
            take_profit_price: 止盈具体价格（优先使用）
            leverage: 杠杆倍数
            td_mode: 交易模式

        Returns:
            asyncio.Task: 监控任务
        """
        # 创建异步任务（不阻塞主线程）
        task = asyncio.create_task(
            self.monitor_and_set_stop_orders(
                inst_id=inst_id,
                order_id=order_id,
                side=side,
                size=size,
                stop_loss_rate=stop_loss_rate,
                take_profit_rate=take_profit_rate,
                stop_loss_price=stop_loss_price,  # 传递具体价格
                take_profit_price=take_profit_price,  # 传递具体价格
                leverage=leverage,
                td_mode=td_mode
            )
        )

        # 记录任务
        self.monitoring_tasks[order_id] = task

        logger.info(f"🚀 订单监控任务已启动: {order_id} (后台运行)")

        return task

    def get_monitoring_count(self) -> int:
        """获取当前监控中的订单数量"""
        return len(self.monitoring_tasks)

    def cancel_monitoring(self, order_id: str):
        """取消订单监控"""
        if order_id in self.monitoring_tasks:
            task = self.monitoring_tasks[order_id]
            task.cancel()
            del self.monitoring_tasks[order_id]
            logger.info(f"⏹️  订单监控已取消: {order_id}")

    def shutdown(self):
        """关闭订单监控器，释放资源"""
        logger.info("🛑 关闭订单监控器...")

        # 取消所有监控任务
        for order_id, task in list(self.monitoring_tasks.items()):
            task.cancel()
            logger.info(f"  取消监控: {order_id}")

        self.monitoring_tasks.clear()

        # 关闭线程池
        self.executor.shutdown(wait=True)
        logger.info("✓ 订单监控器已关闭")
