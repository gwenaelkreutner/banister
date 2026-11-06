#!/usr/bin/env bash
# Installs or updates the Banister plugin in a Hermes profile. Safe to re-run.
#
#   BANISTER_AGENT_TOKEN=...  OWNER_CHAT_ID=...  ./install.sh
#
# BANISTER_AGENT_TOKEN  required on first install: the AGENT_API_TOKEN from Banister's .env
# OWNER_CHAT_ID         required on first install: Telegram user id allowed to tap the buttons
# BANISTER_API          default http://127.0.0.1:8000
# HERMES_PROFILE        default banister (created if missing)
# HERMES_CLONE_FROM     optional profile to clone model and provider settings from
# HERMES_HOME           default ~/.hermes
#
# Afterwards: put the bot's TELEGRAM_BOT_TOKEN in the profile's .env, set COACH_INTERFACE=agent in
# Banister's .env and restart both.
set -euo pipefail
PROFILE=${HERMES_PROFILE:-banister}
HERMES_ROOT=${HERMES_HOME:-$HOME/.hermes}
PROFILE_DIR=$HERMES_ROOT/profiles/$PROFILE
HERE=$(cd "$(dirname "$0")" && pwd)

if [ ! -d "$PROFILE_DIR" ]; then
  DESCRIPTION="Cycling coach on top of Banister: form, finding and composing sessions, reviewing rides."
  if [ -n "${HERMES_CLONE_FROM:-}" ]; then
    hermes profile create "$PROFILE" --clone-from "$HERMES_CLONE_FROM" --no-alias --description "$DESCRIPTION"
    # A clone starts as this agent only: not the source's plugins, skills, jobs or memories.
    find "$PROFILE_DIR/plugins" "$PROFILE_DIR/skills" -mindepth 1 -delete 2>/dev/null || true
    rm -f "$PROFILE_DIR/cron/jobs.json" "$PROFILE_DIR/.skills_prompt_snapshot.json"
    : > "$PROFILE_DIR/memories/MEMORY.md"
  else
    hermes profile create "$PROFILE" --no-alias --description "$DESCRIPTION"
  fi
fi

PLUGIN_DIR=$PROFILE_DIR/plugins/banister
mkdir -p "$PLUGIN_DIR" "$PROFILE_DIR/backups"
cp -p "$PROFILE_DIR/config.yaml" "$PROFILE_DIR/backups/config.yaml.$(date +%Y%m%d-%H%M%S)"
rsync -a --delete --exclude __pycache__ --exclude settings.json "$HERE/banister/" "$PLUGIN_DIR/"

# The skill lives in this repository, outside the plugin: the agent cannot edit a plugin's skill.
# Its corrections show up in `git diff` here: read them before committing to a public repository.
hermes -p "$PROFILE" config set skills.external_dirs "[\"$HERE/skills\"]" >/dev/null

if [ ! -f "$PLUGIN_DIR/settings.json" ]; then
  : "${BANISTER_AGENT_TOKEN:?set BANISTER_AGENT_TOKEN for the first install}"
  : "${OWNER_CHAT_ID:?set OWNER_CHAT_ID for the first install}"
  ( umask 077
    python3 -c 'import json, os, sys; json.dump({"api": os.environ.get("BANISTER_API", "http://127.0.0.1:8000"), "token": os.environ["BANISTER_AGENT_TOKEN"], "chat_id": os.environ["OWNER_CHAT_ID"]}, open(sys.argv[1], "w"))' \
      "$PLUGIN_DIR/settings.json" )
fi
chmod 600 "$PLUGIN_DIR/settings.json"

# Targeted edit: `hermes tools enable` would rewrite composite toolsets.
python3 - "$PROFILE_DIR/config.yaml" <<'PY'
import sys
import yaml

path = sys.argv[1]
config = yaml.safe_load(open(path)) or {}
plugins = config.setdefault("plugins", {})
plugins["enabled"] = ["banister"]
plugins["disabled"] = []
# Injection lets Banister ask the coach to follow up: a ride review, the weekly analysis.
plugins["entries"] = {"banister": {"allow_tool_override": False, "allow_gateway_injection": True}}
toolsets = ["skills", "session_search", "clarify", "todo", "vision", "tts", "banister"]
platforms = config.setdefault("platform_toolsets", {})
for platform in ("cli", "telegram", "cron"):
    platforms[platform] = list(toolsets)
# The reply check rewrites the final text: it must not have been streamed out already.
config.setdefault("display", {})["streaming"] = False
yaml.safe_dump(config, open(path, "w"), allow_unicode=True, sort_keys=False)
PY

if [ ! -s "$PROFILE_DIR/SOUL.md" ]; then
  cat > "$PROFILE_DIR/SOUL.md" <<'SOUL'
You are a cycling coach working with Banister. Load the skill banister-coach as soon as training comes up, and rely on the banister_* tools: Banister computes every number, you never do. Be direct and short, and reply in the athlete's language.

You can improve: when the athlete corrects you or you spot a mistake in your method, update your skill banister-coach with skill_manage (action patch) in the same turn, then say in one line what changed. Do not create another skill to work around it.
SOUL
fi

if grep -q '^TELEGRAM_BOT_TOKEN=' "$PROFILE_DIR/.env" 2>/dev/null; then
  echo "ok: plugin installed. Next: hermes gateway restart"
else
  echo "ok: plugin installed. Add TELEGRAM_BOT_TOKEN to $PROFILE_DIR/.env, then: hermes gateway restart"
fi
