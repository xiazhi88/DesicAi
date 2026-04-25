"""
OKX 持仓管理 API
区分现货余额和合约持仓
"""
from typing import Optional
from ..core.rest_client import OKXRestClient


class PositionAPI:
    """
    持仓管理API

    现货持仓通过账户余额查询
    合约持仓通过持仓接口查询
    """

    def __init__(self, client: OKXRestClient):
        """
        初始化

        Args:
            client: REST客户端
        """
        self.client = client

    # ========== 现货持仓（余额） ==========

    def get_spot_balance(self, ccy: Optional[str] = None) -> dict:
        """
        获取现货账户余额
        现货没有"持仓"概念，这里查询的是账户余额

        Args:
            ccy: 币种，如 BTC。为空时查询所有币种

        Returns:
            现货余额信息，包含可用、冻结等
        """
        params = {}
        if ccy:
            params['ccy'] = ccy

        result = self.client.get('/api/v5/account/balance', params=params, auth_required=True)

        # 可以进一步处理返回数据，提取现货相关信息
        return result

    def get_spot_balance_by_currency(self, ccy: str) -> dict:
        """
        获取单个币种的现货余额

        Args:
            ccy: 币种，如 BTC USDT

        Returns:
            该币种的余额详情
        """
        return self.get_spot_balance(ccy=ccy)

    # ========== 合约持仓 ==========

    def get_contract_positions(
        self,
        inst_type: Optional[str] = None,
        inst_id: Optional[str] = None,
        pos_id: Optional[str] = None
    ) -> dict:
        """
        获取合约持仓
        适用于 MARGIN(杠杆), SWAP(永续), FUTURES(交割), OPTION(期权)

        Args:
            inst_type: 产品类型 MARGIN SWAP FUTURES OPTION
            inst_id: 产品ID
            pos_id: 持仓ID

        Returns:
            合约持仓信息，包含持仓量、未实现盈亏、保证金等
        """
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if inst_id:
            params['instId'] = inst_id
        if pos_id:
            params['posId'] = pos_id

        return self.client.get('/api/v5/account/positions', params=params, auth_required=True)

    def get_margin_positions(self, inst_id: Optional[str] = None) -> dict:
        """
        获取杠杆持仓

        Args:
            inst_id: 产品ID，如 BTC-USDT

        Returns:
            杠杆持仓
        """
        return self.get_contract_positions(inst_type='MARGIN', inst_id=inst_id)

    def get_swap_positions(self, inst_id: Optional[str] = None) -> dict:
        """
        获取永续合约持仓

        Args:
            inst_id: 产品ID，如 BTC-USDT-SWAP

        Returns:
            永续合约持仓
        """
        return self.get_contract_positions(inst_type='SWAP', inst_id=inst_id)

    def get_futures_positions(self, inst_id: Optional[str] = None) -> dict:
        """
        获取交割合约持仓

        Args:
            inst_id: 产品ID，如 BTC-USD-250328

        Returns:
            交割合约持仓
        """
        return self.get_contract_positions(inst_type='FUTURES', inst_id=inst_id)

    def get_option_positions(self, inst_id: Optional[str] = None) -> dict:
        """
        获取期权持仓

        Args:
            inst_id: 产品ID

        Returns:
            期权持仓
        """
        return self.get_contract_positions(inst_type='OPTION', inst_id=inst_id)

    # ========== 历史持仓 ==========

    def get_positions_history(
        self,
        inst_type: Optional[str] = None,
        inst_id: Optional[str] = None,
        mgn_mode: Optional[str] = None,
        close_type: Optional[str] = None,
        pos_id: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取历史持仓信息

        Args:
            inst_type: 产品类型 MARGIN SWAP FUTURES OPTION
            inst_id: 产品ID
            mgn_mode: 保证金模式 cross isolated
            close_type: 平仓类型 1:部分平仓 2:完全平仓 3:强平 4:强减 5:ADL自动减仓
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
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if close_type:
            params['type'] = close_type
        if pos_id:
            params['posId'] = pos_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/account/positions-history', params=params, auth_required=True)

    # ========== 持仓风险 ==========

    def get_position_risk(self, inst_type: Optional[str] = None) -> dict:
        """
        获取持仓风险

        Args:
            inst_type: 产品类型 MARGIN SWAP FUTURES OPTION

        Returns:
            持仓风险信息
        """
        params = {}
        if inst_type:
            params['instType'] = inst_type

        return self.client.get('/api/v5/account/account-position-risk', params=params, auth_required=True)

    # ========== 综合查询 ==========

    def get_all_positions(self) -> dict:
        """
        获取所有持仓（包括现货余额和所有合约持仓）

        Returns:
            {
                'spot_balance': {...},  # 现货余额
                'margin_positions': {...},  # 杠杆持仓
                'swap_positions': {...},  # 永续持仓
                'futures_positions': {...},  # 交割持仓
                'option_positions': {...}  # 期权持仓
            }
        """
        result = {
            'spot_balance': self.get_spot_balance(),
            'margin_positions': self.get_margin_positions(),
            'swap_positions': self.get_swap_positions(),
            'futures_positions': self.get_futures_positions(),
            'option_positions': self.get_option_positions()
        }
        return result

    def get_positions_by_inst_id(self, inst_id: str) -> dict:
        """
        根据产品ID获取持仓
        自动判断是现货还是合约

        Args:
            inst_id: 产品ID，如 BTC-USDT(现货), BTC-USDT-SWAP(永续)

        Returns:
            持仓信息
        """
        # 判断产品类型
        if '-SWAP' in inst_id:
            return self.get_swap_positions(inst_id=inst_id)
        elif inst_id.count('-') >= 2 and inst_id[-6:].isdigit():
            # 类似 BTC-USD-250328 的格式
            return self.get_futures_positions(inst_id=inst_id)
        elif inst_id.count('-') == 1:
            # 现货或杠杆，先尝试获取合约持仓
            contract_pos = self.get_contract_positions(inst_id=inst_id)
            if contract_pos.get('data'):
                return contract_pos
            # 如果没有合约持仓，返回现货余额
            # 从inst_id提取币种
            base_ccy = inst_id.split('-')[0]
            return self.get_spot_balance(ccy=base_ccy)
        else:
            # 默认查询合约持仓
            return self.get_contract_positions(inst_id=inst_id)

    # ========== 调整保证金 ==========

    def increase_decrease_margin(
        self,
        inst_id: str,
        pos_side: str,
        type: str,
        amt: str,
        ccy: Optional[str] = None,
        auto: Optional[bool] = None,
        loanTrans: Optional[bool] = None
    ) -> dict:
        """
        调整保证金

        Args:
            inst_id: 产品ID
            pos_side: 持仓方向 long short
            type: 操作类型 add reduce
            amt: 增加或减少的保证金数量
            ccy: 保证金币种
            auto: 是否自动借币
            loanTrans: 是否支持借币转入/转出

        Returns:
            调整结果
        """
        data = {
            'instId': inst_id,
            'posSide': pos_side,
            'type': type,
            'amt': amt
        }
        if ccy:
            data['ccy'] = ccy
        if auto is not None:
            data['auto'] = str(auto).lower()
        if loanTrans is not None:
            data['loanTrans'] = str(loanTrans).lower()

        return self.client.post('/api/v5/account/position/margin-balance', data=data)

    # ========== 设置杠杆 ==========

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
