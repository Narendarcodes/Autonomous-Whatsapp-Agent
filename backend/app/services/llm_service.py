"""
LLM Service Interface
Defines an abstract interface for LLM providers so callers can use a single API
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncGenerator

from app.schemas.tools import Tool, ToolCall


class LLMService(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the LLM service is available and healthy
        
        Returns:
            True if service is healthy, False otherwise
        """
        raise NotImplementedError()

    @abstractmethod
    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Tool]] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """
        Send chat completion request with optional function calling
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            tools: Optional list of Tool definitions for function calling
            stream: Whether to stream the response
            
        Returns:
            Response dictionary with message and optional tool_calls
        """
        raise NotImplementedError()

    @abstractmethod
    async def generate(self, prompt: str, stream: bool = False) -> str:
        """
        Generate text from a prompt (simpler interface without chat history)
        
        Args:
            prompt: Text prompt
            stream: Whether to stream the response
            
        Returns:
            Generated text
        """
        raise NotImplementedError()

    @abstractmethod
    async def stream_chat(
        self, messages: List[Dict[str, str]], tools: Optional[List[Tool]] = None
    ) -> AsyncGenerator[str, None]:
        """
        Stream chat completion response
        
        Args:
            messages: List of message dictionaries
            tools: Optional tools for function calling
            
        Yields:
            Response chunks as they arrive
        """
        raise NotImplementedError()

    @abstractmethod
    def parse_tool_calls(self, message: Dict[str, Any]) -> List[ToolCall]:
        """
        Parse tool calls from LLM response
        
        Args:
            message: Message dictionary from LLM response
            
        Returns:
            List of ToolCall objects
        """
        raise NotImplementedError()

    @abstractmethod
    def format_tool_result(
        self, tool_call_id: str, tool_name: str, result: Any
    ) -> Dict[str, Any]:
        """
        Format tool execution result for sending back to LLM
        
        Args:
            tool_call_id: ID of the tool call
            tool_name: Name of the tool
            result: Result from tool execution
            
        Returns:
            Formatted result dictionary
        """
        raise NotImplementedError()
