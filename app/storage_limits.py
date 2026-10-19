"""One recoverable storage envelope shared by SQLite and off-host backups."""
MAX_DATABASE_BYTES = 1024 * 1024 * 1024
# Single, unarmored age X25519 recipient: <=16 bytes per 64 KiB plus small header.
MAX_ENCRYPTED_BACKUP_BYTES = MAX_DATABASE_BYTES + 1024 * 1024
TRANSFER_CHUNK_BYTES = 4 * 1024 * 1024
