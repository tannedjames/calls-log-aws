# Uploading call-log folders (one page)

You need the page's address and the passphrase from the Owner. Nothing to
install, no AWS account.

**Each day**

1. Open the page and type the passphrase.
2. Under **Upload folders**, choose the day's folder, for example
   `Nguyen Van A & 2026-06-01`, or a folder that holds several such folders.
3. Each folder is checked first. A refused folder says why, in plain words.
   Fix it on your PC (rename it, take out extra files) and choose it again.
   Nothing is uploaded from a refused folder.
4. Click **Upload** and keep the page open until every folder says
   *Uploaded*.
5. Click **Mark uploaded folders Ready**. Only then does processing start.

**Afterwards**

- The status table shows each day of the last 7 days. *Done* means the Sheet
  was updated. *Pending* or *Processing* means wait; it refreshes by itself
  every minute.
- *Upload not finished* means some images never arrived: choose the folder
  again and upload it again.
- To correct a day, upload the whole folder again. The new upload replaces the
  day's results.
- *Failed* is retried by itself. *Parked* means it failed 3 times: the Owner
  gets an email and looks into it.

**Rules a folder must follow**

- Its name is `<Name> & <yyyy-mm-dd>` with a real date, and the name is a
  registered employee.
- At most 50 images, each at most 15 MB, as `.jpg`, `.jpeg`, `.png`, `.webp`
  or `.bmp`. HEIC photos aren't read: set the phone to save screenshots as
  JPEG, or convert them.

If the page says *Wrong passphrase* or stops answering, tell the Owner.
