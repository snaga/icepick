# アーキテクチャ決定レコード (ADR)

本ディレクトリは、Icepick for Snowflake における重要なアーキテクチャ・設計上の意思決定を記録するリポジトリです。

## ADR 一覧

| 番号 | タイトル | ステータス | 決定日 |
| :---: | :--- | :---: | :---: |
| [0001](0001-workflow-decomposition-rewrite-and-patch.md) | ワークフローの再構築：`fix` 廃止と `rewrite` / `patch` パイプライン分離 | Superseded (by 0006) | 2026-09-12 |
| [0002](0002-source-preserving-targeted-rewrite.md) | 差分生成ロジックの刷新：AST全行再フォーマットから元ソース書式保持型局所置換（TextSplicer）への移行 | Accepted | 2026-09-13 |
| [0003](0003-secure-credential-management-and-multi-source-precedence.md) | セキュア認証情報管理とマルチソース設定優先順位ピラミッドの確立 | Superseded (by 0007) | 2026-09-13 |
| [0004](0004-decouple-snowflake-execution-and-pure-verification-sql-generation.md) | Snowflake 直接接続の廃止と等価性検証 SQL 生成（Verify Generator）への責任分離 | Accepted | 2026-09-13 |
| [0005](0005-prescription-first-architecture-and-decoupling-diagnosis-from-rewriting.md) | 処方箋ファースト（Prescription-First）アーキテクチャの導入と診断・適用の責任分離 | Accepted | 2026-09-13 |
| [0006](0006-remove-legacy-commands-and-standardize-prescription-pipeline.md) | レガシーコマンド（check, rewrite, patch）の削除と処方箋駆動パイプラインへの純化 | Accepted | 2026-09-13 |
| [0007](0007-decouple-llm-integration-and-pure-deterministic-ast-tool-standardization.md) | LLM 連携の完全委譲と純粋決定論的 AST ツールへの純化 | Accepted | 2026-09-16 |


