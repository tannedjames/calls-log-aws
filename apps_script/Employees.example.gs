/**
 * Employees.example.gs -- TEMPLATE with fake values. Safe to commit.
 *
 * Copy to Employees.gs (gitignored) and fill in the real names and IDs.
 * Do NOT add this example file to the live Apps Script project: it defines
 * the same variables as Employees.gs and would override the real map.
 *
 * NAME_TO_ID keys are the employee's folder-name prefix, lower-cased with
 * accents stripped and "đ" -> "d" (e.g. "Nguyễn Văn A & 2026-06-01" ->
 * 'nguyen van a'). Matching is by PREFIX and the first matching key wins, so
 * never add a key that is a prefix of another person's name.
 * The value is the ID from the spreadsheet's URL:
 *   https://docs.google.com/spreadsheets/d/<THIS PART>/edit
 */

var NAME_TO_ID = {
  'nguyen van a':       'SPREADSHEET_ID_OF_NGUYEN_VAN_A',
  'tran thi b':         'SPREADSHEET_ID_OF_TRAN_THI_B'
};

// Parked/inactive employees: backfillMonthLinks() never touches these.
var BACKFILL_SKIP_IDS = [
];

// purgePreAugust() only ever touches these freshly-copied spreadsheets.
var PURGE_SHEET_IDS = [
];
