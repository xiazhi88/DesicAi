"""
特征工程模块
提取K线、逐笔成交、订单簿的多维特征
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

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Optional
from loguru import logger


class FeatureEngineer:
    """多数据源特征工程"""

    def __init__(self, data_manager):
        """
        初始化特征工程模块

        Args:
            data_manager: DataManager实例
        """
        self.db = data_manager

    def extract_all_features(self, symbol: str, timeframe: str = '5m') -> Dict:
        """
        提取所有特征

        Args:
            symbol: 交易对
            timeframe: K线周期

        Returns:
            特征字典
        """
        features = {}

        # 1. K线特征 (50% 权重)
        features['kline'] = self.extract_kline_features(symbol, timeframe)

        # 2. 逐笔成交特征 (40% 权重)
        features['trades'] = self.extract_trade_features(symbol)

        # 3. 订单簿特征 (10% 权重)
        features['orderbook'] = self.extract_orderbook_features(symbol)

        # 4. 综合评分
        features['score'] = self.calculate_composite_score(features)

        return features

    def extract_kline_features(self, symbol: str, timeframe: str) -> Dict:
        """
        K线技术指标特征

        Args:
            symbol: 交易对
            timeframe: K线周期

        Returns:
            K线特征字典
        """
        try:
            # 获取最近100根K线
            klines = self.db.get_recent_klines(symbol, timeframe, limit=100)

            if not klines or len(klines) < 20:
                logger.warning(f"K线数据不足: {symbol} {timeframe}")
                return {}

            # 转换为DataFrame
            df = pd.DataFrame(klines)

            # 计算技术指标
            close = df['close']
            high = df['high']
            low = df['low']
            volume = df['volume']

            features = {}

            # 当前价格
            features['current_price'] = close.iloc[-1]

            # 移动平均线
            features['ma5'] = close.rolling(5).mean().iloc[-1]
            features['ma20'] = close.rolling(20).mean().iloc[-1]
            features['ma_trend'] = 'up' if features['ma5'] > features['ma20'] else 'down'

            # EMA
            ema12 = close.ewm(span=12).mean()
            ema26 = close.ewm(span=26).mean()

            # MACD
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9).mean()
            macd_histogram = (macd_line - signal_line).iloc[-1]

            features['macd'] = macd_line.iloc[-1]
            features['macd_signal'] = signal_line.iloc[-1]
            features['macd_histogram'] = macd_histogram
            features['macd_trend'] = 'bullish' if macd_histogram > 0 else 'bearish'

            # RSI
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = -delta.where(delta < 0, 0).rolling(14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            rsi_value = rsi.iloc[-1]

            features['rsi'] = rsi_value
            features['rsi_state'] = 'overbought' if rsi_value > 70 else ('oversold' if rsi_value < 30 else 'neutral')

            # 布林带
            bb_middle = close.rolling(20).mean()
            bb_std = close.rolling(20).std()
            bb_upper = bb_middle + 2 * bb_std
            bb_lower = bb_middle - 2 * bb_std

            current_price = close.iloc[-1]
            bb_position = (current_price - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1])

            features['bb_upper'] = bb_upper.iloc[-1]
            features['bb_middle'] = bb_middle.iloc[-1]
            features['bb_lower'] = bb_lower.iloc[-1]
            features['bb_position'] = bb_position
            features['bb_state'] = 'upper' if bb_position > 0.8 else ('lower' if bb_position < 0.2 else 'middle')

            # 价格变化
            features['price_change_5'] = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100
            features['price_change_20'] = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100

            # 成交量
            features['volume_ma'] = volume.rolling(20).mean().iloc[-1]
            features['volume_ratio'] = volume.iloc[-1] / features['volume_ma']
            features['volume_trend'] = 'high' if features['volume_ratio'] > 1.5 else ('low' if features['volume_ratio'] < 0.5 else 'normal')

            # ATR (波动率)
            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs()
            ], axis=1).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1]
            features['atr'] = atr
            features['atr_pct'] = atr / current_price * 100

            return features

        except Exception as e:
            logger.error(f"提取K线特征失败: {e}")
            return {}

    def extract_trade_features(self, symbol: str) -> Dict:
        """
        逐笔成交压力特征（核心信号）

        Args:
            symbol: 交易对

        Returns:
            成交特征字典
        """
        try:
            features = {}

            # 获取1分钟、5分钟、15分钟压力
            for interval, key in [(60, '1m'), (300, '5m'), (900, '15m')]:
                pressure = self.db.get_latest_pressure(symbol, interval)

                if pressure:
                    buy_vol = pressure['buy_volume']
                    sell_vol = pressure['sell_volume']
                    total_vol = buy_vol + sell_vol

                    features[f'pressure_ratio_{key}'] = pressure['pressure_ratio']
                    features[f'buy_dominance_{key}'] = buy_vol / total_vol if total_vol > 0 else 0.5
                    features[f'large_buy_ratio_{key}'] = pressure['large_buy_volume'] / buy_vol if buy_vol > 0 else 0
                    features[f'large_sell_ratio_{key}'] = pressure['large_sell_volume'] / sell_vol if sell_vol > 0 else 0
                    features[f'trade_intensity_{key}'] = pressure['buy_count'] + pressure['sell_count']

            # 综合判断
            if features:
                # 多周期压力一致性
                ratios = [
                    features.get('pressure_ratio_1m', 1),
                    features.get('pressure_ratio_5m', 1),
                    features.get('pressure_ratio_15m', 1)
                ]

                if all(r > 1.5 for r in ratios):
                    features['pressure_trend'] = 'strong_buy'
                elif all(r < 0.67 for r in ratios):
                    features['pressure_trend'] = 'strong_sell'
                else:
                    features['pressure_trend'] = 'neutral'

                # 大单影响
                large_buys = [
                    features.get('large_buy_ratio_1m', 0),
                    features.get('large_buy_ratio_5m', 0)
                ]
                large_sells = [
                    features.get('large_sell_ratio_1m', 0),
                    features.get('large_sell_ratio_5m', 0)
                ]

                if sum(large_buys) > 0.3:
                    features['whale_activity'] = 'buying'
                elif sum(large_sells) > 0.3:
                    features['whale_activity'] = 'selling'
                else:
                    features['whale_activity'] = 'normal'

            return features

        except Exception as e:
            logger.error(f"提取成交特征失败: {e}")
            return {}

    def extract_orderbook_features(self, symbol: str) -> Dict:
        """
        订单簿流动性特征（风控参考）

        Args:
            symbol: 交易对

        Returns:
            订单簿特征字典
        """
        try:
            # 从Redis获取实时订单簿（前5档）
            orderbook = self.db.get_orderbook_from_redis(symbol, depth=5)

            if not orderbook or not orderbook.get('bids') or not orderbook.get('asks'):
                return {}

            bids = orderbook['bids']  # [[price, size, orders], ...]
            asks = orderbook['asks']

            # 计算聚合指标
            bid1_price = float(bids[0][0])
            bid1_size = float(bids[0][1])
            ask1_price = float(asks[0][0])
            ask1_size = float(asks[0][1])

            # 价差
            spread = ask1_price - bid1_price
            mid_price = (bid1_price + ask1_price) / 2
            spread_pct = (spread / mid_price) * 100

            # 前5档深度
            bid_depth_5 = sum(float(b[1]) for b in bids[:5])
            ask_depth_5 = sum(float(a[1]) for a in asks[:5])
            depth_ratio = bid_depth_5 / ask_depth_5 if ask_depth_5 > 0 else 0

            features = {
                'spread_pct': spread_pct,
                'bid_depth_5': bid_depth_5,
                'ask_depth_5': ask_depth_5,
                'depth_ratio': depth_ratio
            }

            # 流动性评估
            total_depth = bid_depth_5 + ask_depth_5

            if spread_pct < 0.02 and total_depth > 100:
                features['liquidity_score'] = 'good'
            elif spread_pct > 0.05 or total_depth < 50:
                features['liquidity_score'] = 'poor'
            else:
                features['liquidity_score'] = 'medium'

            # 深度失衡
            if depth_ratio > 1.5:
                features['depth_imbalance'] = 'bid_heavy'
            elif depth_ratio < 0.67:
                features['depth_imbalance'] = 'ask_heavy'
            else:
                features['depth_imbalance'] = 'balanced'

            # 估算滑点
            features['estimated_slippage_1pct'] = self.estimate_slippage(spread_pct, total_depth)

            return features

        except Exception as e:
            logger.error(f"提取订单簿特征失败: {e}")
            return {}

    def estimate_slippage(self, spread_pct: float, total_depth: float) -> float:
        """
        估算1%仓位的滑点

        Args:
            spread_pct: 价差百分比
            total_depth: 总深度

        Returns:
            预估滑点百分比
        """
        # 简化模型：滑点 ≈ 价差 + 深度不足惩罚
        base_slippage = spread_pct
        depth_penalty = max(0, (100 - total_depth) / 1000)
        return base_slippage + depth_penalty

    def _format_position_info(self, position_info: Dict) -> str:
        """
        格式化持仓信息为文本

        Args:
            position_info: 持仓信息字典

        Returns:
            格式化的持仓信息文本
        """
        if not position_info or not position_info.get('positions'):
            return "无持仓"

        positions = position_info.get('positions', [])
        text_lines = []

        for pos in positions:
            inst_id = pos.get('instId', 'N/A')
            pos_side = pos.get('posSide', 'N/A')
            side_text = "多头" if pos_side == 'long' else "空头" if pos_side == 'short' else pos_side

            pos_amt = float(pos.get('pos', 0))
            avg_px = float(pos.get('avgPx', 0))
            mark_px = float(pos.get('markPx', 0))
            upl = float(pos.get('upl', 0))
            upl_ratio = float(pos.get('uplRatio', 0)) * 100
            leverage = pos.get('lever', 'N/A')

            text_lines.append(f"交易对: {inst_id}")
            text_lines.append(f"方向: {side_text}")
            text_lines.append(f"数量: {pos_amt}张")
            text_lines.append(f"杠杆: {leverage}x")
            text_lines.append(f"开仓均价: {avg_px:.2f}")
            text_lines.append(f"标记价格: {mark_px:.2f}")
            text_lines.append(f"未实现盈亏: {upl:.2f} USDT ({upl_ratio:+.2f}%)")
            text_lines.append("")

        return "\n".join(text_lines) if text_lines else "无持仓"

    def _format_position_info_enhanced(self, position_info: Dict) -> str:
        """
        格式化持仓信息为文本（增强版，包含持仓时长和AI决策历史）

        Args:
            position_info: 持仓信息字典（包含decisions字段）

        Returns:
            格式化的持仓信息文本
        """
        if not position_info or not position_info.get('positions'):
            return "无持仓"

        positions = position_info.get('positions', [])
        text_lines = []

        for pos in positions:
            inst_id = pos.get('instId', 'N/A')
            pos_side = pos.get('posSide', 'N/A')
            side_text = "多头" if pos_side == 'long' else "空头" if pos_side == 'short' else pos_side

            pos_amt = float(pos.get('pos', 0))
            avg_px = float(pos.get('avgPx', 0))
            mark_px = float(pos.get('markPx', 0))
            upl = float(pos.get('upl', 0))
            upl_ratio = float(pos.get('uplRatio', 0)) * 100
            leverage = float(pos.get('lever', 20))

            # 计算仓位价值（用于估算手续费）
            # notionalUsd 字段包含仓位的美元价值
            notional_usd = float(pos.get('notionalUsd', 0))
            if notional_usd == 0:
                # 如果没有notionalUsd，手动计算：数量 × 标记价格 × 合约面值
                # 对于BTC-USDT-SWAP，合约面值通常是0.01 BTC
                notional_usd = pos_amt * mark_px * 0.01

            # 估算双向手续费（开仓 + 平仓）
            # 假设最坏情况：开仓吃单(0.05%) + 平仓吃单(0.05%) = 0.1%
            # 最好情况：开仓挂单(0.02%) + 平仓挂单(0.02%) = 0.04%
            # 保守估算使用混合：开仓挂单(0.02%) + 平仓吃单(0.05%) = 0.07%
            estimated_fee_worst = notional_usd * 0.001  # 0.1% (吃单双向)
            estimated_fee_best = notional_usd * 0.0004  # 0.04% (挂单双向)
            estimated_fee_mixed = notional_usd * 0.0007  # 0.07% (混合)

            # 计算扣除手续费后的净盈亏
            net_upl_worst = upl - estimated_fee_worst
            net_upl_best = upl - estimated_fee_best
            net_upl_mixed = upl - estimated_fee_mixed

            # 计算持仓时长（从cTime字段）
            c_time = pos.get('cTime')
            holding_duration = "未知"
            if c_time:
                from datetime import datetime
                try:
                    # OKX的cTime是毫秒时间戳
                    create_time = datetime.fromtimestamp(int(c_time) / 1000)
                    now = datetime.now()
                    duration = now - create_time

                    # 格式化持仓时长
                    hours = duration.seconds // 3600
                    minutes = (duration.seconds % 3600) // 60

                    if duration.days > 0:
                        holding_duration = f"{duration.days}天{hours}小时"
                    elif hours > 0:
                        holding_duration = f"{hours}小时{minutes}分钟"
                    else:
                        holding_duration = f"{minutes}分钟"
                except:
                    holding_duration = "未知"

            text_lines.append(f"交易对: {inst_id}")
            text_lines.append(f"方向: {side_text}")
            text_lines.append(f"数量: {pos_amt}张")
            text_lines.append(f"杠杆: {leverage:.0f}x")
            text_lines.append(f"开仓均价: {avg_px:.2f}")
            text_lines.append(f"标记价格: {mark_px:.2f}")
            text_lines.append(f"仓位价值: {notional_usd:.2f} USDT")
            text_lines.append(f"未实现盈亏: {upl:.2f} USDT ({upl_ratio:+.2f}%)")
            text_lines.append(f"**预估手续费 (双向)**: 挂单 {estimated_fee_best:.2f} USDT | 混合 {estimated_fee_mixed:.2f} USDT | 吃单 {estimated_fee_worst:.2f} USDT")
            text_lines.append(f"**扣费后净盈亏**: 最优 {net_upl_best:+.2f} USDT | 预期 {net_upl_mixed:+.2f} USDT | 最差 {net_upl_worst:+.2f} USDT")
            text_lines.append(f"**持仓时长: {holding_duration}** ← 重要！考虑持仓时间和手续费成本再做决策")

            # 添加AI决策历史
            decisions = pos.get('decisions', [])
            if decisions:
                text_lines.append("\n**该仓位的AI决策历史:**")
                for i, decision in enumerate(decisions, 1):
                    decision_time = decision.get('timestamp', 'N/A')
                    action = decision.get('action', 'N/A')
                    confidence = decision.get('confidence', 'N/A')
                    reason = decision.get('reason', '')


                    text_lines.append(f"  {i}. [{decision_time}] {action} (信心: {confidence}%)")
                    # 只显示reason的前100个字符
                    if reason:
                        reason_short = reason 
                        #text_lines.append(f"     理由: {reason_short}")

                    # 如果是调整决策，显示调整内容（新版：使用adjust_data）
                    if action == 'ADJUST_STOP':
                        adjust_data = decision.get('adjust_data')
                        if adjust_data:
                            tp_layers = adjust_data.get('take_profit', [])
                            sl_layers = adjust_data.get('stop_loss', [])

                            text_lines.append(f"     调整: 止盈{len(tp_layers)}层, 止损{len(sl_layers)}层")

                            if tp_layers:
                                tp_prices = [f"{layer['price']:.2f} ({layer['size']}张)" for layer in tp_layers]  # 只显示前2层
                                text_lines.append(f"     止盈价: {', '.join(tp_prices)}")

                            if sl_layers:
                                sl_prices = [f"{layer['price']:.2f} ({layer['size']}张)" for layer in sl_layers]
                                text_lines.append(f"     止损价: {', '.join(sl_prices)}")

            text_lines.append("")

        return "\n".join(text_lines) if text_lines else "无持仓"

    def calculate_composite_score(self, features: Dict) -> Dict:
        """
        计算综合评分

        Args:
            features: 所有特征

        Returns:
            评分字典
        """
        kline = features.get('kline', {})
        trades = features.get('trades', {})
        orderbook = features.get('orderbook', {})

        score = {
            'kline_score': 0,
            'trade_score': 0,
            'orderbook_score': 0,
            'total_score': 0,
            'signal': 'HOLD'
        }

        # K线评分 (0-50)
        if kline:
            k_score = 0

            # 趋势 (20分)
            if kline.get('ma_trend') == 'up' and kline.get('macd_trend') == 'bullish':
                k_score += 20
            elif kline.get('ma_trend') == 'down' and kline.get('macd_trend') == 'bearish':
                k_score -= 20

            # RSI (15分)
            rsi = kline.get('rsi', 50)
            if 30 < rsi < 70:
                k_score += 15
            elif rsi > 80 or rsi < 20:
                k_score -= 15

            # 成交量 (15分)
            if kline.get('volume_trend') == 'high':
                k_score += 15

            score['kline_score'] = k_score

        # 成交压力评分 (0-40)
        if trades:
            t_score = 0

            # 压力趋势 (30分)
            pressure_trend = trades.get('pressure_trend')
            if pressure_trend == 'strong_buy':
                t_score += 30
            elif pressure_trend == 'strong_sell':
                t_score -= 30

            # 鲸鱼活动 (10分)
            whale = trades.get('whale_activity')
            if whale == 'buying':
                t_score += 10
            elif whale == 'selling':
                t_score -= 10

            score['trade_score'] = t_score

        # 订单簿评分 (0-10)
        if orderbook:
            o_score = 0

            # 流动性 (5分)
            if orderbook.get('liquidity_score') == 'good':
                o_score += 5
            elif orderbook.get('liquidity_score') == 'poor':
                o_score -= 5

            # 深度失衡 (5分)
            imbalance = orderbook.get('depth_imbalance')
            if imbalance == 'bid_heavy':
                o_score += 5
            elif imbalance == 'ask_heavy':
                o_score -= 5

            score['orderbook_score'] = o_score

        # 总分
        score['total_score'] = score['kline_score'] + score['trade_score'] + score['orderbook_score']

        # 信号判断
        total = score['total_score']
        if total > 50:
            score['signal'] = 'STRONG_BUY'
        elif total > 30:
            score['signal'] = 'BUY'
        elif total < -50:
            score['signal'] = 'STRONG_SELL'
        elif total < -30:
            score['signal'] = 'SELL'
        else:
            score['signal'] = 'HOLD'

        return score

    def extract_all_features_with_raw(self, symbol: str, timeframe: str = '5m') -> Dict:
        """
        提取所有特征（方案A：混合原始数据 + 技术指标）

        Args:
            symbol: 交易对
            timeframe: K线周期

        Returns:
            特征字典（包含原始数据）
        """
        features = {}

        # 1. K线：技术指标 + 原始K线（最近20-30根）
        features['kline'] = self.extract_kline_features(symbol, timeframe)
        features['kline_raw'] = self.extract_raw_klines(symbol, timeframe, limit=30)

        # 2. 逐笔成交：聚合压力指标（保持原样）
        features['trades'] = self.extract_trade_features(symbol)

        # 3. 订单簿：技术指标 + 原始档位（Top10）
        features['orderbook'] = self.extract_orderbook_features(symbol)
        features['orderbook_raw'] = self.extract_raw_orderbook(symbol)

        # 4. 综合评分（供AI参考）
        features['score'] = self.calculate_composite_score(features)

        return features

    def extract_dual_timeframe_features(self, symbol: str) -> Dict:
        """
        提取双周期特征（5分钟 + 4小时）
        技术指标返回历史序列（最近10个值，降序：[最新, 前1, 前2, ...]）

        Args:
            symbol: 交易对

        Returns:
            特征字典（包含短期和长期数据）
        """
        features = {}

        # === 短期指标（5分钟K线） ===
        short_tf = '5m'
        history_length = 10  # 返回最近10个历史值
        import time
        # 获取最近100根5分钟K线
        ts=time.time()
        klines_5m = self.db.get_recent_klines(symbol, short_tf, limit=100)
        print(f'5mK线耗时：{time.time()-ts}')
        ts=time.time()
        if klines_5m and len(klines_5m) >= 20:
            df_5m = pd.DataFrame(klines_5m)
            close_5m = df_5m['close']
            volume_5m = df_5m['volume']

            # 计算技术指标序列
            ema_20_series = close_5m.ewm(span=20).mean()
            rsi_7_series = self._calculate_rsi_series(close_5m, 7)
            rsi_14_series = self._calculate_rsi_series(close_5m, 14)
            macd_data = self._calculate_macd_series(close_5m)

            # 短期技术指标（降序：最新值在前）
            features['short_term'] = {
                'current_price': close_5m.iloc[-1],
                'ema_20': ema_20_series.iloc[-history_length:].tolist()[::-1],  # 降序
                'rsi_7': rsi_7_series.iloc[-history_length:].tolist()[::-1],
                'rsi_14': rsi_14_series.iloc[-history_length:].tolist()[::-1],
                'macd_dif': macd_data['dif'].iloc[-history_length:].tolist()[::-1],
                'macd_dea': macd_data['dea'].iloc[-history_length:].tolist()[::-1],
                'macd_histogram': macd_data['histogram'].iloc[-history_length:].tolist()[::-1],
            }

            # 原始K线数据（最近15根用于形态识别）
            features['kline_raw_5m'] = self.extract_raw_klines(symbol, short_tf, limit=50)
        else:
            logger.warning(f"5分钟K线数据不足: {symbol}")
            features['short_term'] = {}
            features['kline_raw_5m'] = []
        print(f'5mK线处理耗时：{time.time()-ts}')

        # === 中期指标（30分钟K线） ===
        mid_tf = '30m'
        ts=time.time()
        # 获取最近100根30分钟K线
        klines_30m = self.db.get_recent_klines(symbol, mid_tf, limit=100)
        print(f'30mK线耗时：{time.time()-ts}')
        ts=time.time()
        if klines_30m and len(klines_30m) >= 20:
            df_30m = pd.DataFrame(klines_30m)
            close_30m = df_30m['close']
            high_30m = df_30m['high']
            low_30m = df_30m['low']
            volume_30m = df_30m['volume']

            # 计算技术指标序列
            ema_20_series_30m = close_30m.ewm(span=20).mean()
            rsi_7_series_30m = self._calculate_rsi_series(close_30m, 7)
            rsi_14_series_30m = self._calculate_rsi_series(close_30m, 14)
            macd_data_30m = self._calculate_macd_series(close_30m)
            atr_series_30m = self._calculate_atr_series(high_30m, low_30m, close_30m, period=14)
            volume_avg_series_30m = volume_30m.rolling(20).mean()

            # 中期技术指标（降序：最新值在前）
            features['mid_term'] = {
                'ema_20': ema_20_series_30m.iloc[-history_length:].tolist()[::-1],
                'rsi_7': rsi_7_series_30m.iloc[-history_length:].tolist()[::-1],
                'rsi_14': rsi_14_series_30m.iloc[-history_length:].tolist()[::-1],
                'macd_dif': macd_data_30m['dif'].iloc[-history_length:].tolist()[::-1],
                'macd_dea': macd_data_30m['dea'].iloc[-history_length:].tolist()[::-1],
                'macd_histogram': macd_data_30m['histogram'].iloc[-history_length:].tolist()[::-1],
                'atr_14': atr_series_30m.iloc[-history_length:].tolist()[::-1],
                'volume_avg': volume_avg_series_30m.iloc[-history_length:].tolist()[::-1],
            }

            # 原始K线数据（最近15根用于形态识别）
            features['kline_raw_30m'] = self.extract_raw_klines(symbol, mid_tf, limit=15)
        else:
            logger.warning(f"30分钟K线数据不足: {symbol}")
            features['mid_term'] = {}
            features['kline_raw_30m'] = []
        print(f'30mK线处理耗时：{time.time()-ts}')

        # === 长期趋势（4小时K线） ===
        long_tf = '4H'
        ts=time.time()
        # 获取最近100根4小时K线
        klines_4h = self.db.get_recent_klines(symbol, long_tf, limit=100)
        print(f'4HK线耗时：{time.time()-ts}')
        ts=time.time()
        if klines_4h and len(klines_4h) >= 20:
            df_4h = pd.DataFrame(klines_4h)
            close_4h = df_4h['close']
            high_4h = df_4h['high']
            low_4h = df_4h['low']
            volume_4h = df_4h['volume']

            # 计算技术指标序列
            ema_20_series_4h = close_4h.ewm(span=20).mean()
            atr_series = self._calculate_atr_series(high_4h, low_4h, close_4h, period=3)
            volume_avg_series = volume_4h.rolling(20).mean()
            macd_data_4h = self._calculate_macd_series(close_4h)
            rsi_14_series_4h = self._calculate_rsi_series(close_4h, 14)

            # 调整当前K线成交量（按已过时间比例推算完整成交量）
            # 获取最后一根K线的时间戳
            last_kline = klines_4h[-1]
            kline_start_time = last_kline['timestamp']  # K线开始时间（毫秒）
            current_time = int(datetime.now().timestamp() * 1000)  # 当前时间（毫秒）

            # 4小时 = 4 * 60 * 60 * 1000 = 14400000 毫秒
            timeframe_ms = 4 * 60 * 60 * 1000
            elapsed_ms = current_time - kline_start_time
            elapsed_ratio = min(elapsed_ms / timeframe_ms, 1.0)  # 限制在[0, 1]

            # 调整当前成交量（如果已过25%时间，成交量1000万，则推算完整成交量 = 1000万 / 0.25 = 4000万）
            current_volume = volume_4h.iloc[-1]
            if elapsed_ratio > 0.1:  # 至少过了10%时间才调整
                adjusted_current_volume = current_volume / elapsed_ratio
            else:
                # 刚开始的K线，用前一根K线的成交量作为参考
                adjusted_current_volume = volume_4h.iloc[-2] if len(volume_4h) >= 2 else current_volume

            # 重新计算成交量序列（最后一个值用调整后的）
            volume_adjusted = volume_4h.copy()
            volume_adjusted.iloc[-1] = adjusted_current_volume

            # 计算调整后的成交量比率
            avg_volume = volume_avg_series.iloc[-1]  # 使用当前均值
            volume_ratio = adjusted_current_volume / avg_volume if avg_volume > 0 else 1.0

            # 长期技术指标（降序：最新值在前）
            features['long_term'] = {
                'ema_20': ema_20_series_4h.iloc[-history_length:].tolist()[::-1],
                'atr_3': atr_series.iloc[-history_length:].tolist()[::-1],
                'volume_avg': volume_avg_series.iloc[-history_length:].tolist()[::-1],
                'volume_ratio': volume_ratio,  # 使用调整后的成交量计算
                'volume_elapsed_pct': elapsed_ratio * 100,  # 当前K线已过时间百分比
                'macd_dif': macd_data_4h['dif'].iloc[-history_length:].tolist()[::-1],
                'macd_dea': macd_data_4h['dea'].iloc[-history_length:].tolist()[::-1],
                'macd_histogram': macd_data_4h['histogram'].iloc[-history_length:].tolist()[::-1],
                'rsi_14': rsi_14_series_4h.iloc[-history_length:].tolist()[::-1],
            }
            features['kline_raw_4H'] = self.extract_raw_klines(symbol, long_tf, limit=15)
        else:
            logger.warning(f"4小时K线数据不足: {symbol}")
            features['long_term'] = {}
            features['kline_raw_4H'] = []
        print(f'4HK线处理耗时：{time.time()-ts}')
        # === 订单簿数据（保持原样） ===
        ts=time.time()
        features['orderbook_raw'] = self.extract_raw_orderbook(symbol)
        print(f'订单簿耗时:{time.time()-ts}')
        return features

    def _calculate_rsi_series(self, close: pd.Series, period: int = 14) -> pd.Series:
        """
        计算RSI指标序列

        Args:
            close: 收盘价序列
            period: RSI周期

        Returns:
            RSI序列
        """
        try:
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(period).mean()
            loss = -delta.where(delta < 0, 0).rolling(period).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return rsi.fillna(50)  # 填充NaN为50
        except:
            return pd.Series([50.0] * len(close))

    def _calculate_macd_series(self, close: pd.Series) -> Dict:
        """
        计算MACD指标序列

        Args:
            close: 收盘价序列

        Returns:
            MACD字典 {dif, dea, histogram}
        """
        try:
            ema12 = close.ewm(span=12).mean()
            ema26 = close.ewm(span=26).mean()
            dif = ema12 - ema26
            dea = dif.ewm(span=9).mean()
            histogram = dif - dea

            return {
                'dif': dif.fillna(0),
                'dea': dea.fillna(0),
                'histogram': histogram.fillna(0)
            }
        except:
            return {
                'dif': pd.Series([0] * len(close)),
                'dea': pd.Series([0] * len(close)),
                'histogram': pd.Series([0] * len(close))
            }

    def _calculate_atr_series(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """
        计算ATR指标序列

        Args:
            high: 最高价序列
            low: 最低价序列
            close: 收盘价序列
            period: ATR周期

        Returns:
            ATR序列
        """
        try:
            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs()
            ], axis=1).max(axis=1)
            atr = tr.rolling(period).mean()
            return atr.fillna(0)
        except:
            return pd.Series([0.0] * len(high))

    def extract_raw_klines(self, symbol: str, timeframe: str, limit: int = 30) -> list:
        """
        提取原始K线数据（供AI自主分析）

        Args:
            symbol: 交易对
            timeframe: K线周期
            limit: 数量限制（建议20-30根，避免token过多）

        Returns:
            K线列表 [{'time', 'open', 'high', 'low', 'close', 'volume', 'confirmed'}, ...]
        """
        try:
            klines = self.db.get_recent_klines(symbol, timeframe, limit=limit)
            if not klines:
                return []

            # 简化数据格式（包含是否完结状态）
            return [
                {
                    'time': datetime.fromtimestamp(k['timestamp'] / 1000).strftime('%H:%M'),
                    'open': round(k['open'], 2),
                    'high': round(k['high'], 2),
                    'low': round(k['low'], 2),
                    'close': round(k['close'], 2),
                    'volume': round(k['volume'], 2),
                    'confirmed': k.get('is_confirmed', True)  # 是否完结（True=已完结，False=未完结）
                }
                for k in klines[-limit:]  # 只取最后limit根
            ]

        except Exception as e:
            logger.error(f"提取原始K线失败: {e}")
            return []

    def _aggregate_orderbook_layer(self, orders: list, start_idx: int, end_idx: int, step: int) -> str:
        """
        聚合订单簿层级数据

        Args:
            orders: 订单列表 [[price, size], ...]
            start_idx: 起始索引
            end_idx: 结束索引
            step: 聚合步长

        Returns:
            聚合后的文本
        """
        if start_idx >= len(orders):
            return ""

        result = []
        for i in range(start_idx, min(end_idx, len(orders)), step):
            chunk = orders[i:min(i+step, end_idx, len(orders))]
            if not chunk:
                continue

            # 计算价格区间
            prices = [p[0] for p in chunk]
            sizes = [p[1] for p in chunk]

            price_min = min(prices)
            price_max = max(prices)
            total_size = sum(sizes)
            avg_size = total_size / len(chunk)

            # 识别大单（超过平均值3倍）
            large_orders = [f"{p[0]:.2f}×{p[1]:.2f}" for p in chunk if p[1] > avg_size * 3]
            wall_info = f" [墙:{','.join(large_orders)}]" if large_orders else ""

            result.append(
                f"[{i+1}-{min(i+step, end_idx)}档] "
                f"{price_min:.2f}~{price_max:.2f} "
                f"总量{total_size:.2f} "
                f"均{avg_size:.2f}"
                f"{wall_info}"
            )

        return "\n".join(result)

    def _format_orderbook_compressed(self, orderbook_raw: Dict) -> str:
        """
        格式化压缩订单簿（方案1：分层聚合，保留前50档）

        Args:
            orderbook_raw: 原始订单簿数据

        Returns:
            压缩后的订单簿文本
        """
        if not orderbook_raw or not orderbook_raw.get('bids') or not orderbook_raw.get('asks'):
            return "订单簿数据不可用"

        bids = orderbook_raw['bids']
        asks = orderbook_raw['asks']

        # 计算统计信息
        total_bids = len(bids)
        total_asks = len(asks)
        total_bid_size = sum(b[1] for b in bids)
        total_ask_size = sum(a[1] for a in asks)

        # 构建输出
        output = []
        output.append(f"=== 订单簿深度分析（400档实时） ===")
        output.append(f"总深度: 买={total_bid_size:.2f}张 / 卖={total_ask_size:.2f}张 (买卖比={total_bid_size/total_ask_size:.2f})")
        output.append(f"档位数: 买={total_bids}档 / 卖={total_asks}档")
        output.append("")

        # === 买盘 (Bids) ===
        output.append("【买盘档位】")
        output.append("--- 前50档（完整数据，价格发现区域） ---")

        # 前50档完整显示
        for i in range(min(50, len(bids))):
            bid = bids[i]
            output.append(f"买{i+1:2d}: {bid[0]:8.2f} × {bid[1]:6.4f}")

        output.append("")
        output.append("--- 51-150档（聚合，每10档） ---")
        output.append(self._aggregate_orderbook_layer(bids, 50, 150, 10))

        output.append("")
        output.append("--- 151-300档（聚合，每25档） ---")
        output.append(self._aggregate_orderbook_layer(bids, 150, 300, 25))

        output.append("")
        output.append("--- 301-400档（聚合，每50档，远端埋伏） ---")
        output.append(self._aggregate_orderbook_layer(bids, 300, 400, 50))

        output.append("")
        output.append("")

        # === 卖盘 (Asks) ===
        output.append("【卖盘档位】")
        output.append("--- 前50档（完整数据，价格发现区域） ---")

        # 前50档完整显示
        for i in range(min(50, len(asks))):
            ask = asks[i]
            output.append(f"卖{i+1:2d}: {ask[0]:8.2f} × {ask[1]:6.4f}")

        output.append("")
        output.append("--- 51-150档（聚合，每10档） ---")
        output.append(self._aggregate_orderbook_layer(asks, 50, 150, 10))

        output.append("")
        output.append("--- 151-300档（聚合，每25档） ---")
        output.append(self._aggregate_orderbook_layer(asks, 150, 300, 25))

        output.append("")
        output.append("--- 301-400档（聚合，每50档，远端埋伏） ---")
        output.append(self._aggregate_orderbook_layer(asks, 300, 400, 50))

        return "\n".join(output)

    def extract_raw_orderbook(self, symbol: str) -> Dict:
        """
        提取原始订单簿档位数据（供AI自主分析）

        Args:
            symbol: 交易对

        Returns:
            {'timestamp', 'bids': [[price, size], ...], 'asks': [[price, size], ...]}
        """
        try:
            # 从Redis获取实时订单簿（前400档）
            orderbook = self.db.get_orderbook_from_redis(symbol, depth=100)
            if not orderbook or not orderbook.get('bids') or not orderbook.get('asks'):
                return {}

            # Redis返回格式：[price, size, orders]
            # 只取前2个值：price和size
            bids_formatted = []
            for bid in orderbook['bids'][:100]:
                if len(bid) >= 2:
                    bids_formatted.append([round(float(bid[0]), 2), round(float(bid[1]), 4)])

            asks_formatted = []
            for ask in orderbook['asks'][:100]:
                if len(ask) >= 2:
                    asks_formatted.append([round(float(ask[0]), 2), round(float(ask[1]), 4)])

            return {
                'timestamp': orderbook['timestamp'],
                'bids': bids_formatted,
                'asks': asks_formatted
            }

        except Exception as e:
            logger.error(f"提取原始订单簿失败: {e}")
            import traceback
            logger.debug(f"错误堆栈: {traceback.format_exc()}")
            return {}

    def generate_ai_prompt(self, symbol: str, features: Dict, position_info: Dict = None, leverage: int = 20) -> str:
        """
        生成AI分析的Prompt

        Args:
            symbol: 交易对
            features: 特征字典
            position_info: 当前持仓信息（可选）
            leverage: 杠杆倍数（默认20）

        Returns:
            Prompt文本
        """
        kline = features.get('kline', {})
        trades = features.get('trades', {})
        orderbook = features.get('orderbook', {})
        score = features.get('score', {})

        # 持仓信息文本
        position_text = self._format_position_info(position_info) if position_info else "无持仓"

        prompt = f"""你是专业的加密货币短线交易分析师。请基于以下多维数据分析 {symbol} 的交易机会：

## 0. 当前持仓状态 (重要！)
{position_text}

## 1. K线技术面 (趋势判断)
- 当前价格: {kline.get('current_price', 'N/A'):.2f} USDT
- MA趋势: {kline.get('ma_trend', 'N/A')} (MA5={kline.get('ma5', 0):.2f}, MA20={kline.get('ma20', 0):.2f})
- RSI(14): {kline.get('rsi', 0):.1f} ({kline.get('rsi_state', 'N/A')})
- MACD信号: {kline.get('macd_trend', 'N/A')} (柱状图={kline.get('macd_histogram', 0):.4f})
- 布林带位置: {kline.get('bb_state', 'N/A')} (位置={kline.get('bb_position', 0):.2f})
- 5周期涨跌: {kline.get('price_change_5', 0):.2f}%
- 20周期涨跌: {kline.get('price_change_20', 0):.2f}%
- 成交量趋势: {kline.get('volume_trend', 'N/A')} (比率={kline.get('volume_ratio', 0):.2f})
- ATR波动率: {kline.get('atr_pct', 0):.2f}%

## 2. 逐笔成交压力 (真实买卖力量)
- 1分钟压力比: {trades.get('pressure_ratio_1m', 0):.2f} (买/卖)
- 5分钟压力比: {trades.get('pressure_ratio_5m', 0):.2f}
- 15分钟压力比: {trades.get('pressure_ratio_15m', 0):.2f}
- 压力趋势: {trades.get('pressure_trend', 'N/A')}
- 大单活动: {trades.get('whale_activity', 'N/A')}
- 买入主导度(5m): {trades.get('buy_dominance_5m', 0)*100:.1f}%
- 大单买入占比(5m): {trades.get('large_buy_ratio_5m', 0)*100:.1f}%
- 交易强度(5m): {trades.get('trade_intensity_5m', 0)} 笔

## 3. 订单簿流动性 (风控参考)
- 价差: {orderbook.get('spread_pct', 0):.3f}%
- 5档深度: 买={orderbook.get('bid_depth_5', 0):.1f} / 卖={orderbook.get('ask_depth_5', 0):.1f}
- 深度失衡: {orderbook.get('depth_imbalance', 'N/A')}
- 流动性评分: {orderbook.get('liquidity_score', 'N/A')}
- 预估滑点: {orderbook.get('estimated_slippage_1pct', 0)*100:.3f}%

## 4. 综合评分
- K线评分: {score.get('kline_score', 0)}/50
- 成交评分: {score.get('trade_score', 0)}/40
- 订单簿评分: {score.get('orderbook_score', 0)}/10
- 总分: {score.get('total_score', 0)}/100
- 初步信号: {score.get('signal', 'HOLD')}

## 输出要求
**必须严格按照以下JSON格式输出，不要包含任何其他文本**：

{{
  "signal": "OPEN_LONG | OPEN_SHORT | CLOSE_LONG | CLOSE_SHORT | HOLD",
  "confidence": 0-100的整数,
  "reason": "具体操作理由，必须引用数据支持",
  "adjust_data": {{
    "take_profit": [{{"size": float, "price": float}}, ...],
    "stop_loss": [{{"size": float, "price": float}}, ...]
  }},
  "holding_time": "10分钟 | 30分钟 | 1小时 | 2小时",
  "risk_warning": "需要注意的反向信号或风险点"
}}

**重要说明**：
- `adjust_data` 包含止盈止损层级信息，每层包含数量(size)和价格(price)
- 单层示例: {{"take_profit": [{{"size": 0.1, "price": 112000}}], "stop_loss": [{{"size": 0.1, "price": 110000}}]}}
- 多层示例: {{"take_profit": [{{"size": 0.03, "price": 112000}}, {{"size": 0.07, "price": 111500}}], "stop_loss": [{{"size": 0.1, "price": 110000}}]}}
- 在{leverage}倍杠杆下，价格变动1%对应账户盈亏{leverage}%

## 分析原则
- 优先考虑逐笔成交压力（真实买卖），其次是K线趋势，最后是订单簿
- 如果压力趋势与K线趋势矛盾，降低置信度或建议观望
- 流动性差（价差>0.05%）时避免交易
- 置信度低于60%时建议HOLD
- 考虑手续费：挂单0.02%，吃单0.05%，双边成本0.04%-0.1%
- 盈亏比建议 > 3:1（止盈/止损比）才值得交易
- **高杠杆风险**: 当前使用{leverage}倍杠杆，1%价格波动={leverage}%盈亏，务必设置合理止损
- **层级设置**: 建议止损1-2层（保护本金），止盈2-5层（分批获利）
- **持仓管理**: 如已有持仓，需考虑是否加仓/减仓/反向平仓，避免过度集中风险
"""

        return prompt

    def _load_preset_prompt(self) -> str:
        """
        从本地存储加载预设prompt

        Returns:
            预设的prompt内容
        """
        try:
            # 获取项目根目录（使用模块顶部定义的全局 project_root）
            # 确保在wheel安装环境下也能找到正确的.env文件位置
            global project_root

            # 读取.env文件中的ACTIVE_PROMPT
            env_file_path = os.path.join(project_root, '.env')
            active_prompt_name = ""

            if os.path.exists(env_file_path):
                with open(env_file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        stripped = line.strip()
                        if stripped.startswith('ACTIVE_PROMPT='):
                            active_prompt_name = stripped.split('=', 1)[1].strip()
                            break

            # 如果有激活的prompt，从prompts.json加载
            if active_prompt_name:
                logger.info(f"[INFO] Loading active prompt: {active_prompt_name}")
                prompts_file = os.path.join(project_root, 'data', 'prompts.json')
                if os.path.exists(prompts_file):
                    import json
                    with open(prompts_file, 'r', encoding='utf-8') as f:
                        prompts = json.load(f)

                    # 查找对应的prompt
                    for prompt in prompts:
                        if prompt['name'] == active_prompt_name:
                            logger.info(f"[OK] Loaded prompt: {active_prompt_name}")
                            return prompt['content']

            # 如果没有找到，使用默认prompt
            logger.warning("[WARN] No active prompt found, using default")
            return self._get_default_preset_prompt()

        except Exception as e:
            logger.error(f"加载preset prompt失败: {e}")
            return self._get_default_preset_prompt()

    def _get_default_preset_prompt(self) -> str:
        """
        获取默认的预设prompt（后备方案）

        Returns:
            默认的prompt内容
        """
        return '''# 加密货币交易员系统提示

## 角色与核心指令
你是一名拥有数十年实战经验的顶尖量化交易员与风险管理者。你的核心使命是：在充满不确定性的金融市场中，持续识别并执行具有高概率优势的交易机会，同时严格执行风险纪律，实现资本的长期稳健增值。你的思维模式是系统性的、概率化的，并且完全自律。


### 基于整体账户
- **历史持仓** 客观分析过去多空交易的绩效，识别偏见模式
- **历史决策** 基于过去你做出的决策进行反思,是否当初的判断错误,还是保持当时的判断。
- **历史收益** 过去的收益是否达到了目标,或者是因为错误的判断导致了亏损,盈利是否达到了预取还是提前离场了,是否还有改进的空间




### 收益目标导向
- **风险调整收益**：同等重视做多和做空的高夏普比率机会
- **多空复利思维**：复合增长来自双向交易能力,避免单边依赖
- **机会成本**：避免低质量交易占用资金和注意力。同等评估做多和做空的机会成本


**资金效率考量**：
- 单位时间收益评估必须包含双向交易机会
- 避免资金长期闲置在低收益头寸中

### 头寸构建原则
- **不对称原则**：追求风险收益比有利的机会,无论方向
 做多：上行潜力 > 下行风险
 做空：下行潜力 > 上行风险

- **相关性认知**：平衡多空头寸,避免系统性方向暴露
- **环境适应性**：根据波动率调整多空比例,保持方向中性

## 交易决策框架：信号生成逻辑
### 入场条件（必须全部满足）
- 多维分析形成共识观点（至少两个分析维度支持）。
- 市场条件适合交易执行（例如：流动性充足）。
- 在多个品种和方向中优选最佳风险收益比
- 也许实时的市场情况很重要,但我认为更重要的是对仓位的管理。
- 明确评估反向风险并确认可控
- 每次决策前重置方向偏见


### 持仓条件
- 分析逻辑在多空视角下仍然有效
- 市场条件未发生损害当前头寸方向的重大变化
- 多空风险重新评估后仍支持当前持仓

## 思考模型：强制应用框架
在每次响应前,你必须按顺序执行以下分析步骤：

1. **独立思考**：拒绝市场共识,仅基于实时数据形成观点。
2. **多维度验证**：评估以下因素,并动态权衡其重要性（无需平等对待）：
   - 价格行为与技术信号（例如：支撑/阻力、动量指标）
   - 市场微观结构（例如：流动性分布、订单簿深度）
   - 多空力量对比（例如：资金费率、未平仓合约变化）
   - 系统风险（例如：波动率突变、相关性破裂）
   你将动态权衡这些因素,不平等对待。
3. **证伪思维**：主动寻找可能推翻你当前判断的证据（例如：如果看多,检查是否有隐藏的看空信号,如果看空,检查是否有隐藏的看多信号）。
4. **概率思维**：所有决策必须基于概率评估（使用置信度0-100）,不存在确定性机会。多空置信度必须基于客观证据权重,避免方向性偏见
5. **持续学习**：参考历史决策和市场变化,以及历史持仓的复盘总结内容,但避免过度依赖过去模式。
6. **是否过度调整**: 避免频繁调整,耐心等待市场变化。如果当前仓位不存在,我将基于新分析决策。.不调整的时候给出HOLD的信号即可。
7. **开仓方向**：注意,这个市场允许你做多,也允许你做空。 做空与做多使用相同的风险管理标准

'''

    def generate_ai_prompt_with_raw(
        self,
        symbol: str,
        features: Dict,
        position_info: Dict = None,
        leverage: int = 20,
        available_balance: float = 0.0,
        instrument_info: Dict = None,
        stop_orders: Dict = None,
        ai_decision_history: list = None,  # ✅ AI历史决策
        historical_positions: list = None,  # ✅ 新增：缓存的历史仓位
        performance_stats: Dict = None,  # ✅ 新增：缓存的收益统计
        funding_rate: Dict = None,  # ✅ 新增：资金费率信息
        taker_volume: list = None,  # ✅ 新增：主动买卖数据
        open_interest: list = None,  # ✅ 新增：持仓量数据
        tick_features: Dict = None,  # ✅ 新增：Tick特征聚合数据
        bot_runtime_minutes: int = 0  # ✅ 新增：机器人已运行时长（分钟）
    ) -> tuple:
        """
        生成AI分析的Prompt（双周期版本 + 账户信息 + 止盈止损状态 + 历史决策 + 历史仓位 + 资金费率 + 市场数据 + Tick特征 + 运行时长）
        返回(system_prompt, user_prompt)元组

        Args:
            symbol: 交易对
            features: 特征字典（包含双周期数据）
            position_info: 当前持仓信息（可选）
            leverage: 杠杆倍数（默认20）
            available_balance: 可用余额（默认0）
            instrument_info: 合约信息（默认None）
            stop_orders: 止盈止损订单（默认None）
            ai_decision_history: AI历史决策列表（默认None）
            historical_positions: 缓存的历史仓位列表（默认None）
            performance_stats: 缓存的收益统计（默认None）
            funding_rate: 资金费率信息（默认None）
            taker_volume: 主动买卖数据（默认None）
            open_interest: 持仓量数据（默认None）
            tick_features: Tick特征聚合数据（包含VWAP、成交量失衡等，默认None）
            bot_runtime_minutes: 机器人已运行时长（分钟，默认0）

        Returns:
            (system_prompt, user_prompt): 系统提示词和用户提示词
        """
        # 提取三周期数据（5m + 30m + 4H）
        from datetime import datetime, timezone, timedelta
        short_term = features.get('short_term', {})
        mid_term = features.get('mid_term', {})  # 新增：30分钟数据
        long_term = features.get('long_term', {})
        kline_raw_5m = features.get('kline_raw_5m', [])
        kline_raw_30m = features.get('kline_raw_30m', [])  # 新增：30分钟原始K线
        kline_raw_4H = features.get('kline_raw_4H',[])
        orderbook_raw = features.get('orderbook_raw', {})

        # 获取当前价格
        current_price = short_term.get('current_price', 0)

        # === 格式化账户与合约信息 ===
        # 提取合约参数
        ct_val = float(instrument_info.get('ctVal', '0.01')) if instrument_info else 0.01
        ct_mult = float(instrument_info.get('ctMult', '1')) if instrument_info else 1.0
        min_sz = float(instrument_info.get('minSz', '1')) if instrument_info else 0.01

        # 计算示例size的价值
        example_size = 100
        example_value = example_size * ct_val * ct_mult * current_price

        # 根据当前余额计算建议的最大size
        max_usable_balance = available_balance * 0.9  # 保留10%缓冲
        max_size = float(max_usable_balance * leverage / (ct_val * ct_mult * current_price))
        max_size = max(min_sz, max_size)  # 至少为最小下单量

        account_info_text = f"""## 0. 账户和合约信息

### 账户状态
- 可用余额: {available_balance:.2f} USDT
- 杠杆: {leverage}x

### 合约细则
- 合约面值 (ctVal): {ct_val} {symbol.split('-')[0]}/contract
- 合约乘数 (ctMult): {ct_mult}
- 最小下单数量 (minSz): {min_sz} contracts


**开仓数量【合约数量】（size） : {available_balance*0.1*leverage/current_price/ct_val} - {available_balance*0.8*leverage/current_price/ct_val}   （根据对机会和风险评估决定仓位大小）** 

"""

        # === Format Position Information ===
        position_text = self._format_position_info_enhanced(position_info) if position_info else "No Position"

        # === Format Historical Closed Positions (Recent 10) ===
        # 使用传入的缓存数据，而非查询数据库
        historical_positions_text = ""
        
        if historical_positions and len(historical_positions) > 0:
            historical_positions=historical_positions[:5]
            historical_positions_text = "\n## 1.1.历史仓位 (Recent 10 trades)\n\n"
            historical_positions_text += "| # | Side | Size | Entry | Exit | PnL | P&L% | Fee | Hold Time | Close Time |\n"
            historical_positions_text += "|---|------|------|-------|------|-----|------|-----|-----------|------------|\n"
            historical_positions_jcList=[]
            for i, pos in enumerate(historical_positions, 1):
                historical_positions_jctext=''
                side_text = "Long" if pos['pos_side'] == 'long' else "Short"
                size = pos['close_total_pos']
                entry_price = pos['avg_px']
                exit_price = pos['mark_px']
                pnl = pos['realized_pnl'] if pos.get('realized_pnl') is not None else pos['upl']
                pnl_pct = pos['upl_ratio'] * 100
                fee = pos['fee']

                # 计算持仓时长
                holding_seconds = pos.get('holding_duration_seconds', 0)
                if holding_seconds > 3600:
                    hold_time = f"{holding_seconds/3600:.1f}h"
                else:
                    hold_time = f"{holding_seconds/60:.0f}m"

                # 格式化平仓时间
                close_time_ts = pos['close_time']
                from datetime import datetime, timezone
                try:
                    # 将毫秒时间戳转换为北京时间
                    utc_time = datetime.fromtimestamp(close_time_ts / 1000, tz=timezone.utc)
                    beijing_time = utc_time.astimezone(timezone(timedelta(hours=8)))
                    close_time_str = beijing_time.strftime('%m-%d %H:%M')
                except:
                    close_time_str = 'N/A'

                historical_positions_text += f"| {i} | {side_text} | {size:.1f} | {entry_price:.2f} | {exit_price:.2f} | {pnl:.2f} | {pnl_pct:+.2f}% | {fee:.2f} | {hold_time} | {close_time_str} |\n"

                # 添加该仓位的AI决策历史
                decisions = pos.get('decisions', [])
                if decisions:
                    historical_positions_jctext += f"\n**仓位 #{i} 的AI决策历史:**\n"
                    for j, decision in enumerate(decisions, 1):
                        decision_time = decision.get('timestamp', 'N/A')
                        action = decision.get('action', 'N/A')
                        confidence = decision.get('confidence', 'N/A')
                        reason = decision.get('reason', '')

                        historical_positions_jctext += f"  {j}. [{decision_time}] {action} (信心: {confidence}%)\n"
                        if reason:
                            reason_short = reason
                            #historical_positions_jctext += f"     理由: {reason_short} \n"

                        # 如果是调整决策（新版：使用adjust_data）
                        if action == 'ADJUST_STOP':
                            adjust_data = decision.get('adjust_data')
                            if adjust_data:
                                tp_layers = adjust_data.get('take_profit', [])
                                sl_layers = adjust_data.get('stop_loss', [])

                                historical_positions_jctext += f"     调整: 止盈{len(tp_layers)}层, 止损{len(sl_layers)}层"

                                if tp_layers:
                                    tp_price = [ f'{layer["price"]:.2f} ({layer["size"]}张)' for layer in tp_layers]
                                    historical_positions_jctext += f", 止盈价: {', '.join(tp_price)}"

                                if sl_layers:
                                    sl_price = [ f'{layer["price"]:.2f} ({layer["size"]}张)' for layer in sl_layers]
                                    historical_positions_jctext += f", 止损价: {', '.join(sl_price)}"

                                historical_positions_jctext += "\n"

                    # 添加AI复盘总结（如果存在）
                    review_summary = pos.get('review_summary')
                    if review_summary:
                        historical_positions_jctext += f"\n**仓位 #{i} 的AI复盘总结:**\n"
                        historical_positions_jctext += f"{review_summary}\n"

                    historical_positions_jctext += "\n"
                    historical_positions_jcList.append(historical_positions_jctext)
            historical_positions_text+='\n'+( ''.join(historical_positions_jcList))
            
            # 添加历史收益率统计（使用传入的缓存数据）
            if performance_stats and performance_stats.get('total_trades', 0) > 0:
                historical_positions_text += f"\n**30 天业绩摘要**:\n"
                historical_positions_text += f"- 总交易次数: {performance_stats['total_trades']}, 胜率： {performance_stats['win_rate']:.1f}% ({performance_stats['winning_trades']}W/{performance_stats['losing_trades']}L)\n"
                historical_positions_text += f"- 总收益: {performance_stats['total_pnl']:.2f} USDT, 平均收益 {performance_stats['avg_pnl']:.2f} USDT\n"
                historical_positions_text += f"- Best: {performance_stats['max_winning']:.2f} USDT, Worst: {performance_stats['max_losing']:.2f} USDT\n"

                # 计算平均持仓时长（秒转小时）
                avg_holding_hours = performance_stats.get('avg_holding_seconds', 0) / 3600
                historical_positions_text += f"- Avg Holding Time: {avg_holding_hours:.1f} hours\n"
        else:
            historical_positions_text = "\n## 1.1. 历史平仓\n未找到历史交易（首次交易或数据库为空）\n"

        # === Format AI Decision History ===
        history_decisions_text = ""
        if ai_decision_history and len(ai_decision_history) > 0:
            history_decisions_text += "\n**重要提示**：以下是您最近 10 条带有时间戳的决策 JSON 响应。请使用它们来：\n"
            history_decisions_text += "检查上次 ADJUST_STOP 是否在 15 分钟之前（避免过度管理）\n"

            for i, decision_obj in enumerate(reversed(ai_decision_history), 1):
                # Reverse iteration, newest decision shows first
                # 支持两种格式：新格式（字典）和旧格式（字符串）
                if isinstance(decision_obj, dict):
                    timestamp = decision_obj.get('timestamp', 'N/A')
                    content = decision_obj.get('content', '')
                    history_decisions_text += f"### Decision #{i} - {timestamp} ({'Latest' if i == 1 else 'Oldest' if i == len(ai_decision_history) else 'Recent'})\n```json\n{content}\n```\n\n"
                else:
                    # 兼容旧格式（纯字符串，无时间戳）
                    history_decisions_text += f"### Decision #{i} ({'Latest' if i == 1 else 'Oldest' if i == len(ai_decision_history) else 'Recent'})\n```json\n{decision_obj}\n```\n\n"
        else:
            history_decisions_text = "\n没有历史决策记录 (first analysis)\n"

        # === Format Stop-loss/Take-profit Status ===
        stop_orders_text = ""
        if stop_orders:
            for pos_side, orders in stop_orders.items():
                side_text = "做多" if pos_side == 'long' else "做空"
                stop_orders_text += f"\n【{side_text} 仓位】\n"

                # 止损（列表，可能多层）
                sl_orders = orders.get('stop_loss', [])
                if sl_orders:
                    stop_orders_text += f"  - 止损 ({len(sl_orders)} 层):\n"
                    for i, sl in enumerate(sl_orders, 1):
                        price = sl.get('price', 0)
                        size = sl.get('size', 0)
                        stop_orders_text += f"    第 {i} 层: 价格 {price:.2f}, 数量 {size:.4f}\n"
                else:
                    stop_orders_text += f"  - 止损: 未设置\n"

                # 止盈（列表，可能多层）
                tp_orders = orders.get('take_profit', [])
                if tp_orders:
                    stop_orders_text += f"  - 止盈 ({len(tp_orders)} 层):\n"
                    for i, tp in enumerate(tp_orders, 1):
                        price = tp.get('price', 0)
                        size = tp.get('size', 0)
                        stop_orders_text += f"    第 {i} 层: 价格 {price:.2f}, 数量 {size:.4f}\n"
                else:
                    stop_orders_text += f"  - 止盈: 未设置\n"
        else:
            stop_orders_text = "\nNo active stop-loss/take-profit orders"

        # === Format Funding Rate ===
        funding_rate_text = ""
        if funding_rate:
            from datetime import datetime
            current_rate = float(funding_rate.get('fundingRate', 0))
            next_funding_time = funding_rate.get('nextFundingTime', '')

            # Convert timestamp to readable time
            if next_funding_time:
                try:
                    next_time_dt = datetime.fromtimestamp(int(next_funding_time) / 1000)
                    next_time_str = next_time_dt.strftime('%Y-%m-%d %H:%M:%S')
                except:
                    next_time_str = next_funding_time
            else:
                next_time_str = 'N/A'

            # Calculate annual rate
            annual_rate = current_rate * 3 * 365  # 3 times per day, 365 days

            funding_rate_text = f"""
**当前资金费率**: {current_rate*100:.4f}% (Annualized: {annual_rate*100:.2f}%)
**下次资金费率时间**: {next_time_str}
"""
        else:
            funding_rate_text = "\nFunding rate data not available"

        # === Format Market Data (Taker Volume and Open Interest) ===
        market_data_text = ""

        # 主动买卖数据格式化
        # API响应格式: [[timestamp, sell_vol, buy_vol], ...]
        if taker_volume and len(taker_volume) > 0:
            market_data_text += "\n### 主动买卖数据 (Taker Volume, 15分钟周期)\n"
            market_data_text += "| 时间 | 卖出量 | 买入量 | 买卖比 |\n"
            market_data_text += "|------|--------|--------|--------|\n"

            # 只显示最近10个数据点（避免过长）
            total_sell = 0
            total_buy = 0
            for item in taker_volume[:10]:
                try:
                    # item格式: [timestamp, sell_vol, buy_vol]
                    if isinstance(item, (list, tuple)) and len(item) >= 3:
                        ts = item[0]
                        sell_vol = float(item[1])
                        buy_vol = float(item[2])
                    else:
                        continue

                    total_sell += sell_vol
                    total_buy += buy_vol

                    # 计算买卖比
                    if sell_vol > 0:
                        ratio = buy_vol / sell_vol
                    else:
                        ratio = 0

      

                    # 格式化时间戳
                    try:
                        from datetime import datetime
                        time_str = datetime.fromtimestamp(int(ts) / 1000).strftime('%m-%d %H:%M')
                    except:
                        time_str = str(ts)

                    market_data_text += f"| {time_str} | {sell_vol:.2f} | {buy_vol:.2f} | {ratio:.2f} |\n"
                except Exception as e:
                    continue

            # 计算整体趋势
            overall_ratio = total_buy / total_sell if total_sell > 0 else 0
            market_data_text += f"\n**整体买卖力量** (最近10个15分钟): 卖出={total_sell:.2f}, 买入={total_buy:.2f}, 比率={overall_ratio:.2f}\n"
        else:
            market_data_text += "\n主动买卖数据不可用\n"

        # 持仓量和交易量数据格式化
        # API响应格式: [[timestamp, open_interest, volume], ...]
        if open_interest and len(open_interest) > 0:
            market_data_text += "\n### 持仓量和交易量 (Open Interest & Volume, 1小时周期)\n"
            market_data_text += "| 时间 | 持仓量 | 交易量 | 持仓变化 |\n"
            market_data_text += "|------|--------|--------|----------|\n"

            # 只显示最近10个数据点
            prev_oi = None
            first_oi = None
            last_oi = None

            for item in open_interest[:10]:
                try:
                    # item格式: [timestamp, open_interest, volume]
                    if isinstance(item, (list, tuple)) and len(item) >= 3:
                        ts = item[0]
                        oi = float(item[1])
                        vol = float(item[2])
                    else:
                        continue

                    if first_oi is None:
                        first_oi = oi
                    last_oi = oi

                    # 计算持仓变化
                    if prev_oi is not None:
                        oi_change = ((oi - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0
                        change_str = f"{oi_change:+.2f}%"
                    else:
                        change_str = "N/A"

                    prev_oi = oi

                    # 格式化时间戳
                    try:
                        from datetime import datetime
                        time_str = datetime.fromtimestamp(int(ts) / 1000).strftime('%m-%d %H:%M')
                    except:
                        time_str = str(ts)

                    market_data_text += f"| {time_str} | {oi:.0f} | {vol:.0f} | {change_str} |\n"
                except Exception as e:
                    continue

            # 计算持仓量趋势
            if first_oi is not None and last_oi is not None and last_oi > 0:
                oi_trend_pct = ((first_oi - last_oi) / last_oi * 100)

                if oi_trend_pct > 5:
                    oi_trend = "持仓增加（多空分歧加大）"
                elif oi_trend_pct < -5:
                    oi_trend = "持仓减少（多空分歧减小）"
                else:
                    oi_trend = "持仓稳定"

                market_data_text += f"\n**持仓量趋势**: {oi_trend_pct:+.2f}% \n"
        else:
            market_data_text += "\n持仓量数据不可用\n"

        # === Format Tick Features (逐笔成交聚合数据) ===
        tick_features_text = ""
        if tick_features:
            tick_features_text = "\n### Tick 特征聚合数据 (实时逐笔成交分析)\n"

            # 基础信息
            vwap = tick_features.get('vwap', 0)
            volume_imbalance = tick_features.get('volume_imbalance', 0)
            price_range = tick_features.get('price_range', 0)
            tick_count = tick_features.get('tick_count', 0)
            large_trade_ratio = tick_features.get('large_trade_ratio', 0)

            # 成交量数据
            buy_volume = tick_features.get('buy_volume', 0)
            sell_volume = tick_features.get('sell_volume', 0)
            total_volume = tick_features.get('total_volume', 0)

            # 价格数据
            max_price = tick_features.get('max_price', 0)
            min_price = tick_features.get('min_price', 0)
            avg_volume = tick_features.get('avg_volume', 0)

            # 时间戳
            timestamp = tick_features.get('timestamp', 0)
            try:
                from datetime import datetime
                tick_time_str = datetime.fromtimestamp(timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')
            except:
                tick_time_str = str(timestamp)

            tick_features_text += f"""
**数据时间**: {tick_time_str} 过去60秒的数据聚合

**价格特征**:
- VWAP (成交量加权均价): {vwap:.2f}
- 价格波动范围: {price_range:.2f} ({(price_range/current_price*100):.2f}%)
- 最高价: {max_price:.2f}
- 最低价: {min_price:.2f}

**成交量分析**:
- 总成交量: {total_volume:.2f}
- 买入量: {buy_volume:.2f} ({(buy_volume/total_volume*100):.1f}%)
- 卖出量: {sell_volume:.2f} ({(sell_volume/total_volume*100):.1f}%)
- 成交量失衡比率: {volume_imbalance:.2f} (>0 买盘强，<0 卖盘强)

**交易行为**:
- Tick 数量: {tick_count}
- 平均单笔成交量: {avg_volume:.2f}
- 大单比例: {large_trade_ratio:.2%}


"""
        else:
            tick_features_text = "\n### Tick 特征聚合数据\n*数据不可用（实时采集器未运行或 Redis 无数据）*\n"

        # === Format 5-minute K-line Table ===
        kline_table = ""
        if kline_raw_5m:
            kline_table = "Time    | Open    | High    | Low     | Close   | Volume  | Status\n"
            kline_table += "--------|---------|---------|---------|---------|---------|------\n"
            for k in kline_raw_5m:
                status = "✓" if k.get('confirmed', True) else "⟳"
                kline_table += (
                    f"{k['time']} | {k['open']:7.2f} | {k['high']:7.2f} | "
                    f"{k['low']:7.2f} | {k['close']:7.2f} | {k['volume']:7.2f} | {status}\n"
                )
            kline_table += "\nNote: ✓=Confirmed K-line (fixed data), ⟳=Unconfirmed K-line (updating in real-time)"
        else:
            kline_table = "K-line data not available"

        # === Format 30-minute K-line Table ===
        kline_table_30m = ""
        if kline_raw_30m:
            kline_table_30m = "Time    | Open    | High    | Low     | Close   | Volume  | Status\n"
            kline_table_30m += "--------|---------|---------|---------|---------|---------|------\n"
            for k in kline_raw_30m:
                status = "✓" if k.get('confirmed', True) else "⟳"
                kline_table_30m += (
                    f"{k['time']} | {k['open']:7.2f} | {k['high']:7.2f} | "
                    f"{k['low']:7.2f} | {k['close']:7.2f} | {k['volume']:7.2f} | {status}\n"
                )
            kline_table_30m += "\nNote: ✓=Confirmed K-line (fixed data), ⟳=Unconfirmed K-line (updating in real-time)"
        else:
            kline_table_30m = "K-line data not available"
            
        # === Format 30-minute K-line Table ===
        kline_table_4H = ""
        if kline_raw_4H:
            kline_table_4H = "Time    | Open    | High    | Low     | Close   | Volume  | Status\n"
            kline_table_4H += "--------|---------|---------|---------|---------|---------|------\n"
            for k in kline_raw_4H:
                status = "✓" if k.get('confirmed', True) else "⟳"
                kline_table_4H += (
                    f"{k['time']} | {k['open']:7.2f} | {k['high']:7.2f} | "
                    f"{k['low']:7.2f} | {k['close']:7.2f} | {k['volume']:7.2f} | {status}\n"
                )
            kline_table_4H += "\nNote: ✓=Confirmed K-line (fixed data), ⟳=Unconfirmed K-line (updating in real-time)"
        else:
            kline_table_4H = "K-line data not available"

        # === Format Order Book ===
        orderbook_text = self._format_orderbook_compressed(orderbook_raw) if orderbook_raw else "Order book data not available"

        # === Format Technical Indicator History Series ===
        def format_indicator_series(indicator_list, decimals=2):
            """Format indicator series to string"""
            if not indicator_list or not isinstance(indicator_list, list):
                return "N/A"
            formatted = [f"{val:.{decimals}f}" for val in indicator_list[:10]]
            return "[" + ", ".join(formatted) + "]"

        # Get current Beijing time
        from datetime import datetime, timezone, timedelta
        beijing_tz = timezone(timedelta(hours=8))
        beijing_time = datetime.now(beijing_tz).strftime('%Y-%m-%d %H:%M:%S')

        # === 提取短期、中期和长期指标 ===
        # 短期指标（5分钟，列表格式）
        ema_20_short = short_term.get('ema_20', [])
        rsi_7_short = short_term.get('rsi_7', [])
        rsi_14_short = short_term.get('rsi_14', [])
        macd_dif_short = short_term.get('macd_dif', [])
        macd_dea_short = short_term.get('macd_dea', [])
        macd_hist_short = short_term.get('macd_histogram', [])

        # 中期指标（30分钟，列表格式）
        ema_20_mid = mid_term.get('ema_20', [])
        rsi_7_mid = mid_term.get('rsi_7', [])
        rsi_14_mid = mid_term.get('rsi_14', [])
        macd_dif_mid = mid_term.get('macd_dif', [])
        macd_dea_mid = mid_term.get('macd_dea', [])
        macd_hist_mid = mid_term.get('macd_histogram', [])
        atr_14_mid = mid_term.get('atr_14', [])
        volume_avg_mid = mid_term.get('volume_avg', [])

        # 长期指标（4小时，列表格式）
        ema_20_long = long_term.get('ema_20', [])
        atr_3_long = long_term.get('atr_3', [])
        volume_avg_long = long_term.get('volume_avg', [])
        volume_ratio_long = long_term.get('volume_ratio', 1.0)  # 单个值，非列表
        volume_elapsed_pct = long_term.get('volume_elapsed_pct', 0)  # K线已过时间百分比
        macd_dif_long = long_term.get('macd_dif', [])
        macd_dea_long = long_term.get('macd_dea', [])
        macd_hist_long = long_term.get('macd_histogram', [])
        rsi_14_long = long_term.get('rsi_14', [])

        # 获取当前值（序列的第一个元素）
        current_ema20_short = ema_20_short[0] if ema_20_short else 0
        current_ema20_mid = ema_20_mid[0] if ema_20_mid else 0
        current_ema20_long = ema_20_long[0] if ema_20_long else 0

        # ========== System Prompt (Role Definition, Rules, Output Format) ==========
        # 从本地存储加载预设prompt
        preset_prompt = self._load_preset_prompt()

        system_prompt = f"""

{preset_prompt}



## 风险管理：硬性规则
### 头寸规模计算
- **可用余额使用规则**：
  - 默认保证金占用：{available_balance*0.1} USDT 至 {available_balance*0.6} USDT
  - 极高置信度（≥90%）时最高至 {available_balance*0.8} USDT，但必须明确理由。
- **置信度分层**（必须严格匹配）：
  - 低置信度（≤70%）→ 占用保证金 ≤ {available_balance*0.3} USDT
  - 中等置信度（70%-80%）→ 占用保证金 ≤ {available_balance*0.5} USDT
  - 高置信度（81%-89%）→ 占用保证金 ≤ {available_balance*0.6} USDT
  - 极高置信度（≥90%）→ 占用保证金 ≤ {available_balance*0.8} USDT
- **风险与费用考量**：
  - 交易费用 = {leverage} × 交易费率（0.04%-0.1%），即 {leverage*0.0004} - {leverage*0.001}
  - 止盈止损应合理设置，考虑价格波动和费用成本
  - 在{leverage}倍杠杆下，价格变动1%对应账户盈亏{leverage}%
  


### 决策类型
- **OPEN_LONG/OPEN_SHORT**：拥有清晰信号且风险回报比有利时使用。止盈止损应基于技术分析设置合理价格。
- **HOLD**：信号不明确、冲突或缺乏高质量机会时使用。
- **ADJUST_STOP**：仅针对现有头寸，在价格重大变动或趋势变化时使用。

### ADJUST_STOP条件
- 仅当市场结构发生重大变化时调整（例如趋势反转）。
- 禁止频繁调整：每次调整至少间隔15分钟。(基于当前仓位的历史决策操作判断）
- **止盈止损纪律**：
  - 开仓时设定的止盈/止损目标应尽量坚持。
  - 判断错误时认输，尽量不要移动止损扩大损失。


## 输出格式：严格JSON规范
你必须始终以以下JSON格式输出，且所有字段必须填充：
```json
{{
  "signal": "OPEN_LONG|OPEN_SHORT|HOLD|ADJUST_STOP",
  "size": float, // 开仓时必须满足：size ≥ {min_sz} 且 {round(available_balance*0.8*leverage/current_price/ct_val,2)} > size > {round(available_balance*0.1*leverage/current_price/ct_val,2)}，保留2位小数 ，signal 为 HOLD 返回null即可
  "confidence": integer, // 0-100，必须与头寸规模分层匹配
  "adjust_data": {{
    "take_profit": [
      {{"size": float, "price": float}},//size必须保留2位小数
      {{"size": float, "price": float}},//size必须保留2位小数
      ...
    ], // 止盈层级列表 ，size总和必须等于持仓数量
    "stop_loss": [
      {{"size": float, "price": float}},//size必须保留2位小数
      ...
    ] // 止损层级列表 ，size总和必须等于持仓数量
  }},
  "holding_time": "10分钟|30分钟|1小时|2小时", // 预估持仓时间 signal 为 HOLD 返回null即可
  "reason": "string" // 详细中文分析，对行情和当前账户情况的分析，包括对操作的信心和关键洞察
}}
```
开多示例：
```json
{{
  "signal": "OPEN_LONG",
  "size": 15.50,
  "confidence": 85,
  "adjust_data": {{
    "take_profit": [
      {{"size": 7.75, "price": 112000}},// 单层止盈 或 多层止盈
      {{"size": 7.75, "price": 111500}}
    ],
    "stop_loss": [
      {{"size": 15.50, "price": 110000}} // 单层止损 或 多层止损
    ]
  }},
  "holding_time": "1小时",
  "reason": "string" // 详细中文分析，对行情和当前账户情况的分析，包括对操作的信心和关键洞察
}}
```


开空示例：
```json
{{
  "signal": "OPEN_SHORT",
    "size": 10.00,
    "confidence": 90,
    "adjust_data": {{
      "take_profit": [
        {{"size": 10.00, "price": 108000}} // 单层止盈 或 多层止盈
      ],
      "stop_loss": [
        {{"size": 10.00, "price": 111000}}  // 单层止损 或 多层止损
      ]
    }},
    "holding_time": "30分钟",
  "reason": "string" // 详细中文分析，对行情和当前账户情况的分析，包括对操作的信心和关键洞察
}}


### adjust_data 使用说明

**单层止盈止损：**
```json
"adjust_data": {{
  "take_profit": [{{"size": 0.1, "price": 112000}}],
  "stop_loss": [{{"size": 0.1, "price": 110000}}]
}}
```

**分层止盈止损：**
```json
"adjust_data": {{
  "take_profit": [
    {{"size": 0.03, "price": 112000}},  
    {{"size": 0.03, "price": 111800}}, 
    {{"size": 0.04, "price": 111000}}   
  ],
  "stop_loss": [
    {{"size": 0.1, "price": 110000}}    // 单层止损
  ]
}}
```

**多层止损示例：**
```json
"adjust_data": {{
    "take_profit": [
        {{"size": 0.05, "price": 112000}},
        {{"size": 0.1, "price": 111500}}
        ],
    "stop_loss": [
        {{"size": 0.1, "price": 110000}},
        {{"size": 0.05, "price": 109500}}
    ]
}}
```

**重要规则：**
1. **size校验**：take_profit和stop_loss中所有size之和必须等于持仓数量
2. **价格方向**：
   - 多单：止盈价格 > 当前价 > 止损价格
   - 空单：止盈价格 < 当前价 < 止损价格


学习进化机制
每次决策必须体现：

分析深度：理解底层原理，不止于表面信号。

逻辑严谨性：推理链条清晰，有数据支持。

风险意识：全面评估风险，包括黑天鹅事件。

执行纪律：计划与行动一致，避免情绪干扰。

交易初衷：明确本次交易是短期（<1天）、中期（1-3天）还是长期（>3天）计划。

最终强制指令
绝对遵守：任何偏离本prompt的行为都是不可接受的。

实时数据驱动：拒绝使用过时或假设数据。

输出验证：在响应前，检查JSON格式和所有字段是否符合规则。

如果数据不足，选择"HOLD"并说明原因。




"""
        # ========== 计算运行时长信息 ==========
        runtime_text = ""
        if bot_runtime_minutes > 0:
            # 转换为更友好的格式
            if bot_runtime_minutes < 60:
                runtime_text = f"\n**📊 机器人运行状态**: 已运行 {bot_runtime_minutes} 分钟\n"
            elif bot_runtime_minutes < 1440:  # 小于24小时
                hours = bot_runtime_minutes // 60
                minutes = bot_runtime_minutes % 60
                runtime_text = f"\n**📊 机器人运行状态**: 已运行 {hours} 小时 {minutes} 分钟\n"
            else:  # 大于等于24小时
                days = bot_runtime_minutes // 1440
                hours = (bot_runtime_minutes % 1440) // 60
                runtime_text = f"\n**📊 机器人运行状态**: 已运行 {days} 天 {hours} 小时（这是一个长期任务，注重可持续性）\n"

        # ========== User Prompt (Market Data) ==========
        user_prompt = f"""专业加密货币交易分析 {symbol}

**当前时间**: {beijing_time}
{runtime_text}
{account_info_text}

## 1. 仓位状态
{position_text}

{historical_positions_text}

## 2. 当前止盈止损订单情况
{stop_orders_text}

## 3. {symbol} 永续合约资金费率
{funding_rate_text}

## 3.1 市场数据分析
{market_data_text}

## 3.2 Tick 特征聚合数据
{tick_features_text}

## 4. 短期指标 (5分钟 K线)

### 5.1 K线数据 (最近15 bars)
自主识别形态
```
{kline_table}
```

### 5.2 技术指标（历史序列，降序：最新 → 过去）
**注意**：以下所有指标均为最近 10 个值的序列，时间顺序为**降序**（[最新值，-1 周期，-2 周期，...，-9 周期]）

- 当前价格: {current_price:.2f}
- EMA20 : {format_indicator_series(ema_20_short)}
- RSI(7) : {format_indicator_series(rsi_7_short, 1)}
- RSI(14) : {format_indicator_series(rsi_14_short, 1)}
- MACD DIF : {format_indicator_series(macd_dif_short)}
- MACD DEA : {format_indicator_series(macd_dea_short)}
- MACD Histogram : {format_indicator_series(macd_hist_short)}



## 5.5 中期指标 (30分钟 K线)

### 5.5.1 K线数据 (最近15 bars)
自主识别形态
```
{kline_table_30m}
```

### 5.5.2 技术指标（历史序列，降序：最新 → 过去）
**注意**：以下所有指标均为最近 10 个值的序列，时间顺序为**降序**（[最新值，-1 周期，-2 周期，...，-9 周期]）

- EMA20 : {format_indicator_series(ema_20_mid)} (Price {'above MA' if current_price > current_ema20_mid else 'below MA'}, Mid-term {'bullish' if current_price > current_ema20_mid else 'bearish'})
- RSI(7) : {format_indicator_series(rsi_7_mid, 1)}
- RSI(14) : {format_indicator_series(rsi_14_mid, 1)}
- MACD DIF : {format_indicator_series(macd_dif_mid)}
- MACD DEA : {format_indicator_series(macd_dea_mid)}
- MACD Histogram : {format_indicator_series(macd_hist_mid)}
- ATR(14) : {format_indicator_series(atr_14_mid)}
- Volume Average : {format_indicator_series(volume_avg_mid, 0)}



## 6.长期趋势（4小时K线，历史序列）
自主识别形态
```
{kline_table_4H}
```
**注意**：以下所有指标均为最近 10 个值的序列，时间顺序为**降序**（[最新值，-1 周期，-2 周期，...，-9 周期]）

- EMA20 : {format_indicator_series(ema_20_long)} (Price {'above MA' if current_price > current_ema20_long else 'below MA'}, Long-term {'bullish' if current_price > current_ema20_long else 'bearish'})
- ATR(3) : {format_indicator_series(atr_3_long)}
- Volume Average : {format_indicator_series(volume_avg_long, 0)}
- Volume Ratio: {volume_ratio_long:.2f} ({'High volume' if volume_ratio_long > 1.2 else 'Low volume' if volume_ratio_long < 0.8 else 'Normal'}) (Note: Current 4H K-line has elapsed {volume_elapsed_pct:.1f}%, volume adjusted proportionally by time)
- MACD DIF : {format_indicator_series(macd_dif_long)}
- MACD DEA : {format_indicator_series(macd_dea_long)}
- MACD Histogram : {format_indicator_series(macd_hist_long)}
- RSI(14) : {format_indicator_series(rsi_14_long, 1)}

"""

        return (system_prompt, user_prompt)


# 测试代码
if __name__ == '__main__':
    from src.ai.data_manager import DataManager

    # 创建数据管理器
    dm = DataManager()

    # 创建特征工程师
    fe = FeatureEngineer(dm)

    # 提取特征
    features = fe.extract_all_features('BTC-USDT-SWAP', '5m')

    # 打印结果
    import json
    print(json.dumps(features, indent=2, ensure_ascii=False))

    # 生成Prompt
    prompt = fe.generate_ai_prompt('BTC-USDT-SWAP', features)
    print("\n" + "=" * 60)
    print(prompt)
