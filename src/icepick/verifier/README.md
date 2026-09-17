# icepick.verifier モジュール境界仕様 (L1 Intent)

## 1. 責務 (Responsibilities & ADR-0004 準拠)
`icepick.verifier` モジュールは、最適化前後のクエリが数学的・集合論的に 100% 同一の結果を返すことを証明するための、決定論的な等価性検証用 SQL を生成する。

**ADR-0004 準拠の設計方針**:
- **Snowflake 接続の完全分離**:
  - Snowflake への直接接続・ドライバ依存（`snowflake-connector-python`）・認証情報（アカウント、ユーザー、パスワード、ロール等）の管理を完全に撤廃。
  - ツールの責務を「等価性検証用 SQL の決定論的生成（Verify Generator）」に純化し、完全ゼロ・クレデンシャルを実現。
- **実行責任の委譲 (UNIX 哲学)**:
  - クエリ実行そのものは、Snowflake 公式の `snow CLI` (`snow sql -f verify.sql`) や社内 CI/CD パイプライン、dbt 等に委譲する。
  - AI エージェントやシェルスクリプトから標準出力経由でパイプ連携（`icepick verify orig.sql opt.sql | snow sql -f -`）可能な高い親和性を提供。

主な機能・責務：
- **双方向 EXCEPT 検証 SQL 生成 (`generate_verification_sql`, `EquivalenceVerifier`)**:
  - `orig EXCEPT opt`（元クエリにあって最適化後に消失した行）および `opt EXCEPT orig`（最適化後に意図せず増殖した行）の双方向差分を検出する CTE 構成 SQL を構築。
- **検証モードのサポート**:
  - **標準モード**: 差分が存在する場合にその実レコードと差分種別（`diff_type`）を出力。
  - **カウントモード (`count_only=True`)**: 差分件数のみを集計して出力し、大規模データセットでの検証効率を向上。
- **非破壊性の保証**:
  - 入力された元クエリおよび最適化クエリに対していかなる破壊的変更も加えない。

## 2. 依存関係の制約 (Dependency Constraints)
- **許可される依存ライブラリ**:
  - Python 標準ライブラリ: `dataclasses`, `logging`
- **禁止される依存**:
  - Snowflake コネクタ（`snowflake-connector-python` 等）や PEP 249 DB-API ライブラリへの依存は厳格に禁止。
  - 外部ネットワーク通信ライブラリへの依存、および直接的な SQL 実行処理の包含を禁止。
  - `icepick.cli` への逆依存を禁止。

## 3. 純粋性と決定論性 (Purity & Determinism)
- クエリ生成処理は内部状態や外部環境に依存せず、同一の入力に対して常に同一の SQL 文字列を出力する完全決定論的な純粋関数として実装する。
