"""
币安 USDT-M 合约客户端封装。

通过 CCXT 提供简洁的币安合约接口。
处理精度、错误处理和数据格式化。
"""

import logging
import ccxt
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from config import get_config
from app.bot.exceptions import AuthenticationError

logger = logging.getLogger(__name__)


@dataclass
class OrderBookData:
    """包含不平衡计算的结构化订单簿数据。"""
    bids: List[List[float]]
    asks: List[List[float]]
    bid_ask_imbalance: float  # 正值 = 买单更多，负值 = 卖单更多
    spread: float
    mid_price: float


@dataclass
class TickerData:
    """结构化行情数据。"""
    symbol: str
    last_price: float
    high_24h: float
    low_24h: float
    volume_24h: float
    change_24h_percent: float
    timestamp: int


@dataclass
class FundingRateData:
    """资金费率数据。"""
    symbol: str
    funding_rate: float
    funding_rate_annualized: float
    next_funding_time: int


class BinanceClient:
    """
    币安 USDT-M 合约的 CCXT 封装。
    
    区分公共（无需认证）和私有（需认证）方法。
    """
    
    def __init__(self, api_key: str = '', api_secret: str = ''):
        """
        初始化币安客户端。
        
        Args:
            api_key: 币安 API Key（公共端点可选）
            api_secret: 币安 API Secret（公共端点可选）
        """
        config = get_config()
        
        # Use provided keys or fall back to config
        self.api_key = api_key or config.BINANCE_API_KEY
        self.api_secret = api_secret or config.BINANCE_API_SECRET
        
        # Initialize CCXT exchange
        self.exchange = ccxt.binanceusdm({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
                'adjustForTimeDifference': False,  # 我们将手动且激进地处理此问题
                'recvWindow': 60000,  # 允许 60s 偏差（我们将故意滞后）
            }
        })
        
        # Cache for market info (precision, limits)
        self._markets_cache: Optional[Dict] = None
        
        # Initial sync
        self.synchronize_time()
        
    def synchronize_time(self):
        """
        显式同步币安服务器时间，并进行激进的回拨。
        
        问题: "Timestamp for this request was 1000ms ahead of the server's time."
        解决方案: 
        1. 获取准确的服务器时间。
        2. 计算偏移量。
        3. 将本地时间向后回拨 3000ms (3秒)。
           这确保我们要么通过，要么“在过去”相对于服务器。
        4. 大的 recvWindow (60000ms) 接受这个“旧”时间戳。
        """
        try:
            # Load server time
            server_time = self.exchange.fetch_time()
            local_time = self.exchange.milliseconds()
            
            # Calculate true difference (Server - Local)
            # If Local is ahead, diff is NEGATIVE.
            true_diff = server_time - local_time
            
            # We want our sent timestamp to be: ServerTime - 3000ms
            # Sent = Local + Offset
            # Server - 3000 = Local + Offset
            # Offset = Server - Local - 3000
            # Offset = true_diff - 3000
            
            aggressive_offset = true_diff - 3000
            
            # Apply to CCXT
            self.exchange.time_difference = aggressive_offset
            self.exchange.options['adjustForTimeDifference'] = False # Ensure auto-adjust doesn't revert this
            
            logger.debug(
                "时间同步完毕。差值: %d ms, 应用偏移: %d ms",
                true_diff, aggressive_offset
            )
        except Exception as e:
            logger.warning("时间同步失败: %s", e)
    
    # =========================================================================
    # 公共端点 (无需认证)
    # =========================================================================
    
    def load_markets(self) -> Dict:
        """加载并缓存市场信息。"""
        if self._markets_cache is None:
            self._markets_cache = self.exchange.load_markets()
        return self._markets_cache
    
    def fetch_ohlcv(
        self, 
        symbol: str, 
        timeframe: str = '1h', 
        limit: int = 300
    ) -> List[List]:
        """
        获取 OHLCV K线数据。
        
        Args:
            symbol: 交易对 (例如 'BTC/USDT')
            timeframe: K线间隔 ('1m', '5m', '15m', '1h', '4h', '1d')
            limit: K线数量 (最大 1500)
            
        Returns:
            List of [timestamp, open, high, low, close, volume]
        """
        return self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    
    def fetch_ohlcv_multi_timeframe(
        self, 
        symbol: str, 
        timeframes: List[str] = None,
        limit: int = 300
    ) -> Dict[str, List[List]]:
        """
        获取多个时间周期的 OHLCV 数据。
        
        Args:
            symbol: 交易对
            timeframes: 时间周期列表 (默认: 配置中的时间周期)
            limit: 每个时间周期的K线数量
            
        Returns:
            Dict 映射 timeframe -> OHLCV 数据
        """
        if timeframes is None:
            config = get_config()
            timeframes = config.TIMEFRAMES
        
        result = {}
        for tf in timeframes:
            result[tf] = self.fetch_ohlcv(symbol, tf, limit)
        
        return result
    
    def fetch_ticker(self, symbol: str) -> TickerData:
        """
        获取当前行情数据。
        
        Args:
            symbol: 交易对
            
        Returns:
            TickerData 包含当前价格和 24h 统计
        """
        ticker = self.exchange.fetch_ticker(symbol)
        
        last_price = ticker.get('last')
        if last_price is None or last_price <= 0:
            logger.warning("无效价格数据 %s: %s", symbol, last_price)
            raise ValueError(f"Invalid ticker price for {symbol}: {last_price}")
        
        return TickerData(
            symbol=symbol,
            last_price=last_price,
            high_24h=ticker.get('high') or last_price,
            low_24h=ticker.get('low') or last_price,
            volume_24h=ticker.get('quoteVolume') or 0,  # Volume in USDT
            change_24h_percent=ticker.get('percentage') or 0,
            timestamp=ticker.get('timestamp') or 0
        )
    
    def fetch_tickers(self, symbols: List[str]) -> Dict[str, TickerData]:
        """
        获取多个交易对的行情数据。
        
        Args:
            symbols: 交易对列表
            
        Returns:
            Dict 映射 symbol -> TickerData
        """
        result = {}
        for symbol in symbols:
            result[symbol] = self.fetch_ticker(symbol)
        return result
    
    def fetch_order_book(self, symbol: str, depth: int = 10) -> OrderBookData:
        """
        获取订单簿并计算买卖不平衡度。
        
        Args:
            symbol: 交易对
            depth: 获取深度 (默认 10)
            
        Returns:
            OrderBookData 包含不平衡度指标
        """
        order_book = self.exchange.fetch_order_book(symbol, limit=depth)
        
        bids = order_book['bids'][:depth]
        asks = order_book['asks'][:depth]
        
        # 计算不平衡度：总买单量 vs 总卖单量
        bid_volume = sum(bid[1] for bid in bids) if bids else 0
        ask_volume = sum(ask[1] for ask in asks) if asks else 0
        total_volume = bid_volume + ask_volume
        
        if total_volume > 0:
            # 范围: -1 (全卖) 到 +1 (全买)
            imbalance = (bid_volume - ask_volume) / total_volume
        else:
            imbalance = 0.0
        
        best_bid = bids[0][0] if bids else 0
        best_ask = asks[0][0] if asks else 0
        spread = best_ask - best_bid if best_bid and best_ask else 0
        mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
        
        return OrderBookData(
            bids=bids,
            asks=asks,
            bid_ask_imbalance=imbalance,
            spread=spread,
            mid_price=mid_price
        )
    
    def fetch_funding_rate(self, symbol: str) -> FundingRateData:
        """
        获取当前资金费率。
        
        Args:
            symbol: 交易对
            
        Returns:
            FundingRateData 包含当前费率和年化费率
        """
        # Use CCXT's fetch_funding_rate method
        funding_info = self.exchange.fetch_funding_rate(symbol)
        
        rate = funding_info.get('fundingRate', 0)
        next_time = funding_info.get('fundingTimestamp', 0)
        
        # 年化: 每天 3 个资金周期, 365 天
        annualized = rate * 3 * 365 * 100  # As percentage
        
        return FundingRateData(
            symbol=symbol,
            funding_rate=rate,
            funding_rate_annualized=annualized,
            next_funding_time=next_time
        )
    
    def fetch_top_gainers_losers(self, limit: int = 50) -> Dict[str, Any]:
        """
        获取涨跌幅榜用于市场广度分析。
        
        Args:
            limit: 分析的头部币种数量
            
        Returns:
            Dict 包含涨幅榜、跌幅榜和涨跌比
        """
        tickers = self.exchange.fetch_tickers()
        
        # 筛选 USDT 交易对并按涨跌幅排序
        usdt_pairs = [
            (symbol, data['percentage']) 
            for symbol, data in tickers.items() 
            if symbol.endswith('/USDT') and data.get('percentage') is not None
        ]
        
        # 按百分比排序 (涨幅榜降序)
        sorted_by_gain = sorted(usdt_pairs, key=lambda x: x[1], reverse=True)
        
        # 取前 N 个进行涨跌比分析
        top_pairs = sorted_by_gain[:limit]
        
        # 统计前 N 个中的涨跌数量用于计算涨跌比
        gainers_in_top = [(s, p) for s, p in top_pairs if p > 0]
        losers_in_top = [(s, p) for s, p in top_pairs if p < 0]
        
        advance_count = len(gainers_in_top)
        decline_count = len(losers_in_top)
        
        if decline_count > 0:
            ad_ratio = advance_count / decline_count
        else:
            # 使用大数值代替 inf，确保 JSON 可序列化
            ad_ratio = 9999.0 if advance_count > 0 else 1.0
        
        # 获取实际的前 10 个涨幅榜 (最正) 和前 10 个跌幅榜 (最负)
        top_10_gainers = sorted_by_gain[:10]
        top_10_losers = sorted(usdt_pairs, key=lambda x: x[1])[:10]  # Ascending = most negative first
        
        return {
            'gainers': top_10_gainers,
            'losers': top_10_losers,
            'advance_count': advance_count,
            'decline_count': decline_count,
            'advance_decline_ratio': ad_ratio
        }
    
    # =========================================================================
    # 私有端点 (需要认证)
    # =========================================================================
    
    def _require_auth(self):
        """检查 API 凭证是否已配置。"""
        if not self.api_key or not self.api_secret:
            raise AuthenticationError("private endpoints")
    
    def fetch_balance(self) -> Dict[str, float]:
        """
        获取账户余额。
        
        Returns:
            Dict 包含 USDT 余额信息
        """
        self._require_auth()
        balance = self.exchange.fetch_balance()
        
        usdt = balance.get('USDT', {})
        return {
            'total': usdt.get('total', 0),
            'free': usdt.get('free', 0),
            'used': usdt.get('used', 0)
        }
    
    def fetch_positions(self, symbols: List[str] = None) -> List[Dict]:
        """
        获取当前持仓。
        
        Args:
            symbols: 可选的交易对列表，用于过滤
            
        Returns:
            List of 持仓字典
        """
        self._require_auth()
        # CCXT binanceusdm 允许传递 symbols 参数来过滤 (映射到 API)
        # 注意: 即使传递了 symbols，某些交易所也可能返回所有并在本地过滤
        positions = self.exchange.fetch_positions(symbols)
        
        # 仅过滤活跃持仓
        active = []
        for pos in positions:
            contracts = float(pos.get('contracts', 0))
            if contracts != 0:
                active.append(self._format_position(pos))
        
        return active
        
    def _format_position(self, pos: Dict) -> Dict:
        """格式化单个持仓数据。"""
        # 移除可能的后缀，如 DOGE/USDT:USDT -> DOGE/USDT
        raw_symbol = pos['symbol']
        symbol = raw_symbol.split(':')[0]
        
        contracts = float(pos.get('contracts', 0))
        return {
            'symbol': symbol,  # 标准化 DOMAIN/QUOTE
            'side': 'LONG' if contracts > 0 else 'SHORT',
            'contracts': abs(contracts),
            'notional': pos.get('notional', 0),
            'entry_price': pos.get('entryPrice', 0),
            'mark_price': pos.get('markPrice', 0),
            'unrealized_pnl': pos.get('unrealizedPnl', 0),
            'percentage': pos.get('percentage', 0),
            'leverage': pos.get('leverage', 1)
        }
    
    # =========================================================================
    # 工具方法
    # =========================================================================
    
    def get_precision(self, symbol: str) -> Dict[str, int]:
        """
        获取交易对的价格和数量精度。
        """
        self.load_markets()
        market = self._markets_cache.get(symbol, {})
        
        precision = market.get('precision', {})
        return {
            'price': precision.get('price', 2),
            'amount': precision.get('amount', 8)
        }
    
    def truncate_to_precision(self, value: float, precision: int) -> float:
        """截断到指定精度。"""
        multiplier = 10 ** precision
        return int(value * multiplier) / multiplier
    
    def get_min_notional(self, symbol: str) -> float:
        """获取最小名义价值。"""
        self.load_markets()
        market = self._markets_cache.get(symbol, {})
        limits = market.get('limits', {})
        cost_limits = limits.get('cost', {})
        return cost_limits.get('min', 5.0)
    
    def calculate_quantity(self, symbol: str, usdt_amount: float, current_price: float = None) -> float:
        """计算下单数量。"""
        if current_price is None:
            ticker = self.fetch_ticker(symbol)
            current_price = ticker.last_price
        
        if current_price <= 0:
            raise ValueError(f"Invalid price for {symbol}: {current_price}")
        
        raw_quantity = usdt_amount / current_price
        precision = self.get_precision(symbol)
        return self.truncate_to_precision(raw_quantity, precision['amount'])
    
    def get_position_size(self, symbol: str) -> dict:
        """
        获取交易对的当前持仓大小。
        
        包含重试机制以应对 API 延迟。
        
        Args:
            symbol: 交易对
            
        Returns:
            Dict 包含合约数、方向和名义价值
        """
        self._require_auth()
        
        import time
        max_retries = 3
        
        for i in range(max_retries):
            # 尝试指定 symbol 获取，此方式在部分 API 上更精确
            try:
                positions = self.fetch_positions([symbol])
                for pos in positions:
                    if pos['symbol'] == symbol:
                        return pos
            except Exception as e:
                logger.warning("尝试获取持仓失败 (%d/%d): %s", i+1, max_retries, e)
            
            # 如果没找到，但在前几次重试中，稍微等待一下
            if i < max_retries - 1:
                time.sleep(1)
        
        return None
    
    # =========================================================================
    # 交易执行 (需要认证)
    # =========================================================================
    
    def create_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float
    ) -> Dict:
        """
        创建市价单。
        
        Args:
            symbol: 交易对 (例如 'BTC/USDT')
            side: 'buy' (买) 或 'sell' (卖)
            quantity: 订单数量 (基础货币)
            
        Returns:
            交易所的订单响应
        """
        self._require_auth()
        
        logger.info(
            "正在创建市价单: %s %s %.8f",
            side.upper(), symbol, quantity
        )
        
        order = self.exchange.create_order(
            symbol=symbol,
            type='market',
            side=side.lower(),
            amount=quantity
        )
        
        logger.info("订单已创建: %s", order.get('id'))
        return order
    
    def create_stop_loss_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float
    ) -> Dict:
        """
        创建市价止损单。
        
        多头持仓: side='sell', 当价格跌至 stop_price 时触发
        空头持仓: side='buy', 当价格涨至 stop_price 时触发
        
        Args:
            symbol: 交易对
            side: 'buy' 或 'sell' (与持仓方向相反)
            quantity: 订单数量
            stop_price: 触发价格
            
        Returns:
            交易所的订单响应
        """
        self._require_auth()
        
        # 将止损价格截断到正确精度
        precision = self.get_precision(symbol)
        stop_price = self.truncate_to_precision(stop_price, precision['price'])
        
        logger.info(
            "正在创建止损单: %s %s %.8f @ %.2f",
            side.upper(), symbol, quantity, stop_price
        )
        
        # 使用币安的 STOP_MARKET 订单类型
        order = self.exchange.create_order(
            symbol=symbol,
            type='STOP_MARKET',
            side=side.lower(),
            amount=quantity,
            params={
                'stopPrice': stop_price,
                'reduceOnly': True  # Only reduce position, don't flip
            }
        )
        
        logger.info("止损单已创建: %s", order.get('id'))
        return order
    
    def cancel_all_orders(self, symbol: str) -> List[Dict]:
        """
        取消交易对的所有挂单（包括普通订单和算法订单/条件委托单）。
        
        币安合约中止损/止盈单被创建为算法订单 (algoType: CONDITIONAL)，
        需要使用专门的 API 来取消。
        
        Args:
            symbol: 交易对
            
        Returns:
            List of 已取消的订单
        """
        self._require_auth()
        cancelled_orders = []
        binance_symbol = symbol.replace('/', '')
        
        # 1. 取消普通订单
        try:
            result = self.exchange.fapiPrivateDeleteAllOpenOrders({
                'symbol': binance_symbol
            })
            logger.info("已取消 %s 的普通订单: %s", symbol, result)
            if isinstance(result, list):
                cancelled_orders.extend(result)
        except Exception as e:
            logger.warning("取消普通订单失败: %s", e)
            # 回退到 CCXT 方法
            try:
                result = self.exchange.cancel_all_orders(symbol)
                if isinstance(result, list):
                    cancelled_orders.extend(result)
            except Exception as e2:
                logger.warning("CCXT cancel_all_orders 也失败: %s", e2)
        
        # 2. 取消算法订单（止损/止盈条件委托单）
        try:
            result = self.exchange.fapiPrivateDeleteAlgoOpenOrders({
                'symbol': binance_symbol
            })
            logger.info("已取消 %s 的算法订单: %s", symbol, result)
        except Exception as e:
            logger.warning("取消算法订单失败: %s", e)
            # 尝试逐个取消
            try:
                algo_orders = self.exchange.fapiPrivateGetOpenAlgoOrders({
                    'symbol': binance_symbol
                })
                for order in algo_orders:
                    try:
                        self.exchange.fapiPrivateDeleteAlgoOrder({
                            'symbol': binance_symbol,
                            'algoId': order.get('algoId')
                        })
                        cancelled_orders.append(order)
                        logger.info("已取消算法订单: %s", order.get('algoId'))
                    except Exception as inner_e:
                        logger.warning("取消算法订单 %s 失败: %s", order.get('algoId'), inner_e)
            except Exception as e2:
                logger.warning("获取算法订单也失败: %s", e2)
        
        return cancelled_orders
    
    def cancel_order_by_id(self, symbol: str, order_id: str) -> Dict:
        """
        根据订单 ID 取消单个订单。
        
        自动检测订单类型（普通订单或算法订单）并调用相应的 API。
        
        Args:
            symbol: 交易对 (例如 'BTC/USDT')
            order_id: 订单 ID
            
        Returns:
            取消结果
        """
        self._require_auth()
        binance_symbol = symbol.replace('/', '')
        
        logger.info("正在取消订单: symbol=%s, order_id=%s", symbol, order_id)
        
        # 先尝试作为普通订单取消
        try:
            result = self.exchange.cancel_order(order_id, symbol)
            logger.info("已取消普通订单: %s", order_id)
            return {'success': True, 'order_id': order_id, 'type': 'normal', 'result': result}
        except Exception as e:
            logger.debug("普通订单取消失败，尝试算法订单: %s", e)
        
        # 尝试作为算法订单取消 (使用 algoId)
        try:
            result = self.exchange.fapiPrivateDeleteAlgoOrder({
                'symbol': binance_symbol,
                'algoId': order_id
            })
            logger.info("已取消算法订单: %s", order_id)
            return {'success': True, 'order_id': order_id, 'type': 'algo', 'result': result}
        except Exception as e:
            logger.error("取消订单失败 %s: %s", order_id, e)
            return {'success': False, 'order_id': order_id, 'error': str(e)}
    
    # =========================================================================
    # ADVANCED TRADING FUNCTIONS (杠杆、保证金模式、止盈止损)
    # =========================================================================
    
    def set_leverage(self, symbol: str, leverage: int) -> Dict:
        """
        设置交易对杠杆。
        
        Args:
            symbol: 交易对 (例如 'BTC/USDT')
            leverage: 杠杆倍数 (1-125)
            
        Returns:
            交易所响应
        """
        self._require_auth()
        
        # 限制杠杆在有效范围内
        leverage = max(1, min(125, leverage))
        
        logger.info("正在设置 %s 杠杆为 %dx", symbol, leverage)
        
        try:
            result = self.exchange.set_leverage(leverage, symbol)
            logger.info("杠杆已设置: %s -> %dx", symbol, leverage)
            return result
        except Exception as e:
            logger.error("设置杠杆失败 %s: %s", symbol, e)
            raise
    
    def set_margin_mode(self, symbol: str, mode: str) -> Dict:
        """
        设置交易对保证金模式。
        
        Args:
            symbol: 交易对
            mode: 'cross' (全仓) 或 'isolated' (逐仓)
            
        Returns:
            交易所响应
        """
        self._require_auth()
        
        mode = mode.lower()
        if mode not in ('cross', 'isolated'):
            raise ValueError(f"无效的保证金模式: {mode}")
        
        logger.info("正在设置 %s 保证金模式为 %s", symbol, mode)
        
        try:
            result = self.exchange.set_margin_mode(mode, symbol)
            logger.info("保证金模式已设置: %s -> %s", symbol, mode)
            return result
        except Exception as e:
            # 如果已经是该模式，币安会返回错误，这是可以忽略的
            if 'No need to change margin type' in str(e):
                logger.info("保证金模式已经是 %s，无需更改", mode)
                return {'info': 'already_set'}
            logger.error("设置保证金模式失败 %s: %s", symbol, e)
            raise
    
    def create_take_profit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        take_profit_price: float
    ) -> Dict:
        """
        创建市价止盈单。
        
        多头持仓: side='sell', 当价格涨至 take_profit_price 时触发
        空头持仓: side='buy', 当价格跌至 take_profit_price 时触发
        
        Args:
            symbol: 交易对
            side: 'buy' 或 'sell' (与持仓方向相反)
            quantity: 订单数量
            take_profit_price: 触发价格
            
        Returns:
            交易所的订单响应
        """
        self._require_auth()
        
        # 将价格截断到正确精度
        precision = self.get_precision(symbol)
        take_profit_price = self.truncate_to_precision(take_profit_price, precision['price'])
        
        logger.info(
            "正在创建止盈单: %s %s %.8f @ %.2f",
            side.upper(), symbol, quantity, take_profit_price
        )
        
        # 使用币安的 TAKE_PROFIT_MARKET 订单类型
        order = self.exchange.create_order(
            symbol=symbol,
            type='TAKE_PROFIT_MARKET',
            side=side.lower(),
            amount=quantity,
            params={
                'stopPrice': take_profit_price,
                'reduceOnly': True
            }
        )
        
        logger.info("止盈单已创建: %s", order.get('id'))
        return order
    
    def get_open_orders(self, symbol: str = None) -> List[Dict]:
        """
        获取交易对或所有交易对的挂单，包括条件委托单（止损/止盈）。
        
        Args:
            symbol: 交易对 (可选, None 表示所有)
            
        Returns:
            List of 挂单 (包括普通订单和条件订单)
        """
        self._require_auth()
        
        all_orders = []
        
        try:
            # 获取普通挂单
            if symbol:
                orders = self.exchange.fetch_open_orders(symbol)
            else:
                orders = self.exchange.fetch_open_orders()
            all_orders.extend(orders)
        except Exception as e:
            logger.warning("获取普通挂单失败: %s", e)
        
        # 获取算法订单 (条件委托单：止损/止盈)
        # 算法订单需要使用专用的 API 端点
        try:
            if symbol:
                binance_symbol = symbol.replace('/', '')
                
                # 使用正确的 API 获取算法订单 (止损/止盈条件委托)
                algo_orders = self.exchange.fapiPrivateGetOpenAlgoOrders({
                    'symbol': binance_symbol
                })
                
                for order in algo_orders:
                    order_id = str(order.get('algoId'))
                    # 检查是否已存在于 all_orders 中
                    if not any(str(o.get('id')) == order_id for o in all_orders):
                        all_orders.append({
                            'id': order_id,
                            'symbol': symbol,
                            'type': order.get('orderType'),  # STOP_MARKET, TAKE_PROFIT_MARKET
                            'side': order.get('side'),
                            'amount': float(order.get('quantity', 0)),
                            'stopPrice': float(order.get('triggerPrice', 0)),
                            'status': order.get('algoStatus'),
                            'is_algo': True,
                            'info': order
                        })
        except Exception as e:
            logger.debug("获取算法订单失败 (可能不影响功能): %s", e)
        
        return all_orders
    
    def cancel_orders_by_type(self, symbol: str, order_type: str) -> List[Dict]:
        """
        取消特定类型的订单。
        
        Args:
            symbol: 交易对
            order_type: 'stop_loss', 'take_profit', 或 'all'
            
        Returns:
            List of 已取消的订单
        """
        self._require_auth()
        
        if order_type.lower() == 'all':
            return self.cancel_all_orders(symbol)
        
        # 获取挂单并按类型过滤
        orders = self.get_open_orders(symbol)
        cancelled = []
        
        # 匹配模式：同时检查 CCXT type 和币安原始 info.type
        # 止损单类型: STOP_MARKET, STOP, stop_market
        # 止盈单类型: TAKE_PROFIT_MARKET, TAKE_PROFIT, take_profit_market
        type_patterns = {
            'stop_loss': ['STOP_MARKET', 'STOP', 'stop_market', 'stop'],
            'take_profit': ['TAKE_PROFIT_MARKET', 'TAKE_PROFIT', 'take_profit_market', 'take_profit']
        }
        
        target_patterns = type_patterns.get(order_type.lower(), [])
        
        for order in orders:
            # 获取订单类型 (检查多个来源)
            ccxt_type = str(order.get('type', '')).upper()
            info_type = str(order.get('info', {}).get('type', '')).upper() if order.get('info') else ''
            
            # 匹配任一来源
            matched = False
            for pattern in target_patterns:
                if ccxt_type == pattern.upper() or info_type == pattern.upper():
                    matched = True
                    break
            
            if matched:
                try:
                    # 根据订单类型选择正确的取消 API
                    if order.get('is_algo'):
                        # 算法订单需要使用 algoId 取消
                        binance_symbol = symbol.replace('/', '')
                        self.exchange.fapiPrivateDeleteAlgoOrder({
                            'symbol': binance_symbol,
                            'algoId': order['id']
                        })
                    else:
                        # 普通订单使用标准 CCXT 方法
                        self.exchange.cancel_order(order['id'], symbol)
                    cancelled.append(order)
                    logger.info("已取消 %s 订单: %s (type=%s, is_algo=%s)", 
                               order_type, order['id'], ccxt_type, order.get('is_algo', False))
                except Exception as e:
                    logger.warning("取消订单失败 %s: %s", order['id'], e)
            else:
                logger.debug("订单 %s 类型不匹配 (type=%s, info.type=%s), 跳过",
                            order['id'], ccxt_type, info_type)
        
        return cancelled

