"""
OKX 账户管理 API
"""
from typing import Optional, List
from ..core.rest_client import OKXRestClient


class AccountAPI:
    """账户管理API"""

    def __init__(self, client: OKXRestClient):
        """
        初始化

        Args:
            client: REST客户端
        """
        self.client = client

    def get_balance(self, ccy: Optional[str] = None) -> dict:
        """
        获取账户余额
        包含现货、合约的余额信息

        Args:
            ccy: 币种，如 BTC。为空时查询所有币种

        Returns:
            账户余额
        """
        params = {}
        if ccy:
            params['ccy'] = ccy

        return self.client.get('/api/v5/account/balance', params=params, auth_required=True)

    def get_usdt_balance(self) -> dict:
        """
        获取USDT余额（简化版）

        Returns:
            dict: {
                'success': bool,
                'totalEq': float,      # 总余额
                'availEq': float,  # 可用余额
                'frozenBal': float      # 冻结余额
            }
        """
        result = self.get_balance(ccy='USDT')

        if result.get('code') != '0':
            return {
                'success': False,
                'error': result.get('msg', '获取余额失败'),
                'totalEq': 0,
                'availEq': 0,
                'frozenBal': 0
            }

        data = result.get('data', [])
        if not data or len(data) == 0:
            return {
                'success': True,
                'totalEq': 0,
                'availEq': 0,
                'frozenBal': 0
            }
        result={
            'success': True,
            'totalEq': float(data[0].get('totalEq', 0)) ,
            'availEq': float(data[0].get('details', [{}])[0].get('availEq', 0)) if len(data[0].get('details', [{}]))>0 else 0,
            'frozenBal': float(data[0].get('details', [{}])[0].get('frozenBal', 0)) if len(data[0].get('details', [{}]))>0 else 0,
            }
        return result

    def get_positions(
        self,
        inst_type: Optional[str] = None,
        inst_id: Optional[str] = None,
        pos_id: Optional[str] = None
    ) -> dict:
        """
        获取持仓信息
        仅适用于合约（MARGIN/SWAP/FUTURES/OPTION）

        Args:
            inst_type: 产品类型 MARGIN SWAP FUTURES OPTION
            inst_id: 产品ID
            pos_id: 持仓ID

        Returns:
            持仓信息
        """
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if inst_id:
            params['instId'] = inst_id
        if pos_id:
            params['posId'] = pos_id

        return self.client.get('/api/v5/account/positions', params=params, auth_required=True)

    def get_positions_history(
        self,
        inst_type: Optional[str] = None,
        inst_id: Optional[str] = None,
        mgnMode: Optional[str] = None,
        type: Optional[str] = None,
        pos_id: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取历史持仓信息

        Args:
            inst_type: 产品类型
            inst_id: 产品ID
            mgnMode: 保证金模式 cross isolated
            type: 平仓类型 1:部分平仓 2:完全平仓 3:强平 4:强减 5:ADL自动减仓
            pos_id: 持仓ID
            after: 请求此时间戳之前的分页内容
            before: 请求此时间戳之后的分页内容
            limit: 返回结果的数量，最大100

        Returns:
            历史持仓
        """
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if inst_id:
            params['instId'] = inst_id
        if mgnMode:
            params['mgnMode'] = mgnMode
        if type:
            params['type'] = type
        if pos_id:
            params['posId'] = pos_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/account/positions-history', params=params, auth_required=True)

    def get_account_position_risk(self, inst_type: Optional[str] = None) -> dict:
        """
        获取账户持仓风险

        Args:
            inst_type: 产品类型 MARGIN SWAP FUTURES OPTION

        Returns:
            持仓风险
        """
        params = {}
        if inst_type:
            params['instType'] = inst_type

        return self.client.get('/api/v5/account/account-position-risk', params=params, auth_required=True)

    def get_bills(
        self,
        inst_type: Optional[str] = None,
        ccy: Optional[str] = None,
        mgn_mode: Optional[str] = None,
        ct_type: Optional[str] = None,
        type: Optional[str] = None,
        sub_type: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        begin: Optional[str] = None,
        end: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取账单流水

        Args:
            inst_type: 产品类型
            ccy: 币种
            mgn_mode: 保证金模式
            ct_type: 合约类型 linear inverse
            type: 账单类型
            sub_type: 账单子类型
            after: 请求此ID之前的分页内容
            before: 请求此ID之后的分页内容
            begin: 筛选的起始时间戳
            end: 筛选的结束时间戳
            limit: 返回结果的数量，最大100

        Returns:
            账单流水
        """
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if ccy:
            params['ccy'] = ccy
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if ct_type:
            params['ctType'] = ct_type
        if type:
            params['type'] = type
        if sub_type:
            params['subType'] = sub_type
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/account/bills', params=params, auth_required=True)

    def get_bills_archive(
        self,
        inst_type: Optional[str] = None,
        ccy: Optional[str] = None,
        mgn_mode: Optional[str] = None,
        ct_type: Optional[str] = None,
        type: Optional[str] = None,
        sub_type: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        begin: Optional[str] = None,
        end: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取账单流水（近三个月）

        Args:
            参数同get_bills

        Returns:
            账单流水
        """
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if ccy:
            params['ccy'] = ccy
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if ct_type:
            params['ctType'] = ct_type
        if type:
            params['type'] = type
        if sub_type:
            params['subType'] = sub_type
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/account/bills-archive', params=params, auth_required=True)

    def get_config(self) -> dict:
        """
        获取账户配置

        Returns:
            账户配置
        """
        return self.client.get('/api/v5/account/config', auth_required=True)

    def set_position_mode(self, pos_mode: str) -> dict:
        """
        设置持仓模式

        Args:
            pos_mode: 持仓模式 long_short_mode(双向持仓) net_mode(单向持仓)

        Returns:
            设置结果
        """
        data = {'posMode': pos_mode}
        return self.client.post('/api/v5/account/set-position-mode', data=data)

    def set_leverage(
        self,
        inst_id: str,
        lever: str,
        mgn_mode: str,
        pos_side: Optional[str] = None
    ) -> dict:
        """
        设置杠杆倍数

        Args:
            inst_id: 产品ID
            lever: 杠杆倍数
            mgn_mode: 保证金模式 cross isolated
            pos_side: 持仓方向 long short net

        Returns:
            设置结果
        """
        data = {
            'instId': inst_id,
            'lever': lever,
            'mgnMode': mgn_mode
        }
        if pos_side:
            data['posSide'] = pos_side

        return self.client.post('/api/v5/account/set-leverage', data=data)

    def get_max_size(
        self,
        inst_id: str,
        td_mode: str,
        ccy: Optional[str] = None,
        px: Optional[str] = None,
        leverage: Optional[str] = None,
        unSpotOffset: Optional[bool] = None
    ) -> dict:
        """
        获取最大可买卖/开仓数量

        Args:
            inst_id: 产品ID
            td_mode: 交易模式 cross isolated cash
            ccy: 保证金币种（仅适用于单币种保证金模式下的全仓杠杆订单）
            px: 委托价格
            leverage: 杠杆倍数
            unSpotOffset: 是否禁止现货对冲

        Returns:
            最大可交易数量
        """
        params = {
            'instId': inst_id,
            'tdMode': td_mode
        }
        if ccy:
            params['ccy'] = ccy
        if px:
            params['px'] = px
        if leverage:
            params['leverage'] = leverage
        if unSpotOffset is not None:
            params['unSpotOffset'] = str(unSpotOffset).lower()

        return self.client.get('/api/v5/account/max-size', params=params, auth_required=True)

    def get_max_avail_size(
        self,
        inst_id: str,
        td_mode: str,
        ccy: Optional[str] = None,
        reduce_only: Optional[bool] = None,
        unSpotOffset: Optional[bool] = None,
        quick_mgn_type: Optional[str] = None
    ) -> dict:
        """
        获取最大可用数量

        Args:
            inst_id: 产品ID
            td_mode: 交易模式
            ccy: 保证金币种
            reduce_only: 是否只减仓
            unSpotOffset: 是否禁止现货对冲
            quick_mgn_type: 快速保证金类型

        Returns:
            最大可用数量
        """
        params = {
            'instId': inst_id,
            'tdMode': td_mode
        }
        if ccy:
            params['ccy'] = ccy
        if reduce_only is not None:
            params['reduceOnly'] = str(reduce_only).lower()
        if unSpotOffset is not None:
            params['unSpotOffset'] = str(unSpotOffset).lower()
        if quick_mgn_type:
            params['quickMgnType'] = quick_mgn_type

        return self.client.get('/api/v5/account/max-avail-size', params=params, auth_required=True)

    def get_leverage_info(
        self,
        inst_id: List[str],
        mgn_mode: str
    ) -> dict:
        """
        获取杠杆倍数信息

        Args:
            inst_id: 产品ID列表
            mgn_mode: 保证金模式

        Returns:
            杠杆倍数信息
        """
        # 多个产品用逗号分隔
        inst_ids = ','.join(inst_id) if isinstance(inst_id, list) else inst_id

        params = {
            'instId': inst_ids,
            'mgnMode': mgn_mode
        }

        return self.client.get('/api/v5/account/leverage-info', params=params, auth_required=True)

    def get_max_loan(
        self,
        inst_id: str,
        mgn_mode: str,
        mgn_ccy: Optional[str] = None
    ) -> dict:
        """
        获取最大可借

        Args:
            inst_id: 产品ID
            mgn_mode: 保证金模式
            mgn_ccy: 保证金币种

        Returns:
            最大可借
        """
        params = {
            'instId': inst_id,
            'mgnMode': mgn_mode
        }
        if mgn_ccy:
            params['mgnCcy'] = mgn_ccy

        return self.client.get('/api/v5/account/max-loan', params=params, auth_required=True)

    def get_fee_rates(
        self,
        inst_type: str,
        inst_id: Optional[str] = None,
        uly: Optional[str] = None,
        category: Optional[str] = None,
        inst_family: Optional[str] = None
    ) -> dict:
        """
        获取交易手续费率

        Args:
            inst_type: 产品类型
            inst_id: 产品ID
            uly: 标的指数
            category: 手续费率类别 1:ClassA 2:ClassB 3:ClassC 4:ClassD
            inst_family: 交易品种

        Returns:
            手续费率
        """
        params = {'instType': inst_type}
        if inst_id:
            params['instId'] = inst_id
        if uly:
            params['uly'] = uly
        if category:
            params['category'] = category
        if inst_family:
            params['instFamily'] = inst_family

        return self.client.get('/api/v5/account/trade-fee', params=params, auth_required=True)

    def get_interest_accrued(
        self,
        inst_id: Optional[str] = None,
        ccy: Optional[str] = None,
        mgn_mode: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取计息记录

        Args:
            inst_id: 产品ID
            ccy: 币种
            mgn_mode: 保证金模式
            after: 请求此时间戳之前的分页内容
            before: 请求此时间戳之后的分页内容
            limit: 返回结果的数量，最大100

        Returns:
            计息记录
        """
        params = {}
        if inst_id:
            params['instId'] = inst_id
        if ccy:
            params['ccy'] = ccy
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/account/interest-accrued', params=params, auth_required=True)

    def get_interest_rate(self, ccy: Optional[str] = None) -> dict:
        """
        获取用户当前杠杆借币利率

        Args:
            ccy: 币种

        Returns:
            利率信息
        """
        params = {}
        if ccy:
            params['ccy'] = ccy

        return self.client.get('/api/v5/account/interest-rate', params=params, auth_required=True)

    def set_greeks(self, greeks_type: str) -> dict:
        """
        设置Greeks展示方式

        Args:
            greeks_type: Greeks类型 PA BS

        Returns:
            设置结果
        """
        data = {'greeksType': greeks_type}
        return self.client.post('/api/v5/account/set-greeks', data=data)

    def set_isolated_mode(self, iso_mode: str, type: str) -> dict:
        """
        设置逐仓自动借币模式

        Args:
            iso_mode: 逐仓自动借币模式 automatic autonomy
            type: 业务类型 MARGIN CONTRACTS

        Returns:
            设置结果
        """
        data = {
            'isoMode': iso_mode,
            'type': type
        }
        return self.client.post('/api/v5/account/set-isolated-mode', data=data)
