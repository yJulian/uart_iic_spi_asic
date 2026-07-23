#!/usr/bin/env bash
# Creates (or reuses) a Python venv at the repo root and installs the
# cocotb testbench dependencies. Run this once inside `nix-shell
# shell.nix` from the LibreLane checkout before running `make sim`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"

if [ ! -d "${VENV_DIR}" ]; then
    echo "Creating venv at ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip -q
pip install -r "${REPO_ROOT}/tb/requirements.txt" -q

echo "cocotb environment ready. Activate it with:"
echo "  source ${VENV_DIR}/bin/activate"
