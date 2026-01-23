"""
OpenNOF1 - 启动脚本

启动带有已初始化交易服务的 Flask 应用程序。
"""

import logging
import os

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from app import create_app
from app.routes import init_service
from app.bot.engine import TradingEngine
from app.bot.service import TradingService
from config import get_config


def main():
    """主入口点。"""
    config = get_config()
    
    # 创建 Flask 应用
    app = create_app(config)
    
    # 初始化交易引擎
    engine = TradingEngine(
        binance_api_key=config.BINANCE_API_KEY,
        binance_api_secret=config.BINANCE_API_SECRET,
        ai_api_key=config.AI_1_API_KEY,
        live_trading=True  # 默认以实盘交易模式启动
    )
    
    # 初始化交易服务
    service = TradingService(engine)
    
    # 将服务传递给路由
    init_service(service)
    
    # 从环境变量获取端口或使用默认值
    port = int(os.environ.get('PORT', 5000))
    
    print(f"""
╔════════════════════════════════════════╗
║                     OPENNOF1                           ║
║              Autonomous Trading Bot                    ║
╠════════════════════════════════════════╣
║  仪表板:   http://localhost:{port}                    ║
║  设置:    http://localhost:{port}/settings            ║
╠════════════════════════════════════════╣
║  模式:    实盘交易 (真实订单)                          ║
║  提示:    可在设置页面切换到模拟模式                   ║
╚════════════════════════════════════════╝
    """)
    
    # 运行 Flask 应用
    # IMPORTANT: use_reloader=False 防止 DEBUG 模式下服务被初始化两次
    app.run(
        host='0.0.0.0',
        port=port,
        debug=config.DEBUG,
        threaded=True,
        use_reloader=False  # 禁用 reloader，避免交易服务重复初始化
    )


if __name__ == '__main__':
    main()
