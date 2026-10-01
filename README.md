# Drive Uploader

A small app that uploads a folder to Google Drive — **resumable**, and it
**skips files that are already on Drive** (same name + same size), so an
interrupted upload is finished by simply pressing **Start Upload** again.

## How to use

1. **First time only:** double-click **`Connect Google Account.bat`**.
   A browser opens — log in with the Google account that owns your Drive
   and click *Allow*. You never need to do this again (unless you change
   your Google password).

2. Double-click **`Start Drive Uploader.bat`**.

3. Pick the folder to upload (e.g. `D:\ALi Saad\Books\linkedin learning`).

4. Choose the destination:
   - **Existing folder** — paste your Drive folder's link (copy it from the
     browser address bar while the folder is open in Drive). The app remembers
     it for next time, and pre-fills it automatically if a `default_link.txt`
     file exists next to `uploader.py` (that file is gitignored).
   - **New folder** — creates a folder in My Drive with the name you type.

5. Optional: **Check what's uploaded** — compares your local folder with
   Drive and tells you exactly which files are already there and which are
   still missing. Saves the details to:
   - `upload_report.txt` — every file, marked `=` (uploaded), `+` (missing),
     `*` (different size), `-` (on Drive only)
   - `missing_on_drive.txt` — just the files that still need uploading

6. Press **Start Upload** and leave it running. You can close the app at
   any time — restarting and pressing Start again continues where it left off.

## Good to know

- **Nothing is ever deleted.** The app only adds/overwrites files; it never
  removes anything from Drive or from your PC.
- Files already on Drive with the **same size** are skipped instantly, so
  restarting is fast.
- Google allows up to ~750 GB of uploads per day; your ~52 GB fits easily.
- Keep the PC awake while uploading (Settings → Power → screen off is fine,
  sleep is not). Closing the app's window pauses the upload — just restart it.
- You can move this whole `DriveUploader` folder anywhere; it is self-contained.
- If the log shows repeated `403 rate limit` errors, Google is throttling —
  pause for an hour and press Start again.
- If it ever says `invalid_grant` / `token expired`, run
  `Connect Google Account.bat` once more.

## Notes for anyone cloning this repo

- Requires **Python 3** with tkinter (included in the standard Windows
  installer — check "tcl/tk and IDLE" during setup).
- `tools/rclone.exe` (Windows x64) is bundled for convenience. rclone is
  © the rclone contributors, MIT licensed — see <https://rclone.org/licence/>.
- Nothing personal is stored in this repo: your Drive folder link, settings
  and reports all live in gitignored files created on your machine.
