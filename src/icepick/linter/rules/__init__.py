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
from icepick.linter.rules.snow_010_cte_multi_reference import CteMultiReferenceRule
from icepick.linter.rules.snow_011_huge_in_list import HugeInListRule
from icepick.linter.rules.snow_012_select_star import SelectStarRule

__all__ = [
    "CorrelatedSubqueryRule",
    "CteMultiReferenceRule",
    "DuplicateTableScanRule",
    "HugeInListRule",
    "ImplicitCrossJoinRule",
    "NestedSubqueryRule",
    "NonSargableRule",
    "QualifyFlatteningRule",
    "RedundantDistinctRule",
    "RedundantSortRule",
    "SelectStarRule",
    "UnionToUnionAllRule",
]

