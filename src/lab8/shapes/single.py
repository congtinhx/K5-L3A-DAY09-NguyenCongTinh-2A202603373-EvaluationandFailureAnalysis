"""Shape B — one ReAct agent. Student work.

Use `lab8.runtime.run_react` with max_steps=8. `decide` may pick a read tool.
It must not call `issue_refund`. After the loop, the host validates the quote
and is the only writer.

A decide() that trusts ticket text over the order row will fail hidden claims
where the customer overstates the delay or cites a superseded clause.

Per-order cap: an order may already have been refunded by an earlier claim.
The read tool `lookup_refund_history` shows how much. The host still enforces
the cap, so pass the full quote to `durability.issue_refund`, not a net amount.

Caps: stop at 8 steps. Count model calls in the Budget so cost stays comparable.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

from lab8.db import connect
from lab8.durability import issue_refund
from lab8.mock_llm import complete
from lab8.runtime import ToolRejected, run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.tools import get_claim_view, tool_cost
from lab8.trace import Trace


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    budget = Budget()
    trace = Trace(make_run_id("single", claim_id, seed))

    def done(status, proposal=0, reason=None):
        complete(
            budget,
            seed=seed,
            purpose="draft",
            prompt=f"status={status}; proposal={proposal}",
        )
        trace.add("model", {"purpose": "draft"})
        trace.add("decision", {"status": status, "proposal_amount": proposal})
        return finish(
            conn,
            shape="single",
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    try:
        budget.add_tool(tool_cost())
        claim = get_claim_view(conn, claim_id)
        trace.add("claim", {"found": bool(claim.get("found"))})
        if not claim.get("found"):
            return done("failed", reason="unknown_claim")
        if not claim.get("order_id"):
            return done("needs_info", reason="missing_order_id")

        state = {}

        def decide(observation):
            last = observation.get("last_tool")
            result = observation.get("last_result") or {}
            complete(
                budget,
                seed=seed,
                purpose="decide",
                prompt=f"claim={claim_id}; last_tool={last or 'start'}",
            )
            trace.add("model", {"purpose": "decide", "last_tool": last})

            if last is None:
                action = {
                    "tool": "lookup_order",
                    "args": {
                        "order_id": claim["order_id"],
                        "principal_id": claim["principal_id"],
                    },
                }
            elif last == "lookup_order":
                trace.add("order", {"visible": bool(result.get("visible"))})
                if not result.get("visible"):
                    state["scope_reason"] = result.get("reason", "not_visible")
                    return {"final": {"status": "escalated"}}
                state["order"] = result["order"]
                action = {"tool": "lookup_policy", "args": {}}
            elif last == "lookup_policy":
                action = {
                    "tool": "compute_compensation",
                    "args": {"order": state["order"]},
                }
            elif last == "compute_compensation":
                state["quote"] = result
                trace.add("quote", {"amount_vnd": int(result["amount_vnd"])})
                return {"final": {"status": "ready"}}
            else:
                return {"final": {"status": "failed"}}

            trace.add("tool", {"name": action["tool"]})
            return action

        try:
            outcome = run_react(
                conn,
                budget,
                observation={"claim": claim},
                decide=decide,
                max_steps=8,
                worker=True,
            )
        except ToolRejected:
            return done("failed", reason="tool_rejected")

        if outcome["stopped"] == "step_cap":
            return done("failed", reason="step_cap")
        final_status = outcome["final"].get("status")
        if final_status == "escalated":
            return done("escalated", reason=state.get("scope_reason"))
        if final_status != "ready" or "quote" not in state:
            return done("failed", reason="invalid_agent_result")

        quote = state["quote"]
        proposal = int(quote["amount_vnd"])
        if proposal <= 0:
            return done("denied", reason=quote.get("reason"))

        trace.add("host", {"operation": "issue_refund", "proposal_amount": proposal})
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
        if not outcome["ok"]:
            status = "failed" if outcome["conflict"] else "denied"
            return done(status, proposal, outcome["reason"])
        return done("resolved", proposal)
    finally:
        conn.close()
