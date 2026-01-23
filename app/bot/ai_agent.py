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


@dataclass
class AIProvider:
    """封装单个 AI 提供商的配置和客户端。"""
    name: str
    api_key: str
    base_url: str
    model: str
    client: Optional[OpenAI] = None
    
    def __post_init__(self):
        """初始化 OpenAI 客户端。"""
        if self.api_key and self.base_url:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
    
    @property
    def is_configured(self) -> bool:
        """检查提供商是否已正确配置。"""
        return bool(self.api_key and self.base_url and self.model)


class AIAgent:
    """
    用于交易决策的 AI 代理。
    
    支持双提供商故障转移：主提供商失败时自动切换到备用提供商。
    """
    
    def __init__(self, api_key: str = None, base_url: str = None):
        """
        初始化 AI 代理。
        
        Args:
            api_key: AI API Key (默认为配置中的主提供商)
            base_url: API Base URL (默认为配置中的主提供商)
        """
        config = get_config()
        self.config = config
        
        # 初始化 AI 提供商 1 (主用)
        self.provider1 = AIProvider(
            name="provider1",
            api_key=api_key or config.AI_1_API_KEY,
            base_url=base_url or config.AI_1_BASE_URL,
            model=config.AI_1_MODEL
        )
        
        # 初始化 AI 提供商 2 (备用, 可选)
        self.provider2 = AIProvider(
            name="provider2",
            api_key=config.AI_2_API_KEY,
            base_url=config.AI_2_BASE_URL,
            model=config.AI_2_MODEL
        ) if config.AI_2_API_KEY else None
        
        if not self.provider1.is_configured:
            logger.warning("未配置 AI 提供商 1")
        
        if self.provider2 and self.provider2.is_configured:
            logger.info("已配置 AI 提供商 2 (故障转移)")
        
        # 向后兼容属性
        self.api_key = self.provider1.api_key
        self.base_url = self.provider1.base_url
        self.client = self.provider1.client
        self.model = self.provider1.model
    
    def _call_provider(
        self,
        provider: AIProvider,
        messages: list,
        temperature: float,
        max_tokens: int
    ):
        """
        向指定的 AI 提供商发送请求。
        
        Args:
            provider: AI 提供商实例
            messages: 消息列表
            temperature: 模型温度
            max_tokens: 最大响应 token 数
            
        Returns:
            OpenAI API 响应对象
            
        Raises:
            Exception: 当 API 调用失败时
        """
        return provider.client.chat.completions.create(
            model=provider.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
    
    def analyze(
        self,
        market_context: str,
        custom_instructions: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> AIResponse:
        """
        分析市场上下文并生成交易决策。
        
        支持双提供商故障转移：主提供商失败时自动切换到备用提供商。
        
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
        if not self.provider1.is_configured:
            raise AIAgentError("未配置 AI 提供商 1")
        
        # 验证参数范围
        if not 0.0 <= temperature <= 2.0:
            logger.warning("无效的 temperature %.2f，使用默认值 0.7", temperature)
            temperature = 0.7
        if max_tokens <= 0:
            logger.warning("无效的 max_tokens %d，使用默认值 2000", max_tokens)
            max_tokens = 2000
        
        # 构建提示词
        user_prompt = build_user_prompt(market_context, custom_instructions)
        
        # 动态替换系统提示中的周期值
        system_prompt = SYSTEM_PROMPT.format(
            interval=self.config.TRADING_INTERVAL_MINUTES
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        logger.info("正在向 AI 提供商 1 发送请求 (%d 字符)", len(user_prompt))
        
        response = None
        provider1_error = None
        
        # 尝试提供商 1
        try:
            response = self._call_provider(
                self.provider1, messages, temperature, max_tokens
            )
        except Exception as e:
            provider1_error = e
            logger.warning("AI 提供商 1 请求失败: %s", e)
            
            # 如果有提供商 2，尝试使用
            if self.provider2 and self.provider2.is_configured:
                logger.info("正在切换到 AI 提供商 2...")
                try:
                    response = self._call_provider(
                        self.provider2, messages, temperature, max_tokens
                    )
                    logger.info("AI 提供商 2 请求成功")
                except Exception as provider2_error:
                    logger.error("AI 提供商 2 也失败: %s", provider2_error)
                    raise AIAgentError(
                        f"AI 提供商 1 和 2 均失败 - 1: {provider1_error}, 2: {provider2_error}"
                    )
            else:
                # 无提供商 2，抛出原始错误
                raise AIAgentError(f"API 请求失败: {provider1_error}")
        
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
    
    def analyze_with_messages(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> AIResponse:
        """
        使用完整消息历史进行分析（用于错误重试）。
        
        Args:
            messages: 完整的消息历史列表 [{"role": "...", "content": "..."}]
            temperature: 模型温度
            max_tokens: 最大响应 token 数
            
        Returns:
            AIResponse 包含解析后的工具调用
        """
        if not self.provider1.is_configured:
            raise AIAgentError("未配置 AI 提供商 1")
        
        logger.info("正在向 AI 提供商 1 发送带消息历史的请求 (%d 条消息)", len(messages))
        
        response = None
        provider1_error = None
        
        # 尝试提供商 1
        try:
            response = self._call_provider(
                self.provider1, messages, temperature, max_tokens
            )
        except Exception as e:
            provider1_error = e
            logger.warning("AI 提供商 1 请求失败: %s", e)
            
            # 如果有提供商 2，尝试使用
            if self.provider2 and self.provider2.is_configured:
                logger.info("正在切换到 AI 提供商 2...")
                try:
                    response = self._call_provider(
                        self.provider2, messages, temperature, max_tokens
                    )
                    logger.info("AI 提供商 2 请求成功")
                except Exception as provider2_error:
                    logger.error("AI 提供商 2 也失败: %s", provider2_error)
                    raise AIAgentError(
                        f"AI 提供商 1 和 2 均失败 - 1: {provider1_error}, 2: {provider2_error}"
                    )
            else:
                # 无提供商 2，抛出原始错误
                raise AIAgentError(f"API 请求失败: {provider1_error}")
        
        if not response.choices:
            raise AIAgentError("AI 响应无效: choices 为空")
        
        raw_response = response.choices[0].message.content or ""
        
        logger.info("收到响应 (%d 字符)", len(raw_response))
        
        tool_calls = parse_tool_calls(raw_response)
        
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }
        else:
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        
        return AIResponse(
            raw_response=raw_response,
            tool_calls=tool_calls,
            has_memory_update=has_memory_update(tool_calls),
            model=response.model,
            usage=usage
        )
