# Uploading call-log folders (one page)

You need the page's address and the passphrase from the Owner. Nothing to
install, no AWS account. The **EN / VI** switch at the top shows the page in
English or Vietnamese; the browser remembers the choice.

**Each day**

1. Open the page and type the passphrase.
2. On the **Upload** tab, choose the day's folder, for example
   `Nguyen Van A & 2026-06-01`, or a folder that holds several such folders.
3. Each folder is checked first. A refused folder says why, in plain words.
   Fix it on your PC (rename it, take out extra files) and choose it again.
   Nothing is uploaded from a refused folder.
4. Click **Upload** and keep the page open until every folder says
   *Uploaded*.
5. Click **Ready: start processing**. Only then does processing start. Once
   every folder says *Waiting* (or *Waiting for Re-run*, below), you can close
   the page.

**Afterwards**

- The **Status** tab shows each day of the last 7 days. *Done* means the
  Sheet was updated. *Waiting* or *Processing* means wait; it refreshes by
  itself every minute.
- *Upload not finished* means some images never arrived: choose the folder
  again and upload it again.
- To correct a day, upload the whole folder again (every image, not only the
  new ones: uploads are never combined). Because the day already has an
  upload, the new one says *Waiting for Re-run* and nothing changes yet. Click
  its **Re-run (use this upload)** on the Status tab: it then replaces the
  day's results and, with sync on, the day's tab in the Sheet. Without that
  click, the earlier upload stays (and is still sent if it wasn't yet). After
  the click it is queued like any other folder. With two or more waiting,
  **Re-run all waiting uploads** below the table does them all at once.
- *Failed* is retried by itself. *Stopped after 3 tries* means it failed 3
  times: the Owner gets an email and looks into it.
- The **Day Sheets** tab finds a stored Day Sheet by name and/or date, to
  download it.

**Rules a folder must follow**

- Its name is `<Name> & <yyyy-mm-dd>` with a real date, and the name is a
  registered employee.
- At most 50 images, each at most 15 MB, as `.jpg`, `.jpeg`, `.png`, `.webp`
  or `.bmp`. HEIC photos aren't read: set the phone to save screenshots as
  JPEG, or convert them.

If the page says *Wrong passphrase* or stops answering, tell the Owner. The
**Owner** tab needs the Owner's own password; the upload passphrase doesn't
open it.
