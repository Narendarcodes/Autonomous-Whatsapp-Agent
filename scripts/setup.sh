#!/bin/bash
# Setup script for WhatsApp AI Calendar Agent (Linux/Mac)
# For Windows, use PowerShell or convert to .bat

set -e

echo "=========================================="
echo "WhatsApp AI Calendar Agent - Setup"
echo "=========================================="
echo ""

# Check prerequisites
echo "Checking prerequisites..."

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi
echo "✅ Docker found"

# Check Docker Compose
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi
echo "✅ Docker Compose found"

# Check Ollama
if ! command -v ollama &> /dev/null; then
    echo "❌ Ollama is not installed. Please install Ollama first."
    echo "   Download from: https://ollama.ai"
    exit 1
fi
echo "✅ Ollama found"

echo ""
echo "=========================================="
echo "Setting up environment..."
echo "=========================================="
echo ""

# Create .env file if it doesn't exist
if [ ! -f backend/.env ]; then
    echo "Creating .env file from template..."
    cp backend/.env.example backend/.env
    echo "✅ .env file created"
    echo ""
    echo "⚠️  IMPORTANT: Edit backend/.env and add your credentials:"
    echo "   - WHATSAPP_TOKEN"
    echo "   - WHATSAPP_PHONE_ID"
    echo "   - WHATSAPP_VERIFY_TOKEN"
    echo "   - GOOGLE_CLIENT_ID"
    echo "   - GOOGLE_CLIENT_SECRET"
    echo "   - POSTGRES_PASSWORD"
    echo "   - REDIS_PASSWORD"
    echo ""
    read -p "Press Enter after editing .env file..."
else
    echo "✅ .env file already exists"
fi

echo ""
echo "=========================================="
echo "Pulling Ollama model..."
echo "=========================================="
echo ""

# Check if model exists
if ollama list | grep -q "mistral:7b-instruct-v0.3-q4_K_M"; then
    echo "✅ Mistral model already downloaded"
else
    echo "Downloading Mistral 7B model (this may take a while)..."
    ollama pull mistral:7b-instruct-v0.3-q4_K_M
    echo "✅ Model downloaded"
fi

echo ""
echo "=========================================="
echo "Starting Docker services..."
echo "=========================================="
echo ""

cd docker

# Build and start services
docker-compose up -d --build

echo ""
echo "Waiting for services to be healthy..."
sleep 10

# Check service status
docker-compose ps

echo ""
echo "=========================================="
echo "Setup Complete! ✅"
echo "=========================================="
echo ""
echo "Services running:"
echo "  - Backend API: http://localhost:8000"
echo "  - API Docs: http://localhost:8000/docs"
echo "  - PostgreSQL: localhost:5432"
echo "  - Redis: localhost:6379"
echo ""
echo "Next steps:"
echo "  1. Test health: curl http://localhost:8000/health"
echo "  2. Setup ngrok for webhook: ngrok http 8000"
echo "  3. Configure WhatsApp webhook in Meta Developer Portal"
echo ""
echo "To view logs: cd docker && docker-compose logs -f"
echo "To stop: cd docker && docker-compose down"
echo ""
