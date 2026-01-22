"""
技术指标计算模块。

提供各种技术分析指标的计算功能，使用纯 pandas/numpy 实现以获得最大兼容性。
"""

import pandas as pd
import numpy as np
import logging
from datetime import datetime
from typing import List, Tuple, Optional
from dataclasses import dataclass
from app.bot.exceptions import InsufficientDataError
from app.bot.tz_utils import from_timestamp, format_time

logger = logging.getLogger(__name__)


def calc_sma(data: List[float], period: int) -> List[Optional[float]]:
    """
    计算简单移动平均 (模块化法则: 提取为可复用函数)。
    
    Args:
        data: 价格数据列表
        period: 计算周期
        
    Returns:
        SMA 值列表，前 period-1 个位置为 None
    """
    if len(data) < period:
        return [None] * len(data)
    result = [None] * (period - 1)
    for i in range(period - 1, len(data)):
        result.append(sum(data[i - period + 1:i + 1]) / period)
    return result


@dataclass
class BollingerBandsData:
    """布林带指标数据。"""
    upper: float
    middle: float
    lower: float
    bandwidth: float  # (Upper - Lower) / Middle
    percent_b: float  # (Price - Lower) / (Upper - Lower)
    is_squeeze: bool  # Bandwidth < 20-period avg


@dataclass
class TrendData:
    """趋势分析数据。"""
    ema_20: float
    ema_50: float
    ema_200: float
    trend_direction: str  # "BULLISH", "BEARISH", "NEUTRAL"
    trend_strength: str  # "STRONG", "MODERATE", "WEAK"


@dataclass
class SupportResistanceData:
    """支撑位和阻力位。"""
    supports: List[float]
    resistances: List[float]
    nearest_support: float
    nearest_resistance: float


@dataclass
class DivergenceData:
    """RSI 背离检测结果。"""
    rsi_value: float
    has_bullish_divergence: bool
    has_bearish_divergence: bool
    divergence_type: str  # "BULLISH", "BEARISH", "NONE"


@dataclass
class IndicatorSummary:
    """单个代码的完整指标摘要。"""
    symbol: str
    current_price: float
    vwap: float
    price_vs_vwap: str  # "ABOVE", "BELOW"
    trend: TrendData
    bollinger: BollingerBandsData
    atr: float
    atr_percent: float  # ATR as % of price
    rsi: float
    rsi_condition: str  # "OVERBOUGHT", "OVERSOLD", "NEUTRAL"
    divergence: DivergenceData
    support_resistance: SupportResistanceData


def create_dataframe(ohlcv_data: List[List]) -> pd.DataFrame:
    """
    将 OHLCV 列表转换为 pandas DataFrame。
    
    Args:
        ohlcv_data: 列表 [timestamp, open, high, low, close, volume]
        
    Returns:
        具有正确列名和 datetime 索引的 DataFrame
    """
    df = pd.DataFrame(
        ohlcv_data,
        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
    )
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df


def _ema(series: pd.Series, period: int) -> pd.Series:
    """计算指数移动平均线 (EMA)。"""
    return series.ewm(span=period, adjust=False).mean()


def _sma(series: pd.Series, period: int) -> pd.Series:
    """计算简单移动平均线 (SMA)。"""
    return series.rolling(window=period).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    计算相对强弱指数 (RSI)。
    
    使用 Wilder 平滑法 (alpha=1/period 的 EMA)。
    """
    delta = series.diff()
    
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    
    # Wilder 平滑法 (等同于 alpha=1/period 的 EMA)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    
    # 避免除以零：当 avg_loss 为 0 时，RSI = 100
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    
    # 当 avg_loss 为 0 时，RSI 应为 100 (纯收益)
    rsi = rsi.fillna(100)
    
    return rsi


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """计算真实波幅 (True Range)。"""
    prev_close = close.shift(1)
    
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """计算平均真实波幅 (ATR)。"""
    tr = _true_range(high, low, close)
    return tr.ewm(alpha=1/period, min_periods=period).mean()


def _bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> tuple:
    """
    计算布林带。
    
    Returns: (upper, middle, lower) 作为 pd.Series
    """
    middle = _sma(series, period)
    std = series.rolling(window=period).std()
    
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    
    return upper, middle, lower


def _vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """
    计算成交量加权平均价格 (VWAP)。
    
    对于加密货币 (7x24)，计算从数据开始的累积 VWAP。
    """
    typical_price = (high + low + close) / 3
    cumvol = volume.cumsum()
    # 避免除零：将 0 替换为 NaN
    cumvol = cumvol.replace(0, np.nan)
    vwap = (typical_price * volume).cumsum() / cumvol
    return vwap


def calculate_emas(df: pd.DataFrame, periods: List[int] = [20, 50, 200]) -> TrendData:
    """
    计算 EMA 并确定趋势方向。
    
    Args:
        df: OHLCV DataFrame
        periods: 要计算的 EMA 周期
        
    Returns:
        TrendData 包含 EMA 和趋势评估
    """
    current_price = df['close'].iloc[-1]
    
    ema_values = {}
    for period in periods:
        ema = _ema(df['close'], period)
        ema_values[period] = ema.iloc[-1] if len(ema) > 0 else current_price
    
    ema_20 = ema_values.get(20, current_price)
    ema_50 = ema_values.get(50, current_price)
    ema_200 = ema_values.get(200, current_price)
    
    # 确定趋势方向
    if current_price > ema_20 > ema_50 > ema_200:
        direction = "BULLISH"
        strength = "STRONG"
    elif current_price > ema_50 > ema_200:
        direction = "BULLISH"
        strength = "MODERATE"
    elif current_price > ema_200:
        direction = "BULLISH"
        strength = "WEAK"
    elif current_price < ema_20 < ema_50 < ema_200:
        direction = "BEARISH"
        strength = "STRONG"
    elif current_price < ema_50 < ema_200:
        direction = "BEARISH"
        strength = "MODERATE"
    elif current_price < ema_200:
        direction = "BEARISH"
        strength = "WEAK"
    else:
        direction = "NEUTRAL"
        strength = "WEAK"
    
    return TrendData(
        ema_20=ema_20,
        ema_50=ema_50,
        ema_200=ema_200,
        trend_direction=direction,
        trend_strength=strength
    )


def calculate_bollinger_bands(
    df: pd.DataFrame, 
    length: int = 20, 
    std: float = 2.0
) -> BollingerBandsData:
    """
    计算带有挤压检测的布林带。
    
    Args:
        df: OHLCV DataFrame
        length: 移动平均周期
        std: 标准差倍数
        
    Returns:
        BollingerBandsData 包含布林带值和分析
    """
    upper, middle, lower = _bollinger_bands(df['close'], length, std)
    
    current_price = df['close'].iloc[-1]
    upper_val = upper.iloc[-1]
    middle_val = middle.iloc[-1]
    lower_val = lower.iloc[-1]
    
    # 处理 NaN 值
    if pd.isna(upper_val) or pd.isna(lower_val):
        return BollingerBandsData(
            upper=current_price,
            middle=current_price,
            lower=current_price,
            bandwidth=0,
            percent_b=0.5,
            is_squeeze=False
        )
    
    bandwidth = (upper_val - lower_val) / middle_val if middle_val != 0 else 0
    percent_b = (current_price - lower_val) / (upper_val - lower_val) if (upper_val - lower_val) != 0 else 0.5
    
    # 检测挤压：当前带宽 < 20周期平均带宽
    bandwidth_series = (upper - lower) / middle
    avg_bandwidth = bandwidth_series.rolling(20).mean().iloc[-1]
    is_squeeze = bandwidth < avg_bandwidth * 0.8 if not pd.isna(avg_bandwidth) else False
    
    return BollingerBandsData(
        upper=upper_val,
        middle=middle_val,
        lower=lower_val,
        bandwidth=bandwidth,
        percent_b=percent_b,
        is_squeeze=is_squeeze
    )


def calculate_atr(df: pd.DataFrame, length: int = 14) -> Tuple[float, float]:
    """
    计算平均真实波幅 (ATR)。
    
    Args:
        df: OHLCV DataFrame
        length: ATR 周期
        
    Returns:
        Tuple of (ATR 值, ATR 占价格百分比)
    """
    atr_series = _atr(df['high'], df['low'], df['close'], length)
    
    atr_value = atr_series.iloc[-1]
    if pd.isna(atr_value):
        return 0.0, 0.0
    
    current_price = df['close'].iloc[-1]
    atr_percent = (atr_value / current_price) * 100 if current_price != 0 else 0
    
    return atr_value, atr_percent


def calculate_rsi(df: pd.DataFrame, length: int = 14) -> Tuple[float, str]:
    """
    计算 RSI 并评估状态。
    
    Args:
        df: OHLCV DataFrame
        length: RSI 周期
        
    Returns:
        Tuple of (RSI 值, 状态字符串)
    """
    rsi_series = _rsi(df['close'], length)
    
    rsi_value = rsi_series.iloc[-1]
    if pd.isna(rsi_value):
        logger.debug("%s RSI 计算返回 NaN，使用默认值 50", 'RSI')
        return 50.0, "NEUTRAL"
    
    if rsi_value >= 70:
        condition = "OVERBOUGHT"
    elif rsi_value <= 30:
        condition = "OVERSOLD"
    else:
        condition = "NEUTRAL"
    
    return rsi_value, condition


def calculate_vwap(df: pd.DataFrame) -> float:
    """
    计算成交量加权平均价格 (VWAP)。
    
    Args:
        df: OHLCV DataFrame
        
    Returns:
        VWAP 值
    """
    vwap_series = _vwap(df['high'], df['low'], df['close'], df['volume'])
    
    vwap_value = vwap_series.iloc[-1]
    if pd.isna(vwap_value):
        logger.debug("VWAP 计算返回 NaN，使用收盘价均值")
        return df['close'].mean()
    
    return vwap_value


def detect_support_resistance(
    df: pd.DataFrame, 
    window: int = 5, 
    num_levels: int = 3
) -> SupportResistanceData:
    """
    使用分形方法检测支撑和阻力位。
    
    Args:
        df: OHLCV DataFrame
        window: 分形检测的回溯窗口
        num_levels: 返回的层级数量
        
    Returns:
        SupportResistanceData 包含关键位
    """
    highs = df['high'].values
    lows = df['low'].values
    current_price = df['close'].iloc[-1]
    
    resistances = []
    supports = []
    
    # 寻找分形高点 (阻力) 和低点 (支撑)
    for i in range(window, len(df) - window):
        # 分形高点：高于周围的 K 线
        if highs[i] == max(highs[i-window:i+window+1]):
            resistances.append(highs[i])
        
        # 分形低点：低于周围的 K 线
        if lows[i] == min(lows[i-window:i+window+1]):
            supports.append(lows[i])
    
    # 排序并去重 (聚集附近的层级)
    resistances = sorted(set(resistances))  # 升序排列
    supports = sorted(set(supports), reverse=True)  # 降序排列
    
    # 过滤：只有当前价格上方的才是阻力位，下方的才是支撑位
    # 阻力位：升序排列后过滤，前 N 个就是最近的
    resistances = [r for r in resistances if r > current_price][:num_levels]
    
    # 支撑位：降序排列后过滤，前 N 个就是最近的
    supports = [s for s in supports if s < current_price][:num_levels]
    
    nearest_resistance = resistances[0] if resistances else current_price * 1.05
    nearest_support = supports[0] if supports else current_price * 0.95
    
    return SupportResistanceData(
        supports=supports,
        resistances=resistances,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance
    )


def detect_divergence(df: pd.DataFrame, lookback: int = 14) -> DivergenceData:
    """
    检测 RSI 背离。
    
    看涨背离：价格创新低，RSI 低点抬高
    看跌背离：价格创新高，RSI 高点降低
    
    Args:
        df: OHLCV DataFrame
        lookback: 检查背离的回溯周期
        
    Returns:
        DivergenceData 包含背离评估
    """
    rsi_series = _rsi(df['close'], 14)
    
    if len(rsi_series) < lookback + 5:
        return DivergenceData(
            rsi_value=50.0,
            has_bullish_divergence=False,
            has_bearish_divergence=False,
            divergence_type="NONE"
        )
    
    rsi_value = rsi_series.iloc[-1]
    if pd.isna(rsi_value):
        rsi_value = 50.0
    
    # 获取近期价格和 RSI 数据
    recent_close = df['close'].iloc[-lookback:]
    recent_rsi = rsi_series.iloc[-lookback:]
    
    # 寻找局部峰值和谷值
    price_highs = recent_close.rolling(3, center=True).max()
    price_lows = recent_close.rolling(3, center=True).min()
    rsi_highs = recent_rsi.rolling(3, center=True).max()
    rsi_lows = recent_rsi.rolling(3, center=True).min()
    
    # 检查看跌背离 (价格更高的高点，RSI 更低的高点)
    has_bearish = False
    price_highs_clean = price_highs.dropna()
    rsi_highs_clean = rsi_highs.dropna()
    
    if len(price_highs_clean) >= 2 and len(rsi_highs_clean) >= 2:
        price_peak_1 = price_highs_clean.iloc[-1]
        price_peak_2 = price_highs_clean.iloc[-2]
        rsi_peak_1 = rsi_highs_clean.iloc[-1]
        rsi_peak_2 = rsi_highs_clean.iloc[-2]
        
        if price_peak_1 > price_peak_2 and rsi_peak_1 < rsi_peak_2:
            has_bearish = True
    
    # 检查看涨背离 (价格更低的低点，RSI 更高的低点)
    has_bullish = False
    price_lows_clean = price_lows.dropna()
    rsi_lows_clean = rsi_lows.dropna()
    
    if len(price_lows_clean) >= 2 and len(rsi_lows_clean) >= 2:
        price_trough_1 = price_lows_clean.iloc[-1]
        price_trough_2 = price_lows_clean.iloc[-2]
        rsi_trough_1 = rsi_lows_clean.iloc[-1]
        rsi_trough_2 = rsi_lows_clean.iloc[-2]
        
        if price_trough_1 < price_trough_2 and rsi_trough_1 > rsi_trough_2:
            has_bullish = True
    
    # 确定背离类型
    if has_bearish:
        divergence_type = "BEARISH"
    elif has_bullish:
        divergence_type = "BULLISH"
    else:
        divergence_type = "NONE"
    
    return DivergenceData(
        rsi_value=rsi_value,
        has_bullish_divergence=has_bullish,
        has_bearish_divergence=has_bearish,
        divergence_type=divergence_type
    )


def calculate_all_indicators(
    symbol: str,
    ohlcv_data: List[List]
) -> IndicatorSummary:
    """
    计算单个代码的所有指标。
    
    Args:
        symbol: 交易对代码
        ohlcv_data: OHLCV 数据 (通常为 1h 周期)
        
    Returns:
        IndicatorSummary 包含所有计算出的指标
    """
    df = create_dataframe(ohlcv_data)
    
    if len(df) < 200:
        raise InsufficientDataError(symbol, required=200, received=len(df))
    
    current_price = df['close'].iloc[-1]
    
    # 计算所有指标
    vwap = calculate_vwap(df)
    trend = calculate_emas(df)
    bollinger = calculate_bollinger_bands(df)
    atr, atr_percent = calculate_atr(df)
    rsi, rsi_condition = calculate_rsi(df)
    divergence = detect_divergence(df)
    sr_levels = detect_support_resistance(df)
    
    return IndicatorSummary(
        symbol=symbol,
        current_price=current_price,
        vwap=vwap,
        price_vs_vwap="ABOVE" if current_price > vwap else "BELOW",
        trend=trend,
        bollinger=bollinger,
        atr=atr,
        atr_percent=atr_percent,
        rsi=rsi,
        rsi_condition=rsi_condition,
        divergence=divergence,
        support_resistance=sr_levels
    )


def format_indicator_summary(summary: IndicatorSummary) -> str:
    """
    为 AI 上下文格式化指标摘要。
    
    Args:
        summary: IndicatorSummary 对象
        
    Returns:
        用于提示词的格式化字符串
    """
    return f"""[ASSET: {summary.symbol}]
- Price: ${summary.current_price:,.2f} | VWAP: ${summary.vwap:,.2f} ({summary.price_vs_vwap})
- Trend: {summary.trend.trend_direction} ({summary.trend.trend_strength}) | EMA20: ${summary.trend.ema_20:,.2f}, EMA50: ${summary.trend.ema_50:,.2f}
- Structure: Support ${summary.support_resistance.nearest_support:,.2f} | Resistance ${summary.support_resistance.nearest_resistance:,.2f}
- Volatility: ATR ${summary.atr:,.2f} ({summary.atr_percent:.2f}%) | BBands {'SQUEEZE' if summary.bollinger.is_squeeze else 'Normal'}
- RSI: {summary.rsi:.1f} ({summary.rsi_condition}) | Divergence: {summary.divergence.divergence_type}"""


def format_ohlcv_for_prompt(ohlcv: list, timeframe: str, limit: int = 20) -> str:
    """
    格式化 K 线数据供 AI 上下文使用。
    
    输出最近 N 根 K 线的关键数据，使用表头格式节省 token。
    格式：表头行 + 数据行 (时间 | 收盘 | 成交量 | MA5 | MA60)
    
    Args:
        ohlcv: K 线数据列表 [[timestamp, open, high, low, close, volume], ...]
        timeframe: 时间周期标识 (1m, 15m, 1h, 1d)
        limit: 输出的 K 线数量
        
    Returns:
        格式化的 K 线字符串
    """
    if not ohlcv or len(ohlcv) < 5:
        return f"[{timeframe} K线] 数据不足"
    
    # 确保不超过实际数据量
    actual_limit = min(limit, len(ohlcv))
    
    # 计算简单移动平均 (使用模块级函数)
    closes = [c[4] for c in ohlcv]
    ma5 = calc_sma(closes, 5)
    ma60 = calc_sma(closes, 60) if len(closes) >= 60 else [None] * len(closes)
    
    # 标题行
    lines = [f"[{timeframe} K线 (最近{actual_limit}根)]"]
    
    # 表头行 (节省每行重复的前缀)
    lines.append("Time | Close | Vol | MA5 | MA60")
    
    # 选择时间格式
    if timeframe == '1d':
        time_fmt = '%m/%d'
    elif timeframe in ('1h', '15m'):
        time_fmt = '%m/%d %H:%M'
    else:
        time_fmt = '%H:%M'
    
    for i in range(-actual_limit, 0):
        candle = ohlcv[i]
        ts = format_time(from_timestamp(candle[0], in_milliseconds=True), time_fmt)
        close = candle[4]
        volume = candle[5]
        
        ma5_val = ma5[i] if ma5[i] else close
        ma60_val = ma60[i] if ma60[i] else close
        
        # 纯数据行，无前缀
        lines.append(f"{ts} | ${close:,.2f} | {volume:,.0f} | ${ma5_val:,.2f} | ${ma60_val:,.2f}")
    
    return "\n".join(lines)

