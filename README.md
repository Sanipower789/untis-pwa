# untis-pwa

Small Flask PWA for WebUntis timetables with optional admin mapping tools and Render-friendly persistence.

## Required environment
- `SECRET_KEY` – long random string (e.g., 64 hex chars). Needed so Flask signed cookies stay valid across deploys.
- `ADMIN_TOKEN` – password for `/admin/login`.
- `UNTIS_BASE`, `UNTIS_SCHOOL`, `UNTIS_USER`, `UNTIS_PASS` (and optional `UNTIS_USER_Q1`, `UNTIS_PASS_Q1`, `UNTIS_ELEMENT_*`) for WebUntis access.

## Session settings
- Sessions are stateless signed cookies; no server-side session store.
- Cookies: `HttpOnly`, `Secure`, `SameSite=Lax`, lifetime 30 days, `SESSION_PERMANENT=True`.
- If `SECRET_KEY` changes, all users are logged out. Set it once in Render env and keep it stable.

## Optional remote backup (free) via Google Drive
`app.py` can POST backups to `BACKUP_WEBHOOK_URL` and auto-restore from `AUTO_RESTORE_URL` when the DB is empty.

### Apps Script (Web App) code
Deploy a Google Apps Script as a Web App (Anyone with link). Replace `SHARED_TOKEN` if you want a shared-secret header check.

```javascript
const FILE_NAME = "untis-backup.json";
const TOKEN = "SHARED_TOKEN"; // optional; empty to disable

function doPost(e) {
  if (TOKEN && e?.parameter?.token !== TOKEN && e?.headers?.["x-backup-token"] !== TOKEN) {
    return ContentService.createTextOutput("unauthorized").setMimeType(ContentService.MimeType.TEXT).setResponseCode(401);
  }
  const body = e.postData?.getDataAsString() || "{}";
  DriveApp.createFile(FILE_NAME, body, MimeType.JSON); // creates new version each time
  return ContentService.createTextOutput("ok").setMimeType(ContentService.MimeType.TEXT);
}

function doGet(e) {
  if (TOKEN && e?.parameter?.token !== TOKEN && e?.headers?.["x-backup-token"] !== TOKEN) {
    return ContentService.createTextOutput("unauthorized").setMimeType(ContentService.MimeType.TEXT).setResponseCode(401);
  }
  const files = DriveApp.getFilesByName(FILE_NAME);
  if (!files.hasNext()) {
    return ContentService.createTextOutput("{}").setMimeType(ContentService.MimeType.JSON);
  }
  const file = files.next();
  return ContentService.createTextOutput(file.getBlob().getDataAsString()).setMimeType(ContentService.MimeType.JSON);
}
```

### Render env for backup
- `BACKUP_WEBHOOK_URL` = Web App deploy URL (use POST).
- `BACKUP_WEBHOOK_TOKEN` = `SHARED_TOKEN` if you set one (sent as `Authorization: Bearer ...`).
- `AUTO_RESTORE_URL` = same Web App URL (GET).
- Optional: `AUTO_BACKUP_INTERVAL_MIN` = minutes between automatic backups (default 5). Requires `BACKUP_WEBHOOK_URL` to be set.
- Optional: `AUTO_RESTORE_FORCE` = `1/true` to restore from `AUTO_RESTORE_URL` on every cold start even if the DB already has rows (overwrites existing data).

## Quick tests
- Local: set `SECRET_KEY`, login, restart server → still logged in; cookie shows HttpOnly/Secure/SameSite=Lax, 30-day expiry.
- Render: set `SECRET_KEY`, deploy, login, redeploy → still logged in.
- Backup: click Admin ▸ Backup; confirm file appears in Drive; clear DB and restart to see auto-restore repopulate.
