#!/usr/bin/env python3
"""
CLI Interface to Test Agent Flow
Tests: Webhook → Agent → Response → WhatsApp
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime
import json
from colorama import init, Fore, Back, Style
import httpx

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

# Initialize colorama for Windows
init(autoreset=True)

# Test configuration
NGROK_URL = "https://expository-joseph-unsanguineously.ngrok-free.dev"
LOCAL_URL = "http://localhost:8000"


def print_header(text):
    """Print colored header"""
    print(f"\n{Fore.CYAN}{'=' * 70}")
    print(f"{Fore.CYAN}{Style.BRIGHT}{text}")
    print(f"{Fore.CYAN}{'=' * 70}{Style.RESET_ALL}\n")


def print_step(number, text):
    """Print step number"""
    print(f"\n{Fore.YELLOW}{Style.BRIGHT}[STEP {number}] {text}{Style.RESET_ALL}")


def print_success(text):
    """Print success message"""
    print(f"{Fore.GREEN}✅ {text}{Style.RESET_ALL}")


def print_error(text):
    """Print error message"""
    print(f"{Fore.RED}❌ {text}{Style.RESET_ALL}")


def print_info(text):
    """Print info message"""
    print(f"{Fore.BLUE}ℹ️  {text}{Style.RESET_ALL}")


def print_json(data, title=""):
    """Print JSON data in colored format"""
    if title:
        print(f"\n{Fore.MAGENTA}{Style.BRIGHT}{title}:{Style.RESET_ALL}")
    print(f"{Fore.WHITE}{json.dumps(data, indent=2)}{Style.RESET_ALL}")


async def test_1_webhook_to_agent():
    """Test 1: Simulate webhook and see if agent receives data"""
    print_step(1, "Testing Webhook → Agent (Data Reception)")
    
    # Sample WhatsApp webhook payload
    webhook_payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "123456789",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "15550123456",
                        "phone_number_id": "874990439034884"
                    },
                    "contacts": [{
                        "profile": {"name": "Test User"},
                        "wa_id": "919876543210"
                    }],
                    "messages": [{
                        "from": "919876543210",
                        "id": f"wamid.TEST{datetime.now().timestamp()}",
                        "timestamp": str(int(datetime.now().timestamp())),
                        "type": "text",
                        "text": {"body": "What's on my calendar today?"}
                    }]
                },
                "field": "messages"
            }]
        }]
    }
    
    print_json(webhook_payload, "📤 Sending Webhook Payload")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            print_info(f"Sending POST to {LOCAL_URL}/webhook...")
            
            response = await client.post(
                f"{LOCAL_URL}/webhook",
                json=webhook_payload,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                print_success("Webhook received successfully!")
                print_json(response.json(), "📥 Webhook Response")
                return True
            else:
                print_error(f"Webhook failed: {response.status_code}")
                print(f"Response: {response.text}")
                return False
                
    except Exception as e:
        print_error(f"Failed to send webhook: {e}")
        return False


async def test_2_agent_processing():
    """Test 2: Monitor agent processing with direct message"""
    print_step(2, "Testing Agent Processing (AI Response Generation)")
    
    # Import after path is set
    try:
        from app.services.agent_engine import agent_engine
        from app.services.oauth_service import oauth_service
        from app.models.user import User
        from app.db.database import SessionLocal
        
        print_info("Connecting to database...")
        db = SessionLocal()
        
        # Check if test user exists
        test_phone = "919876543210"
        user = db.query(User).filter(User.wa_phone == test_phone).first()
        
        if not user:
            print_error(f"No user found with phone {test_phone}")
            print_info("First send a real WhatsApp message to create user")
            return False
        
        print_success(f"Found user: {user.wa_phone}")
        print_info(f"OAuth status: {'✅ Authorized' if oauth_service.check_oauth_status(user) else '❌ Not authorized'}")
        
        # Test messages
        test_messages = [
            "What's on my calendar today?",
            "List my events for this week",
            "Do I have any meetings tomorrow?",
        ]
        
        print("\n" + "=" * 70)
        print(f"{Fore.YELLOW}Select a test message:{Style.RESET_ALL}")
        for i, msg in enumerate(test_messages, 1):
            print(f"{Fore.CYAN}{i}. {msg}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}4. Custom message{Style.RESET_ALL}")
        
        choice = input(f"\n{Fore.GREEN}Enter choice (1-4): {Style.RESET_ALL}").strip()
        
        if choice == "4":
            message = input(f"{Fore.GREEN}Enter your message: {Style.RESET_ALL}").strip()
        else:
            message = test_messages[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= 3 else test_messages[0]
        
        print_info(f"Processing message: '{message}'")
        print(f"{Fore.YELLOW}⏳ Calling agent engine... (this may take 10-30 seconds){Style.RESET_ALL}\n")
        
        # Process with agent
        start_time = datetime.now()
        response = await agent_engine.process_message(
            user=user,
            message=message,
            db=db
        )
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        print_success(f"Agent processed message in {duration:.2f} seconds")
        print(f"\n{Fore.GREEN}{Style.BRIGHT}🤖 Agent Response:{Style.RESET_ALL}")
        print(f"{Fore.WHITE}{'-' * 70}")
        print(f"{response}")
        print(f"{'-' * 70}{Style.RESET_ALL}\n")
        
        db.close()
        return True
        
    except ImportError as e:
        print_error(f"Import error: {e}")
        print_info("Make sure you're running from the correct directory")
        return False
    except Exception as e:
        print_error(f"Agent processing failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_3_response_to_whatsapp():
    """Test 3: Send response to WhatsApp"""
    print_step(3, "Testing Response → WhatsApp (Message Delivery)")
    
    try:
        from app.services.whatsapp_service import whatsapp_service
        from app.core.config import settings
        
        print_info(f"WhatsApp Phone ID: {settings.WHATSAPP_PHONE_ID}")
        print_info(f"API Version: {settings.WHATSAPP_API_VERSION}")
        
        # Ask for recipient number
        print(f"\n{Fore.YELLOW}Enter recipient WhatsApp number:{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Format: Country code + number (e.g., 919876543210){Style.RESET_ALL}")
        recipient = input(f"{Fore.GREEN}Number: {Style.RESET_ALL}").strip()
        
        if not recipient:
            print_error("No recipient provided")
            return False
        
        # Test message options
        print(f"\n{Fore.YELLOW}Select message type:{Style.RESET_ALL}")
        print(f"{Fore.CYAN}1. Simple text message{Style.RESET_ALL}")
        print(f"{Fore.CYAN}2. Button message (interactive){Style.RESET_ALL}")
        print(f"{Fore.CYAN}3. List message (interactive){Style.RESET_ALL}")
        
        msg_type = input(f"\n{Fore.GREEN}Enter choice (1-3): {Style.RESET_ALL}").strip()
        
        print_info("Sending message to WhatsApp API...")
        
        if msg_type == "2":
            # Button message
            success = await whatsapp_service.send_button_message(
                to=recipient,
                text="🤖 Test message from your AI agent! Choose an option:",
                buttons=[
                    {"id": "btn_today", "title": "Today's Events"},
                    {"id": "btn_tomorrow", "title": "Tomorrow's Events"},
                    {"id": "btn_week", "title": "This Week"}
                ]
            )
        elif msg_type == "3":
            # List message
            success = await whatsapp_service.send_list_message(
                to=recipient,
                header="omniWA AI Assistant",
                body="🤖 Test message from your AI agent! Select an option:",
                footer="Powered by AI",
                button_text="View Options",
                sections=[{
                    "title": "Calendar Actions",
                    "rows": [
                        {"id": "view_today", "title": "View Today", "description": "See today's events"},
                        {"id": "view_week", "title": "View Week", "description": "See this week"},
                        {"id": "create", "title": "Create Event", "description": "Add new event"}
                    ]
                }]
            )
        else:
            # Simple text
            success = await whatsapp_service.send_text_message(
                to=recipient,
                message="🤖 Test message from your AI agent!\n\nThis confirms the complete flow:\n✅ Webhook received\n✅ Agent processed\n✅ Response sent to WhatsApp\n\nYour system is working perfectly! 🎉"
            )
        
        if success:
            print_success("Message sent to WhatsApp successfully!")
            print_info("Check your WhatsApp to see the message")
            return True
        else:
            print_error("Failed to send message to WhatsApp")
            return False
            
    except Exception as e:
        print_error(f"Failed to send to WhatsApp: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_complete_flow():
    """Test complete flow: Webhook → Agent → WhatsApp"""
    print_header("🔄 COMPLETE FLOW TEST: Webhook → Agent → WhatsApp")
    
    print(f"{Fore.YELLOW}This will test the entire message flow:{Style.RESET_ALL}")
    print(f"{Fore.CYAN}1. Send webhook with test message{Style.RESET_ALL}")
    print(f"{Fore.CYAN}2. Monitor agent processing{Style.RESET_ALL}")
    print(f"{Fore.CYAN}3. Check response delivery{Style.RESET_ALL}")
    
    input(f"\n{Fore.GREEN}Press Enter to start...{Style.RESET_ALL}")
    
    # Test 1: Webhook
    result1 = await test_1_webhook_to_agent()
    if not result1:
        print_error("Webhook test failed. Fix this before continuing.")
        return
    
    await asyncio.sleep(2)
    
    print(f"\n{Fore.YELLOW}⏳ Wait a few seconds for agent to process...{Style.RESET_ALL}")
    await asyncio.sleep(5)
    
    print_success("Complete flow test finished!")
    print_info("Check docker logs to see the full processing:")
    print(f"{Fore.CYAN}  cd docker && docker-compose logs --tail=50 backend{Style.RESET_ALL}")


async def monitor_logs():
    """Monitor backend logs in real-time"""
    print_header("📊 LIVE LOG MONITOR")
    
    print_info("Fetching recent backend logs...")
    print(f"{Fore.YELLOW}Press Ctrl+C to stop{Style.RESET_ALL}\n")
    
    try:
        import subprocess
        process = subprocess.Popen(
            ["docker-compose", "-f", "docker/docker-compose.yml", "logs", "-f", "--tail=20", "backend"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        for line in process.stdout:
            # Color code log lines
            if "ERROR" in line or "❌" in line:
                print(f"{Fore.RED}{line.strip()}{Style.RESET_ALL}")
            elif "WARNING" in line or "⚠️" in line:
                print(f"{Fore.YELLOW}{line.strip()}{Style.RESET_ALL}")
            elif "SUCCESS" in line or "✅" in line:
                print(f"{Fore.GREEN}{line.strip()}{Style.RESET_ALL}")
            elif "INFO" in line or "📨" in line or "📱" in line:
                print(f"{Fore.CYAN}{line.strip()}{Style.RESET_ALL}")
            else:
                print(line.strip())
                
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Stopping log monitor...{Style.RESET_ALL}")
    except Exception as e:
        print_error(f"Log monitor failed: {e}")


async def main():
    """Main CLI interface"""
    print_header("🤖 WhatsApp AI Agent - Flow Testing CLI")
    
    print(f"{Fore.CYAN}Test Options:{Style.RESET_ALL}\n")
    print(f"{Fore.GREEN}1. Test Webhook → Agent{Style.RESET_ALL} (Does agent receive webhook data?)")
    print(f"{Fore.GREEN}2. Test Agent Processing{Style.RESET_ALL} (How does agent generate responses?)")
    print(f"{Fore.GREEN}3. Test Response → WhatsApp{Style.RESET_ALL} (Does response reach WhatsApp?)")
    print(f"{Fore.GREEN}4. Test Complete Flow{Style.RESET_ALL} (All 3 steps together)")
    print(f"{Fore.GREEN}5. Monitor Live Logs{Style.RESET_ALL} (Watch agent in real-time)")
    print(f"{Fore.GREEN}6. Health Check{Style.RESET_ALL} (Check all services)")
    print(f"{Fore.RED}0. Exit{Style.RESET_ALL}\n")
    
    while True:
        try:
            choice = input(f"{Fore.YELLOW}Select option (0-6): {Style.RESET_ALL}").strip()
            
            if choice == "0":
                print_info("Goodbye!")
                break
            elif choice == "1":
                await test_1_webhook_to_agent()
            elif choice == "2":
                await test_2_agent_processing()
            elif choice == "3":
                await test_3_response_to_whatsapp()
            elif choice == "4":
                await test_complete_flow()
            elif choice == "5":
                await monitor_logs()
            elif choice == "6":
                await health_check()
            else:
                print_error("Invalid choice. Please select 0-6.")
            
            input(f"\n{Fore.CYAN}Press Enter to continue...{Style.RESET_ALL}")
            print("\n" * 2)
            
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Interrupted. Exiting...{Style.RESET_ALL}")
            break
        except Exception as e:
            print_error(f"Error: {e}")
            import traceback
            traceback.print_exc()


async def health_check():
    """Check health of all services"""
    print_step(6, "Health Check - All Services")
    
    checks = [
        ("Backend API", f"{LOCAL_URL}/health"),
        ("Detailed Health", f"{LOCAL_URL}/health/detailed"),
    ]
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, url in checks:
            try:
                print_info(f"Checking {name}...")
                response = await client.get(url)
                
                if response.status_code == 200:
                    print_success(f"{name}: OK")
                    data = response.json()
                    if isinstance(data, dict) and "dependencies" in data:
                        print_json(data, f"  {name} Details")
                else:
                    print_error(f"{name}: Failed ({response.status_code})")
                    
            except Exception as e:
                print_error(f"{name}: Error - {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Interrupted. Goodbye!{Style.RESET_ALL}")
    except Exception as e:
        print_error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
