#!/bin/bash

# Streamlit App Quick Start Script

echo "🚀 Starting Streamlit Batch Data Processor..."
echo ""

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "⚠️  No virtual environment found. Creating one..."
    python -m venv .venv
    echo "✅ Virtual environment created"
fi

# Activate virtual environment
echo "📦 Activating virtual environment..."
source .venv/bin/activate

# Install/upgrade requirements
# echo "📥 Installing dependencies..."
# pip install -q --upgrade pip
# pip install -q -r requirements.txt

# Check for .env file
if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  WARNING: .env file not found!"
    echo "Please create a .env file with your Groq API key:"
    echo "GROQ_API_KEY=your_api_key_here"
    echo ""
    read -p "Press Enter to continue anyway or Ctrl+C to exit..."
fi

# Create necessary directories
mkdir -p uploads output

echo ""
echo "✅ Setup complete!"
echo ""
echo "🌐 Starting Streamlit app on http://localhost:8000"
echo "💡 Press Ctrl+C to stop the server"
echo ""

# Start Streamlit
streamlit run streamlit_app.py --server.port 8000
