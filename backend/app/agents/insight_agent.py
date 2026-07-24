"""Insight Agent (L3) -- Cross Data Inference.

Phase 3's Mapping Agent proposes joins between tables and already executes
them over every row to measure overlap; this agent turns those already-
computed join statistics into readable findings: referential-integrity
gaps (rows on one side with no match on the other) and link coverage.
This is the "any index that can be built from ... linking multiple data"
case the user defined for Business Use Cases, specifically the subset
that requires the Source -> Target Mapping's executed joins to exist --
deterministic, no LLM call, same rationale as every other index agent.
"""
from app.models.schemas import BusinessIndex, SourceTargetMapping

# Below this share of orphaned rows, a mismatch is treated as normal data
# noise (e.g. a handful of test/void records) rather than a reportable gap.
_ORPHAN_RATIO_THRESHOLD = 0.05


def compute_cross_data_insights(mapping: SourceTargetMapping) -> list[BusinessIndex]:
    insights: list[BusinessIndex] = []

    for j in mapping.joins:
        total_left = j.matched_count + j.left_only_count
        total_right = j.matched_count + j.right_only_count
        left_file = j.left.split("::", 1)[0]
        right_file = j.right.split("::", 1)[0]

        if total_left and total_right:
            coverage = j.matched_count / max(total_left, total_right)
            insights.append(BusinessIndex(
                name=f"Link Coverage: {j.target_column}",
                value=f"{coverage:.0%} ({j.matched_count} linked)",
                basis=f"Rows in '{j.left}' successfully joined to '{j.right}' on '{j.target_column}'",
                sources=[left_file, right_file],
            ))

        if total_left and j.left_only_count / total_left >= _ORPHAN_RATIO_THRESHOLD:
            insights.append(BusinessIndex(
                name=f"Referential Gap: {left_file} -> {right_file}",
                value=f"{j.left_only_count} of {total_left} ({j.left_only_count / total_left:.0%})",
                basis=f"Rows in '{j.left}' whose '{j.target_column}' value has no match in '{j.right}'",
                sources=[left_file, right_file],
            ))

        if total_right and j.right_only_count / total_right >= _ORPHAN_RATIO_THRESHOLD:
            insights.append(BusinessIndex(
                name=f"Referential Gap: {right_file} -> {left_file}",
                value=f"{j.right_only_count} of {total_right} ({j.right_only_count / total_right:.0%})",
                basis=f"Rows in '{j.right}' whose '{j.target_column}' value has no match in '{j.left}'",
                sources=[left_file, right_file],
            ))

    return insights
