# Verifier Module (`icepick.verifier`)

## 1. 責務 (Intent / Responsibility)
- 最適化前後のクエリが数学的・集合論的に100%同一の結果を返すことを証明する決定論的等価性検証エンジンを提供します。
- 双方向 `EXCEPT` クエリ（`orig EXCEPT opt` および `opt EXCEPT orig`）を動的に構築し、双方向の差分件数がともに `0` 件であることを客観的に検証します。
- 元クエリおよび最適化クエリに対していかなる破壊的変更も加えない非破壊性を保証します。

## 2. 依存制約 (Dependency Boundaries)
- **依存性**: `sqlglot` のみ。
- **DB接続の抽象化**: 本モジュールは Snowflake コネクタ（`snowflake-connector-python` 等）の直接的な具象依存を持たず、PEP 249 準拠の抽象化された `connection` / `cursor` オブジェクトを引数経由で受け取ります。
- **純粋性**: クエリ生成ロジック（`build_verification_query`）は副作用を持たない純粋関数として振る舞い、検証実行（`verify`）は安全な例外ハンドリングにより呼出元をクラッシュさせない Fail-Safe 設計とします。
