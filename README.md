# untis-pwa

Small Flask PWA for WebUntis timetables with optional admin mapping tools and Render-friendly persistence.

## Required environment
- `SECRET_KEY` – long random string (e.g., 64 hex chars). Needed so Flask signed cookies stay valid across deploys.
- `ADMIN_TOKEN` – password for `/admin/login`.
- Shared Untis settings: `UNTIS_BASE`, `UNTIS_SCHOOL`.
- EF: `UNTIS_USER`, `UNTIS_PASS`, `UNTIS_ELEMENT_ID`, and optional `UNTIS_ELEMENT_TYPE`.
- Q1: `UNTIS_USER_Q1`, `UNTIS_PASS_Q1`, `UNTIS_ELEMENT_ID_Q1`, and optional `UNTIS_ELEMENT_TYPE_Q1`.
- Q2: `UNTIS_USER_Q2`, `UNTIS_PASS_Q2`, `UNTIS_ELEMENT_ID_Q2`, and optional `UNTIS_ELEMENT_TYPE_Q2`.

Both username and password must be configured together for Q1 and Q2, and each enabled grade requires its own element ID. The optional element type inherits from EF when omitted.

At the yearly rollover, do not leave the same Untis account assigned to two grades. Move the previous Q1 account to the Q2 variables, move the previous EF account to the Q1 variables, and put the new EF account in the un-suffixed EF variables.

## Session settings
- Sessions are stateless signed cookies; no server-side session store.
- Cookies: `HttpOnly`, `Secure`, `SameSite=Lax`, lifetime 30 days, `SESSION_PERMANENT=True`.
- If `SECRET_KEY` changes, all users are logged out. Set it once in Render env and keep it stable.

## Optional remote backup (free) via Google Drive
`app.py` can POST backups to `BACKUP_WEBHOOK_URL` and auto-restore from `AUTO_RESTORE_URL` when the DB is empty.

### Apps Script (Web App) code
Deploy a Google Apps Script as a Web App (Anyone with link). Replace `SHARED_TOKEN` with the same value as Render's `BACKUP_WEBHOOK_TOKEN`.

```javascript
const FILE_NAME = "untis-backup.json";
const TOKEN = "SHARED_TOKEN";

function output(value) {
  return ContentService
    .createTextOutput(typeof value === "string" ? value : JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}

function authorized(e) {
  return !TOKEN || (e && e.parameter && e.parameter.token === TOKEN);
}

function newestValidBackup() {
  const files = DriveApp.getFilesByName(FILE_NAME);
  let result = null;
  while (files.hasNext()) {
    const file = files.next();
    const body = file.getBlob().getDataAsString();
    try {
      const parsed = JSON.parse(body);
      if (!parsed.database || !Array.isArray(parsed.database.users)) continue;
      if (!result || file.getLastUpdated() > result.file.getLastUpdated()) {
        result = {file, body};
      }
    } catch (error) {
      // Ignore empty or damaged duplicates left by older script versions.
    }
  }
  return result;
}

function doPost(e) {
  if (!authorized(e)) return output({ok: false, error: "unauthorized"});
  const body = e && e.postData ? e.postData.contents : "";
  const parsed = JSON.parse(body);
  if (!parsed.database || !Array.isArray(parsed.database.users)) {
    return output({ok: false, error: "invalid_backup"});
  }
  const current = newestValidBackup();
  if (current) current.file.setContent(body);
  else DriveApp.createFile(FILE_NAME, body, MimeType.JSON);
  return output({ok: true, users: parsed.database.users.length});
}

function doGet(e) {
  if (!authorized(e)) return output({ok: false, error: "unauthorized"});
  const current = newestValidBackup();
  return current ? output(current.body) : output({ok: false, error: "backup_not_found"});
}
```

After changing the script, create a new Web App deployment and update both Render URLs. Editing the source without creating a new deployment does not update the `/exec` endpoint.

### Render env for backup
- `BACKUP_WEBHOOK_URL` = Web App deploy URL (use POST).
- `AUTO_RESTORE_URL` = same Web App URL (GET).
- `BACKUP_WEBHOOK_TOKEN` = the same value as `TOKEN` in Apps Script.
- Optional: `AUTO_BACKUP_INTERVAL_MIN` = minutes between automatic backups (default 5). Requires `BACKUP_WEBHOOK_URL` to be set.
- Optional: `AUTO_RESTORE_FORCE` = `1/true` to restore from `AUTO_RESTORE_URL` on every cold start even if the DB already has rows (overwrites existing data).

## Quick tests
- Local: set `SECRET_KEY`, login, restart server → still logged in; cookie shows HttpOnly/Secure/SameSite=Lax, 30-day expiry.
- Render: set `SECRET_KEY`, deploy, login, redeploy → still logged in.
- Backup: click Admin ▸ Backup; confirm file appears in Drive; clear DB and restart to see auto-restore repopulate.
