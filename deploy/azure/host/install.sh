#!/usr/bin/env bash
# FUTURE INITIAL INSTALL ONLY. No cloud-init, extension, remote execution or auto-start.
set -euo pipefail
# This phase installs public source/runtimes. Explicit 0600 protects settings;
# code/venv paths must remain traversable by the unprivileged service account.
umask 022
[[ $EUID -eq 0 && $# -eq 2 ]] || { echo 'Usage: sudo bash install.sh <release.tar> <approved-sha256>' >&2; exit 1; }
artifact=$(readlink -e "$1")
expected_sha=$2
[[ "$expected_sha" =~ ^[a-f0-9]{64}$ ]] || exit 1
[[ ! -e /opt/scopeshield && ! -L /opt/scopeshield && ! -e /etc/scopeshield ]] || { echo 'Initial installer refuses an existing application' >&2; exit 1; }
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 && $(uname -m) == x86_64 ]] || exit 1
printf '%s  %s\n' "$expected_sha" "$artifact" | sha256sum --check --status
# Prevent the distribution Caddy package from serving its default site on install.
systemctl mask caddy.service
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install --yes python3.12 python3.12-venv ca-certificates curl xz-utils caddy age
getent passwd scopeshield >/dev/null || useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin scopeshield
install -d -m 0755 /opt/scopeshield-releases
release_dir=$(python3.12 - "$artifact" <<'PY'
import json, pathlib, re, sys, tarfile
with tarfile.open(sys.argv[1]) as archive:
    commit = json.load(archive.extractfile('release-manifest.json'))['commit']
    if not re.fullmatch(r'[a-f0-9]{40}', commit): raise ValueError('Invalid release commit')
    root = pathlib.Path('/opt/scopeshield-releases') / commit
    if root.exists(): raise ValueError('Release already exists; never overwrite')
    for member in archive.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()): raise ValueError('Unsafe archive')
        if path.name.startswith('.env') and not path.name.endswith('.example'): raise ValueError('Environment file in archive')
        if path.suffix in {'.db', '.pem', '.key', '.pub'}: raise ValueError('Data/key in archive')
    root.mkdir(mode=0o755)
    archive.extractall(root, filter='data')
    print(root)
PY
)
node_version=24.22.0
node_sha=cc2f601cee82d9a5a233302fe307abc600727035fd7d2c3a3571755989e95008
node_archive=/opt/scopeshield-releases/node-v${node_version}-linux-x64.tar.xz
[[ ! -e "$node_archive" && ! -e /opt/node-v${node_version}-linux-x64 ]] || { echo 'Existing Node artifact needs review' >&2; exit 1; }
curl --fail --proto '=https' --tlsv1.2 --output "$node_archive" "https://nodejs.org/dist/v${node_version}/node-v${node_version}-linux-x64.tar.xz"
printf '%s  %s\n' "$node_sha" "$node_archive" | sha256sum --check --status
tar --no-same-owner -xJf "$node_archive" -C /opt
for command in node npm npx; do
    [[ ! -e /usr/local/bin/$command && ! -L /usr/local/bin/$command ]] || { echo 'Existing runtime; stop for review' >&2; exit 1; }
    ln -s "/opt/node-v${node_version}-linux-x64/bin/$command" "/usr/local/bin/$command"
done
npm install --global --prefix /usr/local pnpm@11.25.0 --ignore-scripts --no-audit --no-fund
python3.12 -m venv "$release_dir/.venv"
chown -R scopeshield:scopeshield "$release_dir/.venv"
runuser -u scopeshield -- "$release_dir/.venv/bin/python" -m pip install --requirement "$release_dir/requirements-day5.lock.txt" --no-cache-dir
runuser -u scopeshield -- "$release_dir/.venv/bin/python" -m pip check
# Report fonts ship inside the pinned dependency; no runtime font download/host font assumption.
runuser -u scopeshield -- "$release_dir/.venv/bin/python" -I -c 'import reportlab, pypdf; from pathlib import Path; root=Path(reportlab.__file__).parent / "fonts"; assert all((root / name).is_file() for name in ("Vera.ttf", "VeraBd.ttf"))'
chown -R root:root "$release_dir/.venv"
ln -s "$release_dir" /opt/scopeshield
install -d -m 0700 /etc/scopeshield
install -m 0600 "$release_dir/deploy/azure/host/settings.example.json" /etc/scopeshield/settings.json
install -m 0644 "$release_dir/deploy/scopeshield-api.service" /etc/systemd/system/
install -m 0644 "$release_dir/deploy/scopeshield-web.service" /etc/systemd/system/
install -m 0644 "$release_dir/deploy/azure/host/scopeshield-backup.service" /etc/systemd/system/
install -m 0644 "$release_dir/deploy/azure/host/scopeshield-backup.timer" /etc/systemd/system/
systemctl daemon-reload
echo 'Installed only. Caddy remains masked; application and backup timer are NOT enabled/started.'
echo 'Next: approved new-disk initialization, configure exact FQDN, build, migrate and provision private judges.'
