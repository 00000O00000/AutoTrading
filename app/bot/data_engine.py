"""
数据引擎 - 数据聚合的主协调器。

从币安收集数据并构建供 AI 决策的上下文。
"""

import logging
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

from config import get_config
from app.bot.tz_utils import utc_now
from app.bot.binance_client import (
    BinanceClient, 
    TickerData, 
    OrderBookData, 
    FundingRateData
)
from app.bot.macro_data import MacroDataClient
from app.bot.indicators import (
    calculate_all_indicators, 
    format_indicator_summary,
    format_ohlcv_for_prompt,
    IndicatorSummary,
    TrendData,
    BollingerBandsData,
    SupportResistanceData,
    DivergenceData
)
from app.bot.exceptions import InsufficientDataError

logger = logging.getLogger(__name__)


@dataclass
class AssetContext:
    """单个资产的完整上下文。"""
    symbol: str
    ticker: TickerData
    order_book: OrderBookData
    funding_rate: FundingRateData
    indicators: IndicatorSummary
    # 多时间周期 K 线数据 (用于 AI 上下文)
    ohlcv_1m: List[List] = None   # 1 分钟 K 线
    ohlcv_15m: List[List] = None  # 15 分钟 K 线
    ohlcv_1h: List[List] = None   # 1 小时 K 线
    ohlcv_1d: List[List] = None   # 1 日 K 线


@dataclass
class MarketContext:
    """供 AI 决策的完整市场上下文。"""
    timestamp: datetime
    advance_decline_ratio: float
    assets: Dict[str, AssetContext]
    
    # Account data (optional, requires auth)
    account_balance: Optional[Dict[str, float]] = None
    positions: Optional[List[Dict]] = None
    
    # 挂单信息 (止损/止盈条件委托单)
    pending_orders: Optional[List[Dict]] = None
    
    # Memory whiteboard
    memory_content: str = ""


class DataEngine:
    """
    主数据聚合引擎。
    
    协调从币安收集数据，并构建结构化上下文供 AI 消费。
    """
    
    def __init__(
        self,
        binance_api_key: str = '',
        binance_api_secret: str = ''
    ):
        """
        初始化数据引擎。
        
        Args:
            binance_api_key: 可选的 API Key (用于私有端点)
            binance_api_secret: 可选的 API Secret
        """
        self.config = get_config()
        
        # 初始化客户端
        self.binance = BinanceClient(binance_api_key, binance_api_secret)
        self.macro = MacroDataClient()
        
        # 跟踪的交易对 (所有 5 个币种同等对待)
        self.symbols = self.config.TRADING_SYMBOLS
    
    def _create_default_indicators(self, symbol: str, current_price: float) -> IndicatorSummary:
        """
        创建默认的指标摘要 (数据不足时使用)。
        
        Args:
            symbol: 交易对
            current_price: 当前价格
            
        Returns:
            带有默认值的 IndicatorSummary
        """
        return IndicatorSummary(
            symbol=symbol,
            current_price=current_price,
            vwap=current_price,
            price_vs_vwap="NEUTRAL",
            trend=TrendData(
                ema_20=current_price,
                ema_50=current_price,
                ema_200=current_price,
                trend_direction="NEUTRAL",
                trend_strength="WEAK"
            ),
            bollinger=BollingerBandsData(
                upper=current_price * 1.02,
                middle=current_price,
                lower=current_price * 0.98,
                bandwidth=0.04,
                percent_b=0.5,
                is_squeeze=False
            ),
            atr=current_price * 0.02,
            atr_percent=2.0,
            rsi=50.0,
            rsi_condition="NEUTRAL",
            divergence=DivergenceData(
                rsi_value=50.0,
                has_bullish_divergence=False,
                has_bearish_divergence=False,
                divergence_type="NONE"
            ),
            support_resistance=SupportResistanceData(
                supports=[current_price * 0.95],
                resistances=[current_price * 1.05],
                nearest_support=current_price * 0.95,
                nearest_resistance=current_price * 1.05
            )
        )
    
    def fetch_asset_data(self, symbol: str) -> AssetContext:
        """
        获取单个资产的所有数据。
        
        Args:
            symbol: 交易对 (例如 'BTC/USDT')
            
        Returns:
            AssetContext 包含所有数据 (部分字段失败时可能使用默认值)
        """
        # 获取 ticker (必需 - 如果失败则无法继续)
        ticker = self.binance.fetch_ticker(symbol)
        
        # 获取订单簿 (可选 - 失败时使用默认值)
        try:
            order_book = self.binance.fetch_order_book(symbol, depth=10)
        except Exception as e:
            logger.debug("无法获取 %s 订单簿: %s", symbol, e)
            order_book = OrderBookData(
                bids=[], asks=[],
                bid_ask_imbalance=0.0,
                spread=0.0,
                mid_price=ticker.last_price
            )
        
        # 获取资金费率 (可选 - 失败时使用默认值)
        try:
            funding_rate = self.binance.fetch_funding_rate(symbol)
        except Exception as e:
            logger.debug("无法获取 %s 资金费率: %s", symbol, e)
            funding_rate = FundingRateData(
                symbol=symbol,
                funding_rate=0.0,
                funding_rate_annualized=0.0,
                next_funding_time=0
            )
        
        # 获取多时间周期 OHLCV (按照 Project Plan 6.1 规格)
        TIMEFRAMES = {
            '1m': 100,   # 1 分钟，100 根
            '15m': 100,  # 15 分钟，100 根
            '1h': 100,   # 1 小时，100 根
            '1d': 100    # 1 日，100 根
        }
        
        ohlcv_data = {}
        for tf, limit in TIMEFRAMES.items():
            try:
                ohlcv_data[tf] = self.binance.fetch_ohlcv(symbol, tf, limit=limit)
            except Exception as e:
                logger.warning("Could not fetch %s OHLCV for %s: %s", tf, symbol, e)
                ohlcv_data[tf] = []
        
        # 使用 1h 数据计算指标 (保持现有指标计算逻辑)
        try:
            indicators = calculate_all_indicators(symbol, ohlcv_data.get('1h', []))
        except InsufficientDataError as e:
            logger.debug("指标计算数据不足 %s: %s", symbol, e)
            # 创建默认的 IndicatorSummary
            indicators = self._create_default_indicators(symbol, ticker.last_price)
        
        return AssetContext(
            symbol=symbol,
            ticker=ticker,
            order_book=order_book,
            funding_rate=funding_rate,
            indicators=indicators,
            ohlcv_1m=ohlcv_data.get('1m', []),
            ohlcv_15m=ohlcv_data.get('15m', []),
            ohlcv_1h=ohlcv_data.get('1h', []),
            ohlcv_1d=ohlcv_data.get('1d', [])
        )
    
    def fetch_macro_data(self) -> float:
        """
        获取宏观市场数据。
        
        Returns:
            advance_decline_ratio: 市场宽度指标
        """
        # 市场宽度
        try:
            breadth_data = self.binance.fetch_top_gainers_losers(50)
            advance_decline_ratio = breadth_data['advance_decline_ratio']
        except Exception as e:
            logger.warning("Could not fetch market breadth: %s", e)
            advance_decline_ratio = 1.0
        
        return advance_decline_ratio
    
    def fetch_account_data(self) -> tuple:
        """
        获取账户余额和持仓 (需要认证)。
        
        Returns:
            Tuple of (balance_dict, positions_list)
        """
        try:
            balance = self.binance.fetch_balance()
            positions = self.binance.fetch_positions()
            return balance, positions
        except Exception as e:
            logger.debug("Private endpoints not available: %s", e)
            return None, None
    
    def _fetch_pending_orders(self) -> List[Dict]:
        """
        获取所有挂单（算法订单：止损/止盈）。
        
        使用币安私有 API 直接获取，确保数据最新。
        遵循透明法则：让 AI 能看到所有待执行的条件委托。
        
        Returns:
            挂单列表，每个订单包含 symbol, order_id, type, side, trigger_price
        """
        try:
            pending = []
            for symbol in self.symbols:
                binance_symbol = symbol.replace('/', '')
                # 获取算法订单 (止损/止盈)
                algo_orders = self.binance.exchange.fapiPrivateGetOpenAlgoOrders({
                    'symbol': binance_symbol
                })
                for order in algo_orders:
                    pending.append({
                        'symbol': symbol,
                        'order_id': order.get('algoId'),
                        'type': order.get('orderType'),  # STOP_MARKET, TAKE_PROFIT_MARKET
                        'side': order.get('side'),
                        'quantity': float(order.get('quantity', 0)),
                        'trigger_price': float(order.get('triggerPrice', 0)),
                        'is_algo': True
                    })
            return pending
        except Exception as e:
            logger.debug("无法获取挂单: %s", e)
            return []
    
    def aggregate(self, memory_content: str = "") -> MarketContext:
        """
        将所有数据源聚合为完整的市场上下文。
        
        这是交易循环的主要入口点。
        所有数据在此方法中实时刷新，确保 AI 获得最新数据。
        
        Args:
            memory_content: 当前 AI 记忆白板内容
            
        Returns:
            MarketContext 包含所有聚合数据
        """
        start_time = time.time()
        
        # 首先同步时间
        logger.debug("开始数据聚合，同步时间...")
        self.binance.synchronize_time()
        
        # 获取宏观数据
        advance_decline_ratio = self.fetch_macro_data()
        
        # 获取每个资产的数据
        assets = {}
        for symbol in self.symbols:
            try:
                asset_data = self.fetch_asset_data(symbol)
                assets[symbol] = asset_data
            except Exception as e:
                logger.warning("无法获取 %s 数据: %s", symbol, e)
        
        # 获取账户数据
        balance, positions = self.fetch_account_data()
        
        # 获取所有挂单（算法订单：止损/止盈）
        pending_orders = self._fetch_pending_orders()
        
        elapsed = time.time() - start_time
        logger.info("数据聚合完成 (%.1fs, %d 个资产)", elapsed, len(assets))
        
        return MarketContext(
            timestamp=utc_now(),
            advance_decline_ratio=advance_decline_ratio,
            assets=assets,
            account_balance=balance,
            positions=positions,
            pending_orders=pending_orders,
            memory_content=memory_content
        )
    
    def build_prompt_context(self, context: MarketContext) -> str:
        """
        构建供 AI 提示词使用的格式化上下文字符串。
        
        Args:
            context: 来自 aggregate() 的 MarketContext
            
        Returns:
            格式化的 AI 提示词字符串
        """
        sections = []
        
        # 宏观部分
        sections.append("=" * 10)
        sections.append("[MARKET CONTEXT]")
        sections.append("=" * 10)
        sections.append(self.macro.format_macro_summary(
            context.advance_decline_ratio
        ))
        
        # 资产部分 (所有 5 个币种同等对待)
        sections.append("")
        sections.append("=" * 10)
        sections.append("[ASSETS ANALYSIS]")
        sections.append("=" * 10)
        
        for symbol, asset in context.assets.items():
            sections.append("")
            sections.append(format_indicator_summary(asset.indicators))
            
            # 添加多时间周期 K 线数据
            # 输出 100 根 K 线给 AI
            if asset.ohlcv_1d:
                sections.append(format_ohlcv_for_prompt(asset.ohlcv_1d, '1D', limit=100))
            if asset.ohlcv_1h:
                sections.append(format_ohlcv_for_prompt(asset.ohlcv_1h, '1H', limit=100))
            if asset.ohlcv_15m:
                sections.append(format_ohlcv_for_prompt(asset.ohlcv_15m, '15m', limit=100))
            if asset.ohlcv_1m:
                sections.append(format_ohlcv_for_prompt(asset.ohlcv_1m, '1m', limit=100))
            
            # 添加订单簿和资金费率信息
            sections.append(f"  OrderBook: Imbalance {asset.order_book.bid_ask_imbalance:+.2f}|Spread ${asset.order_book.spread:.4f}")
            sections.append(f"  Funding: {asset.funding_rate.funding_rate_annualized:+.2f}% (annualized)")
        
        # 账户部分
        if context.account_balance:
            sections.append("")
            sections.append("=" * 10)
            sections.append("[ACCOUNT]")
            sections.append("=" * 10)
            sections.append(f"Balance: {context.account_balance['total']:.2f} USDT (Free: {context.account_balance['free']:.2f})")
            
            if context.positions:
                sections.append("Open Positions:")
                for pos in context.positions:
                    sections.append(
                        f"  - {pos['symbol']}: {pos['side']} {pos['contracts']} @ ${pos['entry_price']:.2f}|"
                        f"UPNL: ${pos['unrealized_pnl']:+.2f} ({pos['percentage']:+.2f}%)"
                    )
            else:
                sections.append("No open positions.")
            
            # 挂单信息 (止损/止盈条件委托)
            if context.pending_orders:
                sections.append("")
                sections.append("Pending Orders (SL/TP):")
                for order in context.pending_orders:
                    order_type = "SL" if "STOP" in order.get('type', '') else "TP"
                    sections.append(
                        f"  - {order['symbol']}: {order_type} {order['side']} "
                        f"@ ${order['trigger_price']:.4f} (ID: {order['order_id']})"
                    )
        
        # 记忆白板
        if context.memory_content:
            sections.append("")
            sections.append("=" * 10)
            sections.append("[MEMORY WHITEBOARD]")
            sections.append("=" * 10)
            sections.append(context.memory_content)
        
        return "\n".join(sections)
    
    def to_dict(self, context: MarketContext) -> Dict[str, Any]:
        """
        将上下文转换为字典以存储到数据库。
        
        Args:
            context: MarketContext 对象
            
        Returns:
            可序列化的字典
        """
        return {
            'timestamp': context.timestamp.isoformat(),
            'advance_decline_ratio': context.advance_decline_ratio,
            'assets': {
                symbol: {
                    'price': asset.ticker.last_price,
                    'change_24h': asset.ticker.change_24h_percent,
                    'rsi': asset.indicators.rsi,
                    'trend': asset.indicators.trend.trend_direction
                }
                for symbol, asset in context.assets.items()
            }
        }
