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

### 4. 設定管理
- **`tomli` / `tomli_w`** (Python 3.10対応) / `json`:
  - 設定ファイル（`.icepick.toml`, `icepick.json`）のパースおよびシリアライズ（方言やLinterルール設定の管理）。

### 5. 外部通信ライブラリの非依存化 (ADR-0007)
- **ゼロ・ネットワーク依存**:
  - ADR-0007 に基づき、内部 LLM 呼び出し（`httpx`, `google-auth`）を完全に排除。外部ネットワーク通信を一切行わない完全オフライン・高速ローカル動作を実現。

## 🧪 テストツール
- **`pytest`**: ユニットテスト・結合テスト実行フレームワーク（326+ テスト、100% PASS）。
- **`pytest-cov`**: テストカバレッジ測定（目標 90% 以上維持）。
- **デトロイト派（Classical TDD / 状態検証）**:
  - AST変換、ルール診断、Diff生成、検証SQL生成など純粋関数・決定論的コンポーネントに対する実オブジェクト状態検証。
- **ロンドン派（Mockist TDD / 振る舞い検証）**:
  - CLIエントリポイント（Typer CliRunner）に対する検証。

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
- **完全ローカル・オフライン動作 (ADR-0007)**:
  - CLI 内部から外部 API（Gemini / Vertex AI REST API 等）への通信は一切行わず、LLM 推論やピンポイントリライトは呼び出し元の AI コーディングエージェントおよび `ast-digger` に委譲する（機密 SQL 完全保護設計）。
