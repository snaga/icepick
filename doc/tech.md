# テクノロジースタック (tech.md)

## 🛠️ プログラミング言語
- **Python 3.10+**: 型ヒント（typing, dataclasses）、豊富なAST解析エコシステム、データエンジニアリング親和性を重視。

## 🏗️ フレームワーク & ライブラリ

### 1. コアSQL解析 & AST操作
- **`sqlglot` (v30.x+)**:
  - Snowflake方言の完全なパース、ASTトラバース（`walk` / `find_all`）、ノードのインプレース置換（`replace` / `pop`）、高精度なフォーマット出力（`to_sql`）。
  - 各種関数（`DATE`, `TO_DATE`, `DATEADD`, `QUALIFY` 等）の抽象化。

### 2. CLI & ターミナルUI
- **`rich`**:
  - 診断レポートテーブル、カラーDiffシンタックスハイライト、プログレスバー、ステータスバッジの描画。
- **`typer`** (または `click`):
  - サブコマンド（`check`, `rewrite`, `patch`, `verify`）およびオプションフラグ管理。

### 3. 差分生成
- **`difflib`** (Python標準ライブラリ):
  - Unified Diff形式（Git diff互換）の差分テキスト生成およびHunk解析。

### 4. 外部API / LLM連携（超軽量自前RESTクライアント）
- **`httpx`**:
  - 軽量・高速なHTTPクライアント。Gemini API (Google AI Studio) および Google Cloud Vertex AI の REST API (`generateContent`) を直接呼び出す。重厚な外部LLM抽象化レイヤーを排除し、CLIの高速起動と最小限の依存関係を実現。
- **`google-auth`**:
  - Vertex AI 利用時の ADC (Application Default Credentials) に基づく Google Cloud OAuth2 Bearer トークン解決。

### 5. Snowflake接続 & 検証
- **`snowflake-connector-python`**:
  - テスト環境でのクエリ実行、プロファイル（`SYSTEM$EXPLAIN_PLAN_JSON`）取得、`EXCEPT` 差分ゼロ検証。

## 🧪 テストツール
- **`pytest`**: ユニットテスト・結合テスト実行フレームワーク。
- **`pytest-cov`**: テストカバレッジ測定。
- **デトロイト派（Classical TDD）**: AST変換やDiff生成など純粋関数・決定論的コンポーネントに対する実オブジェクト検証。
- **ロンドン派（Mockist TDD）**: Snowflake接続やLLM API呼び出しなど外部I/Oに対するモック検証。

## 🔧 開発ツール
- **`uv`**: 高速パッケージマネージャー兼仮想環境管理。
- **`ruff`**: 高速リンター＆フォーマッター。
- **`mypy`**: 静的型チェッカー。

## 📦 インフラ / プラットフォーム
- **実行環境**: ローカル開発機（Windows / macOS / Linux）でのCLI実行。
- **CI/CD**: GitHub Actions / GitLab CI（PR作成時の自動診断・Diffコメントボット）。
