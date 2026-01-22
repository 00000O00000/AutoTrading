"""
宏观数据源模块。

获取外部数据：市场宽度等宏观指标。
"""

import logging

logger = logging.getLogger(__name__)


class MacroDataClient:
    """
    用于获取宏观市场数据的客户端。
    
    提供市场宽度等宏观分析指标。
    """
    
    def __init__(self, timeout: int = 10):
        """
        初始化宏观数据客户端。
        
        Args:
            timeout: 请求超时时间（秒）
        """
        self.timeout = timeout
    
    def format_macro_summary(self, advance_decline_ratio: float) -> str:
        """
        将宏观数据格式化为人类可读的 AI 上下文摘要。
        
        Args:
            advance_decline_ratio: 市场宽度指标
            
        Returns:
            格式化的摘要字符串
        """
        # 格式化 A/D 比率
        if advance_decline_ratio > 1.5:
            ad_assessment = "强劲 (广泛反弹)"
        elif advance_decline_ratio > 1.0:
            ad_assessment = "健康"
        elif advance_decline_ratio > 0.5:
            ad_assessment = "疲软 (BTC 主导)"
        else:
            ad_assessment = "非常疲软 (市场低迷)"
        
        # 处理 float('inf') 情况
        if advance_decline_ratio >= 9999:
            ad_display = "9999+"
        else:
            ad_display = f"{advance_decline_ratio:.2f}"
        
        return f"""全球市场上下文:
- 市场宽度 (A/D 比率): {ad_display} - {ad_assessment}"""