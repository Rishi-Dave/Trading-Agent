# server/ — adds to root CLAUDE.md; only what differs here

This is the load-bearing module: the safety layer (SPEC §4) lives in `safety.py` and is
the ONLY enforcement point (T1). Rules specific to this package:

- Every order-mutating tool handler calls the safety gate **before** any E*Trade call;
  `place_order` accepts only a `preview_id` (T2). If you add a tool, trace the gate path
  first, implement second.
- Refusals use the exact SPEC §4.1 payload shape — it is a parsed contract, not a
  message.
- Any change here warrants a look at `tests/wall/`: does an existing wall test cover the
  changed behavior, or does the wall need a new (committed-red-first) test? If a wall
  test is red, the safety-wall skill governs — never this file's judgment.
- Fail closed: on any uncertainty, exception, or missing state, refuse the order.
