#!/usr/bin/env bash
# FUTURE TARGET ONLY. Explicit actions; no default deployment or provider calls.
set -euo pipefail
umask 077
[[ $EUID -eq 0 && $# -ge 1 ]] || exit 1
action=$1; shift
root=/opt/scopeshield
release=$(readlink -e "$root")
[[ -L "$root" && "$release" =~ ^/opt/scopeshield-releases/[a-f0-9]{40}$ ]] || { echo 'Unexpected release link' >&2; exit 1; }
python=$root/.venv/bin/python
helper=$root/deploy/azure/host/manage.py
service_run() {
    systemd-run --wait --pipe --collect --unit="scopeshield-maintenance-$(date +%s)-$$" \
        -p User=scopeshield -p Group=scopeshield -p WorkingDirectory="$root" \
        -p EnvironmentFile=/etc/scopeshield/backend.env -p UMask=0077 \
        -p Environment=PYTHONDONTWRITEBYTECODE=1 \
        -p NoNewPrivileges=true -p PrivateTmp=true -p ProtectSystem=strict \
        -p ReadWritePaths=/srv/scopeshield-data \
        -p MemoryMax=768M -p TasksMax=30 "$@"
}
stopped() {
    if systemctl is-active --quiet scopeshield-api.service || systemctl is-active --quiet scopeshield-web.service; then
        echo 'Stop both API and web before maintenance' >&2
        return 1
    fi
}
case "$action" in
configure)
    stopped
    "$python" "$root/deploy/azure/host/configure.py"
    ;;
build)
    stopped
    # Only generated public build settings; never load a workstation environment.
    grep -q '^SCOPESHIELD_FRONTEND_PROFILE=production$' /etc/scopeshield/frontend.env
    chown -R scopeshield:scopeshield "$(readlink -e "$root")/frontend"
    systemd-run --wait --pipe --collect --unit="scopeshield-build-$$" \
        -p User=scopeshield -p Group=scopeshield -p WorkingDirectory="$root/frontend" \
        -p EnvironmentFile=/etc/scopeshield/frontend.env -p MemoryMax=2500M \
        -p UMask=0022 \
        -p Environment=PATH=/usr/local/bin:/usr/bin:/bin \
        /bin/bash -c 'pnpm install --frozen-lockfile && pnpm exec next build --webpack'
    chown -R root:root "$(readlink -e "$root")/frontend"
    chown -R scopeshield:scopeshield "$root/frontend/.next"
    ;;
migrate)
    stopped
    service_run "$python" "$helper" migrate
    ;;
judge-create|judge-revoke)
    stopped
    [[ $# -eq 1 && "$1" =~ ^[a-zA-Z0-9._-]{3,80}$ ]] || exit 1
    service_run "$python" "$helper" validate
    service_run "$python" "$helper" inspect
    verb=create; flags=(--prepare-demo)
    if [[ "$action" == judge-revoke ]]; then verb=revoke; flags=(); fi
    systemd-run --pty --wait --collect --unit="scopeshield-judge-$$" \
        -p User=scopeshield -p Group=scopeshield -p WorkingDirectory="$root" \
        -p EnvironmentFile=/etc/scopeshield/backend.env \
        -p MemoryMax=768M -p TasksMax=30 -p UMask=0077 -p NoNewPrivileges=true \
        -p ProtectSystem=strict -p ReadWritePaths=/srv/scopeshield-data \
        "$python" -B -m scripts.provision_judge "$verb" --username "$1" "${flags[@]}"
    ;;
start|restart)
    [[ $# -eq 1 && "$1" == '--approve-host-activation' ]] || exit 1
    service_run "$python" "$helper" validate
    service_run "$python" "$helper" inspect
    if [[ "$action" == restart ]]; then service_run "$python" "$helper" backup; fi
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
    systemd-analyze verify /etc/systemd/system/scopeshield-api.service /etc/systemd/system/scopeshield-web.service
    systemctl unmask caddy.service
    systemctl enable scopeshield-api.service scopeshield-web.service caddy.service
    systemctl "$action" scopeshield-api.service scopeshield-web.service caddy.service
    ;;
backup|inspect|validate)
    [[ $# -eq 0 ]] || exit 1
    service_run "$python" "$helper" "$action"
    ;;
upload-backup)
    [[ $# -eq 1 && "$1" =~ ^backup-[0-9TZ]+-[a-f0-9]{8}\.db\.age$ ]] || exit 1
    service_run "$python" "$root/deploy/azure/host/blob_backup.py" upload --name "$1"
    ;;
enable-backups)
    [[ $# -eq 1 && "$1" == '--approve-verified-offhost-recovery' ]] || exit 1
    service_run "$python" "$helper" backup
    systemctl enable --now scopeshield-backup.timer
    ;;
restore)
    [[ $# -eq 2 ]] || exit 1
    systemctl stop scopeshield-api.service scopeshield-web.service caddy.service
    service_run "$python" "$helper" restore --source "$1" --target "$2"
    echo 'QUARANTINED. Current DB preserved. No environment switch, marker removal or restart was performed.'
    ;;
rollback)
    [[ $# -eq 2 && "$2" == '--approve-compatible-release-switch' && "$1" =~ ^[a-f0-9]{40}$ ]] || exit 1
    prior=/opt/scopeshield-releases/$1
    [[ -d "$prior" && ! -L "$prior" && -f "$prior/alembic.ini" && -d "$prior/migrations/versions" && -f "$prior/deploy/azure/host/manage.py" && -f "$prior/deploy/azure/host/ops.sh" && -x "$prior/.venv/bin/python" && -d "$prior/frontend/.next" ]] || exit 1
    service_run "$python" "$helper" compatible --release "$prior"
    systemctl stop scopeshield-api.service scopeshield-web.service caddy.service
    service_run "$python" "$helper" backup
    [[ ! -e /opt/scopeshield-switch && ! -L /opt/scopeshield-switch && -L "$root" ]] || exit 1
    ln -s "$prior" /opt/scopeshield-switch
    mv -T /opt/scopeshield-switch "$root"
    echo 'Compatible release selected, current DB retained. Validate and explicitly activate; no downgrade/restart.'
    ;;
export-backup)
    [[ $# -eq 1 && "$1" =~ ^backup-[0-9TZ]+-[a-f0-9]{8}\.db\.age$ ]] || exit 1
    source=/srv/scopeshield-data/backups/$1
    [[ -f "$source" && ! -L "$source" ]] || exit 1
    install -d -m 0700 -o azureuser -g azureuser /home/azureuser/scopeshield-exports
    [[ ! -e /home/azureuser/scopeshield-exports/$1 ]] || exit 1
    install -m 0600 -o azureuser -g azureuser "$source" /home/azureuser/scopeshield-exports/
    sha256sum "/home/azureuser/scopeshield-exports/$1"
    ;;
*) echo 'Unknown action; nothing executed' >&2; exit 1 ;;
esac
