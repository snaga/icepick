# プロジェクト構造 (structure.md)

> [!IMPORTANT]
> **仕様書と作業領域の分離ルール**:
> - **公開仕様マスター (`doc/`)**: 要件・設計・憲法・ADRなど、リポジトリ公開に必要な正式仕様書のみを配置します。プライベートな開発タスク計画や作業メモはリポジトリ公開ドキュメントから除外して管理します。

## 📁 フォルダ構成

```text
Snowflake_Query_Optimizer_PoC/
├── doc/                        # 🌍 公開用ドキュメント・仕様書マスター (Git管理)
│   ├── adr/                    # アーキテクチャ決定レコード
│   │   ├── README.md           # ADR 一覧・索引
│   │   ├── 0001-*.md           # ADR-0001: 局所LLM置換アーキテクチャ
│   │   ├── 0002-*.md           # ADR-0002: エージェント親和性設計
│   │   ├── 0003-*.md           # ADR-0003: WCMセキュア認証基盤
│   │   └── 0004-*.md           # ADR-0004: Snowflake接続分離と検証SQL生成純化
│   ├── product.md              # 製品概要・プロダクト憲法
│   ├── tech.md                 # 技術スタック仕様書
│   ├── structure.md            # プロジェクト構造（このファイル）
│   ├── requirements.md         # 要件定義書 (EARS記法)
│   └── design.md               # 詳細設計書 (Mermaid/IPO)
├── src/
│   └── icepick/                # コアパッケージ
│       ├── __init__.py         # パッケージ初期化 & バージョン公開
│       ├── cli.py              # CLIエントリポイント (typer / rich)
│       ├── config.py           # カスケード設定解決 (Config, ConfigResolver)
│       ├── exceptions.py       # 共通例外定義 (AuthenticationError, ParseError)
│       ├── parser.py           # sqlglotラッパー・Snowflake構文木基盤
│       ├── agent_context.py    # Layer 2 イントロスペクション (agent-context)
│       ├── feedback.py         # フリクションログ記録 (icepick feedback)
│       ├── credentials.py      # 認証ヘルパーエイリアス
│       ├── linter/             # 決定論的AST静的診断エンジン
│       │   ├── __init__.py
│       │   ├── base.py         # BaseRule, DiagnosticIssue, Severity
│       │   ├── engine.py       # LinterEngine (ルール実行・集約)
│       │   └── rules/          # 個別ルール実装
│       │       ├── snow_001_sargable.py            # プルーニング阻害述語
│       │       ├── snow_002_correlated.py          # 相関副クエリ (LLM連携要)
│       │       ├── snow_003_sort.py                # サブクエリ内ORDER BY
│       │       ├── snow_004_implicit_cross_join.py # 暗黙クロス結合
│       │       ├── snow_005_duplicate_scan.py      # 重複テーブルスキャン
│       │       ├── snow_006_union.py               # UNION vs UNION ALL
│       │       └── snow_007_nested_subquery.py     # インラインDerived Table
│       ├── patcher/            # AST In-place置換エンジン
│       │   ├── __init__.py
│       │   ├── in_place.py     # ASTPatcher (node.replace / node.pop)
│       │   ├── subquery_to_cte.py # SubqueryToCTE (CTE自動平坦化)
│       │   ├── splicer.py      # TextSplicer (元コード最小スプライシング)
│       │   └── agentic.py      # AgenticPatcher (LLM連携パッチ適用)
│       ├── prescription/       # 処方箋駆動最適化エンジン (ADR-0005, ADR-0006)
│       │   ├── __init__.py
│       │   ├── models.py       # Prescription, PrescriptionPlan, PrescriptionTarget
│       │   ├── engine.py       # PrescriptionEngine (diagnose, generate_diff, apply_fixes)
│       │   └── README.md       # L1 Intent (モジュール責務とWhy)
│       ├── llm/                # 局所コンテキスト抽出 & LLM連携
│       │   ├── __init__.py
│       │   ├── slicer.py       # ContextSlicer (最小ASTスライス)
│       │   ├── client.py       # LLMClient (高レベルオーケストレーション)
│       │   └── providers/      # プラガブルLLMプロバイダ基盤
│       │       ├── __init__.py # ProviderRegistry, create_provider
│       │       ├── base.py     # BaseLLMProvider 抽象基底クラス
│       │       ├── gemini.py   # GeminiProvider (Google AI Studio REST)
│       │       └── vertex.py   # VertexAIProvider (GCP ADC Bearer REST)
│       ├── diff/               # 差分生成 & パッチ適用
│       │   ├── __init__.py
│       │   ├── formatter.py    # Unified Diff生成 (format_diff, render_diff)
│       │   └── patcher.py      # apply_unified_diff, split_hunks
│       ├── verifier/           # 等価性検証 SQL 生成器 (ADR-0004)
│       │   ├── __init__.py
│       │   └── equivalence.py  # generate_verification_sql (双方向 EXCEPT)
│       ├── security/           # セキュア認証基盤
│       │   ├── __init__.py
│       │   └── credentials.py  # WCM連携、優先順位解決、Actionable Guidance
│       └── health/             # 接続診断エンジン
│           ├── __init__.py
│           └── tester.py       # ConnectionTester (icepick config test)
├── tests/                      # テストスイート (400+ tests, 100% PASS)
│   ├── conftest.py             # 共有フィクスチャ
│   ├── test_parser.py
│   ├── test_linter_engine.py
│   ├── test_rules_*.py         # 各ルール別テスト
│   ├── test_prescription_engine.py # 処方箋診断テスト
│   ├── test_prescription_diff.py   # 処方箋ID選択Diffテスト
│   ├── test_prescription_fix.py    # 処方箋インプレース適用テスト
│   ├── test_patcher.py
│   ├── test_subquery_to_cte.py
│   ├── test_slicer.py
│   ├── test_splicer.py
│   ├── test_diff.py
│   ├── test_equivalence.py     # 検証SQL生成テスト
│   ├── test_llm_client.py
│   ├── test_llm_providers.py
│   ├── test_credentials.py
│   ├── test_config.py
│   ├── test_health.py
│   ├── test_cli.py             # verify / config CLIテスト
│   ├── test_cli_diag.py        # icepick diag CLIテスト
│   ├── test_cli_diff.py        # icepick diff CLIテスト
│   ├── test_cli_fix.py         # icepick fix CLIテスト
│   ├── test_agent_context.py
│   └── test_agent_readiness.py
├── pyproject.toml              # パッケージ定義・ビルド設定 (uv / ruff / mypy)
└── README.md                   # ツール総合利用ガイド
```

## 🏷️ 命名規則
- **ファイル名**: `snake_case.py` (例: `snow_001_sargable.py`, `agent_context.py`)
- **クラス名**: `PascalCase` (例: `LinterEngine`, `BaseLLMProvider`, `EquivalenceVerifier`)
- **関数・メソッド名**: `snake_case()` (例: `generate_verification_sql()`, `slice_node()`)
- **定数名**: `UPPER_SNAKE_CASE` (例: `VALID_DIALECTS`, `PROVIDER_REGISTRY`)
- **ルールID**: `SNOW-XXX` (3桁の連番プレフィックス: `SNOW-001` 〜 `SNOW-007`)

## 🏗️ アーキテクチャの方針
- **単一責任の原則 (SRP)**:
  - 構文パース (`parser.py`)、診断 (`linter/`)、処方箋管理 (`prescription/`)、置換 (`patcher/`)、差分提示 (`diff/`)、等価性検証SQL生成 (`verifier/`)、認証管理 (`security/`)、接続診断 (`health/`) を完全に疎結合に分離する。
- **純粋関数の徹底 (Purity & Determinism)**:
  - AST診断および置換ロジック、検証SQL生成は副作用を持たない決定論的純粋関数として実装し、外部I/O（ファイル書き込み、LLM API、認証ストア）は明示的なアダプタ層に集約する。
- **Fail-Safe設計**:
  - LLMやルールが生成したパッチコードは必ず `sqlglot.parse_one` で事前バリデーションを行い、構文エラーが発生した場合は元のASTノードを絶対に破壊せず安全にフォールバックする。
- **ゼロ・クレデンシャル Snowflake 設計 (ADR-0004)**:
  - データベースへの直接接続・クエリ実行を行わず、検証用 SQL の生成に特化することで、サードパーティ製 CLI がデータベースパスワードを保持・管理するセキュリティリスクを根絶する。
- **Layer 1 Intent の永続化**:
  - 各サブパッケージ直下に `README.md`（`src/icepick/*/README.md`）を配置し、モジュールの責務・依存関係制約・設計選定理由（Why）をコードベース内で直接永続化する。

## 🛠️ インポートパターン
- プロジェクト内モジュールは絶対インポートを使用する：
  ```python
  from icepick.parser import parse_snowflake_sql
  from icepick.linter.base import DiagnosticIssue, BaseRule
  from icepick.llm.providers import create_provider
  ```
- 循環インポートを防ぐため、インターフェースやデータクラス（`base.py`）は各パッケージの最下層に配置する。

## 🔗 その他設計の決定事項
- **ADR による意思決定の永続化 (`doc/adr/`)**:
  - アーキテクチャや責任境界の大きな変更は ADR（Architecture Decision Record）として起票・合意形成を行う。
  - ADR-0001: 局所LLM置換アーキテクチャの採用
  - ADR-0002: AIエージェント親和性向上とイントロスペクション設計
  - ADR-0003: Windows Credential Manager (WCM) によるセキュア認証基盤
  - ADR-0004: Snowflake直接接続の廃止と等価性検証SQL生成への責任分離
- **Actionable Guidance による自己修正支援**:
  - エラー発生時は単なる例外出力にとどまらず、ユーザーおよび自律型 AI エージェントが直ちに自己復旧できる具体的なコマンド例（Actionable Advice）を stderr に出力する。

## 🛡️ Git除外方針 (.gitignore)
プロジェクトの健全性とセキュリティのため、以下のカテゴリを `.gitignore` で確実に除外します。
- **AIエージェント設定・プライベート設定**: `AGENTS.md`, `GEMINI.md`, `/.agent/`, `/.gemini/`, `/.claude/`
- **秘密情報・環境変数**: `.env`, `credentials.json`, `token.json`
- **ビルド・パッケージ成果物**: `dist/`, `build/`, `*.egg-info/`
- **Python キャッシュ・仮想環境**: `__pycache__/`, `*.py[cod]`, `.venv/`
- **テスト・カバレッジ出力**: `.coverage`, `coverage/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`
