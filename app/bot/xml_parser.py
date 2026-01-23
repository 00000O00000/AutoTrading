"""
AI 响应的 XML 工具调用解析器。

从 AI 生成的文本中提取并验证 <tooluse> 块。
遵循规范中定义的 XML-MCP 协议。
"""

import re
import json
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

from app.bot.prompts import TOOL_DEFINITIONS, get_tool_names, LEVERAGE_MIN, LEVERAGE_MAX

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """表示从 AI 响应中解析出的工具调用。"""
    name: str
    info: str
    args: Dict[str, Any]
    raw_json: str  # Original JSON for debugging
    
    def __repr__(self):
        info_preview = (self.info[:50] + '...') if len(self.info) > 50 else self.info
        return f"<ToolCall {self.name}: {info_preview}>"


class XMLParseError(Exception):
    """当 XML 解析失败时引发。"""
    
    def __init__(self, message: str, raw_content: str = None):
        self.raw_content = raw_content
        super().__init__(message)


class ToolValidationError(Exception):
    """当工具验证失败时引发。"""
    
    def __init__(self, tool_name: str, reason: str):
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"Tool '{tool_name}' validation failed: {reason}")


# 提取 <tooluse>...</tooluse> 块的正则表达式
TOOLUSE_PATTERN = re.compile(
    r'<tooluse>\s*(.*?)\s*</tooluse>',
    re.DOTALL | re.IGNORECASE
)


def extract_tooluse_blocks(text: str) -> List[str]:
    """
    从文本中提取所有 <tooluse> 块。
    
    Args:
        text: AI 响应文本
        
    Returns:
        List of tooluse 标签内的 JSON 字符串
    """
    matches = TOOLUSE_PATTERN.findall(text)
    return [m.strip() for m in matches]


def parse_json_safely(json_str: str) -> Dict[str, Any]:
    """
    带错误恢复的 JSON 字符串解析。
    
    处理尾随逗号或未转义引号等常见问题。
    
    Args:
        json_str: 要解析的 JSON 字符串
        
    Returns:
        解析后的字典
        
    Raises:
        XMLParseError: 如果 JSON 无法解析
    """
    # 第一次尝试: 直接解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    
    # 第二次尝试: 修复常见问题
    fixed = json_str
    
    # 移除闭合括号前的尾随逗号
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
    
    # 重试
    try:
        return json.loads(fixed)
    except json.JSONDecodeError as e:
        raise XMLParseError(
            f"Failed to parse JSON: {e}",
            raw_content=json_str
        )


def _validate_leverage(args: Dict[str, Any], arg_name: str, tool_name: str) -> None:
    """
    验证杠杆参数并规范化为字符串。
    
    Args:
        args: 工具参数字典 (会被原地修改)
        arg_name: 杠杆参数名 ('leverage')
        tool_name: 工具名称 (用于错误消息)
        
    Raises:
        ToolValidationError: 如果杠杆无效
    """
    if arg_name not in args:
        return
    
    try:
        lev = int(float(str(args[arg_name])))  # 支持 "10.0" 格式
        if not LEVERAGE_MIN <= lev <= LEVERAGE_MAX:
            raise ValueError()
        args[arg_name] = str(lev)
    except (ValueError, TypeError):
        raise ToolValidationError(
            tool_name,
            f"{arg_name} must be {LEVERAGE_MIN}-{LEVERAGE_MAX}, got: {args.get(arg_name)}"
        )


def _validate_positive_number(args: Dict[str, Any], arg_name: str, tool_name: str) -> None:
    """
    验证数字参数为正数。
    
    Args:
        args: 工具参数字典
        arg_name: 参数名
        tool_name: 工具名称
        
    Raises:
        ToolValidationError: 如果不是正数
    """
    if arg_name not in args:
        return
    
    try:
        value = float(str(args[arg_name]))
        if value <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise ToolValidationError(
            tool_name,
            f"{arg_name} must be a positive number, got: {args.get(arg_name)}"
        )


def validate_tool_call(tool_data: Dict[str, Any]) -> ToolCall:
    """
    验证并从解析数据创建 ToolCall。
    
    Args:
        tool_data: 从 JSON 解析出的字典
        
    Returns:
        验证后的 ToolCall 对象
        
    Raises:
        ToolValidationError: 如果验证失败
    """
    # 检查必需字段
    if 'name' not in tool_data:
        raise ToolValidationError("unknown", "Missing 'name' field")
    
    name = tool_data['name']
    
    if name not in get_tool_names():
        raise ToolValidationError(name, f"Unknown tool. Valid tools: {get_tool_names()}")
    
    if 'args' not in tool_data:
        raise ToolValidationError(name, "Missing 'args' field")
    
    # 获取工具定义
    tool_def = TOOL_DEFINITIONS[name]
    args = tool_data['args']
    
    # 验证 args 是字典类型
    if not isinstance(args, dict):
        raise ToolValidationError(name, f"'args' must be a dictionary, got: {type(args).__name__}")
    
    # 检查必需参数
    for required_arg in tool_def['required_args']:
        if required_arg not in args:
            raise ToolValidationError(
                name, 
                f"Missing required argument: {required_arg}"
            )
    
    # 验证特定工具参数
    if name == "trade_in":
        side = args.get('side', '').upper()
        if side not in tool_def['side_values']:
            raise ToolValidationError(
                name,
                f"Invalid side '{side}'. Must be BUY or SELL"
            )
        # 将方向规范化为大写
        args['side'] = side
        
        # 验证 count_usdt 是正数
        _validate_positive_number(args, 'count_usdt', name)
        
        # 验证杠杆
        _validate_leverage(args, 'leverage', name)
        
        # 验证止损止盈价格是正数
        _validate_positive_number(args, 'stop_loss_price', name)
        _validate_positive_number(args, 'take_profit_price', name)
    
    if name == "close_position":
        try:
            # 处理字符串 "50"、数字 50 和浮点数 50.5
            pct = int(float(str(args['percentage'])))
            if not 1 <= pct <= 100:
                raise ValueError()
            # 为了一致性规范化为字符串
            args['percentage'] = str(pct)
        except (ValueError, TypeError):
            raise ToolValidationError(
                name,
                f"percentage must be 1-100, got: {args.get('percentage')}"
            )
    
    if name == "set_leverage":
        _validate_leverage(args, 'leverage', name)
    
    if name == "set_margin_mode":
        mode = args.get('mode', '').lower()
        if mode not in ['cross', 'isolated']:
            raise ToolValidationError(
                name,
                f"Invalid mode '{mode}'. Must be 'cross' or 'isolated'"
            )
        args['mode'] = mode
    
    if name == "modify_position":
        # 至少需要提供 stop_loss_price 或 take_profit_price 之一
        if 'stop_loss_price' not in args and 'take_profit_price' not in args:
            raise ToolValidationError(
                name,
                "At least one of 'stop_loss_price' or 'take_profit_price' must be provided"
            )
        # 验证价格是正数
        _validate_positive_number(args, 'stop_loss_price', name)
        _validate_positive_number(args, 'take_profit_price', name)
    
    if name == "cancel_orders":
        # 验证 order_type (如果提供)
        order_type = args.get('order_type', 'all').lower()
        if order_type not in ['stop_loss', 'take_profit', 'all']:
            raise ToolValidationError(
                name,
                f"Invalid order_type '{order_type}'. Must be 'stop_loss', 'take_profit', or 'all'"
            )
        args['order_type'] = order_type
    
    if name == "cancel_order":
        # 验证 order_id 必须提供
        if 'order_id' not in args or not args['order_id']:
            raise ToolValidationError(
                name,
                "order_id is required for cancel_order"
            )
    
    # Create ToolCall
    return ToolCall(
        name=name,
        info=tool_data.get('info', ''),
        args=args,
        raw_json=json.dumps(tool_data)
    )


def parse_tool_calls(response_text: str) -> List[ToolCall]:
    """
    从 AI 响应文本中解析所有工具调用。
    
    这是 XML 解析的主要入口点。
    
    Args:
        response_text: 完整的 AI 响应文本
        
    Returns:
        List of 验证后的 ToolCall 对象
    """
    tool_calls = []
    
    # 提取所有 tooluse 块
    json_blocks = extract_tooluse_blocks(response_text)
    
    if not json_blocks:
        logger.warning("No <tooluse> blocks found in AI response")
        return []
    
    # 解析并验证每个块
    for i, json_str in enumerate(json_blocks):
        try:
            tool_data = parse_json_safely(json_str)
            tool_call = validate_tool_call(tool_data)
            tool_calls.append(tool_call)
            
            logger.debug("Parsed tool call: %s", tool_call.name)
            
        except (XMLParseError, ToolValidationError) as e:
            logger.error("Failed to parse tool call #%d: %s", i + 1, e)
            # Continue parsing other blocks
    
    return tool_calls


def has_memory_update(tool_calls: List[ToolCall]) -> bool:
    """检查工具调用是否包含记忆更新。"""
    return any(tc.name == "update_memory" for tc in tool_calls)


def get_trading_actions(tool_calls: List[ToolCall]) -> List[ToolCall]:
    """获取仅与交易相关的工具调用。"""
    return [tc for tc in tool_calls if tc.name in ("trade_in", "close_position")]


def format_tool_calls_summary(tool_calls: List[ToolCall]) -> str:
    """
    将工具调用格式化为人类可读的摘要。
    
    Args:
        tool_calls: List of 解析后的工具调用
        
    Returns:
        格式化的摘要字符串
    """
    if not tool_calls:
        return "No actions taken."
    
    lines = []
    for tc in tool_calls:
        if tc.name == "trade_in":
            lines.append(f"[TRADE] {tc.args.get('side')} {tc.args.get('target')}: {tc.info}")
        elif tc.name == "close_position":
            lines.append(f"[CLOSE] {tc.args.get('target')} {tc.args.get('percentage')}%: {tc.info}")
        elif tc.name == "set_leverage":
            lines.append(f"[LEVERAGE] {tc.args.get('target')} -> {tc.args.get('leverage')}x")
        elif tc.name == "set_margin_mode":
            lines.append(f"[MARGIN] {tc.args.get('target')} -> {tc.args.get('mode')}")
        elif tc.name == "modify_position":
            lines.append(f"[MODIFY] {tc.args.get('target')}: {tc.info}")
        elif tc.name == "cancel_orders":
            lines.append(f"[CANCEL] {tc.args.get('target')} ({tc.args.get('order_type', 'all')})")
        elif tc.name == "cancel_order":
            lines.append(f"[CANCEL_ID] {tc.args.get('target')} order_id={tc.args.get('order_id')}")
        elif tc.name == "update_memory":
            content = tc.args.get('content', '')
            preview = (content[:50] + '...') if len(content) > 50 else content
            lines.append(f"[MEMORY] {preview}")
    
    return "\n".join(lines)
