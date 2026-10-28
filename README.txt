StopGate Lite
=============

Claude Code sometimes ends a turn too early: it says "next I'll run the tests"
and stops, or asks permission for something you already approved.

StopGate Lite is a free, MIT-licensed Claude Code Stop hook. Every time Claude
tries to stop, a small Haiku reviewer (StopChecker) reads a digest of the turn
and decides whether the work is really DONE, genuinely needs your input, or was
abandoned early. In the last case, Claude is sent back to work with a note on
exactly what is left.

  * One file of plain Python, no dependencies
  * A forced-continue cap (default 5) so it can never loop forever
  * Add your own reviewers: drop a .txt prompt file into agents/
  * A test suite that uses a fake `claude` binary (no API calls)

How Claude helped: StopGate was designed and written with Claude Code, and
it's been dogfooded on that same work. Its reviewers kept catching real early
stops while it was being built.

Requirements: the Claude Code CLI (`claude`) on PATH, Python 3.9+, macOS or Linux.

Install
-------
As a Claude Code plugin (Claude Code 2.1.275 or later), inside Claude Code:

  /plugin install stopgate-lite --marketplace minus1ms-dev/stopgate-lite

Or manually, from a clone of this repo:

  bash install.sh             # user-wide (~/.claude/settings.json)
  bash install.sh --project   # this project only (./.claude/settings.json)
  bash uninstall.sh           # remove

Restart Claude Code (or run /hooks) afterwards.

Configure (environment variables)
---------------------------------
  STOPGATE_MODEL          reviewer model (default: haiku)
  STOPGATE_EFFORT         reviewer effort (default: high)
  STOPGATE_AGENT_TIMEOUT  seconds per reviewer (default: 240)
  STOPGATE_MAX_FORCED     max forced continues per prompt (default: 5, 0 = unlimited)
  STOPGATE_DISABLE=1      turn it off temporarily

Writing a reviewer
------------------
Create ~/.claude/hooks/stopgate/agents/2-MyChecker.txt:

  tools: Bash,Read,Grep
  max_turns: 6
  gate: false

  You are MyChecker. Flag ...

Header lines are optional and end at the first blank line. Gate reviewers can
force Claude to continue; the others only send feedback once.

Test
----
  python3 -m unittest discover -s tests -v

Want more?
----------
The full StopGate pack adds TestChecker (catches unverified changes and gives
the exact test command) and CostChecker (flags wasted tokens), tuned prompts,
and support: https://minus1ms.gumroad.com/l/stopgate
