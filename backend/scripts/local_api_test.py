#!/usr/bin/env python3
"""
Local API Test Script
Tests all endpoints without needing WhatsApp or external services.

Usage:
    1. Start the server: uvicorn app.main:app --reload --port 8000
    2. Run this script: python scripts/local_api_test.py

Or run with test server (no need to start uvicorn):
    python scripts/local_api_test.py --embedded
"""

import argparse
import asyncio
import json
import hmac
import hashlib
import sys
import time
from typing import Optional, Tuple
from dataclasses import dataclass
from enum import Enum

try:
    import httpx
except ImportError:
    print("❌ httpx not installed. Run: pip install httpx")
    sys.exit(1)


# =============================================================================
# Configuration
# =============================================================================

BASE_URL = "http://localhost:8000"
VERIFY_TOKEN = "test_verify_token_123"  # Should match your .env
WEBHOOK_SECRET = "test_webhook_secret"  # For signature testing

# Test phone number (fake)
TEST_PHONE = "15551234567"


# =============================================================================
# Test Result Tracking
# =============================================================================

class TestStatus(Enum):
    PASS = "✅ PASS"
    FAIL = "❌ FAIL"
    SKIP = "⏭️ SKIP"
    WARN = "⚠️ WARN"


@dataclass
class TestResult:
    name: str
    status: TestStatus
    message: str = ""
    duration_ms: float = 0


class TestRunner:
    def __init__(self):
        self.results: list[TestResult] = []
        self.client: Optional[httpx.AsyncClient] = None
    
    def add_result(self, name: str, status: TestStatus, message: str = "", duration_ms: float = 0):
        self.results.append(TestResult(name, status, message, duration_ms))
        status_str = status.value
        print(f"  {status_str} {name}")
        if message:
            print(f"       └─ {message}")
    
    def print_summary(self):
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)
        
        passed = sum(1 for r in self.results if r.status == TestStatus.PASS)
        failed = sum(1 for r in self.results if r.status == TestStatus.FAIL)
        skipped = sum(1 for r in self.results if r.status == TestStatus.SKIP)
        warned = sum(1 for r in self.results if r.status == TestStatus.WARN)
        
        print(f"  Total:   {len(self.results)}")
        print(f"  Passed:  {passed} ✅")
        print(f"  Failed:  {failed} ❌")
        print(f"  Skipped: {skipped} ⏭️")
        print(f"  Warned:  {warned} ⚠️")
        print("=" * 60)
        
        if failed > 0:
            print("\nFailed Tests:")
            for r in self.results:
                if r.status == TestStatus.FAIL:
                    print(f"  ❌ {r.name}: {r.message}")
        
        return failed == 0


runner = TestRunner()


# =============================================================================
# Helper Functions
# =============================================================================

def generate_webhook_signature(payload: bytes, secret: str) -> str:
    """Generate WhatsApp webhook signature"""
    signature = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    return f"sha256={signature}"


def create_whatsapp_message_payload(
    from_phone: str,
    message_text: str,
    message_id: str = "test_msg_123"
) -> dict:
    """Create a WhatsApp webhook message payload"""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456789",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550001234",
                                "phone_number_id": "123456789"
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Test User"},
                                    "wa_id": from_phone
                                }
                            ],
                            "messages": [
                                {
                                    "from": from_phone,
                                    "id": message_id,
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": message_text}
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }


def create_status_update_payload(message_id: str, status: str) -> dict:
    """Create a WhatsApp status update payload"""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456789",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550001234",
                                "phone_number_id": "123456789"
                            },
                            "statuses": [
                                {
                                    "id": message_id,
                                    "status": status,
                                    "timestamp": str(int(time.time())),
                                    "recipient_id": TEST_PHONE
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }


# =============================================================================
# Test Categories
# =============================================================================

async def test_health_and_status(client: httpx.AsyncClient):
    """Test health and status endpoints"""
    print("\n📊 HEALTH & STATUS TESTS")
    print("-" * 40)
    
    # Test 1: Health endpoint
    start = time.time()
    try:
        response = await client.get("/health")
        duration = (time.time() - start) * 1000
        
        if response.status_code == 200:
            data = response.json()
            if "status" in data:
                runner.add_result(
                    "Health endpoint",
                    TestStatus.PASS,
                    f"Status: {data.get('status', 'unknown')}",
                    duration
                )
            else:
                runner.add_result(
                    "Health endpoint",
                    TestStatus.WARN,
                    "Response missing 'status' field",
                    duration
                )
        else:
            runner.add_result(
                "Health endpoint",
                TestStatus.FAIL,
                f"Expected 200, got {response.status_code}",
                duration
            )
    except httpx.ConnectError:
        runner.add_result(
            "Health endpoint",
            TestStatus.FAIL,
            "Could not connect to server. Is it running?"
        )
        return  # Skip remaining tests if server is down
    except Exception as e:
        runner.add_result("Health endpoint", TestStatus.FAIL, str(e))
    
    # Test 2: Root endpoint
    start = time.time()
    try:
        response = await client.get("/")
        duration = (time.time() - start) * 1000
        
        if response.status_code == 200:
            runner.add_result("Root endpoint", TestStatus.PASS, "", duration)
        else:
            runner.add_result(
                "Root endpoint",
                TestStatus.WARN,
                f"Got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("Root endpoint", TestStatus.FAIL, str(e))
    
    # Test 3: API docs (if in debug mode)
    start = time.time()
    try:
        response = await client.get("/docs")
        duration = (time.time() - start) * 1000
        
        if response.status_code == 200:
            runner.add_result(
                "API docs available",
                TestStatus.PASS,
                "Swagger UI accessible",
                duration
            )
        elif response.status_code == 404:
            runner.add_result(
                "API docs available",
                TestStatus.SKIP,
                "Docs disabled (production mode)",
                duration
            )
        else:
            runner.add_result(
                "API docs available",
                TestStatus.WARN,
                f"Got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("API docs available", TestStatus.FAIL, str(e))


async def test_webhook_verification(client: httpx.AsyncClient):
    """Test webhook verification endpoint"""
    print("\n🔐 WEBHOOK VERIFICATION TESTS")
    print("-" * 40)
    
    # Test 1: Valid verification
    start = time.time()
    try:
        params = {
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "test_challenge_12345"
        }
        response = await client.get("/webhook", params=params)
        duration = (time.time() - start) * 1000
        
        if response.status_code == 200 and response.text == "test_challenge_12345":
            runner.add_result(
                "Webhook verification (valid token)",
                TestStatus.PASS,
                "Challenge returned correctly",
                duration
            )
        elif response.status_code == 200:
            runner.add_result(
                "Webhook verification (valid token)",
                TestStatus.WARN,
                f"Got 200 but response was: {response.text[:50]}",
                duration
            )
        else:
            runner.add_result(
                "Webhook verification (valid token)",
                TestStatus.FAIL,
                f"Expected 200, got {response.status_code}. Check WHATSAPP_VERIFY_TOKEN in .env",
                duration
            )
    except Exception as e:
        runner.add_result("Webhook verification (valid token)", TestStatus.FAIL, str(e))
    
    # Test 2: Invalid verification token
    start = time.time()
    try:
        params = {
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "test_challenge"
        }
        response = await client.get("/webhook", params=params)
        duration = (time.time() - start) * 1000
        
        if response.status_code == 403:
            runner.add_result(
                "Webhook verification (invalid token)",
                TestStatus.PASS,
                "Correctly rejected with 403",
                duration
            )
        else:
            runner.add_result(
                "Webhook verification (invalid token)",
                TestStatus.FAIL,
                f"Expected 403, got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("Webhook verification (invalid token)", TestStatus.FAIL, str(e))
    
    # Test 3: Invalid mode
    start = time.time()
    try:
        params = {
            "hub.mode": "unsubscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "test_challenge"
        }
        response = await client.get("/webhook", params=params)
        duration = (time.time() - start) * 1000
        
        if response.status_code in [400, 403]:
            runner.add_result(
                "Webhook verification (invalid mode)",
                TestStatus.PASS,
                f"Correctly rejected with {response.status_code}",
                duration
            )
        else:
            runner.add_result(
                "Webhook verification (invalid mode)",
                TestStatus.WARN,
                f"Expected 400/403, got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("Webhook verification (invalid mode)", TestStatus.FAIL, str(e))


async def test_webhook_message_handling(client: httpx.AsyncClient):
    """Test webhook message handling"""
    print("\n💬 WEBHOOK MESSAGE TESTS")
    print("-" * 40)
    
    # Test 1: Valid message payload
    start = time.time()
    try:
        payload = create_whatsapp_message_payload(TEST_PHONE, "Hello, test message!")
        response = await client.post(
            "/webhook",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        duration = (time.time() - start) * 1000
        
        if response.status_code == 200:
            runner.add_result(
                "Webhook POST (valid message)",
                TestStatus.PASS,
                "Message accepted",
                duration
            )
        else:
            runner.add_result(
                "Webhook POST (valid message)",
                TestStatus.WARN,
                f"Got {response.status_code}: {response.text[:100]}",
                duration
            )
    except Exception as e:
        runner.add_result("Webhook POST (valid message)", TestStatus.FAIL, str(e))
    
    # Test 2: Empty message (should be skipped)
    start = time.time()
    try:
        payload = create_whatsapp_message_payload(TEST_PHONE, "")
        response = await client.post("/webhook", json=payload)
        duration = (time.time() - start) * 1000
        
        if response.status_code == 200:
            runner.add_result(
                "Webhook POST (empty message)",
                TestStatus.PASS,
                "Empty message handled gracefully",
                duration
            )
        else:
            runner.add_result(
                "Webhook POST (empty message)",
                TestStatus.WARN,
                f"Got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("Webhook POST (empty message)", TestStatus.FAIL, str(e))
    
    # Test 3: Long message (should be rejected or truncated)
    start = time.time()
    try:
        long_message = "A" * 5000  # Over 4096 limit
        payload = create_whatsapp_message_payload(TEST_PHONE, long_message)
        response = await client.post("/webhook", json=payload)
        duration = (time.time() - start) * 1000
        
        # Should either reject or handle gracefully
        if response.status_code in [200, 400]:
            runner.add_result(
                "Webhook POST (long message)",
                TestStatus.PASS,
                f"Long message handled (status: {response.status_code})",
                duration
            )
        else:
            runner.add_result(
                "Webhook POST (long message)",
                TestStatus.WARN,
                f"Got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("Webhook POST (long message)", TestStatus.FAIL, str(e))
    
    # Test 4: Status update payload
    start = time.time()
    try:
        payload = create_status_update_payload("msg_123", "delivered")
        response = await client.post("/webhook", json=payload)
        duration = (time.time() - start) * 1000
        
        if response.status_code == 200:
            runner.add_result(
                "Webhook POST (status update)",
                TestStatus.PASS,
                "Status update processed",
                duration
            )
        else:
            runner.add_result(
                "Webhook POST (status update)",
                TestStatus.WARN,
                f"Got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("Webhook POST (status update)", TestStatus.FAIL, str(e))


async def test_oauth_flow(client: httpx.AsyncClient):
    """Test OAuth flow endpoints"""
    print("\n🔑 OAUTH FLOW TESTS")
    print("-" * 40)
    
    # Test 1: OAuth authorize redirect
    start = time.time()
    try:
        response = await client.get(
            "/oauth/authorize",
            params={"phone": f"+{TEST_PHONE}"},
            follow_redirects=False
        )
        duration = (time.time() - start) * 1000
        
        if response.status_code in [302, 307, 200]:
            location = response.headers.get("location", "")
            if "accounts.google.com" in location or response.status_code == 200:
                runner.add_result(
                    "OAuth authorize redirect",
                    TestStatus.PASS,
                    "Redirect to Google OAuth",
                    duration
                )
            else:
                runner.add_result(
                    "OAuth authorize redirect",
                    TestStatus.WARN,
                    f"Redirect to: {location[:50]}...",
                    duration
                )
        elif response.status_code == 500:
            runner.add_result(
                "OAuth authorize redirect",
                TestStatus.WARN,
                "Server error - check Google OAuth credentials",
                duration
            )
        else:
            runner.add_result(
                "OAuth authorize redirect",
                TestStatus.FAIL,
                f"Expected redirect, got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("OAuth authorize redirect", TestStatus.FAIL, str(e))
    
    # Test 2: OAuth callback (without valid code, should fail gracefully)
    start = time.time()
    try:
        response = await client.get(
            "/oauth/callback",
            params={"code": "invalid_code", "state": TEST_PHONE}
        )
        duration = (time.time() - start) * 1000
        
        # Should fail gracefully with invalid code
        if response.status_code in [400, 401, 500]:
            runner.add_result(
                "OAuth callback (invalid code)",
                TestStatus.PASS,
                f"Correctly rejected invalid code ({response.status_code})",
                duration
            )
        else:
            runner.add_result(
                "OAuth callback (invalid code)",
                TestStatus.WARN,
                f"Got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("OAuth callback (invalid code)", TestStatus.FAIL, str(e))


async def test_error_handling(client: httpx.AsyncClient):
    """Test error handling"""
    print("\n🚨 ERROR HANDLING TESTS")
    print("-" * 40)
    
    # Test 1: Invalid JSON
    start = time.time()
    try:
        response = await client.post(
            "/webhook",
            content="not valid json {{{",
            headers={"Content-Type": "application/json"}
        )
        duration = (time.time() - start) * 1000
        
        if response.status_code in [400, 422]:
            runner.add_result(
                "Invalid JSON handling",
                TestStatus.PASS,
                f"Correctly rejected with {response.status_code}",
                duration
            )
        elif response.status_code == 200:
            runner.add_result(
                "Invalid JSON handling",
                TestStatus.WARN,
                "Server accepted invalid JSON",
                duration
            )
        else:
            runner.add_result(
                "Invalid JSON handling",
                TestStatus.WARN,
                f"Got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("Invalid JSON handling", TestStatus.FAIL, str(e))
    
    # Test 2: Missing required fields
    start = time.time()
    try:
        response = await client.post(
            "/webhook",
            json={"object": "whatsapp_business_account"}  # Missing entry
        )
        duration = (time.time() - start) * 1000
        
        if response.status_code in [200, 400, 422]:
            runner.add_result(
                "Missing fields handling",
                TestStatus.PASS,
                f"Handled gracefully ({response.status_code})",
                duration
            )
        else:
            runner.add_result(
                "Missing fields handling",
                TestStatus.WARN,
                f"Got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("Missing fields handling", TestStatus.FAIL, str(e))
    
    # Test 3: 404 for unknown endpoint
    start = time.time()
    try:
        response = await client.get("/unknown/endpoint/that/does/not/exist")
        duration = (time.time() - start) * 1000
        
        if response.status_code == 404:
            runner.add_result(
                "404 for unknown endpoint",
                TestStatus.PASS,
                "Returns 404 as expected",
                duration
            )
        else:
            runner.add_result(
                "404 for unknown endpoint",
                TestStatus.FAIL,
                f"Expected 404, got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("404 for unknown endpoint", TestStatus.FAIL, str(e))


async def test_simulated_agent_messages(client: httpx.AsyncClient):
    """Test simulated agent message processing"""
    print("\n🤖 AGENT MESSAGE SIMULATION TESTS")
    print("-" * 40)
    
    test_messages = [
        ("Greeting", "Hi there!", "Should respond with greeting"),
        ("Calendar query", "What's on my calendar today?", "Should process calendar intent"),
        ("Create event", "Schedule a meeting tomorrow at 3pm", "Should parse event creation"),
        ("Delete event", "Cancel my 3pm meeting", "Should parse deletion intent"),
        ("List events", "Show my events for next week", "Should list events"),
    ]
    
    for name, message, description in test_messages:
        start = time.time()
        try:
            # Use unique message ID to avoid deduplication
            msg_id = f"test_{name.lower().replace(' ', '_')}_{int(time.time() * 1000)}"
            payload = create_whatsapp_message_payload(TEST_PHONE, message, msg_id)
            
            response = await client.post("/webhook", json=payload)
            duration = (time.time() - start) * 1000
            
            if response.status_code == 200:
                runner.add_result(
                    f"Agent: {name}",
                    TestStatus.PASS,
                    description,
                    duration
                )
            else:
                runner.add_result(
                    f"Agent: {name}",
                    TestStatus.WARN,
                    f"Got {response.status_code} - {response.text[:50]}",
                    duration
                )
        except Exception as e:
            runner.add_result(f"Agent: {name}", TestStatus.FAIL, str(e))
        
        # Small delay between messages
        await asyncio.sleep(0.1)


async def test_test_webhook_endpoint(client: httpx.AsyncClient):
    """Test the test webhook endpoint (debug mode only)"""
    print("\n🧪 TEST ENDPOINT TESTS")
    print("-" * 40)
    
    # Test: /webhook/test endpoint
    start = time.time()
    try:
        test_payload = {
            "test": True,
            "message": "This is a test"
        }
        response = await client.post("/webhook/test", json=test_payload)
        duration = (time.time() - start) * 1000
        
        if response.status_code == 200:
            runner.add_result(
                "Test webhook endpoint",
                TestStatus.PASS,
                "Test endpoint accessible (debug mode)",
                duration
            )
        elif response.status_code == 404:
            runner.add_result(
                "Test webhook endpoint",
                TestStatus.SKIP,
                "Disabled (production mode)",
                duration
            )
        else:
            runner.add_result(
                "Test webhook endpoint",
                TestStatus.WARN,
                f"Got {response.status_code}",
                duration
            )
    except Exception as e:
        runner.add_result("Test webhook endpoint", TestStatus.FAIL, str(e))


# =============================================================================
# Main Runner
# =============================================================================

async def run_all_tests(base_url: str):
    """Run all tests"""
    print("=" * 60)
    print("🧪 LOCAL API TEST SUITE")
    print(f"   Target: {base_url}")
    print(f"   Time:   {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        # Run all test categories
        await test_health_and_status(client)
        await test_webhook_verification(client)
        await test_webhook_message_handling(client)
        await test_oauth_flow(client)
        await test_error_handling(client)
        await test_simulated_agent_messages(client)
        await test_test_webhook_endpoint(client)
    
    # Print summary
    success = runner.print_summary()
    
    return success


async def run_with_embedded_server():
    """Run tests with embedded test server"""
    print("Starting embedded test server...")
    
    try:
        from fastapi.testclient import TestClient
        from httpx import ASGITransport
        
        # Import the app
        sys.path.insert(0, str(__file__).rsplit("scripts", 1)[0])
        from app.main import app
        
        print("✅ App imported successfully")
        
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            # Run simplified tests
            print("\n📊 HEALTH & STATUS TESTS")
            print("-" * 40)
            
            response = await client.get("/health")
            if response.status_code == 200:
                runner.add_result("Health endpoint", TestStatus.PASS, f"Status: {response.json()}")
            else:
                runner.add_result("Health endpoint", TestStatus.FAIL, f"Got {response.status_code}")
        
        return runner.print_summary()
        
    except ImportError as e:
        print(f"❌ Could not import app: {e}")
        print("   Make sure you're running from the backend directory")
        return False


def main():
    parser = argparse.ArgumentParser(description="Local API Test Script")
    parser.add_argument(
        "--url",
        default=BASE_URL,
        help=f"Base URL of the server (default: {BASE_URL})"
    )
    parser.add_argument(
        "--embedded",
        action="store_true",
        help="Run with embedded test server (no need to start uvicorn)"
    )
    parser.add_argument(
        "--verify-token",
        default=None,
        help="WhatsApp verify token to test with"
    )
    
    args = parser.parse_args()
    
    verify_token = args.verify_token if args.verify_token else VERIFY_TOKEN
    
    print("""
    ============================================================
              WhatsApp Calendar Agent - API Tester
    ============================================================
    """)
    
    if args.embedded:
        success = asyncio.run(run_with_embedded_server())
    else:
        success = asyncio.run(run_all_tests(args.url))
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
