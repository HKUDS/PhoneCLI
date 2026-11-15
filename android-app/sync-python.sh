#!/usr/bin/env bash
# Copy the agent core into the APK's python source dir.
#
# maps/ is excluded on purpose: the app loads maps from assets/maps, which
# sync-maps.sh fills with the few that were measured -- not with every crawl
# that happens to be lying around in the repository.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf app/src/main/python/phoneagent
mkdir -p app/src/main/python
rsync -a --exclude '__pycache__' --exclude 'tests' --exclude '*.pyc' \
    --exclude 'maps' --exclude 'README.md' \
    ../phoneagent/ app/src/main/python/phoneagent/
# The crawler the app runs is the same one the host runs, and it imports the
# upstream helpers by package name. Both have to be inside the APK or
# `phoneagent.build_map` cannot load it there.
UPSTREAM="$PWD/../phonecli_android"
rm -rf app/src/main/python/phonecli app/src/main/python/phonecli_android
mkdir -p app/src/main/python/phonecli app/src/main/python/phonecli_android/evaluation
cp "$UPSTREAM"/phonecli/*.py app/src/main/python/phonecli/
cp "$UPSTREAM"/evaluation/build_map.py app/src/main/python/phonecli_android/evaluation/
touch app/src/main/python/phonecli_android/__init__.py
touch app/src/main/python/phonecli_android/evaluation/__init__.py

echo "synced: $(find app/src/main/python -name '*.py' | wc -l | tr -d ' ') python files"
