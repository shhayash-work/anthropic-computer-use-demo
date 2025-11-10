AWS_ACCESS_KEY_ID={your-access-key-id}
AWS_SECRET_ACCESS_KEY={your-secret-access-key}
AWS_SESSION_TOKEN={your-session-token}
SCREENSHOT_DIR={your-screenshots-directory}

#LANGFUSE_SECRET_KEY={your-langfuse-secret-key}
#LANGFUSE_PUBLIC_KEY={your-langfuge-public-key}
#LANGFUSE_HOST="http://172.17.0.1:3000"

docker run -e API_PROVIDER="bedrock" -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN -e AWS_REGION=ap-northeast-1 -e LANGFUSE_SECRET_KEY=$LANGFUSE_SECRET_KEY -e LANGFUSE_PUBLIC_KEY=$LANGFUSE_PUBLIC_KEY -e LANGFUSE_HOST=$LANGFUSE_HOST -e STREAMLIT_SERVER_FILE_WATCHER_TYPE=none -e WIDTH=1366 -e HEIGHT=768 -v $(pwd)/computer_use_demo:/home/computeruse/computer_use_demo/ -v $HOME/.anthropic:/home/computeruse/.anthropic -v $SCREENSHOT_DIR:/home/computeruse/screenshots -v $(pwd)/conversations:/home/computeruse/conversations -p 5900:5900 -p 8501:8501 -p 6080:6080 -it computer-use-demo:local
