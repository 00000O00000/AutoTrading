"""
OpenNOF1 Web 界面的 Flask 路由。

提供仪表板、API 端点。设置功能已整合到仪表板页面。
"""

import hmac
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from flask import Blueprint, render_template, jsonify, request, redirect

from app import db
from app.models import MemoryBoard, MarketSnapshot, TradeDecision, EquitySnapshot
from config import get_config
from app.bot.service import TradingService

logger = logging.getLogger(__name__)

# 主路由蓝图
main_bp = Blueprint('main', __name__)

# 服务实例 (由 run.py 设置)
_service: Optional[TradingService] = None


def _format_timestamp(dt: datetime) -> Optional[str]:
    """将 datetime 序列化为带时区的 ISO 字符串。
    
    假设数据库存储的 naive datetime 为 UTC 时间，
    转换到配置的时区后输出，确保浏览器能正确解析。
    """
    if dt is None:
        return None
    config = get_config()
    tz = timezone(timedelta(hours=config.TIMEZONE_OFFSET))
    
    # 如果是 naive datetime，假设为 UTC，然后转换到配置时区
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    # 转换到配置的时区
    local_dt = dt.astimezone(tz)
    return local_dt.isoformat()


def init_service(service: TradingService):
    """初始化交易服务引用。"""
    global _service
    _service = service


# =============================================================================
# PAGE ROUTES
# =============================================================================

@main_bp.route('/')
def dashboard():
    """仪表板页面。"""
    return render_template('dashboard.html')


@main_bp.route('/settings')
def settings():
    """设置页面 - 重定向到仪表板（设置已整合到右侧面板）。"""
    return redirect('/')


# =============================================================================
# API ROUTES
# =============================================================================

@main_bp.route('/api/status')
def api_status():
    """获取机器人状态。"""
    if not _service:
        return jsonify({'error': 'Service not initialized'}), 503
    
    config = get_config()
    status = _service.get_status()
    status['timezone_offset'] = config.TIMEZONE_OFFSET
    return jsonify(status)


@main_bp.route('/api/tickers')
def api_tickers():
    """获取当前行情数据（含 24h 迷你走势）。"""
    if not _service:
        return jsonify({'error': 'Service not initialized'}), 503
    
    try:
        tickers = []
        for symbol in _service.engine.data_engine.symbols:
            ticker = _service.engine.data_engine.binance.fetch_ticker(symbol)
            
            # 获取 24h K线数据作为 sparkline (1h 间隔, 24 根)
            try:
                ohlcv = _service.engine.data_engine.binance.fetch_ohlcv(symbol, '1h', 24)
                sparkline = [candle[4] for candle in ohlcv]  # 收盘价
            except Exception as e:
                logger.debug("获取 %s sparkline 失败: %s", symbol, e)
                sparkline = []
            
            tickers.append({
                'symbol': symbol,
                'price': ticker.last_price,
                'change_24h': ticker.change_24h_percent,
                'high': ticker.high_24h,
                'low': ticker.low_24h,
                'volume': ticker.volume_24h,
                'sparkline': sparkline
            })
        return jsonify(tickers)
    except Exception as e:
        logger.error("Failed to fetch tickers: %s", e)
        return jsonify({'error': str(e)}), 500


@main_bp.route('/api/alpha')
def api_alpha():
    """获取 Alpha 指标。"""
    if not _service:
        return jsonify({'error': 'Service not initialized'}), 503
    
    try:
        binance = _service.engine.data_engine.binance
        
        breadth = binance.fetch_top_gainers_losers(50)
        
        return jsonify({
            'advance_decline_ratio': breadth['advance_decline_ratio'],
            'top_gainers': breadth['gainers'][:3],
            'top_losers': breadth['losers'][:3]
        })
    except Exception as e:
        logger.error("Failed to fetch alpha: %s", e)
        return jsonify({'error': str(e)}), 500


@main_bp.route('/api/decisions')
def api_decisions():
    """获取近期交易决策（含工具调用详情）。"""
    try:
        decisions = TradeDecision.query.order_by(
            TradeDecision.timestamp.desc()
        ).limit(20).all()
        
        result = []
        for d in decisions:
            # 解析 tool_args
            try:
                args = json.loads(d.tool_args) if d.tool_args else {}
            except Exception:
                args = {}
            
            result.append({
                'id': d.id,
                'timestamp': _format_timestamp(d.timestamp),
                'symbol': d.symbol,
                'action': d.action,
                'info': d.display_info,
                'tool_name': d.tool_name,
                'args': args,
                'status': d.execution_status,
                'price': d.executed_price,
                'reasoning': d.ai_reasoning  # AI 分析文本
            })
        
        return jsonify(result)
    except Exception as e:
        logger.error("Failed to fetch decisions: %s", e)
        return jsonify({'error': str(e)}), 500


@main_bp.route('/api/positions')
def api_positions():
    """获取当前持仓。"""
    if not _service:
        return jsonify({'error': 'Service not initialized'}), 503
    
    try:
        positions = _service.engine.data_engine.binance.fetch_positions()
        return jsonify(positions)
    except Exception as e:
        logger.error("Failed to fetch positions: %s", e)
        return jsonify({'error': str(e)}), 500


@main_bp.route('/api/memory')
def api_memory():
    """获取当前记忆白板内容。"""
    try:
        board = MemoryBoard.get_or_create()
        return jsonify({
            'content': board.content,
            'last_updated': board.last_updated.isoformat() if board.last_updated else None
        })
    except Exception as e:
        logger.error("Failed to fetch memory: %s", e)
        return jsonify({'error': str(e)}), 500


# =============================================================================
# CONTROL ROUTES
# =============================================================================

@main_bp.route('/api/start', methods=['POST'])
def api_start():
    """启动交易循环。"""
    if not _service:
        return jsonify({'error': 'Service not initialized'}), 503
    
    try:
        _service.start()
        return jsonify({'success': True, 'message': 'Trading loop started'})
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 400


@main_bp.route('/api/stop', methods=['POST'])
def api_stop():
    """停止交易循环。"""
    if not _service:
        return jsonify({'error': 'Service not initialized'}), 503
    
    try:
        _service.stop()
        return jsonify({'success': True, 'message': 'Trading loop stopping...'})
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 400


@main_bp.route('/api/verify-password', methods=['POST'])
def api_verify_password():
    """验证控制台密码。"""
    config = get_config()
    data = request.get_json() or {}
    password = data.get('password', '')
    
    # 使用时序安全的密码比较防止时序攻击
    if hmac.compare_digest(password, config.CONSOLE_PASSWORD):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '密码错误'}), 401


@main_bp.route('/api/live', methods=['POST'])
def api_toggle_live():
    """切换实盘交易模式。"""
    if not _service:
        return jsonify({'error': 'Service not initialized'}), 503
    
    data = request.get_json() or {}
    enable = data.get('enable', False)
    
    _service.enable_live_trading(enable)
    
    return jsonify({
        'success': True, 
        'live_trading': _service.live_trading
    })


@main_bp.route('/api/instructions', methods=['GET'])
def api_get_instructions():
    """获取当前自定义交易指令。"""
    try:
        from app.models import SystemSettings
        settings = SystemSettings.get_or_create()
        return jsonify({
            'instructions': settings.custom_instructions or '',
            'last_updated': settings.last_updated.isoformat() if settings.last_updated else None
        })
    except Exception as e:
        logger.error("Failed to fetch instructions: %s", e)
        return jsonify({'error': str(e)}), 500


@main_bp.route('/api/instructions', methods=['POST'])
def api_instructions():
    """更新自定义交易指令。"""
    if not _service:
        return jsonify({'error': 'Service not initialized'}), 503
    
    data = request.get_json() or {}
    instructions = data.get('instructions', '')
    
    _service.set_custom_instructions(instructions)
    
    return jsonify({'success': True, 'message': 'Instructions updated'})



@main_bp.route('/api/run-once', methods=['POST'])
def api_run_once():
    """运行单个交易循环。"""
    if not _service:
        return jsonify({'error': 'Service not initialized'}), 503
    
    try:
        result = _service.run_once()
        return jsonify(result)
    except Exception as e:
        logger.error("Cycle failed: %s", e)
        return jsonify({'error': str(e)}), 500


@main_bp.route('/api/close-all', methods=['POST'])
def api_close_all_positions():
    """一键全平：平掉所有持仓并取消所有挂单。"""
    if not _service:
        return jsonify({'error': 'Service not initialized'}), 503
    
    try:
        binance = _service.engine.data_engine.binance
        results = {'closed': [], 'cancelled': [], 'errors': []}
        
        # 1. 获取所有持仓
        positions = binance.fetch_positions()
        
        # 2. 平掉每个持仓
        for pos in positions:
            symbol = pos['symbol']
            contracts = pos['contracts']
            side = pos['side']  # 'LONG' or 'SHORT'
            
            if contracts <= 0:
                continue
            
            try:
                # 先取消该交易对的所有挂单
                binance.cancel_all_orders(symbol)
                results['cancelled'].append(symbol)
                
                # 平仓（方向与持仓相反）
                close_side = 'SELL' if side == 'LONG' else 'BUY'
                order = binance.create_market_order(symbol, close_side, contracts)
                results['closed'].append({
                    'symbol': symbol,
                    'side': close_side,
                    'quantity': contracts,
                    'order_id': order.get('id')
                })
                logger.info("已平仓: %s %s %.4f", close_side, symbol, contracts)
            except Exception as e:
                error_msg = f"{symbol}: {str(e)}"
                results['errors'].append(error_msg)
                logger.error("平仓失败 %s: %s", symbol, e)
        
        success = len(results['errors']) == 0
        return jsonify({
            'success': success,
            'message': f"已平仓 {len(results['closed'])} 个持仓",
            'results': results
        })
    except Exception as e:
        logger.error("一键全平失败: %s", e)
        return jsonify({'error': str(e)}), 500


# =============================================================================
# ACCOUNT & EQUITY ROUTES
# =============================================================================

@main_bp.route('/api/account-summary')
def api_account_summary():
    """获取账户总览数据。"""
    if not _service:
        return jsonify({'error': 'Service not initialized'}), 503
    
    try:
        # 获取当前账户数据
        balance = _service.engine.data_engine.binance.fetch_balance()
        positions = _service.engine.data_engine.binance.fetch_positions()
        
        total_equity = balance.get('total', 0)
        free_balance = balance.get('free', 0)
        # 安全处理 unrealized_pnl 可能为 None 的情况
        unrealized_pnl = sum((p.get('unrealized_pnl') or 0) for p in positions) if positions else 0
        
        # 如果 total 不包含未实现盈亏
        if total_equity == free_balance and unrealized_pnl != 0:
            total_equity = free_balance + unrealized_pnl
        
        # 获取基准净值（第一个快照）
        first_snapshot = EquitySnapshot.get_first()
        base_equity = first_snapshot.total_equity if first_snapshot else total_equity
        
        # 计算总收益
        total_profit = total_equity - base_equity
        total_profit_pct = (total_profit / base_equity * 100) if base_equity > 0 else 0
        
        # 获取24小时前的净值
        snapshot_24h = EquitySnapshot.get_24h_ago()
        if snapshot_24h:
            profit_24h = total_equity - snapshot_24h.total_equity
            profit_24h_pct = (profit_24h / snapshot_24h.total_equity * 100) if snapshot_24h.total_equity > 0 else 0
        else:
            profit_24h = 0
            profit_24h_pct = 0
        
        return jsonify({
            'total_equity': total_equity,
            'free_balance': free_balance,
            'unrealized_pnl': unrealized_pnl,
            'position_count': len(positions) if positions else 0,
            'base_equity': base_equity,
            'total_profit': total_profit,
            'total_profit_pct': total_profit_pct,
            'profit_24h': profit_24h,
            'profit_24h_pct': profit_24h_pct
        })
    except Exception as e:
        logger.error("Failed to fetch account summary: %s", e)
        return jsonify({'error': str(e)}), 500


@main_bp.route('/api/equity-history')
def api_equity_history():
    """获取收益历史数据（用于曲线图）。"""
    try:
        limit = request.args.get('limit', 100, type=int)
        # 限制最大查询数量防止性能问题
        limit = min(limit, 1000)
        snapshots = EquitySnapshot.get_history(limit)
        
        # 获取基准净值
        first_snapshot = EquitySnapshot.get_first()
        base_equity = first_snapshot.total_equity if first_snapshot else 0
        
        data = []
        for s in snapshots:
            profit_pct = ((s.total_equity - base_equity) / base_equity * 100) if base_equity > 0 else 0
            data.append({
                'timestamp': s.timestamp.isoformat(),
                'equity': s.total_equity,
                'profit_pct': profit_pct,
                'unrealized_pnl': s.unrealized_pnl
            })
        
        return jsonify({
            'base_equity': base_equity,
            'data': data
        })
    except Exception as e:
        logger.error("Failed to fetch equity history: %s", e)
        return jsonify({'error': str(e)}), 500

