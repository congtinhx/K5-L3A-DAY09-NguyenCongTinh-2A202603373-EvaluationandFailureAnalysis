"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    rows = pr.get("rows") or []
    base_rows = base.get("rows") or []
    graded = _graded_metrics(spec, base_rows or rows)

    # 1. every reported number must be a finite number (NaN < min is False: a naive gate passes it)
    for name, rep in (("PR", pr), ("baseline", base)):
        for k, v in (rep.get("metrics") or {}).items():
            if isinstance(v, float) and not math.isfinite(v):
                problems.append(f"{name} metric {k} is {v} (crashed or dropped grader)")

    # 2. row count = items x seeds, no duplicated (item, seed)
    for name, rep in (("PR", pr), ("baseline", base)):
        man = rep.get("manifest") or {}
        r = rep.get("rows") or []
        want = int(man.get("items") or 0) * len(man.get("seeds") or [])
        if len(r) != want:
            problems.append(f"{name} has {len(r)} rows, manifest says {man.get('items')} items x "
                            f"{len(man.get('seeds') or [])} seeds = {want}")
        keys = [(x.get("item_id"), x.get("seed")) for x in r]
        dups = sorted({k for k in keys if keys.count(k) > 1}, key=str)
        if dups:
            problems.append(f"{name} has duplicated (item, seed) rows: {dups[:5]}")

    # 3. same (item, seed) set and same manifest as the baseline (no cherry-picked items or seeds)
    pr_keys = {(x.get("item_id"), x.get("seed")) for x in rows}
    base_keys = {(x.get("item_id"), x.get("seed")) for x in base_rows}
    if pr_keys != base_keys:
        miss, extra = sorted(base_keys - pr_keys, key=str), sorted(pr_keys - base_keys, key=str)
        problems.append(f"(item, seed) set differs from baseline: missing {miss[:5]}{'...' if len(miss) > 5 else ''}"
                        f" extra {extra[:5]}{'...' if len(extra) > 5 else ''}")
    pm, bm = pr.get("manifest") or {}, base.get("manifest") or {}
    for k in ("items", "seeds"):
        if pm.get(k) != bm.get(k):
            problems.append(f"manifest {k} {pm.get(k)!r} differs from baseline {bm.get(k)!r}")

    # 4. no grader errors, no silently missing grades
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s) in PR report, "
                        f"first: {pr['evaluator_failures'][0]}")
    for m in graded:
        missing = [f"{x.get('item_id')}@{x.get('seed')}" for x in rows if x.get(m) is None]
        if missing:
            problems.append(f"{len(missing)} row(s) have no grade for {m} (e.g. {missing[:3]})")
        if m not in (pr.get("metrics") or {}):
            problems.append(f"spec metric {m} missing from PR metrics")

    # 5. the headline numbers must be the ones the rows produce (no hand-edited metrics)
    if not problems:
        for m, want in _recompute(rows, graded).items():
            if m not in graded and m not in (pr.get("metrics") or {}):
                continue   # summary metric this spec does not report
            got = (pr.get("metrics") or {}).get(m)
            if want is not None and (not isinstance(got, (int, float)) or abs(float(got) - want) > 1e-3):
                problems.append(f"metric {m} = {got!r} but its rows give {round(want, 4)}")

    # 6. slice tags per item must match the baseline's (relabelling hides a slice regression)
    pr_tags, base_tags = _tags(rows), _tags(base_rows)
    for item, tags in sorted(pr_tags.items()):
        if len(tags) > 1:
            problems.append(f"{item} has inconsistent slice tags across seeds: {sorted(map(sorted, tags))}")
        elif item in base_tags and tags != base_tags[item]:
            problems.append(f"{item} slice tags {sorted(next(iter(tags)))} differ from baseline "
                            f"{sorted(next(iter(base_tags[item])))}")

    # 6b. ... and match the eval set file itself (tags derive from labels, so neither report may edit them)
    es = Path(str(spec.get("eval_set") or ""))
    if spec.get("eval_set") and es.is_file():
        want_tags = {it["item_id"]: tuple(sorted(it.get("slices") or []))
                     for it in (json.loads(line) for line in es.read_text(encoding="utf-8").splitlines() if line.strip())}
        for name, tags_of in (("PR", pr_tags), ("baseline", base_tags)):
            bad = sorted(i for i, t in tags_of.items() if i in want_tags and t != {want_tags[i]})
            if bad:
                problems.append(f"{name} slice tags differ from {es} for {bad[:5]}")

    # 7. ratchet: on the same eval-set version thresholds may only get stricter
    if base_spec is not None:
        problems += ratchet_problems(spec, base_spec)
    return problems


def _graded_metrics(spec: dict, rows: list[dict]) -> list[str]:
    """Spec metrics that are graded per row (present in the baseline rows / registered graders)."""
    try:
        from lab9.graders import GRADERS
        registered = set(GRADERS)
    except Exception:  # noqa: BLE001
        registered = set()
    in_rows = set().union(*(r.keys() for r in rows)) if rows else set()
    summary = {"pass_5", "cost_per_success"}
    return [m["id"] for m in spec.get("metrics", [])
            if (m["id"] in in_rows or m["id"] in registered) and m["id"] not in summary
            and not m["id"].startswith(("pass_at_", "pass_hat_"))]


def _recompute(rows: list[dict], metric_ids: list[str]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for m in metric_ids:
        vals = [r[m] for r in rows if r.get(m) is not None]
        if not vals:
            out[m] = None
        elif m in ("secret_leak", "approval_bypass"):
            out[m] = float(sum(bool(v) for v in vals))
        else:
            out[m] = sum(bool(v) for v in vals) / len(vals)
    by_item: dict[str, list[bool]] = {}
    for r in rows:
        if "pass_rate" in r:
            by_item.setdefault(r["item_id"], []).append(bool(r.get("pass_rate")))
    if by_item:
        n = min(len(v) for v in by_item.values())
        out["pass_5"] = sum(all(v) for v in by_item.values()) / len(by_item)
        for k in range(1, n + 1):
            out[f"pass_hat_{k}"] = sum(stats.pass_hat_k(len(v), sum(v), k) for v in by_item.values()) / len(by_item)
            out[f"pass_at_{k}"] = sum(stats.pass_at_k(len(v), sum(v), k) for v in by_item.values()) / len(by_item)
    return out


def _tags(rows: list[dict]) -> dict[str, set[tuple[str, ...]]]:
    out: dict[str, set[tuple[str, ...]]] = {}
    for r in rows:
        out.setdefault(r.get("item_id"), set()).add(tuple(sorted(r.get("slices") or [])))
    return out


def _looser(new: dict[str, Any], old: dict[str, Any]) -> list[str]:
    why = []
    if "max" in old and ("max" not in new or new["max"] > old["max"]):
        why.append(f"max {old['max']} -> {new.get('max')}")
    if "min" in old and ("min" not in new or new["min"] < old["min"]):
        why.append(f"min {old['min']} -> {new.get('min')}")
    if "max_increase" in old and ("max_increase" not in new or new["max_increase"] > old["max_increase"]):
        why.append(f"max_increase {old['max_increase']} -> {new.get('max_increase')}")
    if old.get("no_significant_drop") and not new.get("no_significant_drop"):
        why.append("no_significant_drop dropped")
    return why


def ratchet_problems(spec: dict, base_spec: dict) -> list[str]:
    """Same eval-set version => no threshold may loosen, no blocking metric/slice may relax or vanish."""
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        return []   # a new eval-set version starts a new baseline and new thresholds
    problems = []
    new_m = {m["id"]: m for m in spec.get("metrics", [])}
    for m in base_spec.get("metrics", []):
        n = new_m.get(m["id"])
        if n is None:
            problems.append(f"ratchet: metric {m['id']} removed from spec")
            continue
        if m.get("blocking") and not n.get("blocking"):
            problems.append(f"ratchet: metric {m['id']} no longer blocking")
        problems += [f"ratchet: metric {m['id']} {w}" for w in _looser(n.get("threshold") or {}, m.get("threshold") or {})]
    new_s = {(x["name"], x["metric"]): x for x in spec.get("slices") or []}
    for x in base_spec.get("slices") or []:
        n = new_s.get((x["name"], x["metric"]))
        sid = f"slice:{x['name']}/{x['metric']}"
        if n is None:
            problems.append(f"ratchet: {sid} removed from spec")
            continue
        if x.get("blocking") and not n.get("blocking"):
            problems.append(f"ratchet: {sid} no longer blocking")
        problems += [f"ratchet: {sid} {w}" for w in _looser(n, x)]
    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        val = round(sum(a.values()) / len(a), 4) if a else None
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "min" in sl and val < sl["min"]:
            verdict = "fail"
        elif "max" in sl and val > sl["max"]:
            verdict = "fail"
        if sl.get("no_significant_drop") and val is not None:
            keys = sorted(set(a) & set(b))
            if keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}] n={len(keys)}"
                if hi < 0:
                    verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        th = {k: sl[k] for k in ("min", "max", "no_significant_drop") if k in sl}
        lines.append(f"  {sid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
