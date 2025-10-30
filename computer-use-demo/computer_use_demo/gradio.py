#!/usr/bin/env python3
"""
Gradio-based Computer Use Demo
Replaces Streamlit to avoid re-rendering issues
"""
import asyncio
import base64
import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Any, cast

import gradio as gr

# ロガー設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/gradio.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
from anthropic import APIResponse
from anthropic.types.beta import BetaMessage, BetaTextBlock, BetaToolUseBlock

from computer_use_demo.loop import (
    APIProvider,
    sampling_loop,
)
from computer_use_demo.tools import ToolResult, ToolVersion
from typing import get_args, cast

# グローバルで実行中のスレッドを管理
_active_thread = None
_active_thread_lock = threading.Lock()

# デフォルトモデル名の定義（streamlit.pyから）
PROVIDER_TO_DEFAULT_MODEL_NAME: dict[APIProvider, str] = {
    APIProvider.ANTHROPIC: "claude-sonnet-4-5-20250929",
    APIProvider.BEDROCK: "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    APIProvider.VERTEX: "claude-sonnet-4-5@20250929",
}

# ストレージディレクトリ
STORAGE_DIR = Path.home() / ".anthropic"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

def load_from_storage(key: str) -> str | None:
    """ストレージからデータを読み込む"""
    try:
        file_path = STORAGE_DIR / f"{key}.txt"
        if file_path.exists():
            return file_path.read_text().strip()
    except Exception:
        pass
    return None

def save_to_storage(key: str, value: str) -> None:
    """ストレージにデータを保存する"""
    try:
        file_path = STORAGE_DIR / f"{key}.txt"
        file_path.write_text(value)
    except Exception:
        pass

def validate_auth(provider: APIProvider, api_key: str | None) -> str | None:
    """認証情報を検証"""
    if provider == APIProvider.ANTHROPIC:
        if not api_key:
            return "❌ Anthropic API Key is required"
    elif provider == APIProvider.BEDROCK:
        import boto3
        if not os.getenv("AWS_REGION"):
            return "❌ AWS_REGION environment variable is required for Bedrock"
    elif provider == APIProvider.VERTEX:
        if not os.getenv("CLOUD_ML_REGION"):
            return "❌ CLOUD_ML_REGION environment variable is required for Vertex"
    return None

# グローバル状態管理
class AppState:
    def __init__(self):
        self.messages = []
        self.tools = {}
        self.responses = {}
        
        # 環境変数から設定を読み込む
        provider_str = os.getenv("API_PROVIDER", "anthropic")
        self.provider = APIProvider(provider_str)
        
        # モデル: 環境変数 > デフォルト
        env_model = os.getenv("ANTHROPIC_MODEL")
        if env_model:
            self.model = env_model
        else:
            self.model = PROVIDER_TO_DEFAULT_MODEL_NAME[self.provider]
        
        # API認証情報: ストレージ > 環境変数
        self.api_key = load_from_storage("api_key") or os.getenv("ANTHROPIC_API_KEY", "")
        
        # その他の設定
        self.custom_system_prompt = load_from_storage("system_prompt") or ""
        self.only_n_most_recent_images = 3
        self.hide_images = False
        
        # Tool Version: Bedrockと互換性のある最新版を使用
        # computer_use_20250429はBedrockでサポートされていないため、20250124を使用
        self.tool_version = cast(ToolVersion, "computer_use_20250124")
        
        self.output_tokens = 16384
        self.max_output_tokens = 16384
        self.thinking = False
        self.thinking_budget = 1000
        self.token_efficient_tools_beta = False
        self.auth_validated = False
        self.hide_warning = os.getenv("HIDE_WARNING", "true").lower() == "true"

state = AppState()


def save_conversation(user_message: str):
    """会話履歴をJSONファイルに保存"""
    try:
        # 保存ディレクトリの作成
        save_dir = Path("/home/computeruse/conversations")
        save_dir.mkdir(parents=True, exist_ok=True)
        
        # 会話データの構築
        conversation = {
            "timestamp": datetime.now().isoformat(),
            "user_message": user_message,
            "message_count": len(state.messages),
            "last_messages": state.messages[-10:] if len(state.messages) > 10 else state.messages,  # 最新10件のみ
            "tools_summary": {
                k: {
                    "has_output": bool(v.output),
                    "has_error": bool(v.error),
                    "has_screenshot": bool(v.base64_image),
                    "output_preview": v.output[:200] if v.output else None,
                    "error": v.error[:200] if v.error else None,
                }
                for k, v in list(state.tools.items())[-20:]  # 最新20件のツール結果のみ
            }
        }
        
        # JSONファイルに保存
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = save_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(conversation, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Conversation saved to {filepath}")
    except Exception as e:
        logger.error(f"Failed to save conversation: {type(e).__name__}: {str(e)}")


def format_message_for_display(message: dict) -> str:
    """メッセージを表示用にフォーマット"""
    role = message.get("role", "")
    content = message.get("content", "")
    
    if isinstance(content, str):
        return f"**{role.upper()}**: {content}"
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(f"**{role.upper()}**: {block.get('text', '')}")
                elif block.get("type") == "tool_use":
                    parts.append(f"**TOOL USE**: {block.get('name', '')}\n```json\n{block.get('input', '')}\n```")
                elif block.get("type") == "tool_result":
                    tool_id = block.get("tool_use_id", "")
                    if tool_id in state.tools:
                        tool_result = state.tools[tool_id]
                        if tool_result.output:
                            parts.append(f"**TOOL OUTPUT**: {tool_result.output}")
                        if tool_result.error:
                            parts.append(f"**TOOL ERROR**: {tool_result.error}")
                        if tool_result.base64_image and not state.hide_images:
                            # 画像は別途処理
                            pass
        return "\n\n".join(parts) if parts else ""
    return str(content)


def get_vnc_iframe():
    """noVNC iframeのHTML（高さ80vh）"""
    return """
    <div style="width: 100%; height: 80vh; position: relative;">
        <iframe
            src="http://127.0.0.1:6080/vnc.html?&resize=scale&autoconnect=1&view_only=1&reconnect=1&reconnect_delay=2000"
            style="width: 100%; height: 100%; border: none;"
            allow="fullscreen"
        ></iframe>
        <button
            onclick="var iframe = this.previousElementSibling; 
                     var src = iframe.src; 
                     if(src.includes('view_only=1')) {
                         iframe.src = src.replace('view_only=1', 'view_only=0'); 
                         this.innerText = 'Screen Control (ON)';
                     } else {
                         iframe.src = src.replace('view_only=0', 'view_only=1'); 
                         this.innerText = 'Screen Control (OFF)';
                     }"
            style="position: absolute; top: 10px; right: 10px; z-index: 1000; 
                   padding: 8px 16px; background: #007bff; color: white; 
                   border: none; border-radius: 4px; cursor: pointer;"
        >
            Screen Control (OFF)
        </button>
    </div>
    """


def chat_fn(message: str, history: list):
    """チャット処理（リアルタイム表示対応）- Streamlitの実装を移行"""
    if not message.strip():
        yield history, ""
        return
    
    # ユーザーメッセージを追加
    state.messages.append({"role": "user", "content": message})
    # 履歴に追加（Streamlitのように個別のメッセージとして）
    history.append((message, None))
    yield history, ""
    
    # 応答を蓄積するバッファ（スレッドセーフ）
    response_parts = []
    
    # sampling_loop実行（Streamlitと同じコールバック構造）
    def output_callback(content_block):
        """Claudeの応答を受信（Streamlitの_render_message相当）"""
        if isinstance(content_block, dict):
            if content_block.get("type") == "text":
                text = content_block.get("text", "")
                response_parts.append(f"**Claude:** {text}")
            elif content_block.get("type") == "thinking":
                thinking = content_block.get("thinking", "")
                response_parts.append(f"**[Thinking]**\n\n{thinking}")
            elif content_block.get("type") == "tool_use":
                tool_name = content_block.get("name", "")
                tool_input = content_block.get("input", {})
                response_parts.append(f"**Tool Use:** `{tool_name}`\n```json\n{tool_input}\n```")
        elif isinstance(content_block, str):
            response_parts.append(content_block)
    
    def tool_output_callback(tool_output: ToolResult, tool_id: str):
        """ツール実行結果を受信（Streamlitの_tool_output_callback相当）"""
        state.tools[tool_id] = tool_output
        
        # ツール結果を応答に追加（リアルタイム表示）
        if tool_output.output:
            # 出力が長い場合は最初の500文字のみ表示
            output_preview = tool_output.output[:500]
            if len(tool_output.output) > 500:
                output_preview += f"\n... ({len(tool_output.output)} chars total)"
            response_parts.append(f"✅ **Tool Output:**\n```\n{output_preview}\n```")
        
        if tool_output.error:
            response_parts.append(f"❌ **Tool Error:**\n```\n{tool_output.error}\n```")
        
        if tool_output.base64_image and not state.hide_images:
            response_parts.append(f"**Screenshot:**\n\n![Screenshot](data:image/png;base64,{tool_output.base64_image})")
    
    def api_response_callback(request, response, response_id):
        """API応答を記録（Streamlitの_api_response_callback相当）"""
        state.responses[response_id] = (request, response)
    
    try:
        global _active_thread, _active_thread_lock
        
        # 既存のスレッドがあれば終了を待つ（タイムアウト付き）
        with _active_thread_lock:
            if _active_thread and _active_thread.is_alive():
                logger.info("Waiting for previous thread to finish...")
                _active_thread.join(timeout=1.0)  # 1秒待機
        
        # sampling_loopを別スレッドで実行
        result_container = {"messages": None, "error": None, "done": False}
        
        def run_sampling_loop():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                updated_messages = loop.run_until_complete(sampling_loop(
                    system_prompt_suffix=state.custom_system_prompt,
                    model=state.model,
                    provider=state.provider,
                    messages=state.messages,
                    output_callback=output_callback,
                    tool_output_callback=tool_output_callback,
                    api_response_callback=api_response_callback,
                    api_key=state.api_key,
                    only_n_most_recent_images=state.only_n_most_recent_images,
                    tool_version=state.tool_version,
                    max_tokens=state.output_tokens,
                    thinking_budget=state.thinking_budget if state.thinking else None,
                    token_efficient_tools_beta=state.token_efficient_tools_beta,
                ))
                
                loop.close()
                result_container["messages"] = updated_messages
            except Exception as e:
                result_container["error"] = e
            finally:
                result_container["done"] = True
        
        # バックグラウンドスレッドで実行
        thread = threading.Thread(target=run_sampling_loop, daemon=True)
        thread.start()
        
        # スレッドをグローバル変数に保存
        with _active_thread_lock:
            _active_thread = thread
        
        # リアルタイム更新ループ（Streamlitのように個別メッセージとして表示）
        last_response_count = 0
        while not result_container["done"]:
            if len(response_parts) > last_response_count:
                # 新しい応答を個別に追加
                for i in range(last_response_count, len(response_parts)):
                    history.append((None, response_parts[i]))
                last_response_count = len(response_parts)
                yield history, ""
            time.sleep(0.5)  # 0.5秒ごとに更新
        
        # 最終結果を反映
        if result_container["error"]:
            raise result_container["error"]
        
        state.messages = result_container["messages"]
        
        # 会話履歴をJSONで保存
        save_conversation(message)
        
        # 残りのresponse_partsがある場合は追加
        if len(response_parts) > last_response_count:
            for i in range(last_response_count, len(response_parts)):
                history.append((None, response_parts[i]))
        
        # response_partsが空の場合は最後のメッセージから取得
        if not response_parts:
            last_message = state.messages[-1] if state.messages else None
            if last_message and last_message.get("role") == "assistant":
                response_text = format_message_for_display(last_message)
                history.append((None, response_text))
            else:
                # エラー
                history.append((None, "⚠️ No response from Claude. Check logs:\n`docker exec <container> tail /tmp/computer_use_demo.log`"))
        
    except Exception as e:
        import traceback
        import logging
        logging.error(f"Chat function error: {type(e).__name__}: {str(e)}", exc_info=True)
        error_msg = f"❌ **Error:** `{type(e).__name__}`\n\n```\n{str(e)}\n```\n\n**Traceback:**\n```\n{traceback.format_exc()}\n```"
        history.append((None, error_msg))
    
    yield history, ""


def reset_fn():
    """状態をリセット"""
    state.messages = []
    state.tools = {}
    state.responses = {}
    
    # Xvfbとtint2をリセット
    subprocess.run("pkill Xvfb; pkill tint2", shell=True)
    subprocess.run("./start_all.sh", shell=True)
    
    return [], ""


# 警告メッセージ
WARNING_TEXT = """⚠️ **Security Alert**

Computer use is a beta feature. Please be aware that computer use poses unique risks that are distinct from standard API features or chat interfaces. These risks are heightened when using computer use to interact with the internet. To minimize risks, consider taking precautions such as:

1. Use a dedicated virtual machine or container with minimal privileges to prevent direct system attacks or accidents.
2. Avoid giving the model access to sensitive data, such as account login information, to prevent information theft.
3. Limit internet access to an allowlist of domains to reduce exposure to malicious content.
4. Ask a human to confirm decisions that may result in meaningful real-world consequences as well as any tasks requiring affirmative consent, such as accepting cookies, executing financial transactions, or agreeing to terms of service.

In some circumstances, Claude will follow commands found in content even if it conflicts with the user's instructions. For example, instructions on webpages or contained in images may override user instructions or cause Claude to make mistakes. We suggest taking precautions to isolate Claude from sensitive data and actions to avoid risks related to prompt injection.

Finally, please inform end users of relevant risks and obtain their consent prior to enabling computer use in your own products.
"""

# Gradio UI構築
with gr.Blocks(title="Claude Computer Use Demo", theme=gr.themes.Soft(), css="""
    .gradio-container { max-width: 100% !important; padding: 0 !important; }
    .header-container {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2rem 3rem;
        margin: 0;
        border-radius: 0;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    }
    .header-title {
        color: white;
        font-size: 2rem;
        font-weight: 700;
        margin: 0 0 0.5rem 0;
        letter-spacing: -0.5px;
    }
    .header-description {
        color: rgba(255, 255, 255, 0.9);
        font-size: 1rem;
        margin: 0;
        font-weight: 400;
    }
    .settings-button {
        background: rgba(255, 255, 255, 0.2) !important;
        border: 1px solid rgba(255, 255, 255, 0.3) !important;
        color: white !important;
        font-size: 1.5rem !important;
        padding: 0.5rem 1rem !important;
        border-radius: 8px !important;
        transition: all 0.3s ease !important;
    }
    .settings-button:hover {
        background: rgba(255, 255, 255, 0.3) !important;
        transform: scale(1.05);
    }
    #vnc-container { height: 80vh !important; }
    #chat-container { height: 80vh !important; }
    
    /* チャットセクションのスタイル */
    #chat-container .gradio-chatbot {
        border: 1px solid #e9ecef;
        border-radius: 0;
        box-shadow: inset 0 2px 4px rgba(0,0,0,0.03);
        height: calc(80vh - 120px) !important;  /* 仮想OS画面の高さに合わせる（入力欄の高さを引く） */
    }
    
    /* 送信ボタンのスタイル */
    .send-button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
        border: none !important;
        color: white !important;
        font-size: 1.5rem !important;
        font-weight: bold !important;
        transition: all 0.3s ease !important;
        border-radius: 8px !important;
        height: 48px !important;  /* 入力欄と同じ高さ */
    }
    
    .send-button:hover {
        background: linear-gradient(135deg, #764ba2 0%, #667eea 100%) !important;
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4) !important;
    }
    
    .send-button:active {
        transform: translateY(0);
    }
    
    /* メッセージ入力欄のスタイル */
    .message-input textarea, .message-input input {
        border: 2px solid #e9ecef !important;
        border-radius: 8px !important;
        padding: 0.75rem 1rem !important;
        transition: border-color 0.3s ease !important;
        height: 48px !important;  /* 送信ボタンの高さに合わせる */
        resize: none !important;
        line-height: 1.5 !important;
    }
    
    .message-input textarea:focus, .message-input input:focus {
        border-color: #667eea !important;
        box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1) !important;
    }
""") as demo:
    # ヘッダー
    with gr.Row(elem_classes="header-container"):
        with gr.Column(scale=10):
            gr.HTML("""
                <div class="header-title">Claude Computer Use Demo</div>
                <div class="header-description">
                    Anthropic Claudeを使用してコンピューターを操作するデモです。左側の仮想OS画面でClaudeの操作を確認できます。
                </div>
            """)
        with gr.Column(scale=1, min_width=100):
            settings_btn = gr.Button("⚙️", elem_classes="settings-button", size="sm")
    
    # 警告メッセージ（HIDE_WARNINGが設定されていない場合のみ表示）
    if not state.hide_warning:
        gr.Markdown(f"⚠️ {WARNING_TEXT}")
    
    # 認証エラーメッセージ
    auth_error_msg = gr.Markdown("", visible=False)
    auth_error = validate_auth(state.provider, state.api_key)
    if auth_error:
        auth_error_msg = gr.Markdown(auth_error, visible=True)
        state.auth_validated = False
    else:
        state.auth_validated = True
    
    # 設定パネル（モーダル）
    with gr.Accordion("⚙️ Settings", open=False, visible=False) as settings_panel:
        provider_dropdown = gr.Dropdown(
            choices=[p.value for p in APIProvider],
            value=state.provider.value,
            label="API Provider",
        )
        model_input = gr.Textbox(
            value=state.model,
            label="Model",
        )
        # API KeyはAnthropicの場合のみ表示
        api_key_input = gr.Textbox(
            value=state.api_key if state.provider == APIProvider.ANTHROPIC else "",
            label="Claude API Key",
            type="password",
            visible=(state.provider == APIProvider.ANTHROPIC),
        )
        images_num = gr.Slider(
            minimum=0,
            maximum=10,
            value=state.only_n_most_recent_images,
            step=1,
            label="Only send N most recent images",
        )
        custom_prompt = gr.Textbox(
            value=state.custom_system_prompt,
            label="Custom System Prompt Suffix",
            lines=3,
        )
        hide_images_checkbox = gr.Checkbox(
            value=state.hide_images,
            label="Hide screenshots",
        )
        token_efficient_checkbox = gr.Checkbox(
            value=state.token_efficient_tools_beta,
            label="Enable token-efficient tools beta",
        )
        # Tool Versionを動的に取得
        tool_versions = get_args(ToolVersion)
        tool_version_radio = gr.Radio(
            choices=list(tool_versions),
            value=state.tool_version,
            label="Tool Versions",
        )
        output_tokens_num = gr.Slider(
            minimum=1024,
            maximum=32768,
            value=state.output_tokens,
            step=1024,
            label="Max Output Tokens",
        )
        thinking_checkbox = gr.Checkbox(
            value=state.thinking,
            label="Thinking Enabled",
        )
        thinking_budget_num = gr.Slider(
            minimum=0,
            maximum=10000,
            value=state.thinking_budget,
            step=100,
            label="Thinking Budget",
        )
        reset_btn = gr.Button("🔄 Reset", variant="primary")
    
    # メインコンテンツ
    with gr.Row(equal_height=True):
        # 左側: 仮想OSの画面 (scale: 2, 高さ80vh)
        with gr.Column(scale=2):
            gr.HTML(get_vnc_iframe(), elem_id="vnc-container")
        
        # 右側: チャットUI (scale: 1, 高さ80vh)
        with gr.Column(scale=1, elem_id="chat-container"):
            # チャットインターフェース（高さは仮想OS画面と近く揃えるためにCSSで調整）
            chatbot = gr.Chatbot(
                label="",
                height=600,  # 初期値、CSSで上書きされる
                show_copy_button=True,
            )
            
            # メッセージ入力欄と送信ボタンを横並びに
            with gr.Row():
                msg_input = gr.Textbox(
                    placeholder="Type a message to send to Claude...",
                    label="",
                    lines=1,
                    scale=9,
                    container=False,
                    elem_classes="message-input"
                )
                # モダンな送信ボタン
                send_btn = gr.Button(
                    value="➤",
                    variant="primary",
                    scale=1,
                    min_width=60,
                    elem_classes="send-button"
                )
    
    # イベントハンドラ
    # 設定ボタンのトグル
    def toggle_settings():
        return gr.update(visible=not settings_panel.visible)
    
    settings_btn.click(
        fn=lambda: gr.update(visible=True, open=True),
        outputs=settings_panel,
    )
    
    send_btn.click(
        fn=chat_fn,
        inputs=[msg_input, chatbot],
        outputs=[chatbot, msg_input],
    )
    
    msg_input.submit(
        fn=chat_fn,
        inputs=[msg_input, chatbot],
        outputs=[chatbot, msg_input],
    )
    
    reset_btn.click(
        fn=reset_fn,
        outputs=[chatbot, msg_input],
    )
    
    # 設定の更新
    def update_provider(value):
        state.provider = APIProvider(value)
        state.model = PROVIDER_TO_DEFAULT_MODEL_NAME[state.provider]
        state.auth_validated = False
        # API Key入力の表示/非表示を切り替え
        api_key_visible = (state.provider == APIProvider.ANTHROPIC)
        return state.model, gr.update(visible=api_key_visible)
    
    def update_model(value):
        state.model = value
        return value
    
    def update_api_key(value):
        state.api_key = value
        save_to_storage("api_key", value)
        state.auth_validated = False
        return value
    
    def update_images_num(value):
        state.only_n_most_recent_images = int(value)
        return value
    
    def update_custom_prompt(value):
        state.custom_system_prompt = value
        save_to_storage("system_prompt", value)
        return value
    
    def update_hide_images(value):
        state.hide_images = value
        return value
    
    def update_token_efficient(value):
        state.token_efficient_tools_beta = value
        return value
    
    def update_tool_version(value):
        state.tool_version = value
        return value
    
    def update_output_tokens(value):
        state.output_tokens = int(value)
        state.max_output_tokens = int(value)
        return value
    
    def update_thinking(value):
        state.thinking = value
        return value
    
    def update_thinking_budget(value):
        state.thinking_budget = int(value)
        return value
    
    provider_dropdown.change(update_provider, inputs=provider_dropdown, outputs=[model_input, api_key_input])
    model_input.change(update_model, inputs=model_input)
    api_key_input.change(update_api_key, inputs=api_key_input)
    images_num.change(update_images_num, inputs=images_num)
    custom_prompt.change(update_custom_prompt, inputs=custom_prompt)
    hide_images_checkbox.change(update_hide_images, inputs=hide_images_checkbox)
    token_efficient_checkbox.change(update_token_efficient, inputs=token_efficient_checkbox)
    tool_version_radio.change(update_tool_version, inputs=tool_version_radio)
    output_tokens_num.change(update_output_tokens, inputs=output_tokens_num)
    thinking_checkbox.change(update_thinking, inputs=thinking_checkbox)
    thinking_budget_num.change(update_thinking_budget, inputs=thinking_budget_num)


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=8501,
        share=False,
    )

