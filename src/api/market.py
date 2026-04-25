"""
OKX 市场行情 API
支持 SPOT(现货), MARGIN(杠杆), SWAP(永续), FUTURES(交割), OPTION(期权)
"""
from typing import Optional
from ..core.rest_client import OKXRestClient


class MarketAPI:
    """市场行情API"""

    def __init__(self, client: OKXRestClient):
        """
        初始化

        Args:
            client: REST客户端
        """
        self.client = client

    def get_tickers(self, inst_type: str, uly: Optional[str] = None, inst_family: Optional[str] = None) -> dict:
        """
        获取所有产品行情信息

        Args:
            inst_type: 产品类型 SPOT-现货, MARGIN-杠杆, SWAP-永续, FUTURES-交割, OPTION-期权
            uly: 标的指数 (仅适用于交割/永续/期权)
            inst_family: 交易品种 (仅适用于交割/永续/期权)

        Returns:
            行情列表
        """
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_family:
            params['instFamily'] = inst_family

        return self.client.get('/api/v5/market/tickers', params=params)

    def get_ticker(self, inst_id: str) -> dict:
        """
        获取单个产品行情信息

        Args:
            inst_id: 产品ID，如 BTC-USDT(现货), BTC-USDT-SWAP(永续), BTC-USD-250328(交割)

        Returns:
            行情信息
        """
        params = {'instId': inst_id}
        return self.client.get('/api/v5/market/ticker', params=params)

    def get_index_tickers(self, quote_ccy: Optional[str] = None, inst_id: Optional[str] = None) -> dict:
        """
        获取指数行情

        Args:
            quote_ccy: 指数计价单位，如 USD BTC USDT
            inst_id: 指数，如 BTC-USD

        Returns:
            指数行情
        """
        params = {}
        if quote_ccy:
            params['quoteCcy'] = quote_ccy
        if inst_id:
            params['instId'] = inst_id

        return self.client.get('/api/v5/market/index-tickers', params=params)

    def get_orderbook(self, inst_id: str, sz: Optional[str] = None) -> dict:
        """
        获取产品深度

        Args:
            inst_id: 产品ID
            sz: 深度档位数量，最大值400。默认400档

        Returns:
            深度数据
        """
        params = {'instId': inst_id}
        if sz:
            params['sz'] = sz

        return self.client.get('/api/v5/market/books', params=params)

    def get_candles(
        self,
        inst_id: str,
        bar: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取K线数据

        Args:
            inst_id: 产品ID
            bar: K线周期 1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M/3M/6M/1Y
            after: 请求此时间戳之前的分页内容
            before: 请求此时间戳之后的分页内容
            limit: 返回结果的数量，最大300

        Returns:
            K线数据
        """
        params = {'instId': inst_id}
        if bar:
            params['bar'] = bar
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/market/candles', params=params)

    def get_history_candles(
        self,
        inst_id: str,
        bar: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取历史K线数据（最近3个月）

        Args:
            inst_id: 产品ID
            bar: K线周期
            after: 请求此时间戳之前的分页内容
            before: 请求此时间戳之后的分页内容
            limit: 返回结果的数量，最大100

        Returns:
            历史K线数据
        """
        params = {'instId': inst_id}
        if bar:
            params['bar'] = bar
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/market/history-candles', params=params)

    def get_index_candles(
        self,
        inst_id: str,
        bar: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取指数K线数据

        Args:
            inst_id: 指数，如 BTC-USD
            bar: K线周期
            after: 请求此时间戳之前的分页内容
            before: 请求此时间戳之后的分页内容
            limit: 返回结果的数量，最大100

        Returns:
            指数K线数据
        """
        params = {'instId': inst_id}
        if bar:
            params['bar'] = bar
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/market/index-candles', params=params)

    def get_mark_price_candles(
        self,
        inst_id: str,
        bar: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取标记价格K线数据

        Args:
            inst_id: 产品ID
            bar: K线周期
            after: 请求此时间戳之前的分页内容
            before: 请求此时间戳之后的分页内容
            limit: 返回结果的数量，最大100

        Returns:
            标记价格K线数据
        """
        params = {'instId': inst_id}
        if bar:
            params['bar'] = bar
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/market/mark-price-candles', params=params)

    def get_trades(self, inst_id: str, limit: Optional[str] = None) -> dict:
        """
        获取交易记录

        Args:
            inst_id: 产品ID
            limit: 返回结果的数量，最大500

        Returns:
            最近成交记录
        """
        params = {'instId': inst_id}
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/market/trades', params=params)

    def get_history_trades(
        self,
        inst_id: str,
        type: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取历史交易记录

        Args:
            inst_id: 产品ID
            type: 分页类型 1:tradeId 2:timestamp
            after: 请求此ID/时间戳之前的分页内容
            before: 请求此ID/时间戳之后的分页内容
            limit: 返回结果的数量，最大100

        Returns:
            历史成交记录
        """
        params = {'instId': inst_id}
        if type:
            params['type'] = type
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/market/history-trades', params=params)

    def get_option_trades(
        self,
        inst_id: str,
        opt_type: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        limit: Optional[str] = None
    ) -> dict:
        """
        获取期权成交记录

        Args:
            inst_id: 产品ID
            opt_type: 期权类型 C:看涨 P:看跌
            after: 请求此时间戳之前的分页内容
            before: 请求此时间戳之后的分页内容
            limit: 返回结果的数量，最大100

        Returns:
            期权成交记录
        """
        params = {'instId': inst_id}
        if opt_type:
            params['optType'] = opt_type
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit

        return self.client.get('/api/v5/market/option/instrument-family-trades', params=params)

    def get_24h_volume(self) -> dict:
        """
        获取24小时总成交量

        Returns:
            24小时总成交量
        """
        return self.client.get('/api/v5/market/platform-24-volume')

    def get_oracle(self) -> dict:
        """
        获取Oracle预言机数据

        Returns:
            预言机数据
        """
        return self.client.get('/api/v5/market/open-oracle')

    def get_exchange_rate(self) -> dict:
        """
        获取法币汇率

        Returns:
            法币汇率
        """
        return self.client.get('/api/v5/market/exchange-rate')

    def get_index_components(self, index: str) -> dict:
        """
        获取指数成分数据

        Args:
            index: 指数，如 BTC-USD

        Returns:
            指数成分
        """
        params = {'index': index}
        return self.client.get('/api/v5/market/index-components', params=params)

    def get_block_tickers(self, inst_type: str, uly: Optional[str] = None, inst_family: Optional[str] = None) -> dict:
        """
        获取大宗交易所有产品行情信息

        Args:
            inst_type: 产品类型 SPOT SWAP FUTURES OPTION
            uly: 标的指数
            inst_family: 交易品种

        Returns:
            大宗交易行情
        """
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_family:
            params['instFamily'] = inst_family

        return self.client.get('/api/v5/market/block-tickers', params=params)

    def get_block_ticker(self, inst_id: str) -> dict:
        """
        获取大宗交易单个产品行情信息

        Args:
            inst_id: 产品ID

        Returns:
            大宗交易行情
        """
        params = {'instId': inst_id}
        return self.client.get('/api/v5/market/block-ticker', params=params)

    def get_block_trades(self, inst_id: str) -> dict:
        """
        获取大宗交易公共成交数据

        Args:
            inst_id: 产品ID

        Returns:
            大宗交易成交数据
        """
        params = {'instId': inst_id}
        return self.client.get('/api/v5/market/block-trades', params=params)
