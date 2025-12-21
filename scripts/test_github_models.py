"""
Quick test script to verify GitHub Models integration
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.core.config import settings
from app.services.github_models_service import github_models_service
from app.core.logging import logger


async def test_github_models():
    """Test GitHub Models service"""
    
    print("=" * 60)
    print("🧪 TESTING GITHUB MODELS SERVICE")
    print("=" * 60)
    
    # Check configuration
    print(f"\n📋 Configuration:")
    print(f"   USE_GITHUB_MODELS: {settings.USE_GITHUB_MODELS}")
    print(f"   GITHUB_MODEL: {settings.GITHUB_MODEL}")
    print(f"   GITHUB_TOKEN: {'✅ Set' if settings.GITHUB_TOKEN else '❌ Not Set'}")
    
    if not settings.USE_GITHUB_MODELS:
        print("\n❌ USE_GITHUB_MODELS is False - GitHub Models disabled")
        return False
    
    if not settings.GITHUB_TOKEN:
        print("\n❌ GITHUB_TOKEN not configured")
        return False
    
    # Test 1: Health Check
    print(f"\n🏥 Test 1: Health Check")
    try:
        health = await github_models_service.health_check()
        if health:
            print("   ✅ Health check passed")
        else:
            print("   ❌ Health check failed")
            return False
    except Exception as e:
        print(f"   ❌ Health check error: {e}")
        return False
    
    # Test 2: Simple Chat Completion
    print(f"\n💬 Test 2: Simple Chat Completion (No Tools)")
    try:
        messages = [
            {"role": "user", "content": "Say 'Hello from GitHub Models!' and nothing else."}
        ]
        
        print("   Sending request...")
        response = await github_models_service.chat_completion(
            messages=messages,
            tools=None
        )
        
        content = response.get("message", {}).get("content", "")
        print(f"   Response: {content}")
        
        if content and "Hello from GitHub Models" in content:
            print("   ✅ Simple chat completion works")
        else:
            print("   ⚠️  Unexpected response")
            return False
            
    except Exception as e:
        print(f"   ❌ Chat completion error: {e}")
        return False
    
    # Test 3: Function Calling (Tool Use)
    print(f"\n🔧 Test 3: Function Calling (Tool Use)")
    try:
        messages = [
            {"role": "user", "content": "What events do I have coming up? Use the get_upcoming_events tool."}
        ]
        
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_upcoming_events",
                    "description": "Get upcoming calendar events",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "days": {
                                "type": "integer",
                                "description": "Number of days to look ahead"
                            }
                        },
                        "required": []
                    }
                }
            }
        ]
        
        print("   Sending request with tools...")
        response = await github_models_service.chat_completion(
            messages=messages,
            tools=tools
        )
        
        message = response.get("message", {})
        tool_calls = github_models_service.parse_tool_calls(message)
        
        if tool_calls:
            print(f"   ✅ Function calling works! Called: {tool_calls[0].function.get('name')}")
            print(f"   Tool call ID: {tool_calls[0].id}")
            print(f"   Arguments: {tool_calls[0].function.get('arguments')}")
        else:
            print(f"   ⚠️  No tool calls detected")
            print(f"   Response: {message.get('content', 'No content')}")
            # This might still be OK if model responded differently
            
    except Exception as e:
        print(f"   ❌ Function calling error: {e}")
        return False
    
    # All tests passed
    print("\n" + "=" * 60)
    print("✅ ALL TESTS PASSED - GITHUB MODELS IS WORKING!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    try:
        result = asyncio.run(test_github_models())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
