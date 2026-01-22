"""
OpenNOF1 的配置管理。

从环境变量加载设置，并提供合理的默认值。
遵循 12-Factor App 方法论。
"""

import os
from dotenv import load_dotenv

# 加载 .env 文件 (如果存在)
load_dotenv()


class Config:
    """基础配置。"""
    
    # Flask 配置
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-prod')
    DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    
    # 数据库 - 开发环境使用 SQLite 回退
    DATABASE_URL = os.getenv('DATABASE_URL', '')
    if DATABASE_URL:
        SQLALCHEMY_DATABASE_URI = DATABASE_URL
    else:
        # SQLite 回退
        SQLALCHEMY_DATABASE_URI = 'sqlite:///opennof1.db'
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # 币安 API
    BINANCE_API_KEY = os.getenv('BINANCE_API_KEY', '')
    BINANCE_API_SECRET = os.getenv('BINANCE_API_SECRET', '')
    
    # DeepSeek API
    DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', '')
    DEEPSEEK_BASE_URL = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
    DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
    
    # 交易配置
    TRADING_SYMBOLS = os.getenv(
        'TRADING_SYMBOLS', 
        'BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,DOGE/USDT'
    ).split(',')
    
    TRADING_INTERVAL_MINUTES = int(os.getenv('TRADING_INTERVAL_MINUTES', '3'))
    
    # 要获取的 OHLCV 时间周期
    TIMEFRAMES = ['1m', '15m', '1h', '4h', '1d']
    
    # 每个时间周期获取的 K 线数量
    CANDLE_LIMIT = 300
    
    # 控制台密码 (用于设置页)
    CONSOLE_PASSWORD = os.getenv('CONSOLE_PASSWORD', 'admin')
    
    # 时区设置 (格式: "+8", "-5" 等，范围 +14 至 -12)
    _tz_str = os.getenv('TIMEZONE', '+8')
    try:
        TIMEZONE_OFFSET = int(_tz_str.replace('+', ''))
        if not -12 <= TIMEZONE_OFFSET <= 14:
            TIMEZONE_OFFSET = 8  # 默认 UTC+8
    except ValueError:
        TIMEZONE_OFFSET = 8  # 解析失败使用默认值


class DevelopmentConfig(Config):
    """开发环境配置。"""
    DEBUG = True


class ProductionConfig(Config):
    """生产环境配置。"""
    DEBUG = False


# 配置选择器
config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
}

def get_config():
    """根据环境获取配置。"""
    env = os.getenv('FLASK_ENV', 'development')
    return config_map.get(env, DevelopmentConfig)
