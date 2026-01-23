"""
交易引擎 - 交易循环的主协调器。

集成数据引擎、AI 代理、执行器并执行工具调用。
"""

import logging
import json
from datetime import datetime
from typing import Optional, List

from app import db
from app.models import MemoryBoard, MarketSnapshot, TradeDecision, EquitySnapshot, SystemSettings
from app.bot.data_engine import DataEngine, MarketContext
from app.bot.ai_agent import AIAgent, AIResponse
from app.bot.tz_utils import utc_now, now_with_tz
from app.bot.executor import TradeExecutor, ExecutionResult
from app.bot.xml_parser import ToolCall

logger = logging.getLogger(__name__)


class TradingEngine:
    """
    主交易循环协调器。
    
    协调数据收集、AI 分析和行动执行。
    """
    
    def __init__(
        self,
        binance_api_key: str = '',
        binance_api_secret: str = '',
        ai_api_key: str = '',
        live_trading: bool = True
    ):
        """
        初始化交易引擎。
        
        Args:
            binance_api_key: 币安 API Key
            binance_api_secret: 币安 API Secret
            ai_api_key: AI 提供商 API Key
            live_trading: 启用实盘交易 (False = 模拟交易)
        """
        self.data_engine = DataEngine(binance_api_key, binance_api_secret)
        self.ai_agent = AIAgent(api_key=ai_api_key)
        self.executor = TradeExecutor(self.data_engine.binance)
        
        self.live_trading = live_trading
        if not live_trading:
            logger.warning("纸面交易模式 - 订单将不会被执行")
    
    def set_custom_instructions(self, instructions: str):
        """设置自定义交易指令 (持久化到数据库)。"""
        try:
            settings = SystemSettings.get_or_create()
            settings.update_instructions(instructions)
            logger.info("自定义指令已保存到数据库")
        except Exception as e:
            logger.error("无法保存自定义指令: %s", e)
    
    def _get_custom_instructions(self) -> str:
        """从数据库获取自定义交易指令。"""
        try:
            settings = SystemSettings.get_or_create()
            return settings.custom_instructions or ''
        except Exception as e:
            logger.warning("无法读取自定义指令: %s", e)
            return ''
    
    def enable_live_trading(self, enable: bool = True):
        """启用或禁用实盘交易模式。"""
        self.live_trading = enable
        if enable:
            logger.warning("实盘交易已启用 - 将执行真实订单！")
        else:
            logger.info("纸面交易模式已启用")
    
    def _get_memory_content(self) -> str:
        """获取当前记忆白板内容。"""
        try:
            board = MemoryBoard.get_or_create()
            return board.content
        except Exception as e:
            logger.warning("无法读取记忆: %s", e)
            return ""
    
    def _save_memory_content(self, content: str) -> bool:
        """保存更新后的记忆内容。返回是否成功。"""
        try:
            board = MemoryBoard.get_or_create()
            board.update(content)
            logger.info("记忆白板已更新")
            return True
        except Exception as e:
            logger.error("无法保存记忆: %s", e)
            return False
    
    def _save_snapshot(self, context: MarketContext) -> Optional[MarketSnapshot]:
        """保存市场快照到数据库。"""
        try:
            snapshot = MarketSnapshot(
                timestamp=context.timestamp,
                advance_decline_ratio=context.advance_decline_ratio,
                indicators_data=json.dumps(self.data_engine.to_dict(context))
            )
            db.session.add(snapshot)
            db.session.commit()
            return snapshot
        except Exception as e:
            logger.error("无法保存快照: %s", e)
            return None
    
    def _save_equity_snapshot(self, context: MarketContext):
        """保存账户净值快照用于收益曲线。"""
        try:
            # 计算总净值
            total_equity = 0.0
            free_balance = 0.0
            unrealized_pnl = 0.0
            position_count = 0
            
            if context.account_balance:
                free_balance = context.account_balance.get('free', 0)
                total_equity = context.account_balance.get('total', 0)
            
            if context.positions:
                position_count = len(context.positions)
                # 安全处理 unrealized_pnl 可能为 None 的情况
                unrealized_pnl = sum(
                    (p.get('unrealized_pnl') or 0) for p in context.positions
                )
                # 总净值 = 可用余额 + 未实现盈亏 (如果 total 不包含 unrealized)
                if total_equity == free_balance:
                    total_equity = free_balance + unrealized_pnl
            
            # 只有在有有效数据时才保存
            if total_equity > 0:
                snapshot = EquitySnapshot(
                    total_equity=total_equity,
                    free_balance=free_balance,
                    unrealized_pnl=unrealized_pnl,
                    position_count=position_count
                )
                db.session.add(snapshot)
                db.session.commit()
                logger.info("净值快照已保存: $%.2f", total_equity)
        except Exception as e:
            logger.warning("无法保存净值快照: %s", e)
    
    # 工具动作映射 (表示法则: 将知识折叠进数据)
    TOOL_ACTION_MAP = {
        "trade_in": lambda args: (args.get('side', 'LONG'), args.get('target', 'UNKNOWN')),
        "close_position": lambda args: ("CLOSE", args.get('target', 'UNKNOWN')),
        "update_memory": lambda args: ("MEMORY", "SYSTEM"),
        "set_leverage": lambda args: ("LEVERAGE", args.get('target', 'UNKNOWN')),
        "set_margin_mode": lambda args: ("MARGIN", args.get('target', 'UNKNOWN')),
        "modify_position": lambda args: ("MODIFY", args.get('target', 'UNKNOWN')),
        "cancel_orders": lambda args: ("CANCEL", args.get('target', 'UNKNOWN')),
        "cancel_order": lambda args: ("CANCEL_ID", args.get('target', 'UNKNOWN')),
    }
    
    def _save_decision(
        self,
        tool_call: ToolCall,
        ai_reasoning: str,
        snapshot: Optional[MarketSnapshot],
        execution_result: Optional[ExecutionResult] = None,
        success: bool = True
    ) -> Optional[TradeDecision]:
        """保存工具调用到数据库（包括所有类型的工具）。"""
        try:
            # 使用映射表确定行动类型和交易对 (扩展性法则: 新增工具只需修改映射表)
            mapper = self.TOOL_ACTION_MAP.get(tool_call.name)
            if mapper:
                action, symbol = mapper(tool_call.args)
            else:
                action, symbol = tool_call.name.upper(), "UNKNOWN"
            
            # 确定执行状态
            if execution_result:
                status = "SUCCESS" if execution_result.success else "FAILED"
            else:
                # 没有 execution_result 时，使用传入的 success 参数
                status = "SUCCESS" if success else "FAILED"
            
            decision = TradeDecision(
                timestamp=utc_now(),
                symbol=symbol,
                action=action,
                display_info=tool_call.info,
                tool_name=tool_call.name,
                tool_args=json.dumps(tool_call.args),
                ai_reasoning=ai_reasoning,
                snapshot_id=snapshot.id if snapshot else None,
                execution_status=status,
                order_id=execution_result.order_id if execution_result else None,
                executed_price=execution_result.executed_price if execution_result else None,
                executed_quantity=execution_result.quantity if execution_result else None
            )
            db.session.add(decision)
            db.session.commit()
            return decision
        except Exception as e:
            logger.error("无法保存决策: %s", e)
            return None
    
    def _execute_tool(self, tool_call: ToolCall) -> tuple:
        """
        执行单个工具调用。
        
        Args:
            tool_call: 解析后的工具调用
            
        Returns:
            Tuple of (success: bool, execution_result: Optional[ExecutionResult])
        """
        logger.info("执行工具: %s", tool_call.name)
        
        try:
            if tool_call.name == "update_memory":
                content = tool_call.args.get('content', '')
                success = self._save_memory_content(content)
                return success, None
            
            elif tool_call.name == "trade_in":
                symbol = tool_call.args.get('target', '')
                side = tool_call.args.get('side', 'LONG')
                amount_usdt = float(tool_call.args.get('count_usdt', 0))
                
                # 新增参数
                leverage_str = tool_call.args.get('leverage')
                leverage = int(leverage_str) if leverage_str else None
                
                stop_loss = tool_call.args.get('stop_loss_price')
                stop_loss_price = float(stop_loss) if stop_loss else None
                
                take_profit = tool_call.args.get('take_profit_price')
                take_profit_price = float(take_profit) if take_profit else None
                
                if self.live_trading:
                    result = self.executor.open_position(
                        symbol=symbol,
                        side=side,
                        amount_usdt=amount_usdt,
                        leverage=leverage,
                        stop_loss_price=stop_loss_price,
                        take_profit_price=take_profit_price
                    )
                    return result.success, result
                else:
                    logger.info(
                        "[模拟] TRADE_IN: %s %s, 金额=%.2f USDT, 杠杆=%s, 止损=%s, 止盈=%s",
                        side, symbol, amount_usdt,
                        f"{leverage}x" if leverage else 'default',
                        stop_loss_price or 'none',
                        take_profit_price or 'none'
                    )
                    return True, None
            
            elif tool_call.name == "close_position":
                symbol = tool_call.args.get('target', '')
                # xml_parser 已经验证并规范化 percentage 为字符串数字
                percentage = int(tool_call.args.get('percentage', '100'))
                reason = tool_call.args.get('reason', '')
                
                if self.live_trading:
                    result = self.executor.close_position(
                        symbol=symbol,
                        percentage=percentage,
                        reason=reason
                    )
                    return result.success, result
                else:
                    logger.info(
                        "[模拟] CLOSE_POSITION: %s %d%%, 原因=%s",
                        symbol, percentage, reason
                    )
                    return True, None
            
            elif tool_call.name == "set_leverage":
                symbol = tool_call.args.get('target', '')
                leverage = int(tool_call.args.get('leverage', 1))
                
                if self.live_trading:
                    result = self.executor.set_leverage(symbol, leverage)
                    return result.success, result
                else:
                    logger.info("[模拟] SET_LEVERAGE: %s -> %dx", symbol, leverage)
                    return True, None
            
            elif tool_call.name == "set_margin_mode":
                symbol = tool_call.args.get('target', '')
                mode = tool_call.args.get('mode', 'cross')
                
                if self.live_trading:
                    result = self.executor.set_margin_mode(symbol, mode)
                    return result.success, result
                else:
                    logger.info("[模拟] SET_MARGIN_MODE: %s -> %s", symbol, mode)
                    return True, None
            
            elif tool_call.name == "modify_position":
                symbol = tool_call.args.get('target', '')
                stop_loss = tool_call.args.get('stop_loss_price')
                stop_loss_price = float(stop_loss) if stop_loss else None
                take_profit = tool_call.args.get('take_profit_price')
                take_profit_price = float(take_profit) if take_profit else None
                
                if self.live_trading:
                    result = self.executor.modify_position_tpsl(
                        symbol, stop_loss_price, take_profit_price
                    )
                    return result.success, result
                else:
                    logger.info(
                        "[模拟] MODIFY_POSITION: %s, 止损=%s, 止盈=%s",
                        symbol, stop_loss_price or 'unchanged', take_profit_price or 'unchanged'
                    )
                    return True, None
            
            elif tool_call.name == "cancel_orders":
                symbol = tool_call.args.get('target', '')
                order_type = tool_call.args.get('order_type', 'all')
                
                if self.live_trading:
                    result = self.executor.cancel_orders(symbol, order_type)
                    return result.success, result
                else:
                    logger.info("[模拟] CANCEL_ORDERS: %s (%s)", symbol, order_type)
                    return True, None
            
            elif tool_call.name == "cancel_order":
                symbol = tool_call.args.get('target', '')
                order_id = tool_call.args.get('order_id', '')
                
                if self.live_trading:
                    result = self.executor.cancel_order_by_id(symbol, order_id)
                    return result.success, result
                else:
                    logger.info("[模拟] CANCEL_ORDER: %s, order_id=%s", symbol, order_id)
                    return True, None
            
            else:
                logger.warning("未知工具: %s", tool_call.name)
                return False, None
                
        except Exception as e:
            logger.exception("工具执行失败 [%s]: %s", tool_call.name, e)
            return False, None
    
    def run_cycle(self) -> dict:
        """
        运行单个交易循环。
        
        这是交易循环的主要入口点。
        包含 AI 决策容错机制：当工具执行失败时，将错误反馈给 AI 并重试。
        
        Returns:
            包含循环结果的字典
        """
        cycle_start = now_with_tz()
        logger.info("=== 开始交易循环于 %s ===", cycle_start)
        
        result = {
            "timestamp": cycle_start.isoformat(),
            "success": False,
            "error": None,
            "actions": [],
            "memory_updated": False,
            "tokens_used": 0,
            "live_trading": self.live_trading,
            "retry_count": 0
        }
        
        # 最大重试次数
        MAX_RETRIES = 3
        
        try:
            # 第一步: 获取记忆内容
            memory_content = self._get_memory_content()
            logger.info("已加载记忆 (%d 字符)", len(memory_content))
            
            # 第二步: 聚合市场数据
            context = self.data_engine.aggregate(memory_content)
            logger.info("已聚合 %d 个资产的数据", len(context.assets))
            
            # 第三步: 构建提示词上下文
            prompt_context = self.data_engine.build_prompt_context(context)
            
            # 第四步: 保存快照 (提前保存，用于记录所有决策)
            snapshot = self._save_snapshot(context)
            
            # 第五步: 构建初始消息历史
            from app.bot.prompts import SYSTEM_PROMPT, build_user_prompt
            system_prompt = SYSTEM_PROMPT.format(
                interval=self.data_engine.config.TRADING_INTERVAL_MINUTES
            )
            user_prompt = build_user_prompt(prompt_context, self._get_custom_instructions())
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            # 第六步: AI 决策与执行循环 (带重试)
            retry_count = 0
            while retry_count <= MAX_RETRIES:
                # 获取 AI 分析
                if retry_count == 0:
                    ai_response = self.ai_agent.analyze(
                        market_context=prompt_context,
                        custom_instructions=self._get_custom_instructions()
                    )
                else:
                    # 使用带消息历史的分析
                    ai_response = self.ai_agent.analyze_with_messages(messages)
                
                result["tokens_used"] += ai_response.usage.get("total_tokens", 0)
                logger.info("AI 分析完成 (%d tokens, 重试 #%d)", 
                           ai_response.usage.get("total_tokens", 0), retry_count)
                
                # 将 AI 回复加入消息历史
                messages.append({"role": "assistant", "content": ai_response.raw_response})
                
                if not ai_response.tool_calls:
                    logger.info("AI 未返回工具调用，循环结束")
                    break
                
                # 执行工具调用并收集错误
                all_success = True
                error_messages = []
                
                for tool_call in ai_response.tool_calls:
                    success, execution_result = self._execute_tool(tool_call)
                    
                    if tool_call.name == "update_memory" and success:
                        result["memory_updated"] = True
                    
                    # 保存所有工具决策
                    self._save_decision(
                        tool_call, 
                        ai_response.reasoning, 
                        snapshot,
                        execution_result,
                        success
                    )
                    
                    # 记录交易操作到结果
                    if tool_call.name in ("trade_in", "close_position"):
                        result["actions"].append({
                            "tool": tool_call.name,
                            "info": tool_call.info,
                            "args": tool_call.args,
                            "success": success,
                            "executed": self.live_trading
                        })
                    
                    # 收集失败信息
                    if not success:
                        all_success = False
                        error_msg = f"工具 '{tool_call.name}' 执行失败"
                        if execution_result and execution_result.error:
                            error_msg += f": {execution_result.error}"
                        error_messages.append({
                            "tool": tool_call.name,
                            "args": tool_call.args,
                            "error": error_msg
                        })
                
                # 如果所有工具都成功，退出循环
                if all_success:
                    logger.info("所有工具执行成功")
                    break
                
                # 如果有失败，构建错误反馈并重试
                retry_count += 1
                result["retry_count"] = retry_count
                
                if retry_count > MAX_RETRIES:
                    logger.warning("达到最大重试次数 (%d)，停止重试", MAX_RETRIES)
                    break
                
                # 构建错误反馈消息
                error_feedback = "⚠️ 工具执行出现错误，请重新决策：\n\n"
                for err in error_messages:
                    error_feedback += f"- {err['tool']}({err['args']}): {err['error']}\n"
                error_feedback += "\n请根据上述错误信息，调整您的决策并重新调用工具。"
                
                messages.append({"role": "user", "content": error_feedback})
                logger.info("工具执行失败，发送错误反馈给 AI 进行重试 (#%d)", retry_count)
            
            result["success"] = True
            logger.info("循环完成: %d 个动作, %d 次重试", len(result["actions"]), retry_count)
            
            # 第七步: 保存账户净值快照（用于收益曲线）
            self._save_equity_snapshot(context)
            
        except Exception as e:
            logger.exception("循环失败: %s", e)
            result["error"] = str(e)
        
        return result
    
    def get_status(self) -> dict:
        """获取当前引擎状态。"""
        return {
            "symbols": self.data_engine.symbols,
            "has_custom_instructions": bool(self._get_custom_instructions()),
            "memory_length": len(self._get_memory_content()),
            "live_trading": self.live_trading,
            # 只检查 API Key 是否配置，不进行实时 API 测试（避免频繁调用 DeepSeek）
            "ai_connected": bool(self.ai_agent.api_key)
        }
