"""
时区工具模块。

提供全局时区转换和格式化函数，基于配置的 TIMEZONE_OFFSET。

设计原则：
- 数据库存储：始终使用 UTC 时间
- 显示输出：转换到配置的时区
"""

from datetime import datetime, timezone, timedelta
from config import get_config


def get_timezone():
    """获取配置的时区对象。"""
    config = get_config()
    offset = config.TIMEZONE_OFFSET
    return timezone(timedelta(hours=offset))


def utc_now() -> datetime:
    """获取当前 UTC 时间（用于数据库存储）。
    
    Returns:
        带 UTC 时区信息的 datetime 对象
    """
    return datetime.now(timezone.utc)


def now_with_tz() -> datetime:
    """获取配置时区的当前时间（用于显示）。
    
    Returns:
        带配置时区信息的 datetime 对象
    """
    return datetime.now(get_timezone())


def format_time(dt: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """
    将 datetime 格式化为配置时区的字符串（用于显示）。
    
    Args:
        dt: datetime 对象（如果是 naive，假设为 UTC）
        fmt: 格式字符串
        
    Returns:
        格式化后的时间字符串
    """
    tz = get_timezone()
    
    # 如果是 naive datetime，假设为 UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    # 转换到配置的时区
    local_dt = dt.astimezone(tz)
    return local_dt.strftime(fmt)


def from_timestamp(ts: int, in_milliseconds: bool = False) -> datetime:
    """
    从 Unix 时间戳创建 UTC datetime（用于存储）。
    
    Args:
        ts: Unix 时间戳
        in_milliseconds: 如果为 True，时间戳单位是毫秒
        
    Returns:
        带 UTC 时区的 datetime 对象
    """
    if in_milliseconds:
        ts = ts / 1000
    
    return datetime.fromtimestamp(ts, tz=timezone.utc)

