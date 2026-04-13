export interface Env {
  BACKUPS?: R2Bucket;
  /** Optional pepper for vault id = SHA256(pepper + "\\n" + bearer_token). */
  BACKUP_VAULT_PEPPER?: string;
  /** Optional single origin for CORS (desktop app is unaffected if unset). */
  CORS_ALLOW_ORIGIN?: string;
}
