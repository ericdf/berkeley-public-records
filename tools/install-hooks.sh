#!/bin/sh
# Install this repository's git hooks (the 25 MB pre-commit check). Run once per clone:
#   sh tools/install-hooks.sh
set -e
cd "$(git rev-parse --show-toplevel)"
chmod +x tools/hooks/*
git config core.hooksPath tools/hooks
echo "Hooks installed: commits of files over 25 MB will be refused."
