#!/usr/bin/env bash
# Copies the Android app maps into the APK so the macro layer ships with it.
#
# These are NOT the YAMLs in phonecli/app_maps: those are iOS maps
# (package: com.apple.*), and their element text and coordinates mean nothing
# on Android. The ones here were generated on the device by
# `python3 -m phoneagent.build_map`.
set -euo pipefail

APP="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$APP/.." && pwd)"
SRC="$ROOT/phoneagent/maps"
DEST="$APP/app/src/main/assets/maps"

# Only maps that have been measured at replay time ship. A map that lands less
# often than the VLM fallback costs time without saving a model call, so the
# counts here are the bench's "exact landing" column, not an opinion:
#
#   settings   16 screens    8/10   crawled inside the app (2026-11-09)
#   (calendar is not shipped: its main activity refuses to start on a
#    secondary display, see docs/map-building.md §11.3)
#   clock       2 screens    9/9    crawled inside the app (2026-11-09)
#
# Two maps are now built by the app itself, on the display the agent uses.
# Measured in-app, the host-crawled clock map scored 0/10 on that display and
# the host-crawled settings map 4/10.
#
# The settings map is now built by the app itself, on the display the agent
# uses. Measured in-app, the host-crawled map it replaces scored 4/10 on that
# same display -- a map is best on the screen it was recorded on.
#
# calculator (4/8, three screens) is not shipped: half of its operations end
# somewhere else, which is slower than letting the model do it.
#
# "<source>:<asset name>" pairs.
MAPS=(
    "k90_settings_inapp.yaml:settings.yaml"
    "k90_clock_inapp.yaml:clock.yaml"
)

mkdir -p "$DEST"
rm -f "$DEST"/*.yaml
for entry in "${MAPS[@]}"; do
    map="${entry%%:*}"
    name="${entry##*:}"
    if [ ! -f "$SRC/$map" ]; then
        echo "missing $SRC/$map -- generate it with phoneagent.build_map" >&2
        exit 1
    fi
    cp "$SRC/$map" "$DEST/$name"
done
echo "maps asset: $(ls -1 "$DEST" | tr '\n' ' ')"
