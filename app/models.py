"""
OpenNOF1 的数据库模型。

定义记忆、快照、交易决策和账户净值历史的数据结构。
"""

from datetime import datetime, timedelta
from app import db


class MemoryBoard(db.Model):
    """
    AI 记忆白板 - 单行记录，总是更新。
    
    这是"无限白板"，赋予 AI 跨越交易周期的持续市场认知。
    """
    __tablename__ = 'memory_board'
    
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False, default='')
    last_updated = db.Column(
        db.DateTime, 
        default=datetime.utcnow, 
        onupdate=datetime.utcnow
    )
    
    @classmethod
    def get_or_create(cls):
        """获取单例记忆白板，如果需要则创建。"""
        board = cls.query.first()
        if board is None:
            board = cls(content='')
            db.session.add(board)
            db.session.commit()
        return board
    
    def update(self, content: str):
        """更新白板内容。"""
        self.content = content
        self.last_updated = datetime.utcnow()
        db.session.commit()
    
    def __repr__(self):
        return f'<MemoryBoard updated={self.last_updated}>'


class SystemSettings(db.Model):
    """
    系统设置 - 单行记录，存储持久化配置。
    
    包括自定义交易指令等需要跨重启保留的设置。
    """
    __tablename__ = 'system_settings'
    
    id = db.Column(db.Integer, primary_key=True)
    custom_instructions = db.Column(db.Text, nullable=False, default='')
    last_updated = db.Column(
        db.DateTime, 
        default=datetime.utcnow, 
        onupdate=datetime.utcnow
    )
    
    @classmethod
    def get_or_create(cls):
        """获取单例设置，如果需要则创建。"""
        settings = cls.query.first()
        if settings is None:
            settings = cls(custom_instructions='')
            db.session.add(settings)
            db.session.commit()
        return settings
    
    def update_instructions(self, instructions: str):
        """更新自定义指令。"""
        self.custom_instructions = instructions
        self.last_updated = datetime.utcnow()
        db.session.commit()
    
    def __repr__(self):
        return f'<SystemSettings updated={self.last_updated}>'


class MarketSnapshot(db.Model):
    """
    记录每个 AI 决策点的关键指标。
    
    用于回测和 AI 决策分析。
    """
    __tablename__ = 'market_snapshot'
    
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # 市场宽度
    advance_decline_ratio = db.Column(db.Float)
    
    # BTC 统治力代理
    btc_dominance = db.Column(db.Float)
    
    # 所有技术指标 (序列化的 JSON)
    indicators_data = db.Column(db.Text)
    
    # 相关交易决策
    decisions = db.relationship('TradeDecision', backref='snapshot', lazy='dynamic')
    
    def __repr__(self):
        return f'<MarketSnapshot {self.timestamp} A/D={self.advance_decline_ratio}>'


class TradeDecision(db.Model):
    """
    记录 AI 做出的每个交易决策。
    
    存储采取的行动和推理。
    """
    __tablename__ = 'trade_decision'
    
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # 交易详情
    symbol = db.Column(db.String(20), nullable=False)
    action = db.Column(db.String(20), nullable=False)  # LONG, SHORT, CLOSE, HOLD, MEMORY, etc.
    
    # 前端显示信息
    display_info = db.Column(db.String(255))
    
    # 工具调用详情
    tool_name = db.Column(db.String(50))  # update_memory, trade_in, close_position
    tool_args = db.Column(db.Text)  # JSON 格式的参数
    
    # 完整的 AI 推理 (思维链)
    ai_reasoning = db.Column(db.Text)
    
    # 链接到市场快照
    snapshot_id = db.Column(
        db.Integer, 
        db.ForeignKey('market_snapshot.id'),
        nullable=True
    )
    
    # Execution details
    order_id = db.Column(db.String(50))
    executed_price = db.Column(db.Float)
    executed_quantity = db.Column(db.Float)
    execution_status = db.Column(db.String(20))  # SUCCESS, FAILED, PENDING
    
    def __repr__(self):
        return f'<TradeDecision {self.symbol} {self.action} @ {self.timestamp}>'


class EquitySnapshot(db.Model):
    """
    账户净值快照 - 用于绘制收益曲线。
    
    每次交易循环结束后记录账户状态。
    """
    __tablename__ = 'equity_snapshot'
    
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # 账户数据
    total_equity = db.Column(db.Float, nullable=False)  # 总净值 (余额 + 未实现盈亏)
    free_balance = db.Column(db.Float, nullable=False)  # 可用余额
    unrealized_pnl = db.Column(db.Float, default=0)     # 未实现盈亏
    
    # 持仓数量
    position_count = db.Column(db.Integer, default=0)
    
    @classmethod
    def get_latest(cls):
        """获取最新的净值快照。"""
        return cls.query.order_by(cls.timestamp.desc()).first()
    
    @classmethod
    def get_first(cls):
        """获取最早的净值快照（基准线）。"""
        return cls.query.order_by(cls.timestamp.asc()).first()
    
    @classmethod
    def get_history(cls, limit: int = 100):
        """获取净值历史记录（按时间升序）。"""
        # 先降序取最近 N 条，再反转为升序（简单且兼容所有数据库）
        records = cls.query.order_by(cls.timestamp.desc()).limit(limit).all()
        return records[::-1]
    
    @classmethod
    def get_24h_ago(cls):
        """获取24小时前的净值快照。"""
        target_time = datetime.utcnow() - timedelta(hours=24)
        return cls.query.filter(cls.timestamp <= target_time).order_by(cls.timestamp.desc()).first()
    
    def __repr__(self):
        return f'<EquitySnapshot {self.timestamp} equity={self.total_equity}>'


class PendingOrder(db.Model):
    """
    挂单/条件委托单追踪。
    
    记录止损/止盈等条件委托单，使 AI 能够：
    - 查看当前所有挂单
    - 通过 ID 精确取消单个订单
    
    设计原则 (Unix 哲学):
    - 透明法则: 让挂单状态可见
    - 表示法则: 将订单知识折叠进数据
    """
    __tablename__ = 'pending_order'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # 订单标识
    symbol = db.Column(db.String(20), nullable=False, index=True)
    order_id = db.Column(db.String(50), nullable=False)  # orderId 或 algoId
    
    # 订单类型
    order_type = db.Column(db.String(30))  # STOP_MARKET, TAKE_PROFIT_MARKET
    side = db.Column(db.String(10))  # BUY, SELL
    
    # 订单详情
    quantity = db.Column(db.Float)
    trigger_price = db.Column(db.Float)
    
    # 是否为算法订单 (止损/止盈使用 algoId)
    is_algo = db.Column(db.Boolean, default=True)
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # 状态: NEW, CANCELLED, FILLED, EXPIRED
    status = db.Column(db.String(20), default='NEW')
    
    @classmethod
    def add_order(cls, symbol: str, order_id: str, order_type: str, 
                  side: str, quantity: float, trigger_price: float,
                  is_algo: bool = True):
        """添加新的挂单记录。"""
        order = cls(
            symbol=symbol,
            order_id=order_id,
            order_type=order_type,
            side=side,
            quantity=quantity,
            trigger_price=trigger_price,
            is_algo=is_algo
        )
        db.session.add(order)
        db.session.commit()
        return order
    
    @classmethod
    def get_open_orders(cls, symbol: str = None):
        """获取所有状态为 NEW 的挂单。"""
        query = cls.query.filter_by(status='NEW')
        if symbol:
            query = query.filter_by(symbol=symbol)
        return query.order_by(cls.created_at.desc()).all()
    
    @classmethod
    def mark_cancelled(cls, order_id: str):
        """标记订单为已取消。"""
        order = cls.query.filter_by(order_id=order_id).first()
        if order:
            order.status = 'CANCELLED'
            db.session.commit()
        return order
    
    @classmethod
    def cleanup_old_orders(cls, hours: int = 24):
        """清理超过指定小时数的旧订单记录。"""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        cls.query.filter(
            cls.created_at < cutoff,
            cls.status != 'NEW'
        ).delete()
        db.session.commit()
    
    def __repr__(self):
        return f'<PendingOrder {self.symbol} {self.order_type} ID={self.order_id}>'

