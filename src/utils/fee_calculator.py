"""
手续费计算工具
处理合约交易的手续费计算和盈亏分析
"""
from typing import Dict, Tuple
from loguru import logger


class FeeCalculator:
    """手续费计算器"""

    # OKX合约手续费率
    MAKER_FEE_RATE = 0.0002  # 挂单手续费 0.02%
    TAKER_FEE_RATE = 0.0005  # 吃单手续费 0.05%

    def __init__(self, maker_rate: float = None, taker_rate: float = None):
        """
        初始化手续费计算器

        Args:
            maker_rate: 挂单手续费率（默认0.0002）
            taker_rate: 吃单手续费率（默认0.0005）
        """
        self.maker_rate = maker_rate or self.MAKER_FEE_RATE
        self.taker_rate = taker_rate or self.TAKER_FEE_RATE

    def calculate_fee(
        self,
        position_value: float,
        is_maker: bool = True
    ) -> float:
        """
        计算单边手续费

        Args:
            position_value: 仓位价值（本金 × 杠杆）
            is_maker: 是否为挂单

        Returns:
            手续费金额（USDT）

        Example:
            >>> calc = FeeCalculator()
            >>> # 10U本金，20倍杠杆 = 200U仓位
            >>> calc.calculate_fee(200, is_maker=True)
            0.04  # 200 * 0.0002
            >>> calc.calculate_fee(200, is_maker=False)
            0.1   # 200 * 0.0005
        """
        rate = self.maker_rate if is_maker else self.taker_rate
        fee = position_value * rate
        return fee

    def calculate_round_trip_fee(
        self,
        position_value: float,
        open_is_maker: bool = True,
        close_is_maker: bool = True
    ) -> Tuple[float, float, float]:
        """
        计算往返手续费（开仓 + 平仓）

        Args:
            position_value: 仓位价值
            open_is_maker: 开仓是否挂单
            close_is_maker: 平仓是否挂单

        Returns:
            (开仓费, 平仓费, 总费用)

        Example:
            >>> calc = FeeCalculator()
            >>> # 都用挂单
            >>> calc.calculate_round_trip_fee(200, True, True)
            (0.04, 0.04, 0.08)
            >>> # 都用吃单
            >>> calc.calculate_round_trip_fee(200, False, False)
            (0.1, 0.1, 0.2)
            >>> # 开仓挂单，平仓吃单（止损）
            >>> calc.calculate_round_trip_fee(200, True, False)
            (0.04, 0.1, 0.14)
        """
        open_fee = self.calculate_fee(position_value, open_is_maker)
        close_fee = self.calculate_fee(position_value, close_is_maker)
        total_fee = open_fee + close_fee

        return open_fee, close_fee, total_fee

    def calculate_breakeven_pnl(
        self,
        principal: float,
        leverage: int,
        open_is_maker: bool = True,
        close_is_maker: bool = True
    ) -> Dict:
        """
        计算盈亏平衡点

        Args:
            principal: 本金（USDT）
            leverage: 杠杆倍数
            open_is_maker: 开仓是否挂单
            close_is_maker: 平仓是否挂单

        Returns:
            盈亏平衡信息字典

        Example:
            >>> calc = FeeCalculator()
            >>> result = calc.calculate_breakeven_pnl(10, 20, True, True)
            >>> result['breakeven_pnl_pct']
            0.4  # 需要盈利0.4%才能覆盖手续费
        """
        position_value = principal * leverage

        # 计算往返手续费
        open_fee, close_fee, total_fee = self.calculate_round_trip_fee(
            position_value, open_is_maker, close_is_maker
        )

        # 盈亏平衡百分比（相对于本金）
        breakeven_pct = (total_fee / principal) * 100

        # 盈亏平衡百分比（相对于仓位价值）
        breakeven_pnl_pct = (total_fee / position_value) * 100

        return {
            'principal': principal,
            'leverage': leverage,
            'position_value': position_value,
            'open_fee': open_fee,
            'close_fee': close_fee,
            'total_fee': total_fee,
            'breakeven_pct': breakeven_pct,  # 相对本金
            'breakeven_pnl_pct': breakeven_pnl_pct,  # 相对仓位
            'fee_rate_str': f"{self.maker_rate*100:.2f}%" if open_is_maker else f"{self.taker_rate*100:.2f}%"
        }

    def calculate_net_profit(
        self,
        principal: float,
        leverage: int,
        pnl_pct: float,
        open_is_maker: bool = True,
        close_is_maker: bool = True
    ) -> Dict:
        """
        计算净收益（扣除手续费）

        Args:
            principal: 本金
            leverage: 杠杆
            pnl_pct: 盈亏百分比（相对入场价）
            open_is_maker: 开仓是否挂单
            close_is_maker: 平仓是否挂单

        Returns:
            收益信息字典

        Example:
            >>> calc = FeeCalculator()
            >>> # 10U本金，20倍杠杆，盈利1%
            >>> result = calc.calculate_net_profit(10, 20, 0.01, True, True)
            >>> result['net_profit']
            1.92  # 毛利2U - 手续费0.08U
        """
        position_value = principal * leverage

        # 毛利（不含手续费）
        gross_profit = position_value * pnl_pct

        # 手续费
        _, _, total_fee = self.calculate_round_trip_fee(
            position_value, open_is_maker, close_is_maker
        )

        # 净利润
        net_profit = gross_profit - total_fee

        # 净收益率（相对本金）
        net_profit_pct = (net_profit / principal) * 100

        # 是否盈利
        is_profitable = net_profit > 0

        return {
            'principal': principal,
            'leverage': leverage,
            'position_value': position_value,
            'pnl_pct': pnl_pct * 100,  # 转为百分比
            'gross_profit': gross_profit,
            'total_fee': total_fee,
            'net_profit': net_profit,
            'net_profit_pct': net_profit_pct,
            'is_profitable': is_profitable,
            'roi': net_profit / principal  # 投资回报率
        }

    def recommend_risk_reward_ratio(
        self,
        principal: float,
        leverage: int,
        open_is_maker: bool = True,
        close_is_maker: bool = True,
        min_profit_pct: float = 2.0  # 最小期望收益2%
    ) -> Dict:
        """
        推荐盈亏比

        Args:
            principal: 本金
            leverage: 杠杆
            open_is_maker: 开仓是否挂单
            close_is_maker: 平仓是否挂单
            min_profit_pct: 最小期望收益率（%）

        Returns:
            盈亏比建议

        Example:
            >>> calc = FeeCalculator()
            >>> result = calc.recommend_risk_reward_ratio(10, 20)
            >>> result['min_take_profit_rate']
            0.0145  # 建议止盈率1.45%
        """
        # 计算盈亏平衡
        breakeven = self.calculate_breakeven_pnl(principal, leverage, open_is_maker, close_is_maker)

        # 最小止盈率 = 盈亏平衡 + 最小期望收益
        min_take_profit_pct = breakeven['breakeven_pnl_pct'] + min_profit_pct
        min_take_profit_rate = min_take_profit_pct / 100

        # 推荐止损率（通常是止盈的1/3）
        recommended_stop_loss_rate = min_take_profit_rate / 3

        # 盈亏比
        risk_reward_ratio = min_take_profit_rate / recommended_stop_loss_rate

        return {
            'breakeven_pnl_pct': breakeven['breakeven_pnl_pct'],
            'min_profit_pct': min_profit_pct,
            'min_take_profit_pct': min_take_profit_pct,
            'min_take_profit_rate': min_take_profit_rate,
            'recommended_stop_loss_rate': recommended_stop_loss_rate,
            'risk_reward_ratio': risk_reward_ratio,
            'recommendation': f"建议止盈率>={min_take_profit_rate*100:.2f}%, 止损率<={recommended_stop_loss_rate*100:.2f}%, 盈亏比={risk_reward_ratio:.1f}:1"
        }

    def is_trade_worth_it(
        self,
        principal: float,
        leverage: int,
        stop_loss_rate: float,
        take_profit_rate: float,
        open_is_maker: bool = True,
        close_is_maker: bool = True
    ) -> Dict:
        """
        判断交易是否值得执行

        Args:
            principal: 本金
            leverage: 杠杆
            stop_loss_rate: 止损率
            take_profit_rate: 止盈率
            open_is_maker: 开仓是否挂单
            close_is_maker: 平仓是否挂单

        Returns:
            判断结果

        Example:
            >>> calc = FeeCalculator()
            >>> result = calc.is_trade_worth_it(10, 20, 0.005, 0.015, True, True)
            >>> result['is_worth']
            True
            >>> result['reason']
            '盈亏比3.0:1 > 建议2.5:1，扣除手续费后净收益2.92U (29.2%)'
        """
        # 计算盈亏平衡
        breakeven = self.calculate_breakeven_pnl(principal, leverage, open_is_maker, close_is_maker)

        # 计算盈利时的净收益
        profit_result = self.calculate_net_profit(principal, leverage, take_profit_rate, open_is_maker, close_is_maker)

        # 计算亏损时的净亏损
        loss_result = self.calculate_net_profit(principal, leverage, -stop_loss_rate, open_is_maker, False)  # 止损通常是市价单

        # 盈亏比
        risk_reward_ratio = take_profit_rate / stop_loss_rate

        # 判断标准
        MIN_RISK_REWARD = 1.5  # 最小盈亏比要求

        is_worth = False
        reason = ""

        if risk_reward_ratio < MIN_RISK_REWARD:
            reason = f"盈亏比{risk_reward_ratio:.1f}:1 < 建议{MIN_RISK_REWARD}:1，不建议交易"
        elif profit_result['net_profit'] <= 0:
            reason = f"即使止盈也无法覆盖手续费，净收益{profit_result['net_profit']:.2f}U"
        elif take_profit_rate <= breakeven['breakeven_pnl_pct'] / 100:
            reason = f"止盈率{take_profit_rate*100:.2f}% <= 盈亏平衡{breakeven['breakeven_pnl_pct']:.2f}%"
        else:
            is_worth = True
            reason = f"盈亏比{risk_reward_ratio:.1f}:1 > 建议{MIN_RISK_REWARD}:1，扣除手续费后净收益{profit_result['net_profit']:.2f}U ({profit_result['net_profit_pct']:.1f}%)"

        return {
            'is_worth': is_worth,
            'reason': reason,
            'risk_reward_ratio': risk_reward_ratio,
            'breakeven_pnl_pct': breakeven['breakeven_pnl_pct'],
            'take_profit_rate': take_profit_rate * 100,
            'stop_loss_rate': stop_loss_rate * 100,
            'expected_profit_if_win': profit_result['net_profit'],
            'expected_loss_if_stop': loss_result['net_profit'],  # 负数
            'total_fee': breakeven['total_fee']
        }


# 使用示例和测试
if __name__ == '__main__':
    calc = FeeCalculator()

    print("=" * 60)
    print("手续费计算工具 - 示例")
    print("=" * 60)

    # 示例1：计算手续费
    print("\n【示例1】10U本金，20倍杠杆，仓位价值200U")
    fee_maker = calc.calculate_fee(200, is_maker=True)
    fee_taker = calc.calculate_fee(200, is_maker=False)
    print(f"挂单手续费: {fee_maker:.2f} USDT")
    print(f"吃单手续费: {fee_taker:.2f} USDT")

    # 示例2：往返手续费
    print("\n【示例2】往返手续费（开仓+平仓）")
    open_fee, close_fee, total = calc.calculate_round_trip_fee(200, True, True)
    print(f"开仓(挂单): {open_fee:.2f} USDT")
    print(f"平仓(挂单): {close_fee:.2f} USDT")
    print(f"总费用: {total:.2f} USDT")

    open_fee, close_fee, total = calc.calculate_round_trip_fee(200, False, False)
    print(f"\n如果都用吃单: {total:.2f} USDT")

    # 示例3：盈亏平衡
    print("\n【示例3】盈亏平衡点")
    breakeven = calc.calculate_breakeven_pnl(10, 20, True, True)
    print(f"本金: {breakeven['principal']} USDT")
    print(f"杠杆: {breakeven['leverage']}x")
    print(f"仓位价值: {breakeven['position_value']} USDT")
    print(f"往返手续费: {breakeven['total_fee']:.2f} USDT")
    print(f"盈亏平衡(相对本金): {breakeven['breakeven_pct']:.2f}%")
    print(f"盈亏平衡(相对仓位): {breakeven['breakeven_pnl_pct']:.2f}%")

    # 示例4：净收益计算
    print("\n【示例4】净收益计算（盈利1%）")
    result = calc.calculate_net_profit(10, 20, 0.01, True, True)
    print(f"毛利: {result['gross_profit']:.2f} USDT")
    print(f"手续费: {result['total_fee']:.2f} USDT")
    print(f"净利润: {result['net_profit']:.2f} USDT")
    print(f"净收益率: {result['net_profit_pct']:.1f}%")

    # 示例5：盈亏比建议
    print("\n【示例5】盈亏比建议")
    recommendation = calc.recommend_risk_reward_ratio(10, 20, True, True)
    print(recommendation['recommendation'])

    # 示例6：判断交易是否值得
    print("\n【示例6】判断交易是否值得")
    result = calc.is_trade_worth_it(10, 20, 0.005, 0.015, True, True)
    print(f"是否值得: {'✓ 是' if result['is_worth'] else '✗ 否'}")
    print(f"理由: {result['reason']}")
    print(f"盈亏比: {result['risk_reward_ratio']:.1f}:1")
    print(f"预期盈利(止盈): {result['expected_profit_if_win']:.2f} USDT")
    print(f"预期亏损(止损): {result['expected_loss_if_stop']:.2f} USDT")

    # 示例7：不值得的交易
    print("\n【示例7】不值得的交易（止盈太低）")
    result = calc.is_trade_worth_it(10, 20, 0.005, 0.006, True, True)
    print(f"是否值得: {'✓ 是' if result['is_worth'] else '✗ 否'}")
    print(f"理由: {result['reason']}")
