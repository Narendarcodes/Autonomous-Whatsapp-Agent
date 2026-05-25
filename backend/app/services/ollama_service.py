"""
Ollama Service
Handles communication with Ollama LLM for chat completion and function calling
"""

import httpx
import json
from typing import List, Dict, Any, Optional, AsyncGenerator
from datetime import datetime

from app.core.config import settings
from app.core.logging import logger
from app.schemas.tools import Tool, ToolCall
from app.services.llm_service import LLMService


class OllamaService(LLMService):
    """Service for interacting with Ollama LLM"""
    
    def __init__(self):
        self.base_url = settings.OLLAMA_HOST
        self.model = settings.OLLAMA_MODEL
        self.timeout = settings.OLLAMA_TIMEOUT
        self.temperature = settings.OLLAMA_TEMPERATURE
        self.max_tokens = settings.OLLAMA_MAX_TOKENS
        
    async def health_check(self) -> bool:
        """
        Check if Ollama is running and model is available
        
        Returns:
            True if Ollama is healthy
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                
                if response.status_code != 200:
                    logger.error(f"Ollama health check failed: {response.status_code}")
                    return False
                
                models = response.json().get("models", [])
                model_found = any(
                    self.model in model.get("name", "")
                    for model in models
                )
                
                if not model_found:
                    logger.error(f"Model {self.model} not found in Ollama")
                    return False
                
                logger.info(f"✅ Ollama is healthy, model {self.model} available")
                return True
                
        except Exception as e:
            logger.error(f"Ollama health check error: {e}")
            return False
    
    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Tool]] = None,
        stream: bool = False
    ) -> Dict[str, Any]:
        """
        Send chat completion request to Ollama with optional function calling
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            tools: Optional list of Tool definitions for function calling
            stream: Whether to stream the response
            
        Returns:
            Response dictionary with message and optional tool_calls
        """
        try:
            # Prepare payload
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": stream,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens,
                }
            }
            
            # Add tools if provided
            if tools:
                payload["tools"] = [tool.dict() for tool in tools]
                logger.debug(f"Added {len(tools)} tools to request")
            
            logger.info(f"🧠 Sending chat request to Ollama (model: {self.model})")
            logger.debug(f"Messages: {len(messages)}, Tools: {len(tools) if tools else 0}")
            
            start_time = datetime.utcnow()
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload
                )
                
                if response.status_code != 200:
                    logger.error(f"Ollama request failed: {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    raise Exception(f"Ollama API error: {response.status_code}")
                
                result = response.json()
                
                # Calculate processing time
                elapsed = (datetime.utcnow() - start_time).total_seconds()
                logger.info(f"✅ Ollama response received in {elapsed:.2f}s")
                
                # Extract message
                message = result.get("message", {})
                
                # Log response details
                if message.get("tool_calls"):
                    logger.info(f"🔧 LLM requested {len(message['tool_calls'])} tool calls")
                    for tc in message["tool_calls"]:
                        logger.debug(f"Tool: {tc['function']['name']}")
                else:
                    content_preview = message.get("content", "")[:100]
                    logger.debug(f"Response: {content_preview}...")
                
                return result
                
        except httpx.TimeoutException:
            logger.error(f"Ollama request timed out after {self.timeout}s")
            raise Exception(f"LLM request timed out")
        except Exception as e:
            logger.error(f"Ollama chat completion error: {e}")
            raise
    
    async def generate(
        self,
        prompt: str,
        stream: bool = False
    ) -> str:
        """
        Generate text from a prompt (simpler interface without chat history)
        
        Args:
            prompt: Text prompt
            stream: Whether to stream the response
            
        Returns:
            Generated text
        """
        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": stream,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens,
                }
            }
            
            logger.info(f"🧠 Generating text from prompt")
            logger.debug(f"Prompt: {prompt[:100]}...")
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json=payload
                )
                
                if response.status_code != 200:
                    logger.error(f"Ollama generate failed: {response.status_code}")
                    raise Exception(f"Ollama API error: {response.status_code}")
                
                result = response.json()
                generated_text = result.get("response", "")
                
                logger.info(f"✅ Generated {len(generated_text)} characters")
                
                return generated_text
                
        except Exception as e:
            logger.error(f"Ollama generation error: {e}")
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
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens,
                }
            }
            
            if tools:
                payload["tools"] = [tool.dict() for tool in tools]
            
            logger.info(f"🧠 Starting streaming chat")
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=payload
                ) as response:
                    
                    if response.status_code != 200:
                        logger.error(f"Stream failed: {response.status_code}")
                        raise Exception(f"Ollama API error: {response.status_code}")
                    
                    async for line in response.aiter_lines():
                        if line.strip():
                            try:
                                chunk = json.loads(line)
                                if "message" in chunk:
                                    content = chunk["message"].get("content", "")
                                    if content:
                                        yield content
                            except json.JSONDecodeError:
                                continue
                    
                    logger.info(f"✅ Streaming completed")
                    
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            raise
    
    def parse_tool_calls(self, message: Dict[str, Any]) -> List[ToolCall]:
        """
        Parse tool calls from LLM response
        
        Args:
            message: Message dictionary from Ollama response
            
        Returns:
            List of ToolCall objects
        """
        try:
            tool_calls_raw = message.get("tool_calls", [])
            
            if not tool_calls_raw:
                return []
            
            tool_calls = []
            for tc in tool_calls_raw:
                # Parse tool call
                tool_call = ToolCall(
                    id=tc.get("id", f"call_{len(tool_calls)}"),
                    type=tc.get("type", "function"),
                    function=tc.get("function", {})
                )
                tool_calls.append(tool_call)
            
            logger.debug(f"Parsed {len(tool_calls)} tool calls")
            return tool_calls
            
        except Exception as e:
            logger.error(f"Error parsing tool calls: {e}")
            return []
    
    def format_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result: Any
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
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": json.dumps(result) if not isinstance(result, str) else result
        }


# Global instance
ollama_service = OllamaService()
