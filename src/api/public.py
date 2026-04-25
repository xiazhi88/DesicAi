"""
OKX 公共数据 API
"""
from typing import Optional, List
from ..core.rest_client import OKXRestClient


class PublicAPI:
    """公共数据API"""

    def __init__(self, client: OKXRestClient):
        """
        初始化

        Args:
            client: REST客户端
        """
        self.client = client

    def get_instruments(
        self,
        inst_type: str,
        uly: Optional[str] = None,
        inst_family: Optional[str] = None,
        inst_id: Optional[str] = None
    ) -> dict:
        """
        获取交易产品信息

        Args:
            inst_type: 产品类型 SPOT-现货, MARGIN-杠杆, SWAP-永续, FUTURES-交割, OPTION-期权
            uly: 标的指数
            inst_family: 交易品种
            inst_id: 产品ID

        Returns:
            交易产品列表
        """
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_family:
            params['instFamily'] = inst_family
        if inst_id:
            params['instId'] = inst_id

        return self.client.get('/api/v5/public/instruments', params=params)

    def get_delivery_exercise_history(
        self,
        inst_type: str,
        uly: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取交割和行权记录

        Args:
            inst_type: 产品类型 FUTURES OPTION
            uly: 标的指数
            after: 请求此时间戳之前的分页内容
            before: 请求此时间戳之后的分页内容
            limit: 返回结果的数量

        Returns:
            交割和行权记录
        """
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/public/delivery-exercise-history', params=params)

    def get_open_interest(self, inst_type: str, uly: Optional[str] = None, inst_id: Optional[str] = None) -> dict:
        """
        获取持仓总量

        Args:
            inst_type: 产品类型 SWAP FUTURES OPTION
            uly: 标的指数
            inst_id: 产品ID

        Returns:
            持仓总量
        """
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id

        return self.client.get('/api/v5/public/open-interest', params=params)

    def get_funding_rate(self, inst_id: str) -> dict:
        """
        获取资金费率

        Args:
            inst_id: 产品ID (永续合约)

        Returns:
            资金费率
        """
        params = {'instId': inst_id}
        return self.client.get('/api/v5/public/funding-rate', params=params)

    def get_funding_rate_history(
        self,
        inst_id: str,
        after: Optional[str] = None,
        before: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取资金费率历史

        Args:
            inst_id: 产品ID
            after: 请求此时间戳之前的分页内容
            before: 请求此时间戳之后的分页内容
            limit: 返回结果的数量

        Returns:
            资金费率历史
        """
        params = {'instId': inst_id}
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/public/funding-rate-history', params=params)

    def get_limit_price(self, inst_id: str) -> dict:
        """
        获取限价

        Args:
            inst_id: 产品ID

        Returns:
            限价信息
        """
        params = {'instId': inst_id}
        return self.client.get('/api/v5/public/price-limit', params=params)

    def get_opt_summary(
        self,
        uly: str,
        exp_time: Optional[str] = None
    ) -> dict:
        """
        获取期权定价

        Args:
            uly: 标的指数
            exp_time: 到期日

        Returns:
            期权定价
        """
        params = {'uly': uly}
        if exp_time:
            params['expTime'] = exp_time

        return self.client.get('/api/v5/public/opt-summary', params=params)

    def get_estimated_price(self, inst_id: str) -> dict:
        """
        获取预估交割/行权价格

        Args:
            inst_id: 产品ID

        Returns:
            预估价格
        """
        params = {'instId': inst_id}
        return self.client.get('/api/v5/public/estimated-price', params=params)

    def get_discount_rate_interest_free_quota(
        self,
        ccy: Optional[str] = None,
        discount_lv: Optional[str] = None
    ) -> dict:
        """
        获取折算率和免息额度

        Args:
            ccy: 币种
            discount_lv: 折算率等级

        Returns:
            折算率和免息额度
        """
        params = {}
        if ccy:
            params['ccy'] = ccy
        if discount_lv:
            params['discountLv'] = discount_lv

        return self.client.get('/api/v5/public/discount-rate-interest-free-quota', params=params)

    def get_system_time(self) -> dict:
        """
        获取系统时间

        Returns:
            系统时间戳
        """
        return self.client.get('/api/v5/public/time')

    def get_mark_price(
        self,
        inst_type: str,
        uly: Optional[str] = None,
        inst_family: Optional[str] = None,
        inst_id: Optional[str] = None
    ) -> dict:
        """
        获取标记价格

        Args:
            inst_type: 产品类型
            uly: 标的指数
            inst_family: 交易品种
            inst_id: 产品ID

        Returns:
            标记价格
        """
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_family:
            params['instFamily'] = inst_family
        if inst_id:
            params['instId'] = inst_id

        return self.client.get('/api/v5/public/mark-price', params=params)

    def get_position_tiers(
        self,
        inst_type: str,
        td_mode: str,
        uly: Optional[str] = None,
        inst_family: Optional[str] = None,
        inst_id: Optional[str] = None,
        ccy: Optional[str] = None,
        tier: Optional[str] = None
    ) -> dict:
        """
        获取仓位档位信息

        Args:
            inst_type: 产品类型
            td_mode: 交易模式 cross isolated
            uly: 标的指数
            inst_family: 交易品种
            inst_id: 产品ID
            ccy: 保证金币种
            tier: 档位

        Returns:
            仓位档位信息
        """
        params = {'instType': inst_type, 'tdMode': td_mode}
        if uly:
            params['uly'] = uly
        if inst_family:
            params['instFamily'] = inst_family
        if inst_id:
            params['instId'] = inst_id
        if ccy:
            params['ccy'] = ccy
        if tier:
            params['tier'] = tier

        return self.client.get('/api/v5/public/position-tiers', params=params)

    def get_interest_rate_loan_quota(self) -> dict:
        """
        获取杠杆利率和借币限额

        Returns:
            利率和限额
        """
        return self.client.get('/api/v5/public/interest-rate-loan-quota')

    def get_vip_interest_rate_loan_quota(self) -> dict:
        """
        获取市场借贷信息（公共）

        Returns:
            市场借贷信息
        """
        return self.client.get('/api/v5/public/vip-interest-rate-loan-quota')

    def get_underlying(self, inst_type: str) -> dict:
        """
        获取标的指数

        Args:
            inst_type: 产品类型 SWAP FUTURES OPTION

        Returns:
            标的指数列表
        """
        params = {'instType': inst_type}
        return self.client.get('/api/v5/public/underlying', params=params)

    def get_insurance_fund(
        self,
        inst_type: str,
        type: Optional[str] = None,
        uly: Optional[str] = None,
        ccy: Optional[str] = None,
        before: Optional[str] = None,
        after: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取风险准备金余额

        Args:
            inst_type: 产品类型
            type: 类型 liquidation_balance_deposit bankruptcy_loss platform_revenue
            uly: 标的指数
            ccy: 币种
            before: 请求此时间戳之后的分页内容
            after: 请求此时间戳之前的分页内容
            limit: 返回结果的数量

        Returns:
            风险准备金余额
        """
        params = {'instType': inst_type}
        if type:
            params['type'] = type
        if uly:
            params['uly'] = uly
        if ccy:
            params['ccy'] = ccy
        if before:
            params['before'] = before
        if after:
            params['after'] = after
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/public/insurance-fund', params=params)

    def unit_convert(
        self,
        type: str,
        inst_id: str,
        sz: str,
        px: Optional[str] = None,
        unit: Optional[str] = None
    ) -> dict:
        """
        张币转换

        Args:
            type: 转换类型 1:币转张 2:张转币
            inst_id: 产品ID
            sz: 数量
            px: 价格
            unit: 币种单位

        Returns:
            转换结果
        """
        params = {'type': type, 'instId': inst_id, 'sz': sz}
        if px:
            params['px'] = px
        if unit:
            params['unit'] = unit

        return self.client.get('/api/v5/public/convert-contract-coin', params=params)
