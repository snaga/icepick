"""Individual AST diagnostic rules."""

from icepick.linter.rules.snow_001_sargable import NonSargableRule
from icepick.linter.rules.snow_002_correlated import CorrelatedSubqueryRule
from icepick.linter.rules.snow_003_sort import RedundantSortRule
from icepick.linter.rules.snow_004_implicit_cross_join import ImplicitCrossJoinRule
from icepick.linter.rules.snow_005_duplicate_scan import DuplicateTableScanRule
from icepick.linter.rules.snow_006_union import UnionToUnionAllRule
from icepick.linter.rules.snow_007_nested_subquery import NestedSubqueryRule
from icepick.linter.rules.snow_008_redundant_distinct import RedundantDistinctRule
from icepick.linter.rules.snow_009_qualify_flattening import QualifyFlatteningRule

__all__ = [
    "CorrelatedSubqueryRule",
    "DuplicateTableScanRule",
    "ImplicitCrossJoinRule",
    "NestedSubqueryRule",
    "NonSargableRule",
    "QualifyFlatteningRule",
    "RedundantDistinctRule",
    "RedundantSortRule",
    "UnionToUnionAllRule",
]

