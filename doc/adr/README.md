# アーキテクチャ決定レコード (ADR)

本ディレクトリは、Icepick for Snowflake における重要なアーキテクチャ・設計上の意思決定を記録するリポジトリです。

## ADR 一覧

| 番号 | タイトル | ステータス | 決定日 |
| :---: | :--- | :---: | :---: |
| [0001](0001-workflow-decomposition-rewrite-and-patch.md) | ワークフローの再構築：`fix` 廃止と `rewrite` / `patch` パイプライン分離 | Accepted | 2026-09-12 |
| [0002](0002-source-preserving-targeted-rewrite.md) | 差分生成ロジックの刷新：AST全行再フォーマットから元ソース書式保持型局所置換（TextSplicer）への移行 | Accepted | 2026-09-13 |
| [0003](0003-secure-credential-management-and-multi-source-precedence.md) | セキュア認証情報管理とマルチソース設定優先順位ピラミッドの確立 | Accepted | 2026-09-13 |
