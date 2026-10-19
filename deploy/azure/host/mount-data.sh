#!/usr/bin/env bash
# FUTURE TARGET ONLY. Never run on this workstation. Formatting is a separate opt-in.
set -euo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run as root on the approved VM' >&2; exit 1; }
[[ $# -le 1 ]] || exit 1
initialize=${1:-}
[[ -z "$initialize" || "$initialize" == '--approve-initializing-new-empty-scopeshield-data' ]] || exit 1
device=$(readlink -e /dev/disk/azure/scsi1/lun0)
[[ -b "$device" ]] || { echo 'Expected Azure SCSI LUN 0' >&2; exit 1; }
[[ $(blockdev --getsize64 "$device") == 34359738368 ]] || { echo 'Expected the approved 32 GiB data disk' >&2; exit 1; }
mount_dir=/srv/scopeshield-data
if mountpoint -q "$mount_dir"; then
    [[ $(readlink -e "$(findmnt -rn -o SOURCE --target "$mount_dir")") == "$device" ]] || exit 1
    [[ -z "$initialize" ]] || { echo 'Refusing initialization of a mounted disk' >&2; exit 1; }
else
    [[ -z $(findmnt -rn -S "$device") ]] || { echo 'Disk is mounted elsewhere' >&2; exit 1; }
    [[ $(lsblk -nr -o TYPE "$device") == disk ]] || { echo 'Unexpected partitions; stop for review' >&2; exit 1; }
    filesystem=$(blkid -o value -s TYPE "$device" || true)
    if [[ -n "$initialize" ]]; then
        [[ -z "$filesystem" && -z $(wipefs --no-act --noheadings "$device") ]] || { echo 'Existing signature; NEVER format' >&2; exit 1; }
        # Only a newly created Empty Bicep data disk, after explicit operator approval.
        mkfs.ext4 -L scopeshield-data "$device"
    else
        [[ "$filesystem" == ext4 ]] || { echo 'No approved ext4 filesystem. Initialization requires separate approval.' >&2; exit 1; }
    fi
    [[ ! -d "$mount_dir" || -z $(find "$mount_dir" -mindepth 1 -maxdepth 1 -print -quit) ]] || { echo 'Mount would hide existing files' >&2; exit 1; }
    install -d -m 0700 "$mount_dir"
    disk_uuid=$(blkid -s UUID -o value "$device")
    [[ "$disk_uuid" =~ ^[a-f0-9-]{36}$ ]] || exit 1
    if awk '$2 == "/srv/scopeshield-data" { found=1 } END { exit !found }' /etc/fstab; then
        awk -v expected="UUID=$disk_uuid" '$2 == "/srv/scopeshield-data" && $1 != expected { bad=1 } END { exit bad }' /etc/fstab
    else
        printf 'UUID=%s /srv/scopeshield-data ext4 defaults,nodev,nosuid,noexec,nofail,x-systemd.device-timeout=30 0 2\n' "$disk_uuid" >> /etc/fstab
    fi
    mount "$mount_dir"
fi
[[ $(findmnt -rn -o FSTYPE --target "$mount_dir") == ext4 ]] || exit 1
if [[ ! -e "$mount_dir/.scopeshield-volume" ]]; then
    [[ -n "$initialize" && ! -e "$mount_dir/scopeshield.db" ]] || { echo 'Missing identity on existing storage; stop' >&2; exit 1; }
    cat /proc/sys/kernel/random/uuid > "$mount_dir/.scopeshield-volume"
fi
chown scopeshield:scopeshield "$mount_dir" "$mount_dir/.scopeshield-volume"
chmod 0700 "$mount_dir"
chmod 0600 "$mount_dir/.scopeshield-volume"
echo 'Data mount verified. Record device UUID and marker privately; no application started.'
