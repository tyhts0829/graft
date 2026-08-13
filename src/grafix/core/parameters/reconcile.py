"""
Purpose:
    保存済みgroupと新たに観測したgroupの安全な再リンク候補を副作用なしで計画する。
Use when:
    source編集でsite_idが変わったparameter調整の自動対応やorphan判定を扱う場合。
Constraints:
    - 異なるoperation間を対応させず、一対一で十分な根拠があるmatchだけを自動採用する。
    - 同点や同じ旧groupへの競合は推測で解決せず、候補付きorphanとして残す。
    - live storeを変更せず、mappingの適用はreconcile_opsへ委ねる。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from grafix.core.value_validation import exact_integer

from .identity import GroupKey, group_key
from .key import ParameterKey
from .meta import ParamMeta


@dataclass(frozen=True, slots=True)
class GroupFingerprint:
    """(op, site_id) グループの特徴量。"""

    op: str
    args: frozenset[str]
    kind_by_arg: Mapping[str, str]
    label: str | None

    def __post_init__(self) -> None:
        """引数 kind の owned immutable copy を保持する。"""

        object.__setattr__(
            self,
            "kind_by_arg",
            MappingProxyType(dict(self.kind_by_arg)),
        )


ReconcileOrphanReason = Literal["tie", "claimed"]


@dataclass(frozen=True, slots=True)
class ReconcileOrphan:
    """自動対応を確定できなかった fresh group と旧候補を表す。

    ``new_group`` は現在のコードで観測された group、
    ``candidate_old_groups`` は保存データ側の候補である。候補は fresh 側の
    同点首位、または同じ stale を同点首位にした fresh 間の競合に限定する。
    """

    new_group: GroupKey
    candidate_old_groups: tuple[GroupKey, ...]
    score: int
    reason: ReconcileOrphanReason


@dataclass(frozen=True, slots=True)
class ReconcilePlan:
    """自動 migrate と手動判断待ち orphan をまとめた再リンク計画。"""

    matches: tuple[tuple[GroupKey, GroupKey], ...]
    orphans: tuple[ReconcileOrphan, ...]

    def mapping(self) -> dict[GroupKey, GroupKey]:
        """``old_group -> new_group`` のコピーを返す。"""

        return dict(self.matches)


def build_group_fingerprints(
    snapshot: Mapping[ParameterKey, tuple[ParamMeta, object, int, str | None]],
) -> dict[GroupKey, GroupFingerprint]:
    """snapshot から (op, site_id) -> fingerprint を生成して返す。"""

    args_by_group: dict[GroupKey, set[str]] = {}
    kinds_by_group: dict[GroupKey, dict[str, str]] = {}
    label_by_group: dict[GroupKey, str | None] = {}

    for key, (meta, _state, _ordinal, label) in snapshot.items():
        group = (key.op, key.site_id)
        args_by_group.setdefault(group, set()).add(key.arg)
        kinds_by_group.setdefault(group, {})[key.arg] = meta.kind
        if group not in label_by_group:
            label_by_group[group] = label

    out: dict[GroupKey, GroupFingerprint] = {}
    for group, args in args_by_group.items():
        out[group] = GroupFingerprint(
            op=group[0],
            args=frozenset(args),
            kind_by_arg=kinds_by_group.get(group, {}),
            label=label_by_group.get(group),
        )
    return out


def _match_score(a: GroupFingerprint, b: GroupFingerprint) -> int:
    """fingerprint 間の類似度スコアを返す（大きいほど近い）。"""

    if a.op != b.op:
        return -(10**9)

    score = 0

    # label は衝突しうるが、ある場合は強いヒントとして使う。
    if a.label is not None and b.label is not None and a.label == b.label:
        score += 100

    shared_args = a.args & b.args
    score += 10 * len(shared_args)

    kind_matches = 0
    for arg in shared_args:
        if a.kind_by_arg.get(arg) == b.kind_by_arg.get(arg):
            kind_matches += 1
    score += 5 * kind_matches

    if a.args == b.args:
        score += 30

    return score


def plan_group_reconciliation(
    *,
    stale: Sequence[GroupKey],
    fresh: Sequence[GroupKey],
    fingerprints: Mapping[GroupKey, GroupFingerprint],
    min_score: int = 15,
) -> ReconcilePlan:
    """stale/fresh の安全な 1:1 対応と未確定候補を返す。

    Notes
    -----
    - 対応付けは op 単位で行う（op が異なるものは候補にしない）。
    - 同点首位は誤マッチを避けて orphan とし、候補を失わない。
    - 同じ stale を複数 fresh が首位に選んだ場合は、最高点が一意なときだけ
      自動採用する。最高点も同点なら該当 fresh を orphan とする。
    """

    stale_list = sorted(group_key(group, name="stale group") for group in stale)
    fresh_list = sorted(group_key(group, name="fresh group") for group in fresh)
    minimum_score = exact_integer(min_score, name="min_score")

    stale_by_op: dict[str, list[GroupKey]] = {}
    for op, site_id in stale_list:
        stale_by_op.setdefault(op, []).append((op, site_id))

    candidates: list[tuple[int, GroupKey, GroupKey]] = []
    orphans: list[ReconcileOrphan] = []

    for fresh_group in fresh_list:
        fresh_fp = fingerprints.get(fresh_group)
        if fresh_fp is None:
            continue

        scored: list[tuple[int, GroupKey]] = []

        for stale_group in stale_by_op.get(fresh_fp.op, []):
            stale_fp = fingerprints.get(stale_group)
            if stale_fp is None:
                continue

            score = _match_score(stale_fp, fresh_fp)
            if score < minimum_score:
                continue

            scored.append((score, stale_group))

        if not scored:
            continue

        best_score = max(score for score, _group in scored)
        best_stale = tuple(sorted(group for score, group in scored if score == best_score))
        if len(best_stale) != 1:
            orphans.append(
                ReconcileOrphan(
                    new_group=fresh_group,
                    candidate_old_groups=best_stale,
                    score=best_score,
                    reason="tie",
                )
            )
            continue

        candidates.append((best_score, best_stale[0], fresh_group))

    # 同じ stale を複数 fresh が選んだ場合、最高点が一意なときだけ確定する。
    # 最高点まで同点なら、文字列順で勝者を決めず全件を手動判断へ回す。
    candidates_by_stale: dict[GroupKey, list[tuple[int, GroupKey]]] = {}
    for score, stale_group, fresh_group in candidates:
        candidates_by_stale.setdefault(stale_group, []).append((score, fresh_group))

    matches: list[tuple[GroupKey, GroupKey]] = []
    used_stale: set[GroupKey] = set()
    used_fresh: set[GroupKey] = set()
    for stale_group in sorted(candidates_by_stale):
        proposals = candidates_by_stale[stale_group]
        best_score = max(score for score, _fresh_group in proposals)
        best_fresh = tuple(
            sorted(fresh_group for score, fresh_group in proposals if score == best_score)
        )
        if len(best_fresh) != 1:
            for fresh_group in best_fresh:
                orphans.append(
                    ReconcileOrphan(
                        new_group=fresh_group,
                        candidate_old_groups=(stale_group,),
                        score=best_score,
                        reason="claimed",
                    )
                )
            continue

        fresh_group = best_fresh[0]
        if fresh_group in used_fresh:
            orphans.append(
                ReconcileOrphan(
                    new_group=fresh_group,
                    candidate_old_groups=(stale_group,),
                    score=best_score,
                    reason="claimed",
                )
            )
            continue
        matches.append((stale_group, fresh_group))
        used_stale.add(stale_group)
        used_fresh.add(fresh_group)

    # 自動採用済み stale は手動候補として再利用できない。候補を 1:1 に絞り、
    # 全候補が使用済みになった orphan は一覧から外す。
    available_orphans: list[ReconcileOrphan] = []
    for orphan in orphans:
        available = tuple(group for group in orphan.candidate_old_groups if group not in used_stale)
        if available:
            available_orphans.append(
                ReconcileOrphan(
                    new_group=orphan.new_group,
                    candidate_old_groups=available,
                    score=orphan.score,
                    reason=orphan.reason,
                )
            )
    matches.sort()
    available_orphans.sort(key=lambda orphan: orphan.new_group)
    return ReconcilePlan(matches=tuple(matches), orphans=tuple(available_orphans))


__all__ = [
    "GroupFingerprint",
    "ReconcileOrphan",
    "ReconcileOrphanReason",
    "ReconcilePlan",
    "build_group_fingerprints",
    "plan_group_reconciliation",
]
