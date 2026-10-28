#!/usr/bin/env python3
"""StopGate Lite: a Claude Code Stop hook that runs every reviewer in agents/*.txt in
parallel (Haiku by default), then feeds their combined feedback back to the main
model (decision=block) so it fixes problems before ending its turn.

Add a reviewer by dropping a new agents/<Name>.txt file. Optional header lines:
  tools: Bash,Read,Grep,Glob      (default: no tools)
  max_turns: 10                   (default: 3)
  gate: true                      (its "continue" verdict may force continuation)
Everything after the first blank line is the prompt.

Forced continues are limited by STOPGATE_MAX_FORCED (default 5; 0 = unlimited).
Environment: STOPGATE_MODEL, STOPGATE_EFFORT, STOPGATE_AGENT_TIMEOUT, STOPGATE_DISABLE.
"""
import concurrent.futures as cf, json, os, pathlib, re, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
AGENTS_DIR, STATE_DIR, SANDBOX = HERE / "agents", HERE / "state", HERE / "sandbox"
MODEL = os.environ.get("STOPGATE_MODEL", "haiku")
EFFORT = os.environ.get("STOPGATE_EFFORT", "high")
AGENT_TIMEOUT = int(os.environ.get("STOPGATE_AGENT_TIMEOUT", "240"))
DIGEST_LIMIT = 60_000
MAX_FORCED = int(os.environ.get("STOPGATE_MAX_FORCED", "5"))
CLAUDE_BIN = os.environ.get("STOPGATE_CLAUDE_BIN", "claude")
LOG = HERE / "stopgate.log"


def log(msg):
    with LOG.open("a") as f:
        f.write(f"{time.strftime('%F %T')} {msg}\n")


def clip(s, n):
    s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"…[+{len(s) - n} chars]"


def is_human_prompt(e):
    if e.get("type") != "user" or e.get("isMeta"):
        return False
    c = e.get("message", {}).get("content")
    if isinstance(c, str):
        return True
    return isinstance(c, list) and any(b.get("type") == "text" for b in c) and \
        not any(b.get("type") == "tool_result" for b in c)


def build_digest(transcript_path, hook_input):
    entries = []
    with open(transcript_path) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except ValueError:
                pass
    start = max((i for i, e in enumerate(entries) if is_human_prompt(e)), default=0)
    turn = entries[start:]

    def ts(e):
        t = e.get("timestamp")
        return time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")) if t else None

    prompt = turn[0].get("message", {}).get("content") if turn else ""
    if isinstance(prompt, list):
        prompt = "\n".join(b.get("text", "") for b in prompt if b.get("type") == "text")
    times = [t for t in map(ts, turn) if t]
    usage, seen, models = {}, set(), set()
    tool_calls, tool_ts, events, hooks = {}, {}, [], []
    for e in turn:
        m = e.get("message", {})
        if e.get("type") == "assistant":
            models.add(m.get("model"))
            if m.get("id") not in seen and m.get("usage"):
                seen.add(m.get("id"))
                for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                          "cache_creation_input_tokens"):
                    usage[k] = usage.get(k, 0) + (m["usage"].get(k) or 0)
            for b in m.get("content", []):
                if b.get("type") == "tool_use":
                    tool_calls[b["name"]] = tool_calls.get(b["name"], 0) + 1
                    tool_ts[b["id"]] = ts(e)
                    events.append(f"[{e.get('timestamp','')[11:19]}] TOOL {b['name']}: {clip(b.get('input'), 600)}")
                elif b.get("type") == "text" and b.get("text", "").strip():
                    events.append(f"[{e.get('timestamp','')[11:19]}] SAID: {clip(b['text'], 1500)}")
                elif b.get("type") == "thinking" and b.get("thinking"):
                    events.append(f"[{e.get('timestamp','')[11:19]}] THOUGHT: {clip(b['thinking'], 400)}")
        elif e.get("type") == "user" and isinstance(m.get("content"), list):
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    dur = (ts(e) or 0) - (tool_ts.get(b.get("tool_use_id")) or ts(e) or 0)
                    flag = "ERROR " if b.get("is_error") else ""
                    events.append(f"  -> {flag}result ({dur:.0f}s): {clip(b.get('content'), 500)}")
                elif b.get("type") == "text" and e is not turn[0]:
                    events.append(f"INJECTED USER/HOOK TEXT: {clip(b['text'], 800)}")
        elif e.get("type") == "attachment":
            a = e.get("attachment", {})
            if a.get("hookEvent") or "hook" in str(a.get("type")):
                hooks.append(f"{a.get('type')} {a.get('hookName')}: {clip(a.get('content') or a.get('stderr') or '', 300)}")
        elif e.get("type") == "system" and e.get("level") in ("error", "warning"):
            events.append(f"SYSTEM {e.get('level')}: {clip(e.get('content'), 400)}")

    stats = {
        "turn_wall_clock_seconds": round(times[-1] - times[0]) if len(times) > 1 else 0,
        "models": sorted(x for x in models if x),
        "api_calls": len(seen),
        "token_usage": usage,
        "tool_calls_by_name": tool_calls,
        "stop_hook_active": hook_input.get("stop_hook_active"),
        "cwd": hook_input.get("cwd"),
        "transcript_path": transcript_path,
    }
    body = "\n".join(events)
    if len(body) > DIGEST_LIMIT:  # keep the start and the most recent activity
        body = body[:DIGEST_LIMIT // 3] + "\n…[middle of turn elided]…\n" + body[-2 * DIGEST_LIMIT // 3:]
    final = hook_input.get("last_assistant_message") or next(
        (b.get("text") for e in reversed(turn) if e.get("type") == "assistant"
         for b in reversed(e.get("message", {}).get("content", [])) if b.get("type") == "text"), "")
    return (f"## USER PROMPT FOR THIS TURN\n{clip(prompt, 6000)}\n\n## STATS\n{json.dumps(stats, indent=1)}\n\n"
            f"## HOOK EVENTS\n{chr(10).join(hooks) or '(none)'}\n\n## TURN TIMELINE\n{body}\n\n"
            f"## FINAL MESSAGE TO USER (the stop)\n{clip(final, 6000)}\n")


def load_agents():
    agents = []
    for p in sorted(AGENTS_DIR.glob("*.txt")):
        head, _, prompt = p.read_text().partition("\n\n")
        meta = dict(re.findall(r"^(\w+):\s*(.+)$", head, re.M))
        agents.append({"name": p.stem.split("-", 1)[-1], "prompt": prompt.strip() or head,
                       "tools": meta.get("tools", ""), "max_turns": meta.get("max_turns", "3"),
                       "gate": meta.get("gate", "false").lower() == "true"})
    return agents


RESPONSE_SPEC = """
Respond with ONLY a JSON object, no prose around it:
{"verdict": "ok" | "issue", "continue": true | false, "feedback": "<concise, concrete, actionable advice for the main model; empty if ok>"}
"continue" means the main model must resume work instead of ending its turn (only meaningful for gate reviewers; others use false).
"""


def run_agent(agent, digest):
    prompt = (f"{agent['prompt']}\n{RESPONSE_SPEC}\n\n# DIGEST OF THE MAIN MODEL'S TURN\n"
              f"(Full transcript JSONL is at the transcript_path in STATS if you have file tools.)\n\n{digest}")
    cmd = [CLAUDE_BIN, "-p", "--model", MODEL, "--effort", EFFORT, "--output-format", "json",
           "--no-session-persistence", "--setting-sources", "project",
           "--max-turns", str(agent["max_turns"]), "--permission-mode", "bypassPermissions"]
    cmd += ["--allowedTools", agent["tools"]] if agent["tools"] else ["--tools", ""]
    env = {**os.environ, "STOPGATE_CHILD": "1"}
    t0 = time.time()
    try:
        out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=SANDBOX,
                             env=env, timeout=AGENT_TIMEOUT)
        res = json.loads(out.stdout)
        text = res.get("result", "")
        cost = res.get("total_cost_usd")
    except Exception as ex:  # noqa: BLE001
        log(f"{agent['name']} failed: {ex!r}")
        return agent, {"verdict": "error", "continue": False, "feedback": f"reviewer failed: {ex!r}"}
    m = re.search(r"\{.*\}", text, re.S)
    try:
        parsed = json.loads(m.group(0)) if m else None
    except ValueError:
        parsed = None
    parsed = parsed or {"verdict": "issue", "continue": False, "feedback": text.strip()}
    log(f"{agent['name']} {time.time() - t0:.0f}s cost={cost} -> {json.dumps(parsed)[:300]}")
    return agent, parsed


def main():
    if os.environ.get("STOPGATE_CHILD") or os.environ.get("STOPGATE_DISABLE"):
        return
    STATE_DIR.mkdir(exist_ok=True)
    SANDBOX.mkdir(exist_ok=True)
    hook_input = json.load(sys.stdin)
    sid = hook_input.get("session_id", "unknown")
    state_file = STATE_DIR / f"{sid}.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    if not hook_input.get("stop_hook_active"):
        state = {"forced": 0, "feedback_sent": False}

    digest = build_digest(hook_input["transcript_path"], hook_input)
    agents = load_agents()
    if not agents:
        return
    with cf.ThreadPoolExecutor(len(agents)) as ex:
        results = list(ex.map(lambda a: run_agent(a, digest), agents))

    must_continue = any(r.get("continue") for a, r in results if a["gate"])
    if must_continue and MAX_FORCED and state["forced"] >= MAX_FORCED:
        log(f"{sid}: forced-continue limit {MAX_FORCED} reached; allowing stop")
        must_continue = False
    issues = [(a, r) for a, r in results if r.get("verdict") != "ok" and str(r.get("feedback", "")).strip()]
    if not must_continue and (not issues or state["feedback_sent"]):
        state_file.write_text(json.dumps(state))
        return

    lines = [f"### {a['name']} [{r.get('verdict')}{', CONTINUE' if a['gate'] and r.get('continue') else ''}]\n{r.get('feedback')}"
             for a, r in results if str(r.get("feedback", "")).strip()]
    if must_continue:
        state["forced"] += 1
        log(f"{sid}: forced continue #{state['forced']}")
        header = ("STOPGATE: your stop was rejected — you ended the turn without a clear, well-scoped "
                  "question for the user or without finishing. Continue working. Reviewer feedback:")
    else:
        header = ("STOPGATE: your stop is acceptable, but reviewers flagged improvements. Apply what is "
                  "actionable now (e.g. fix harness/tooling issues), note lessons for the rest of this session, "
                  "then end your turn again. Don't repeat your previous answer to the user. Feedback:")
    state["feedback_sent"] = True
    state_file.write_text(json.dumps(state))
    print(json.dumps({"decision": "block", "reason": header + "\n\n" + "\n\n".join(lines)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:  # never wedge the session on a reviewer bug
        log(f"fatal: {ex!r}")
