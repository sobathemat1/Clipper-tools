#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  source "${ROOT_DIR}/.env"
  set +a
fi

CONDA_ENV="${CLIPPER_CONDA_ENV:-}"
if [[ -z "${CONDA_ENV}" ]]; then
  for candidate in "${HOME}/anaconda3/envs/clipper" "${HOME}/miniconda3/envs/clipper"; do
    if [[ -x "${candidate}/bin/uvicorn" ]]; then
      CONDA_ENV="${candidate}"
      break
    fi
  done
fi

if [[ -n "${CONDA_ENV}" && -x "${CONDA_ENV}/bin/uvicorn" ]]; then
  SITE_PKG="${CONDA_ENV}/lib/python3.11/site-packages"
  if [[ -d "${SITE_PKG}/nvidia/cublas/lib" || -d "${SITE_PKG}/nvidia/cudnn/lib" ]]; then
    export LD_LIBRARY_PATH="${SITE_PKG}/nvidia/cublas/lib:${SITE_PKG}/nvidia/cudnn/lib:${LD_LIBRARY_PATH:-}"
  fi
  exec "${CONDA_ENV}/bin/uvicorn" backend.app.main:app --host 0.0.0.0 --port "${CLIPPER_PORT:-8000}"
fi

exec uvicorn backend.app.main:app --host 0.0.0.0 --port "${CLIPPER_PORT:-8000}"
