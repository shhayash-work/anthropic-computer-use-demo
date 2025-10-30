# Gradio版 Computer Use Demo

## 🎯 Gradioに移行した理由

Streamlitの再レンダリング問題により、長時間のタスク実行時に`sampling_loop`が中断される問題がありました。
Gradioはイベント駆動型のため、この問題が発生しません。

## 📊 主な変更点

### **画面レイアウト**
```
┌─────────────────────────────────────────┐
│  http://localhost:8501                  │
├──────────────────────┬──────────────────┤
│  仮想デスクトップ     │  チャットUI       │
│  (noVNC)            │                  │
│  Scale: 2           │  Scale: 1        │
│                     │                  │
│  ┌────────────┐     │  [Settings]      │
│  │  Firefox    │     │  - API Provider  │
│  │  Terminal   │     │  - Model         │
│  └────────────┘     │  - ...           │
│                     │                  │
│  [Screen Control]   │  [Chat]          │
│                     │  User: ...       │
│                     │  Claude: ...     │
│                     │                  │
│                     │  [Input Box]     │
└──────────────────────┴──────────────────┘
```

### **主な機能**
- ✅ チャットインターフェース（右側）
- ✅ noVNC デスクトップビュー（左側）
- ✅ サイドバー設定（Accordion内）
- ✅ リアルタイム更新（yieldなし、非同期処理）
- ✅ **再レンダリング問題を解決**

## 🚀 使用方法

### **Gradio版（デフォルト）**
```bash
docker run \
  -e API_PROVIDER="bedrock" \
  -e AWS_ACCESS_KEY_ID="..." \
  -e AWS_SECRET_ACCESS_KEY="..." \
  -e AWS_SESSION_TOKEN="..." \
  -e AWS_REGION=ap-northeast-1 \
  -e WIDTH=1366 -e HEIGHT=768 \
  -v $(pwd)/computer_use_demo:/home/computeruse/computer_use_demo/ \
  -v $HOME/.anthropic:/home/computeruse/.anthropic \
  -p 5900:5900 -p 8501:8501 -p 6080:6080 \
  -it computer-use-demo:local
```

→ http://localhost:8501 にアクセス

### **Streamlit版（旧版）**
```bash
docker run \
  ... (同じ設定) \
  --entrypoint ./entrypoint.sh \
  -it computer-use-demo:local
```

→ http://localhost:8080 にアクセス

## 🔧 開発

### **Gradioアプリの起動**
```bash
python -m computer_use_demo.gradio_app
```

### **Streamlitアプリの起動**
```bash
python -m streamlit run computer_use_demo/streamlit.py
```

## 📝 技術スタック

- **Gradio 4.0+**: WebUIフレームワーク
- **Python 3.11+**: バックエンド
- **noVNC**: 仮想デスクトップ表示
- **Anthropic API**: Claude Computer Use

## 🎵 将来の拡張

### **音声入力/出力**
```python
# Gradioの音声機能を使用
audio_input = gr.Audio(sources=["microphone"])
audio_output = gr.Audio()

def process_voice(audio):
    # Speech-to-Text (Whisper)
    text = transcribe(audio)
    # Claudeに送信
    response = sampling_loop(text)
    # Text-to-Speech (ElevenLabs / Hume AI)
    audio_response = text_to_speech(response)
    return audio_response
```

## 🐛 トラブルシューティング

### **Gradioが起動しない**
```bash
docker logs <container_id> 2>&1 | grep gradio
cat /tmp/gradio_stdout.log
```

### **noVNCが表示されない**
```bash
# x11vncが起動しているか確認
docker exec <container_id> ps aux | grep x11vnc
```

## 📚 参考資料

- [Gradio Documentation](https://gradio.app/docs/)
- [Anthropic Computer Use API](https://docs.anthropic.com/claude/docs)
- [noVNC](https://novnc.com/)


