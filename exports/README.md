# Drop your WhatsApp exports here

Put the `.zip` files WhatsApp gives you straight into this folder, then run
`merge.bat` (Windows) or `python -m app` from the project root.

Nothing in this folder is ever committed to git — see [`.gitignore`](../.gitignore).

## Getting an export out of WhatsApp

**On your phone** (exports can only be made from the phone, not WhatsApp Web):

1. Open the conversation.
2. **iPhone:** tap the contact/group name at the top → scroll down → **Export Chat**.
   **Android:** tap **⋮** → **More** → **Export chat**.
3. Choose **Attach Media** if you want photos, voice notes and documents in the
   archive. Choose **Without Media** for a text-only export — much smaller, and
   WhatsMerge will show a placeholder where each attachment was.
4. Send the file to yourself (email, Drive, Files) and save it here.

WhatsApp exports one conversation per file. Repeat for each chat you want.

## You can drop in

- `.zip` archives exactly as WhatsApp produced them — **this is the easy path**
- `.txt` transcripts, if you already unzipped them (keep the media files beside them)
- Subfolders — nesting is fine, everything below this folder is picked up

## Several exports of the same chat

Drop them all in. WhatsMerge recognises them as one conversation and stitches
them into a single timeline, counting the overlap only once. Filenames do not
matter:

```
exports/
├─ WhatsApp Chat with Ahmed Al-Rashid.zip          ← March export
├─ WhatsApp Chat with Ahmed Al-Rashid (2).zip      ← June export, overlaps
├─ ahmed-backup-2026.zip                           ← renamed, still matched
└─ WhatsApp Chat with Gulf Shipments Q1.zip
```

The "Attach Media" and "Without Media" exports of the same chat also combine:
where one has a real photo and the other only `<Media omitted>`, the real file
wins.

## A note on size

An export with media can be large. Media is embedded in the output HTML by
default, so a 300 MB export produces a roughly 400 MB file. If the result is
unwieldy, run with `--media external` to keep media in a folder beside the HTML
instead.
