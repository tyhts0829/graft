# どこで: `src/grafix/core/parameters/reconcile.py`。
# 何を: ParamStore の「グループ（op, site_id）同士」の再リンク候補を作る純粋関数を提供する。
# なぜ: site_id が編集で揺れても、誤マッチを避けつつ GUI 調整値を可能な範囲で引き継ぐため。

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from .key import ParameterKey
from .meta import ParamMeta

GroupKey = tuple[str, str]  # (op, site_id)


@dataclass(frozen=True, slots=True)
class GroupFingerprint:
    """(op, site_id) グループの特徴量。"""

    op: str
    args: frozenset[str]
    kind_by_arg: Mapping[str, str]
    label: str | None


def build_group_fingerprints(
    snapshot: Mapping[ParameterKey, tuple[ParamMeta, object, int, str | None]],
) -> dict[GroupKey, GroupFingerprint]:
    """snapshot から (op, site_id) -> fingerprint を生成して返す。"""

    args_by_group: dict[GroupKey, set[str]] = {}
    kinds_by_group: dict[GroupKey, dict[str, str]] = {}
    label_by_group: dict[GroupKey, str | None] = {}

    for key, (meta, _state, _ordinal, label) in snapshot.items():
        group = (str(key.op), str(key.site_id))
        args_by_group.setdefault(group, set()).add(str(key.arg))
        kinds_by_group.setdefault(group, {})[str(key.arg)] = str(meta.kind)
        if group not in label_by_group:
            label_by_group[group] = str(label) if label is not None else None

    out: dict[GroupKey, GroupFingerprint] = {}
    for group, args in args_by_group.items():
        out[group] = GroupFingerprint(
            op=str(group[0]),
            args=frozenset(args),
            kind_by_arg=kinds_by_group.get(group, {}),
            label=label_by_group.get(group),
        )
    return out


def _match_score(a: GroupFingerprint, b: GroupFingerprint) -> int:
    """fingerprint 間の類似度スコアを返す（大きいほど近い）。"""

    if a.op != b.op:
        return -10**9

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


def match_groups(
    *,
    stale: Sequence[GroupKey],
    fresh: Sequence[GroupKey],
    fingerprints: Mapping[GroupKey, GroupFingerprint],
    min_score: int = 15,
) -> dict[GroupKey, GroupKey]:
    """stale -> fresh の 1:1 対応を作って返す（曖昧なら対応付けない）。

    Notes
    -----
    - 対応付けは op 単位で行う（op が異なるものは候補にしない）。
    - 誤マッチを避けるため、同点首位が複数ある場合は採用しない。
    """

    stale_list = sorted([(str(op), str(site_id)) for op, site_id in stale])
    fresh_list = sorted([(str(op), str(site_id)) for op, site_id in fresh])

    stale_by_op: dict[str, list[GroupKey]] = {}
    for op, site_id in stale_list:
        stale_by_op.setdefault(op, []).append((op, site_id))

    candidates: list[tuple[tuple[int, str, str], GroupKey, GroupKey]] = []
    # (rank, stale_group, fresh_group)
    # rank はソート用。score が主、文字列は順序安定化のダミー。

    for fresh_group in fresh_list:
        fresh_fp = fingerprints.get(fresh_group)
        if fresh_fp is None:
            continue

        best_score: int | None = None
        best_stale: GroupKey | None = None
        tied = False

        for stale_group in stale_by_op.get(fresh_fp.op, []):
            stale_fp = fingerprints.get(stale_group)
            if stale_fp is None:
                continue

            score = _match_score(stale_fp, fresh_fp)
            if score < int(min_score):
                continue

            if best_score is None or score > best_score:
                best_score = score
                best_stale = stale_group
                tied = False
            elif score == best_score:
                tied = True

        if best_stale is None or best_score is None or tied:
            continue

        rank = (int(best_score), fresh_group[0], fresh_group[1])
        candidates.append((rank, best_stale, fresh_group))

    # score が高いものから確定させる（1:1 を守る）。
    candidates.sort(reverse=True)
    out: dict[GroupKey, GroupKey] = {}
    used_stale: set[GroupKey] = set()
    used_fresh: set[GroupKey] = set()
    for _rank, stale_group, fresh_group in candidates:
        if stale_group in used_stale or fresh_group in used_fresh:
            continue
        out[stale_group] = fresh_group
        used_stale.add(stale_group)
        used_fresh.add(fresh_group)
    return out
