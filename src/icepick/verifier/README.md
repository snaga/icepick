# Verifier Module (`icepick.verifier`)

## 1. 責務 (Intent / Responsibility)
- 最適化前後のクエリが数学的・集合論的に100%同一の結果を返すことを証明する決定論的等価性検証 SQL 生成エンジンを提供します。
- 双方向 `EXCEPT` クエリ（`orig EXCEPT opt` および `opt EXCEPT orig` の UNION ALL）を決定論的に構築し、差分行出力または件数集約（`--count-only`）クエリを出力します。
- 元クエリおよび最適化クエリに対していかなる破壊的変更も加えない非破壊性を保証します。

## 2. 依存制約 (Dependency Boundaries)
- **依存性**: `sqlglot` のみ（完全ローカル・副作用ゼロ）。
- **ゼロ・クレデンシャル ＆ データベース接続の完全排除 (ADR-0004)**:
  - Snowflake データベースへの直接接続、ドライバ（`snowflake-connector-python` 等）依存、および認証情報（パスワード・キー）の保持・要求を完全に撤廃。
  - 生成された検証用 SQL は、公式 `snow CLI`（例: `icepick verify orig.sql opt.sql | snow sql -f -`）や社内 CI/CD パイプラインに委譲して安全に実行・判定します。
- **純粋性**: クエリ生成ロジック（`generate_verification_sql`）は副作用を持たない決定論的純粋関数として実装します。
