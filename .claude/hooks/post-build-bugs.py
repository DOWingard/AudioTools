#!/usr/bin/env python3
"""Post-build bug capture hook.

Fires on every Stop event. Scans the session transcript for evidence that
bugs/errors were encountered during a build or implementation session.
If thresholds are met, outputs a prompt to Claude's context asking it to
run .agent/workflows/addBugs.md.

Guards:
- stop_hook_active=true  → skip (Claude is already responding to this hook)
- last message mentions addBugs/remediation → workflow already ran, skip
- Not enough signal → skip silently
"""
import sys, json, re, os

try:
    data = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)

# Prevent re-trigger: Stop fires again after Claude responds to our prompt.
# stop_hook_active is set to True on that second firing.
if data.get('stop_hook_active', False):
    sys.exit(0)

last_msg = data.get('last_assistant_message', '').lower()

# Skip if addBugs workflow was already run or is currently running.
already_ran = re.search(
    r'addbugs?|agent.context|remediation plan|failure pattern|bugs? (captured|extracted|added)',
    last_msg,
)
if already_ran:
    sys.exit(0)

transcript_path = data.get('transcript_path', '')
if not transcript_path or not os.path.exists(transcript_path):
    sys.exit(0)

try:
    text = open(transcript_path, encoding='utf-8', errors='ignore').read()
except Exception:
    sys.exit(0)

t = text.lower()

# --- Error / bug signals ---
error_count = len(re.findall(
    r'\b('
    r'error|exception|traceback|typeerror|syntaxerror|nameerror|attributeerror|'
    r'valueerror|keyerror|importerror|modulenotfounderror|runtimeerror|oserror|'
    r'bug|crash|fatal|diagnostic|stack[\s_]trace|build[\s_]fail|test[\s_]fail|'
    r'unhandled|uncaught|segfault|assertion'
    r')\b',
    t,
))

# --- Build / implementation signals ---
build_count = len(re.findall(
    r'\b('
    r'edit|write|fix(?:ed|ing)?|patch|resolv|implement|refactor|'
    r'build|compil|deploy|docker|npm|pytest|'
    r'endpoint|route|component|function|class|method|import|hook'
    r')\b',
    t,
))

# --- Code was actually changed this session ---
# Transcript JSONL uses compact JSON: "name":"Edit" (no spaces around colon)
had_edits = (
    '"name":"Edit"' in text
    or '"name":"Write"' in text
    or '"name":"MultiEdit"' in text
)

# Thresholds: enough error signals + implementation activity + at least one edit
if error_count >= 4 and build_count >= 10 and had_edits:
    print(
        f"[post-build hook] {error_count} error signals and {build_count} "
        f"implementation signals detected in this session. "
        f"If bugs were found and fixed, run the .agent/workflows/addBugs.md "
        f"workflow now to capture them for future sessions."
    )

sys.exit(0)
