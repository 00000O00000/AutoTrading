"""
OpenNOF1 的 Flask 应用程序工厂。

创建并配置包含数据库集成的 Flask 应用。
"""

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# 数据库实例 - 跨模块共享
db = SQLAlchemy()


def create_app(config_object=None):
    """
    应用程序工厂。
    
    Args:
        config_object: 要使用的配置类。如果为 None，则自动检测。
        
    Returns:
        配置好的 Flask 应用程序。
    """
    app = Flask(__name__)
    
    # 加载配置
    if config_object is None:
        from config import get_config
        config_object = get_config()
    
    app.config.from_object(config_object)
    
    # 初始化数据库
    db.init_app(app)
    
    # 创建表
    with app.app_context():
        from app import models  # noqa: F401
        db.create_all()
    
    # 注册蓝图
    from app.routes import main_bp
    app.register_blueprint(main_bp)
    
    return app
