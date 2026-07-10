#!/usr/bin/env bash
# PreToolUse hook: block Bash commands that could leak trading secrets (SPEC §2 T3).
# Defense-in-depth only — the real controls are .gitignore, log redaction, fixture scrubbing.
# Receives hook JSON on stdin. Exit 0 = allow, exit 2 = block (reason on stderr).

set -euo pipefail

INPUT=$(cat)

# /usr/bin/python3 (system): pyenv shims intercept bare python3 and fail on this
# project's .python-version (uv-format pin), which would silently disable this hook.
COMMAND=$(echo "$INPUT" | /usr/bin/python3 -c "
import sys, json
data = json.load(sys.stdin)
if data.get('tool_name') != 'Bash':
    print('')
else:
    print(data.get('tool_input', {}).get('command', ''))
" 2>/dev/null || echo "")

[ -z "$COMMAND" ] && exit 0

BLOCK_PATTERNS=(
  '(cat|less|more|head|tail|grep|bat)[^|;&]*\.env\b'
  '(cat|less|more|head|tail|grep|bat)[^|;&]*tokens/'
  '(cat|less|more|head|tail|grep|bat)[^|;&]*\.tokens\.json'
  '(cat|less|more|head|tail|grep|bat)[^|;&]*\.ssh'
  '(cat|less|more|head|tail|grep|bat)[^|;&]*credentials'
  '(cat|less|more|head|tail|grep|bat)[^|;&]*\.netrc'
  '(echo|curl|wget|printf)[^|;&]*(CONSUMER_KEY|CONSUMER_SECRET|OAUTH|ACCESS_TOKEN|API_KEY|SECRET|PASSWORD|NTFY_TOPIC)'
  'sqlite3[^|;&]*(token|secret|oauth)'
  '\bprintenv\b'
  '(^|[;&|][[:space:]]*)env[[:space:]]*($|[;&|])'
)

for pattern in "${BLOCK_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qiE "$pattern"; then
    echo "secret-guard: blocked — command matches secret-exfiltration pattern (SPEC §2 T3). Use redacted logging or read the non-secret config instead." >&2
    exit 2
  fi
done

exit 0
