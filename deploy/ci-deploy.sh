#!/bin/bash
# Deploy bot ke Oracle VM. Dipakai GitHub Actions (workflow_dispatch) atau manual.
#
#   bash deploy/ci-deploy.sh deploy [sha]   -> install commit, update dependency, restart
#   bash deploy/ci-deploy.sh health          -> tunggu bot sehat, rollback otomatis kalau gagal
#   bash deploy/ci-deploy.sh rollback        -> kembalikan ke commit sebelumnya
set -eu

BOT_DIR="${BOT_DIR:-/home/ubuntu/bot-saham}"
SERVICE="${SERVICE:-bot-saham}"
STATE_FILE="$BOT_DIR/data/.last_deployed_sha"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-150}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-10}"
MODE="${1:-deploy}"
TARGET_SHA="${2:-}"

cd "$BOT_DIR"
mkdir -p data logs

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

# Interpreter yang dipakai service (venv atau system python), biar dependency
# diinstall ke tempat yang sama dengan yang dipakai bot saat jalan.
service_python() {
    bin=$(systemctl cat "$SERVICE" 2>/dev/null \
        | grep -m1 '^ExecStart=' \
        | sed 's/^ExecStart=//' \
        | awk '{print $1}' || true)
    if [ -n "$bin" ] && [ -x "$bin" ]; then
        echo "$bin"
    else
        echo python3
    fi
}

install_deps() {
    py=$(service_python)
    log "Install dependency dengan $py"
    "$py" -m pip install --disable-pip-version-check -r requirements.txt \
        || "$py" -m pip install --disable-pip-version-check --break-system-packages -r requirements.txt
}

do_rollback() {
    if [ ! -s "$STATE_FILE" ]; then
        log "ROLLBACK: commit sebelumnya tidak tercatat, skip"
        return 1
    fi
    prev=$(cat "$STATE_FILE")
    log "ROLLBACK: kembali ke commit $prev"
    git checkout -f master </dev/null
    git reset --hard "$prev" </dev/null
    install_deps || log "ROLLBACK: install dependency gagal, tetap restart"
    sudo -n systemctl restart "$SERVICE"
    log "ROLLBACK selesai, service direstart"
}

wait_healthy() {
    attempts=$(( ${1:-$HEALTH_TIMEOUT} / HEALTH_INTERVAL + 1 ))
    py=$(service_python)
    while [ "$attempts" -gt 0 ]; do
        attempts=$((attempts - 1))
        if systemctl is-active --quiet "$SERVICE"; then
            set -a
            . "$BOT_DIR/.env" 2>/dev/null || true
            set +a
            if "$py" scripts/health_check.py --json --quiet; then
                log "Health check: SEHAT"
                return 0
            fi
            log "Health check belum lolos, ulangi $HEALTH_INTERVAL detik lagi"
        else
            log "Service $SERVICE belum active, tunggu $HEALTH_INTERVAL detik"
        fi
        sleep "$HEALTH_INTERVAL"
    done
    log "Health check GAGAL sampai timeout"
    return 1
}

case "$MODE" in
    deploy)
        git rev-parse HEAD > "$STATE_FILE"
        log "Commit sebelumnya: $(cat "$STATE_FILE")"
        target="${TARGET_SHA:-origin/master}"
        log "Deploy $target"
        GIT_TERMINAL_PROMPT=0 git fetch --quiet origin master </dev/null
        git checkout -f master </dev/null
        git reset --hard "$target" </dev/null
        log "Sekarang di commit $(git rev-parse HEAD)"
        if ! install_deps; then
            log "Install dependency gagal, rollback"
            do_rollback || true
            exit 1
        fi
        sudo -n systemctl restart "$SERVICE"
        log "Service $SERVICE direstart, cek kesehatan di step berikutnya"
        ;;
    health)
        if wait_healthy "$HEALTH_TIMEOUT"; then
            exit 0
        fi
        log "Health check gagal -> rollback otomatis"
        do_rollback || true
        exit 1
        ;;
    rollback)
        do_rollback
        ;;
    *)
        echo "Mode tidak dikenal: $MODE (pilihan: deploy|health|rollback)" >&2
        exit 2
        ;;
esac
