"""
GitHub Models Service
Handles LLM interactions via GitHub Models API (OpenAI-compatible)
"""

import httpx
import json
import asyncio
from typing import List, Dict, Any, Optional, AsyncGenerator
from datetime import datetime

from app.core.config import settings
from app.core.logging import logger
from app.core.circuit_breaker import get_circuit_breaker, CircuitOpenError
from app.schemas.tools import Tool, ToolCall
from app.services.llm_service import LLMService


class GitHubModelsService(LLMService):
    """Service for interacting with GitHub Models API"""
    
    def __init__(self):
        self.api_base = "https://models.inference.ai.azure.com"
        self.model = settings.GITHUB_MODEL
        self.token = settings.GITHUB_TOKEN
        self.timeout = 60.0  # Fast cloud-based inference
        # Persistent HTTP client with connection pooling
        self._client: Optional[httpx.AsyncClient] = None
        # Retry configuration
        self._max_retries = 3
        self._base_delay = 1.0
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create persistent HTTP client with connection pooling"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            )
        return self._client
    
    async def close(self):
        """Close the HTTP client"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        
    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List] = None
    ) -> Dict[str, Any]:
        """
        Send chat completion request to GitHub Models
        
        Args:
            messages: List of chat messages
            tools: Optional list of function definitions (Tool objects)
            
        Returns:
            Response from GitHub Models API
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 1000,
                "top_p": 1.0
            }
            
            # Convert Tool objects to OpenAI format if provided
            if tools:
                openai_tools = []
                for tool in tools:
                    # Convert properties with proper handling for arrays
                    properties = {}
                    for k, v in tool.function.parameters.properties.items():
                        prop = {
                            "type": v.type,
                            "description": v.description
                        }
                        # Add enum if present
                        if hasattr(v, 'enum') and v.enum:
                            prop["enum"] = v.enum
                        # Add items for arrays (GitHub Models/OpenAI requires this)
                        if v.type == "array":
                            if hasattr(v, 'items') and v.items:
                                prop["items"] = v.items
                            else:
                                # Default to string items for arrays without explicit type
                                prop["items"] = {"type": "string"}
                        properties[k] = prop
                    
                    openai_tools.append({
                        "type": "function",
                        "function": {
                            "name": tool.function.name,
                            "description": tool.function.description,
                            "parameters": {
                                "type": tool.function.parameters.type,
                                "properties": properties,
                                "required": tool.function.parameters.required or []
                            }
                        }
                    })
                payload["tools"] = openai_tools
                payload["tool_choice"] = "auto"
            
            logger.info(f"🤖 Sending request to GitHub Models ({self.model})")
            logger.debug(f"Messages: {len(messages)} | Tools: {len(tools) if tools else 0}")
            
            # Check circuit breaker
            breaker = get_circuit_breaker("llm_service")
            if not breaker.can_execute():
                raise CircuitOpenError("LLM service circuit is open - too many recent failures")
            
            start_time = datetime.now()
            last_error = None
            
            # Retry loop with exponential backoff
            for attempt in range(self._max_retries):
                try:
                    client = await self._get_client()
                    response = await client.post(
                        f"{self.api_base}/chat/completions",
                        headers=headers,
                        json=payload
                    )
                    
                    response.raise_for_status()
                    result = response.json()
                    
                    duration = (datetime.now() - start_time).total_seconds()
                    logger.info(f"✅ GitHub Models response received in {duration:.2f}s")
                    
                    # Record success for circuit breaker
                    breaker.record_success()
                    
                    # Transform to standard format
                    if "choices" in result and len(result["choices"]) > 0:
                        return {"message": result["choices"][0]["message"]}
                    else:
                        return {"message": {"role": "assistant", "content": ""}}
                    
                except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout) as e:
                    last_error = e
                    if attempt < self._max_retries - 1:
                        delay = self._base_delay * (2 ** attempt)
                        logger.warning(f"⚠️ LLM request failed (attempt {attempt + 1}), retrying in {delay}s: {e}")
                        await asyncio.sleep(delay)
                    continue
                except httpx.HTTPStatusError as e:
                    # Don't retry on 4xx errors (except 429)
                    if 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                        raise
                    last_error = e
                    if attempt < self._max_retries - 1:
                        delay = self._base_delay * (2 ** attempt)
                        logger.warning(f"⚠️ LLM request failed with {e.response.status_code} (attempt {attempt + 1}), retrying in {delay}s")
                        await asyncio.sleep(delay)
                    continue
            
            # All retries exhausted
            breaker.record_failure()
            if isinstance(last_error, httpx.TimeoutException):
                raise Exception("LLM request timed out after retries")
            raise last_error or Exception("LLM request failed after retries")
        
        except CircuitOpenError:
            logger.error("🔌 LLM service circuit breaker is open - service unavailable")
            raise Exception("LLM service temporarily unavailable. Please try again later.")
        except httpx.HTTPStatusError as e:
            breaker = get_circuit_breaker("llm_service")
            breaker.record_failure()
            logger.error(f"❌ GitHub Models HTTP error: {e.response.status_code}")
            logger.error(f"Response: {e.response.text}")
            if e.response.status_code == 401:
                raise Exception("Invalid GitHub token. Please check GITHUB_TOKEN in .env")
            raise Exception(f"LLM API error: {e.response.status_code}")
        except Exception as e:
            breaker = get_circuit_breaker("llm_service")
            breaker.record_failure()
            logger.error(f"❌ GitHub Models error: {str(e)}")
            raise
    
    def parse_tool_calls(self, message: Dict[str, Any]) -> List[ToolCall]:
        """
        Parse tool calls from API response message
        
        Args:
            message: Message object from API response
            
        Returns:
            List of ToolCall objects
        """
        tool_calls = []
        
        if "tool_calls" in message and message["tool_calls"]:
            for tool_call in message["tool_calls"]:
                # Convert to ToolCall schema
                tool_calls.append(
                    ToolCall(
                        id=tool_call["id"],
                        type="function",
                        function=tool_call["function"]
                    )
                )
                
            logger.info(f"🔧 Parsed {len(tool_calls)} tool call(s)")
        
        return tool_calls
    
    def format_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result: Any
    ) -> Dict[str, Any]:
        """
        Format tool execution result for LLM
        
        Args:
            tool_call_id: ID of the tool call
            tool_name: Name of the tool
            result: Tool execution result
            
        Returns:
            Formatted message for LLM
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": json.dumps(result)
        }
    
    async def health_check(self) -> bool:
        """
        Check if GitHub Models API is accessible
        
        Returns:
            True if API is healthy, False otherwise
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            
            # Simple health check - try to list models
            client = await self._get_client()
            response = await client.get(
                f"{self.api_base}/models",
                headers=headers
            )
            
            return response.status_code == 200
                
        except Exception as e:
            logger.error(f"GitHub Models health check failed: {e}")
            return False
    
    async def generate(self, prompt: str, stream: bool = False) -> str:
        """
        Generate text from a prompt (simpler interface without chat history)
        
        Args:
            prompt: Text prompt
            stream: Whether to stream the response
            
        Returns:
            Generated text
        """
        try:
            messages = [{"role": "user", "content": prompt}]
            result = await self.chat_completion(messages, tools=None, stream=stream)
            return result.get("message", {}).get("content", "")
        except Exception as e:
            logger.error(f"Generate error: {e}")
            raise
    
    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Tool]] = None
    ) -> AsyncGenerator[str, None]:
        """
        Stream chat completion response
        
        Args:
            messages: List of message dictionaries
            tools: Optional tools for function calling
            
        Yields:
            Response chunks as they arrive
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 1000,
                "stream": True
            }
            
            if tools:
                openai_tools = []
                for tool in tools:
                    properties = {}
                    for k, v in tool.function.parameters.properties.items():
                        prop = {"type": v.type, "description": v.description}
                        if hasattr(v, 'enum') and v.enum:
                            prop["enum"] = v.enum
                        if v.type == "array":
                            prop["items"] = getattr(v, 'items', {"type": "string"})
                        properties[k] = prop
                    
                    openai_tools.append({
                        "type": "function",
                        "function": {
                            "name": tool.function.name,
                            "description": tool.function.description,
                            "parameters": {
                                "type": tool.function.parameters.type,
                                "properties": properties,
                                "required": tool.function.parameters.required or []
                            }
                        }
                    })
                payload["tools"] = openai_tools
                payload["tool_choice"] = "auto"
            
            logger.info(f"🤖 Starting streaming chat with GitHub Models")
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.api_base}/chat/completions",
                    headers=headers,
                    json=payload
                ) as response:
                    response.raise_for_status()
                    
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                if "choices" in chunk and len(chunk["choices"]) > 0:
                                    delta = chunk["choices"][0].get("delta", {})
                                    if "content" in delta:
                                        yield delta["content"]
                            except json.JSONDecodeError:
                                continue
            
            logger.info(f"✅ Streaming completed")
            
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            raise


# Global instance
github_models_service = GitHubModelsService()
