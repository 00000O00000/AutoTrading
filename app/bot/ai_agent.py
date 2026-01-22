"""
AI 代理 - 用于交易决策的 AI 集成。

使用自定义 base URL 的 OpenAI SDK 与 AI 进行通信。
"""

import logging
import re
from typing import List, Optional
from dataclasses import dataclass
from openai import OpenAI

from config import get_config
from app.bot.prompts import SYSTEM_PROMPT, build_user_prompt
from app.bot.xml_parser import parse_tool_calls, ToolCall, has_memory_update
from app.bot.exceptions import OpenNOF1Error

logger = logging.getLogger(__name__)


class AIAgentError(OpenNOF1Error):
    """当 AI 代理遇到错误时引发。"""
    pass


@dataclass
class AIResponse:
    """来自 AI 代理的结构化响应。"""
    raw_response: str
    tool_calls: List[ToolCall]
    has_memory_update: bool
    model: str
    usage: dict  # Token 使用情况
    
    @property
    def reasoning(self) -> str:
        """提取推理文本 (第一个工具调用之前的所有内容)。"""
        if not self.raw_response:
            return ""
        # 不区分大小写查找 <tooluse>
        match = re.search(r'<tooluse>', self.raw_response, re.IGNORECASE)
        if match:
            return self.raw_response[:match.start()].strip()
        return self.raw_response


class AIAgent:
    """
    用于交易决策的 AI 代理。
    
    通过兼容 OpenAI 的 API 与 AI 通信。
    """
    
    DEFAULT_MODEL = "deepseek-chat"
    
    def __init__(self, api_key: str = None, base_url: str = None):
        """
        初始化 AI 代理。
        
        Args:
            api_key: AI API Key (默认为配置中的值)
            base_url: API Base URL (默认为配置中的值)
        """
        config = get_config()
        
        self.api_key = api_key or config.DEEPSEEK_API_KEY
        self.base_url = base_url or config.DEEPSEEK_BASE_URL
        
        if not self.api_key:
            logger.warning("未配置 AI API 密钥")
        
        # 保存配置引用
        self.config = config
        
        # 使用自定义 base URL 初始化 OpenAI 客户端
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
        
        self.model = config.DEEPSEEK_MODEL
    
    def analyze(
        self,
        market_context: str,
        custom_instructions: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> AIResponse:
        """
        分析市场上下文并生成交易决策。
        
        Args:
            market_context: 来自 DataEngine 的格式化市场数据
            custom_instructions: 可选的用户提供规则
            temperature: 模型温度 (0.0-1.0)
            max_tokens: 最大响应 token 数
            
        Returns:
            AIResponse 包含解析后的工具调用
            
        Raises:
            AIAgentError: 当发生 API 或解析错误时
        """
        if not self.api_key:
            raise AIAgentError("未配置 AI API 密钥")
        
        # 验证参数范围
        if not 0.0 <= temperature <= 2.0:
            logger.warning("无效的 temperature %.2f，使用默认值 0.7", temperature)
            temperature = 0.7
        if max_tokens <= 0:
            logger.warning("无效的 max_tokens %d，使用默认值 2000", max_tokens)
            max_tokens = 2000
        
        # 构建提示词
        user_prompt = build_user_prompt(market_context, custom_instructions)
        
        logger.info("正在向 AI 发送请求 (%d 字符)", len(user_prompt))
        
        try:
            # 动态替换系统提示中的周期值
            system_prompt = SYSTEM_PROMPT.format(
                interval=self.config.TRADING_INTERVAL_MINUTES
            )
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens
            )
            
        except Exception as e:
            logger.error("AI API 错误: %s", e)
            raise AIAgentError(f"API 请求失败: {e}")
        
        # 检查响应有效性
        if not response.choices:
            logger.error("AI 返回了空的 choices 列表")
            raise AIAgentError("AI 响应无效: choices 为空")
        
        # 提取响应内容
        raw_response = response.choices[0].message.content or ""
        
        if not raw_response:
            logger.warning("AI 返回了空响应")
        
        logger.info("收到响应 (%d 字符)", len(raw_response))
        logger.debug("原始响应: %s", raw_response[:500] if raw_response else 'empty')
        
        # 解析工具调用
        tool_calls = parse_tool_calls(raw_response)
        
        if not tool_calls:
            logger.warning("未能从响应中解析出有效的工具调用")
        
        # 构建使用情况信息 (安全处理 usage 为 None 的情况)
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }
        else:
            usage = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        
        return AIResponse(
            raw_response=raw_response,
            tool_calls=tool_calls,
            has_memory_update=has_memory_update(tool_calls),
            model=response.model,
            usage=usage
        )
    
    def test_connection(self) -> bool:
        """
        使用最小请求测试 API 连接。
        
        Returns:
            True 如果连接成功
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": "Say 'OK' if you can hear me."}
                ],
                max_tokens=10
            )
            if not response.choices:
                logger.error("连接测试返回空的 choices")
                return False
            content = response.choices[0].message.content or ""
            return "OK" in content.upper()
        except Exception as e:
            logger.error("连接测试失败: %s", e)
            return False
