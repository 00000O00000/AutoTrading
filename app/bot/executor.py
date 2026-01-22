"""
交易执行器 - 处理交易执行逻辑。

将 AI 工具调用转换为实际的币安订单，
包含精度处理、安全检查和错误处理。
"""

import logging
from typing import Optional, Dict
from dataclasses import dataclass

from app.bot.binance_client import BinanceClient
from app.bot.exceptions import (
    OrderExecutionError,
    InsufficientBalanceError,
    PositionNotFoundError
)

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """交易执行结果。"""
    success: bool
    order_id: Optional[str] = None
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    executed_price: float = 0.0
    error: Optional[str] = None
    sl_failed: bool = False  # 止损设置失败标志
    tp_failed: bool = False  # 止盈设置失败标志


class TradeExecutor:
    """
    交易执行处理器。
    
    将 AI 决策转换为币安订单，包含：
    - USDT 转数量
    - 精度处理
    - 余额检查
    - 止损订单设置
    """
    
    # 安全限制
    MIN_TRADE_USDT = 10.0  # 最小交易金额
    
    def __init__(self, binance_client: BinanceClient):
        """
        初始化交易执行器。
        
        Args:
            binance_client: 已配置的 BinanceClient 实例
        """
        self.client = binance_client
    
    def open_position(
        self,
        symbol: str,
        side: str,
        amount_usdt: float,
        leverage: Optional[int] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None
    ) -> ExecutionResult:
        """
        开仓或加仓，支持可选的杠杆和止盈止损。
        
        Args:
            symbol: 交易对 (例如 'BTC/USDT')
            side: 'BUY' (做多) 或 'SELL' (做空)
            amount_usdt: 交易金额 (USDT)
            leverage: 杠杆倍数 (1-125), None 表示保持当前
            stop_loss_price: 可选止损触发价格
            take_profit_price: 可选止盈触发价格
            
        Returns:
            ExecutionResult 包含订单详情
        """
        side = side.upper()
        
        logger.info(
            "正在开仓: %s %s %.2f USDT (杠杆: %s, 止损: %s, 止盈: %s)",
            side, symbol, amount_usdt, 
            f"{leverage}x" if leverage else 'default',
            stop_loss_price or 'none',
            take_profit_price or 'none'
        )
        
        try:
            # 验证方向
            if side not in ('BUY', 'SELL'):
                raise ValueError(f"无效方向: {side}")
            
            # 如果指定了则设置杠杆
            if leverage and leverage > 0:
                try:
                    self.client.set_leverage(symbol, leverage)
                except Exception as e:
                    logger.warning("设置杠杆失败，使用当前杠杆: %s", e)
            
            # 检查余额
            balance = self.client.fetch_balance()
            free_balance = balance.get('free', 0)
            
            # 安全检查
            if amount_usdt < self.MIN_TRADE_USDT:
                raise ValueError(
                    f"交易金额 {amount_usdt} 低于最小限制 {self.MIN_TRADE_USDT}"
                )
            
            # 检查是否超过可用余额
            # 注意: 这里检查的是名义价值，实际保证金需要除以杠杆
            # 币安会在下单时进行最终验证，这里只是初步检查
            if amount_usdt > free_balance:
                raise InsufficientBalanceError(amount_usdt, free_balance)
            
            # 检查调整后的最小名义价值
            min_notional = self.client.get_min_notional(symbol)
            if amount_usdt < min_notional:
                raise ValueError(
                    f"金额 {amount_usdt:.2f} 低于最小名义价值 {min_notional}"
                )
            
            # 计算数量
            quantity = self.client.calculate_quantity(symbol, amount_usdt)
            
            if quantity <= 0:
                raise ValueError(f"{amount_usdt} USDT 计算出的数量为 0")
            
            # 执行市价单
            order = self.client.create_market_order(symbol, side, quantity)
            
            # 获取成交价
            executed_price = float(order.get('average', 0) or order.get('price', 0))
            
            # 止盈止損单的相反方向
            opposite_side = 'SELL' if side == 'BUY' else 'BUY'
            
            sl_failed = False
            tp_failed = False
            
            # 若指定则设置止损 (市价止损单)
            if stop_loss_price and stop_loss_price > 0:
                try:
                    self.client.create_stop_loss_order(
                        symbol, opposite_side, quantity, stop_loss_price
                    )
                    logger.info("止损单已设置 @ %.2f", stop_loss_price)
                except Exception as e:
                    logger.warning("无法设置止损: %s", e)
                    sl_failed = True
            
            # 若指定则设置止盈 (市价止盈单)
            if take_profit_price and take_profit_price > 0:
                try:
                    self.client.create_take_profit_order(
                        symbol, opposite_side, quantity, take_profit_price
                    )
                    logger.info("止盈单已设置 @ %.2f", take_profit_price)
                except Exception as e:
                    logger.warning("无法设置止盈: %s", e)
                    tp_failed = True
            
            logger.info(
                "仓位已开启: %s %s %.8f @ %.4f",
                side, symbol, quantity, executed_price
            )
            
            return ExecutionResult(
                success=True,
                order_id=order.get('id'),
                symbol=symbol,
                side=side,
                quantity=quantity,
                executed_price=executed_price,
                sl_failed=sl_failed,
                tp_failed=tp_failed
            )
            
        except (InsufficientBalanceError, ValueError) as e:
            logger.error("交易验证失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                side=side,
                error=str(e)
            )
        except Exception as e:
            logger.error("订单执行失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                side=side,
                error=str(e)
            )
    
    def close_position(
        self,
        symbol: str,
        percentage: int,
        reason: str = ""
    ) -> ExecutionResult:
        """
        平仓或减仓。
        
        Args:
            symbol: 交易对
            percentage: 平仓百分比 (1-100)
            reason: 平仓原因 (用于日志)
            
        Returns:
            ExecutionResult 包含订单详情
        """
        logger.info(
            "正在平仓: %s %d%% (原因: %s)",
            symbol, percentage, reason or 'none'
        )
        
        try:
            # 验证百分比
            if not 1 <= percentage <= 100:
                raise ValueError(f"无效百分比: {percentage}")
            
            # 获取当前仓位
            position = self.client.get_position_size(symbol)
            
            if position is None:
                raise PositionNotFoundError(symbol)
            
            current_contracts = position['contracts']
            position_side = position['side']  # 'LONG' or 'SHORT'
            
            if current_contracts <= 0:
                raise PositionNotFoundError(symbol)
            
            # 计算平仓数量
            close_quantity = current_contracts * (percentage / 100.0)
            
            # 截断到精度
            precision = self.client.get_precision(symbol)
            close_quantity = self.client.truncate_to_precision(
                close_quantity, precision['amount']
            )
            
            if close_quantity <= 0:
                raise ValueError("计算出的平仓数量为 0")
            
            # 确定平仓方向 (与持仓相反)
            close_side = 'SELL' if position_side == 'LONG' else 'BUY'
            
            # 执行平仓单
            order = self.client.create_market_order(symbol, close_side, close_quantity)
            
            executed_price = float(order.get('average', 0) or order.get('price', 0))
            
            # 全平仓时取消该币种现有的止损单 (在平仓成功后执行)
            # For partial closes, existing stop-loss will remain but may be oversized
            if percentage == 100:
                try:
                    self.client.cancel_all_orders(symbol)
                except Exception as e:
                    logger.warning("取消订单失败: %s", e)
            else:
                # Log warning about existing stop-loss orders
                logger.info(
                    "部分平仓 %d%% - 现有止损订单保持有效",
                    percentage
                )
            
            logger.info(
                "仓位已平仓: %s %s %.8f @ %.4f",
                close_side, symbol, close_quantity, executed_price
            )
            
            return ExecutionResult(
                success=True,
                order_id=order.get('id'),
                symbol=symbol,
                side=close_side,
                quantity=close_quantity,
                executed_price=executed_price
            )
            
        except PositionNotFoundError as e:
            logger.warning("无仓位可平: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
        except Exception as e:
            logger.error("平仓失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
    
    def set_leverage(self, symbol: str, leverage: int) -> ExecutionResult:
        """
        设置交易对杠杆。
        
        Args:
            symbol: 交易对
            leverage: 杠杆倍数 (1-125)
            
        Returns:
            ExecutionResult
        """
        logger.info("正在设置杠杆: %s -> %dx", symbol, leverage)
        
        try:
            self.client.set_leverage(symbol, leverage)
            return ExecutionResult(
                success=True,
                symbol=symbol,
                side=f"LEVERAGE_{leverage}x"
            )
        except Exception as e:
            logger.error("设置杠杆失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
    
    def set_margin_mode(self, symbol: str, mode: str) -> ExecutionResult:
        """
        设置交易对保证金模式。
        
        Args:
            symbol: 交易对
            mode: 'cross' (全仓) 或 'isolated' (逐仓)
            
        Returns:
            ExecutionResult
        """
        logger.info("正在设置保证金模式: %s -> %s", symbol, mode)
        
        try:
            self.client.set_margin_mode(symbol, mode)
            return ExecutionResult(
                success=True,
                symbol=symbol,
                side=f"MARGIN_{mode.upper()}"
            )
        except Exception as e:
            logger.error("设置保证金模式失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
    
    def modify_position_tpsl(
        self, 
        symbol: str,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None
    ) -> ExecutionResult:
        """
        修改现有仓位的止盈止损。
        
        取消现有止盈止损单并创建新单。
        
        Args:
            symbol: 交易对
            stop_loss_price: 新止损价 (None 表示不修改)
            take_profit_price: 新止盈价 (None 表示不修改)
            
        Returns:
            ExecutionResult
        """
        logger.info(
            "正在修改仓位止盈止损: %s (止损: %s, 止盈: %s)",
            symbol, stop_loss_price or 'unchanged', take_profit_price or 'unchanged'
        )
        
        try:
            # 获取当前仓位
            position = self.client.get_position_size(symbol)
            
            if position is None:
                raise PositionNotFoundError(symbol)
            
            quantity = position['contracts']
            position_side = position['side']  # 'LONG' or 'SHORT'
            opposite_side = 'SELL' if position_side == 'LONG' else 'BUY'
            
            # 取消现有止盈止损单
            if stop_loss_price:
                self.client.cancel_orders_by_type(symbol, 'stop_loss')
            if take_profit_price:
                self.client.cancel_orders_by_type(symbol, 'take_profit')
            
            # 创建新止损
            if stop_loss_price and stop_loss_price > 0:
                try:
                    self.client.create_stop_loss_order(
                        symbol, opposite_side, quantity, stop_loss_price
                    )
                    logger.info("新止损已设置 @ %.2f", stop_loss_price)
                except Exception as e:
                    logger.warning("设置止损失败: %s", e)
            
            # 创建新止盈
            if take_profit_price and take_profit_price > 0:
                try:
                    self.client.create_take_profit_order(
                        symbol, opposite_side, quantity, take_profit_price
                    )
                    logger.info("新止盈已设置 @ %.2f", take_profit_price)
                except Exception as e:
                    logger.warning("设置止盈失败: %s", e)
            
            return ExecutionResult(
                success=True,
                symbol=symbol,
                side="MODIFY_TPSL"
            )
            
        except PositionNotFoundError as e:
            logger.warning("无仓位可修改: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
        except Exception as e:
            logger.error("修改止盈止损失败: %s", e)
            return ExecutionResult(
                success=False,
                symbol=symbol,
                error=str(e)
            )
