#!/bin/bash
# Setup script for ToolifyX TikTok Downloader Backend

echo "=========================================="
echo "ToolifyX TikTok Downloader Setup"
echo "=========================================="

# Install dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt

# Verify yt-dlp installation
echo ""
echo "Checking yt-dlp installation..."
python3 -c "import yt_dlp; print(f'yt-dlp version: {yt_dlp.version.__version__}')" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "WARNING: yt-dlp not installed properly. Run: pip install yt-dlp"
    echo "This is REQUIRED for the downloader to work."
fi

# Create .env template if not exists
if [ ! -f .env ]; then
    echo ""
    echo "Creating .env template..."
    cat > .env << 'EOF'
# Admin password for stats reset
ADMIN_PASSWORD=your_secure_password_here

# Optional: Paid API keys for fallback
# Get from https://rapidapi.com/LaurynProsacco58/api/tiktok-video-downloader-api-no-watermark
RAPIDAPI_KEY=your_rapidapi_key_here

# Get from https://scrapebadger.com
SCRAPEBADGER_KEY=your_scrapebadger_key_here
EOF
    echo ".env created. Please edit it with your actual values."
fi

echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "IMPORTANT: yt-dlp is REQUIRED. Install it with:"
echo "  pip install yt-dlp"
echo ""
echo "To start the server:"
echo "  python app_fixed.py"
echo ""
echo "Or with gunicorn (production):"
echo "  gunicorn -w 4 -b 0.0.0.0:5000 app_fixed:app"
echo ""
echo "Test the health endpoint:"
echo "  curl http://localhost:5000/health"
