# Icepick for Snowflake

Snowflake SQL向けのAST（抽象構文木）解析ベースのクエリオプティマイザ＆アンチパターンリンター CLIツール。

## 特徴
- **決定論的静的診断 (Linter)**: `sqlglot` を用いたSnowflake SQLの高速・高精度な構文解析とアンチパターン検出。
- **安全なAST In-place置換**: Non-Sargable条件のSargable化、冗長ソート削除、サブクエリのCTE平坦化。
- **局所LLM連携**: 複雑な相関副クエリに対する最小限コンテキストでの安全なリライト。
- **Unified Diff 出力**: 標準Git互換のDiffプレビューおよび対話型Hunkレビュー (`[y]/[n]/[q]`)。
- **決定論的等価性検証**: 双方向 `EXCEPT` クエリを用いた結果等価性の検証。

## インストール & セットアップ
```bash
pip install -e ".[dev]"
```

## 使用方法
```bash
# クエリの診断
icepick check path/to/query.sql

# 対話型リファクタリング
icepick fix path/to/query.sql --interactive
```
