# テクノロジースタック (tech.md)

## 🛠️ プログラミング言語
- **Python 3.10+**: 型ヒント（typing, dataclasses）、豊富なAST解析エコシステム、データエンジニアリング親和性を重視。`mypy --strict` 準拠の静的型付け。

## 🏗️ フレームワーク & ライブラリ

### 1. コアSQL解析 & AST操作
- **`sqlglot` (v30.x+)**:
  - Snowflake方言の完全なパース、ASTトラバース（`walk` / `find_all`）、ノードのインプレース置換（`replace` / `pop`）、高精度なフォーマット出力（`to_sql`）。
  - 各種関数（`DATE`, `TO_DATE`, `DATEADD`, `QUALIFY` 等）の抽象化。

### 2. CLI & ターミナルUI
- **`typer`**:
  - サブコマンド（`diag`, `diff`, `fix`, `verify`, `config`, `feedback`, `agent-context`）およびオプションフラグ管理。
- **`rich`**:
  - 診断レポートテーブル、カラーDiffシンタックスハイライト、プログレスバー、ステータスバッジの描画。

### 3. 差分生成 & パッチ適用
- **`difflib`** (Python標準ライブラリ):
  - Unified Diff形式（Git diff互換）の差分テキスト生成およびHunk解析。

### 4. セキュア認証 & 設定管理
- **Windows Credential Manager (WCM) / `cmdkey`**:
  - Windows資格情報マネージャーネイティブAPI / CLIによる機密情報（`icepick:gemini_api_key`）のセキュア保管。
- **`tomli` / `tomli_w`** (Python 3.10対応) / `json`:
  - 設定ファイル（`.icepick.toml`, `icepick.json`）のパースおよびシリアライズ。

### 5. HTTP通信 & クラウド認証
- **`httpx`**:
  - 軽量・高速なHTTPクライアント。Gemini API (Google AI Studio) および Google Cloud Vertex AI の REST API (`generateContent`) を直接呼び出す。重厚な外部LLM抽象化レイヤーを排除し、CLIの高速起動と最小限の依存関係を実現。
- **`google-auth`**:
  - Vertex AI 利用時の ADC (Application Default Credentials) に基づく Google Cloud OAuth2 Bearer トークン解決。

## 🧪 テストツール
- **`pytest`**: ユニットテスト・結合テスト実行フレームワーク（375+ テスト、100% PASS）。
- **`pytest-cov`**: テストカバレッジ測定（目標 90% 以上維持）。
- **デトロイト派（Classical TDD / 状態検証）**:
  - AST変換、ルール診断、Diff生成、検証SQL生成など純粋関数・決定論的コンポーネントに対する実オブジェクト状態検証。
- **ロンドン派（Mockist TDD / 振る舞い検証）**:
  - LLM API呼び出し、外部プロセス（gcloud / cmdkey）、CLIエントリポイント（Typer CliRunner）に対するモック検証。

## 🔧 開発ツール
- **`uv`**: 超高速パッケージマネージャー兼仮想環境管理。
- **`ruff`**: 高速リンター＆フォーマッター（PEP 8、flake8、isort統合）。
- **`mypy`**: 静的型チェッカー（`strict = true` 完全適合）。

## 📦 インフラ / プラットフォーム
- **実行環境**: ローカル開発機（Windows 11 / macOS / Linux）でのCLI実行。
- **CI/CD**: GitHub Actions / GitLab CI（PR作成時の自動診断・Diffコメントボット）。
- **Snowflake連携**:
  - データベース直接接続を行わない**ゼロ・クレデンシャル設計**（ADR-0004）。
  - 公式 `snow CLI`（Snowflake CLI）とのパイプライン連携（`icepick verify orig.sql opt.sql | snow sql -f -`）を標準サポート。

## 🔗 外部API / サービス
- **Google Gemini API (Google AI Studio)**:
  - モデル: `gemini-2.5-flash` 等
  - 認証方式: API キー（WCM: `icepick:gemini_api_key` または環境変数）
  - エンドポイント: `https://generativelanguage.googleapis.com/v1beta/`
- **Google Cloud Vertex AI**:
  - モデル: `gemini-2.5-flash` 等
  - 認証方式: Application Default Credentials (ADC) / `gcloud auth application-default login`
  - エンドポイント: `https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent`
