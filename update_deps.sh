#!/usr/bin/env bash
set -euo pipefail

CONDA_ENV="${CLIPPER_CONDA_ENV:-}"
if [[ -z "${CONDA_ENV}" ]]; then
  for candidate in "${HOME}/anaconda3/envs/clipper" "${HOME}/miniconda3/envs/clipper"; do
    if [[ -x "${candidate}/bin/pip" ]]; then
      CONDA_ENV="${candidate}"
      break
    fi
  done
fi

if [[ -n "${CONDA_ENV}" && -x "${CONDA_ENV}/bin/pip" ]]; then
  "${CONDA_ENV}/bin/pip" install -q -U yt-dlp
  echo "Updated yt-dlp version: $("${CONDA_ENV}/bin/yt-dlp" --version)"
else
  pip install -q -U yt-dlp
  echo "Updated yt-dlp version: $(yt-dlp --version)"
fi
