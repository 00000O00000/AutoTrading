"""
OpenNOF1 的自定义异常。

遵循修复法则 (Repair Rule)：当必须失败时，要尽早且大声地失败。
每个异常都清楚地表明失败点和上下文。
"""


class OpenNOF1Error(Exception):
    """OpenNOF1 的基类异常。"""
    pass


class DataFetchError(OpenNOF1Error):
    """当无法从外部源获取数据时引发。"""
    
    def __init__(self, source: str, symbol: str = None, reason: str = None):
        self.source = source
        self.symbol = symbol
        self.reason = reason
        
        msg = f"Failed to fetch data from {source}"
        if symbol:
            msg += f" for {symbol}"
        if reason:
            msg += f": {reason}"
        
        super().__init__(msg)


class InsufficientDataError(OpenNOF1Error):
    """当数据不足以进行计算时引发。"""
    
    def __init__(self, symbol: str, required: int, received: int):
        self.symbol = symbol
        self.required = required
        self.received = received
        
        super().__init__(
            f"Insufficient data for {symbol}: "
            f"need {required} candles, got {received}"
        )


class AuthenticationError(OpenNOF1Error):
    """当 API 凭证丢失或无效时引发。"""
    
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        super().__init__(
            f"API credentials required for {endpoint}. "
            f"Set BINANCE_API_KEY and BINANCE_API_SECRET in .env"
        )


class ConfigurationError(OpenNOF1Error):
    """当配置无效或丢失时引发。"""
    
    def __init__(self, key: str, reason: str = None):
        self.key = key
        msg = f"Invalid configuration for {key}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class OrderExecutionError(OpenNOF1Error):
    """当订单执行失败时引发。"""
    
    def __init__(self, symbol: str, side: str, reason: str):
        self.symbol = symbol
        self.side = side
        self.reason = reason
        super().__init__(
            f"Failed to execute {side} order for {symbol}: {reason}"
        )


class InsufficientBalanceError(OpenNOF1Error):
    """当余额不足以进行交易时引发。"""
    
    def __init__(self, required: float, available: float):
        self.required = required
        self.available = available
        super().__init__(
            f"Insufficient balance: need {required:.2f} USDT, "
            f"have {available:.2f} USDT"
        )


class PositionNotFoundError(OpenNOF1Error):
    """当尝试平仓不存在的仓位时引发。"""
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        super().__init__(f"No open position found for {symbol}")
