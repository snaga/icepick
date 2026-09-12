design_content = """# 詳細設計書 (design.md)

## 1. システムアーキテクチャ概要

本システムは、CLI経由でSnowflake SQLを受け取り、AST解析・診断・局所置換・差分提示・等価性検証のパイプラインを実行する。

```mermaid
flowchart TD
    CLI["snow_opt.cli (CLI Controller)"] --> Parser["snow_opt.parser.SQLParser"]
    Parser --> AST["sqlglot.Expression (Root AST)"]
    AST --> Linter["snow_opt.linter.LinterEngine"]
    Linter --> Rules["Rules (SNOW-001 ~ SNOW-007)"]
    Rules --> Issues["List[DiagnosticIssue]"]
    Issues --> Patcher["snow_opt.patcher.ASTPatcher"]
    Patcher -->|Rule-based| InPlace["In-place Node Replacement"]
    Patcher -->|LLM-based| Slicer["snow_opt.llm.ContextSlicer"]
    Slicer --> LLMClient["snow_opt.llm.LLMClient"]
    LLMClient --> InPlace
    Patcher -->|Subquery to CTE| CTEExt["snow_opt.patcher.SubqueryToCTE"]
    CTEExt --> InPlace
    InPlace --> Diff["snow_opt.diff.DiffFormatter"]
    Diff --> UI["Rich Terminal Diff / .patch"]
    UI --> Verifier["snow_opt.verifier.EquivalenceVerifier"]
    Verifier --> Snowflake["Snowflake DB (EXCEPT Test)"]
```

## 2. データモデル / 型定義

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Any
import sqlglot
from sqlglot import exp

class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

@dataclass
class DiagnosticIssue:
    rule_id: str                      # 例: "SNOW-001"
    rule_name: str                    # 例: "Non-Sargable Predicate"
    severity: Severity                # 重要度
    description: str                  # 診断理由・影響説明
    target_node: exp.Expression       # AST上の対象ノード
    line_number: Optional[int]        # 発生行番号
    snippet: str                      # 元のSQLコード断片
    suggested_replacement: Optional[exp.Expression] = None  # 置換案ノード
    requires_llm: bool = False        # LLMリライトが必要かどうか

@dataclass
class OptimizationResult:
    original_sql: str                 # 入力元SQL
    base_formatted_sql: str           # 正規化後SQL (Diff基準)
    optimized_sql: str                # 最適化後SQL
    issues: List[DiagnosticIssue]     # 検出された問題リスト
    unified_diff: str                 # 生成されたUnified Diffテキスト
    is_verified: Optional[bool] = None# 等価性検証の合否
```

## 3. コンポーネント詳細設計

### 3.1 `LinterEngine` (`snow_opt/linter/`)
- `BaseRule` を継承した個別ルールクラスを動的にロードして実行する。
- 各ルールは `check(ast: exp.Expression) -> List[DiagnosticIssue]` を実装する。
- 個別ルール一覧:
  - `NonSargableRule` (`SNOW-001`): `exp.EQ` 等の比較述語で、カラムへの関数適用を検出し、リテラル側変換の範囲条件ノードを `suggested_replacement` にセット。
  - `CorrelatedSubqueryRule` (`SNOW-002`): 副クエリ内のカラム参照で外側エイリアスを持つものを検出し、`requires_llm=True` をセット。
  - `RedundantSortRule` (`SNOW-003`): `exp.Subquery` / `exp.CTE` 内の `exp.Order` かつ LIMITなしを検出し、`target_node=order_node`, `suggested_replacement=None` (pop削除) をセット。
  - `NestedSubqueryRule` (`SNOW-007`): `exp.From` や `exp.Join` 内の `exp.Subquery` を検出し、CTE外出し対象としてフラグ付け。

### 3.2 `ASTPatcher` & `SubqueryToCTE` (`snow_opt/patcher/`)
- **In-place置換**:
  - `issue.suggested_replacement` が存在する場合: `issue.target_node.replace(issue.suggested_replacement)`
  - `suggested_replacement` が None の場合: `issue.target_node.pop()`
- **サブクエリのCTE平坦化 (`SubqueryToCTE`)**:
  1. 対象サブクエリの内部SELECT (`subquery.this`) を取得。
  2. 一意なCTE別名（例: `cte_<alias>_<index>`）を決定。
  3. `exp.CTE(this=inner_select, alias=exp.TableAlias(this=cte_alias))` を構築。
  4. トップレベルの `ast.args["with_"]`（存在しない場合は `ast.set("with_", exp.With(expressions=[...]))`）に追加。
  5. 元のサブクエリノードを `exp.Table(this=cte_alias, alias=original_alias)` で置換。

### 3.3 `ContextSlicer` & `LLMClient` (`snow_opt/llm/`)
- `ContextSlicer`:
  - ターゲットノード（相関サブクエリ等）と、親ノード（CTEやテーブル名）、参照されているカラムのスキーマ定義を抽出し、最小限のコンテキストMarkdownを生成。
- `LLMClient`:
  - プロンプトをLLM APIに送信し、Markdownコードブロックから置換SQLテキストを抽出。
  - `sqlglot.parse_one(llm_sql, read="snowflake")` で即座に構文検証し、構文OKなら新しいASTノードを返す。

### 3.4 `DiffFormatter` (`snow_opt/diff/`)
- `difflib.unified_diff()` をラップし、入力SQLを `sqlglot` でフォーマットした基準テキストと、最適化後テキストの差分を計算。
- インデント差異による偽陽性Diffを排除し、純粋な意味変更のみをHunkとして抽出。
- `rich.syntax.Syntax(diff, "diff")` でターミナルカラー描画。

### 3.5 `EquivalenceVerifier` (`snow_opt/verifier/`)
- 双方向 `EXCEPT` クエリを自動構築してSnowflakeで実行：
  ```sql
  WITH orig AS ( <ORIGINAL_SQL> ),
       opt AS ( <OPTIMIZED_SQL> )
  SELECT 'orig_not_in_opt' AS diff_type, COUNT(*) AS cnt FROM (SELECT * FROM orig EXCEPT SELECT * FROM opt)
  UNION ALL
  SELECT 'opt_not_in_orig' AS diff_type, COUNT(*) AS cnt FROM (SELECT * FROM opt EXCEPT SELECT * FROM orig);
  ```
- 両方の `cnt` が `0` の場合のみ `is_verified=True`。

## 4. シーケンス図（対話型リファクタリングフロー）

```mermaid
sequenceDiagram
    autonumber
    actor Dev as 開発者
    participant CLI as snow_opt.cli
    participant Parser as SQLParser
    participant Linter as LinterEngine
    participant Patcher as ASTPatcher
    participant Diff as DiffFormatter

    Dev->>CLI: snow-opt check models/batch.sql
    CLI->>Parser: parse(sql_text)
    Parser-->>CLI: AST
    CLI->>Linter: diagnose(AST)
    Linter-->>CLI: List[DiagnosticIssue]
    CLI->>Dev: 診断テーブル表示 (SNOW-001, 002, 003)

    Dev->>CLI: snow-opt fix models/batch.sql --interactive
    loop 各Issue (Hunk) ごと
        CLI->>Patcher: apply_issue(issue)
        CLI->>Diff: format_diff(orig, opt)
        Diff-->>CLI: Unified Diff (Hunk)
        CLI->>Dev: Diffプレビュー表示 [y/n/e/q]?
        Dev-->>CLI: 'y' (適用承認)
        CLI->>Patcher: commit_node_replacement()
    end
    CLI->>Dev: 最適化完了 & .patch 保存
```
"""

with open('Temp/Snowflake_Query_Optimizer_PoC/SPECS/design.md', 'w', encoding='utf-8') as f:
    f.write(design_content.strip() + '\n')
print('Successfully created design.md')
