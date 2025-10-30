#!/bin/bash
set -e

./start_all.sh
./novnc_startup.sh

# Gradioアプリケーションを起動（ポート8501）
python -m computer_use_demo.gradio > /tmp/gradio_stdout.log 2>&1 &

echo "✨ Computer Use Demo (Gradio) is ready!"
echo "➡️  Open http://localhost:8501 in your browser to begin"

# Keep the container running
tail -f /dev/null

