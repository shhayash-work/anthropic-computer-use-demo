# Langfuse セットアップガイド

このガイドでは、LLMの入出力、トークン使用量、コストを記録するためのLangfuseのセットアップ方法を説明します。

## Langfuseとは

Langfuseは、LLMアプリケーションの入出力を記録・可視化するためのオープンソースツールです。以下の情報を確認できます：

- ✅ LLMへの入力プロンプトと出力
- ✅ トークン使用量（入力・出力・キャッシュ）
- ✅ コスト（自動計算）
- ✅ 途中思考（Extended Thinking）
- ✅ 実行時間
- ✅ エラー情報

## セットアップ手順

### 1. Langfuseのインストール

```bash
pip install langfuse
```

または、requirements.txtから：

```bash
pip install -r computer_use_demo/requirements.txt
```

### 2. Langfuseサーバーの起動

#### オプションA: Docker Composeを使用（推奨）

```bash
# Langfuseリポジトリをクローン
git clone https://github.com/langfuse/langfuse.git
cd langfuse

# Docker Composeで起動
docker compose up
```

起動後、http://localhost:3000 にアクセスできます。

**注意**: この方法は開発用です。本番環境では別の方法を使用してください。

#### オプションB: Langfuse Cloudを使用

https://cloud.langfuse.com でアカウントを作成します（無料プランあり）。

### 3. APIキーの取得

1. Langfuseにログイン（初回はSign upでアカウント作成）
2. プロジェクトを作成（「+ New project」をクリック）
3. 左メニューの「Settings」→「Create new API keys」をクリック
4. 以下の2つのキーをメモ：
   - **Secret Key** (sk-lf-...)
   - **Public Key** (pk-lf-...)

### 4. 環境変数の設定

プロジェクトルートに `.env` ファイルを作成（または既存のファイルに追記）：

```bash
# Langfuse設定
LANGFUSE_SECRET_KEY="sk-lf-xxxxxxxxxxxxxxxx"
LANGFUSE_PUBLIC_KEY="pk-lf-xxxxxxxxxxxxxxxx"
LANGFUSE_HOST="http://localhost:3000"  # ローカルの場合
# LANGFUSE_HOST="https://cloud.langfuse.com"  # Cloudの場合
```

**セキュリティ注意**: `.env` ファイルは `.gitignore` に追加してください。

### 5. アプリケーションの起動

通常通りアプリケーションを起動します：

```bash
# Streamlitの場合
python -m streamlit run computer_use_demo/streamlit.py

# Gradioの場合
python -m computer_use_demo.gradio
```

環境変数が正しく設定されていれば、自動的にLangfuseへのログ記録が開始されます。

## Langfuseでの確認方法

### 1. Tracesの確認

1. Langfuseダッシュボード（http://localhost:3000）にアクセス
2. 左メニューの「Traces」をクリック
3. 各セッションのログが一覧表示されます

### 2. 詳細情報の確認

各Traceをクリックすると、以下の情報が確認できます：

- **Input**: sampling_loopへの入力引数
- **Output**: 最終的な出力メッセージ
- **Usage**: トークン使用量
  - Input tokens
  - Output tokens
  - Cache tokens
- **Cost**: 自動計算されたコスト
- **Metadata**: 
  - Provider（anthropic/bedrock/vertex）
  - Model名
  - Loop iteration数
  - Stop reason
- **Duration**: 実行時間

### 3. コストの集計

- ダッシュボードで期間ごとの総コストを確認
- モデル別、プロバイダー別の集計も可能

## トラブルシューティング

### Langfuseが記録されない

1. 環境変数が正しく設定されているか確認：
   ```bash
   echo $LANGFUSE_SECRET_KEY
   echo $LANGFUSE_PUBLIC_KEY
   echo $LANGFUSE_HOST
   ```

2. Langfuseがインストールされているか確認：
   ```bash
   pip show langfuse
   ```

3. ログを確認：
   ```bash
   tail -f /tmp/computer_use_demo.log
   ```
   
   以下のようなメッセージがあれば、Langfuseは無効化されています：
   ```
   WARNING - Langfuse not installed. LLM observability disabled.
   ```

### コストが表示されない

Bedrockのモデル名（例: `global.anthropic.claude-sonnet-4-5-20250929-v1:0`）は、Langfuseが自動認識できない場合があります。

この場合、メタデータのトークン情報から手動で計算できます：

- Claude Sonnet 4.5の料金（2024年11月時点）:
  - 入力: $3 / 1M tokens
  - 出力: $15 / 1M tokens

### Langfuseサーバーに接続できない

1. Docker Composeが起動しているか確認：
   ```bash
   docker ps
   ```

2. ポート3000が使用可能か確認：
   ```bash
   curl http://localhost:3000
   ```

## 無効化する方法

Langfuseを一時的に無効化したい場合：

1. 環境変数を削除またはコメントアウト
2. または、langfuseをアンインストール：
   ```bash
   pip uninstall langfuse
   ```

アプリケーションは自動的にLangfuse無しで動作します。

## 参考リンク

- [Langfuse公式ドキュメント](https://langfuse.com/docs)
- [Langfuse GitHub](https://github.com/langfuse/langfuse)
- [Zenn記事: Langfuse による LLM アプリのログの取得](https://zenn.dev/machinelearning/articles/langfuse_usage)

