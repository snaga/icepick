# 0001. ワークフローの再構築：単一責任の原則に基づく `fix` コマンドの廃止と `rewrite` / `patch` の分離

* **ステータス**: Accepted
* **決定日**: 2026-09-12

---

## 1. 背景と課題 (Context)

従来の Icepick では、クエリの最適化適用に `icepick fix <file>` コマンドを使用していました。しかし、このコマンドは以下の複数の責務を同時に抱えており、コマンドの肥大化（Fat Command）と開発者体験の複雑化を招いていました：

1. **複数の関心事の混在**: 「AST診断」「修正ノード生成」「Diff表示（`--diff`）」「ファイル直接上書き（`--write`）」「パッチファイル保存（`--patch`）」「対話型適用（`--interactive`）」が 1 コマンドに集約され、単一責任の原則（Single Responsibility Principle: SRP）に反していた。
2. **安全境界（Safety Boundary）の曖昧さ**: 開発者や AI エージェントから見て、「このコマンドを実行したときに実ファイルが書き換えられるのかどうか」がフラグの組み合わせに依存しており、意図しないファイル変更のリスクが存在した。
3. **Unix パイプライン連携の欠如**: 修正差分を標準出力（stdout）に出力し、それを標準入力（stdin）経由で適用したり、別ツール（CI/CD、`git apply` 等）へストリーム連携する自然なインターフェースが欠けていた。

---

## 2. 決定事項 (Decision)

### ① `icepick fix` の完全廃止
`fix` コマンドを完全に廃止し、以下の直感的で単一責務を持つ 4 大コアコマンド体系に再構成します：

```text
1. icepick check <file>                 # 診断・アンチパターン検出 (Read-only)
2. icepick rewrite <file>               # 最適化コード・Diff/パッチ生成 (Read-only, stdout)
3. icepick patch <file> [patch_file]    # Diff/パッチのファイル適用 (Mutate, stdin対応)
4. icepick verify <orig> <opt>          # 適用前後のセマンティクス等価性証明 (Snowflake EXCEPT)
```

### ② `rewrite` コマンドの新設（生成の単一責務 / 非破壊）
- クエリの AST 最適化を行い、修正差分（Unified Diff）を **標準出力（stdout）** に出力します。
- 実ファイルへの変更は 1 バイトも行わない「純粋な読み取り・生成コマンド」とします。
- リダイレクト（`> query.patch`）または `-o, --output` オプションでファイル保存をサポートします。

### ③ `patch` コマンドの新設（適用の単一責務 / 唯一の破壊的操作）
- ツール内で**「実ファイルを変更する権限を持つ唯一のコマンド」**として位置づけます。
- 第 2 引数のパッチファイルが省略された場合、**`sys.stdin`（標準入力）から自動で Diff を読み込みます**。これにより、以下のスマートなパイプライン連携をサポートします：
  ```bash
  icepick rewrite models/mart.sql | icepick patch models/mart.sql
  ```
- 対話型適用（`--interactive` / `-i`）、事前確認（`--dry-run`）、非対話安全ガード（`--force` / `-f`）を `patch` コマンドの責務として統合します。

### ④ `verify` による適用後検証
- パッチ適用前クエリと適用後クエリを受け取り、Snowflake 上で双方向 EXCEPT クエリを実行して結果セットが完全に一致することを証明します。

---

## 3. 影響と評価 (Consequences)

### メリット (Positive)
- **安全境界の完全な明確化**: `check`, `rewrite`, `verify` が 100% 読み取り専用であることが保証され、ファイル変更を伴う操作が `patch` だけに限定されます。
- **Git・Unix 哲学への完全準拠**: パイプライン連携（`rewrite | patch`）や標準のリダイレクト、Git 互換パッチの取り回しが極めて直感的になります。
- **CI/CD 自動化の容易化**: PR の CI で `rewrite` して差分を PR コメントに投稿し、ローカル開発者が `patch` で承認・適用する運用が容易になります。

### トレードオフ・デメリット (Negative / Mitigation)
- **Breaking Change**: 既存の `icepick fix` に依存する CLI コマンドやテストスイート（`tests/test_cli.py`, `tests/test_agent_readiness.py`）の更新が必要となります。
  - *対策*: フェーズ 9 のタスクとして移行を計画し、テストを段階的に新コマンド体系へ更新します。
