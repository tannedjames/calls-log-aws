/**
 * Call-log Sheets sync — Web App endpoint.
 *
 * Receives a JSON POST from the local OCR tool (sheets_sync.py) and, for the
 * matching person's spreadsheet:
 *   1. creates a yyyy-mm-dd worksheet by duplicating the "Mẫu" day template
 *      (if it does not already exist), placed in date order;
 *   2. writes the OCR'd session rows into B7:D22 (+ B2 total, B3 first start),
 *      mirroring excel_writer.py;
 *   3. sets the month-sheet column-B cell for that date to a HYPERLINK
 *      ("...#gid=<newgid>"; "Mở->") pointing at the new tab.
 *
 * Time cells (B/C) are written as NUMERIC day-fractions (like excel_writer.py),
 * NOT as =TIME() formulas, so they are immune to the spreadsheet's argument
 * separator locale (this sheet uses ";", which would break a comma formula).
 *
 * Deploy: Deploy > New deployment > Web app, "Execute as: Me",
 * "Who has access: Anyone". After ANY edit, redeploy a NEW VERSION.
 *
 * Needs Employees.gs (gitignored; template: Employees.example.gs) in the same
 * Apps Script project -- it defines NAME_TO_ID and the maintenance ID lists.
 */

// ---- Settings -------------------------------------------------------------
//
// SECRET is redacted in the committed copy of this file. The DEPLOYED Apps
// Script project holds the real value, and it must match SHEETS_SYNC_SECRET in
// secrets_local.py (gitignored) or every sync fails with "bad secret".
//
// When pasting this file into the Apps Script editor, restore the real secret
// first -- do not paste the placeholder over a working deployment.
//
// Better long-term: move it into Script Properties and read it with
//   PropertiesService.getScriptProperties().getProperty('SECRET')
// so the literal never lives in the file at all.
var SECRET = 'REDACTED_SEE_secrets_local.py';

// NAME_TO_ID (employee name prefix -> spreadsheet ID), BACKFILL_SKIP_IDS and
// PURGE_SHEET_IDS are personal data, so they live in Employees.gs, which is
// gitignored. Apps Script shares one global scope across a project's .gs
// files, so the code below reads them unchanged. Format: Employees.example.gs.

// Layout (mirrors config.py)
var FIRST_ROW = 7, LAST_ROW = 22;
var COL_START = 2, COL_END = 3, COL_SPAN = 4; // B, C, D

// ---- Helpers --------------------------------------------------------------
function timeFrac_(h, m){ return (h * 3600 + m * 60) / 86400; }  // Excel/Sheets day-fraction

function norm_(s){
  return String(s || '')
    .toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/đ/g, 'd')
    .replace(/\s+/g, ' ')
    .trim();
}
function matchId_(name){
  var k = norm_(name);
  for (var key in NAME_TO_ID){
    if (k === key || k.indexOf(key) === 0) return NAME_TO_ID[key];
  }
  return null;
}
function findTemplate_(sheets){
  return sheets.filter(function(s){
    var n = s.getName();
    return /(Mẫu|Sample|サンプル|テンプレート)/i.test(n) && !/(đăng|bài|post)/i.test(n);
  })[0];
}
function findMonth_(sheets, m){
  var re1 = new RegExp('Tháng\\s*0?' + m + '\\b');
  var re2 = new RegExp('(^|[^0-9])0?' + m + '月');
  return sheets.filter(function(s){
    var n = s.getName();
    return re1.test(n) || re2.test(n);
  })[0];
}
// Year-aware month-tab lookup used by both link directions. Built on
// monthKey_ (below), so it handles "📅 Tháng 9 2026" AND full-width
// "📅 ２０２６年９月" (findMonth_ alone misses the latter: JS \d is ASCII-only,
// see asciiDigits_). A tab whose name carries no year counts as 2026 (same
// default as monthKey_). Falls back to the old findMonth_ only when that tab
// names no other year, so a 2027-01 day can never link to "Tháng 1 2026".
function findMonthTab_(sheets, y, m){
  var hit = sheets.filter(function(s){
    var k = monthKey_(s.getName());
    return k && k.y === y && k.m === m;
  })[0];
  if (hit) return hit;
  var old = findMonth_(sheets, m);
  if (!old) return undefined;
  var yr = /(\d{4})/.exec(asciiDigits_(old.getName()));
  return (!yr || +yr[1] === y) ? old : undefined;
}
function json_(o){
  return ContentService.createTextOutput(JSON.stringify(o))
    .setMimeType(ContentService.MimeType.JSON);
}

// ---- Endpoint -------------------------------------------------------------
function doPost(e){
  try{
    var body = JSON.parse(e.postData.contents);
    if (body.secret !== SECRET) return json_({ok: false, error: 'bad secret'});

    var id = matchId_(body.name);
    if (!id) return json_({ok: false, error: 'unknown name: ' + body.name});

    var date = String(body.date);            // "yyyy-mm-dd" — canonical, used for MATCHING
    var pp = date.split('-'), y = +pp[0], mo = +pp[1], d = +pp[2];

    // Tab NAME uses an unpadded day: "2026-10-2", not "2026-10-02".
    // The month keeps its leading zero ("2026-09-3"), matching the tabs that
    // already exist in these sheets. Days 10-31 are unchanged either way.
    //
    // Deliberately separate from `date`: the wire protocol and every lookup
    // below stay on canonical yyyy-mm-dd, so only the visible tab name
    // changes. dayKey_() parses \d{1,2} per field, so ordering, sorting and
    // the duplicate guard all keep working across both spellings.
    var tabName = pp[0] + '-' + pp[1] + '-' + String(d);

    var ss = SpreadsheetApp.openById(id);
    var sheets = ss.getSheets();
    var tmpl = findTemplate_(sheets);
    if (!tmpl) return json_({ok: false, error: 'no template tab'});

    // Exact name first; then any day tab for the SAME date under another
    // spelling (unpadded "2026-09-3", full-width digits). Without this a
    // re-sync of such a date created a duplicate "2026-09-03" tab (§7.6).
    var sh = ss.getSheetByName(date);
    if (!sh){
      var wantKey = dayKey_(date);
      sh = sheets.filter(function(s){ return dayKey_(s.getName()) === wantKey; })[0] || null;
    }
    if (!sh){
      // Insert in DATE order, not sync order.
      //
      // The old logic took the highest index of any existing date tab and
      // inserted there, i.e. it always appended after whatever day happened to
      // be synced last. Re-running or backfilling a date therefore left the
      // tabs permanently out of sequence.
      //
      // Compare with dayKey_ (numeric y*10000+m*100+d), NOT as strings: these
      // sheets contain BOTH "2026-07-13" and unpadded "2026-07-6", and
      // "2026-07-6" > "2026-07-13" is true as a string.
      var newKey = dayKey_(date);
      var dayTabs = sheets.filter(function(s){ return isDayTabName_(s.getName()); });

      var insertIdx = null;
      for (var di = 0; di < dayTabs.length; di++){
        if (dayKey_(dayTabs[di].getName()) > newKey){
          // getIndex() is 1-based, insertSheet() is 0-based: this places the
          // new tab immediately BEFORE the first later date.
          insertIdx = dayTabs[di].getIndex() - 1;
          break;
        }
      }
      if (insertIdx === null && dayTabs.length){
        // Latest date so far -> straight after the last existing day tab.
        insertIdx = dayTabs[dayTabs.length - 1].getIndex();
      }
      if (insertIdx === null){
        // No day tabs yet: sit after the month tab, else before the template.
        var mmTmp = findMonth_(sheets, mo);
        insertIdx = mmTmp ? mmTmp.getIndex() : Math.max(0, tmpl.getIndex() - 1);
      }
      sh = ss.insertSheet(tabName, insertIdx, {template: tmpl});
    }

    var sessions = body.sessions || [];
    var capacity = LAST_ROW - FIRST_ROW + 1;
    var written = Math.min(sessions.length, capacity);

    sh.getRange(FIRST_ROW, COL_START, capacity, 3).clearContent();

    for (var i = 0; i < written; i++){
      var se = sessions[i];
      var r = FIRST_ROW + i;
      var sp = String(se.start).split(':'), ep = String(se.end).split(':');
      // numeric values (locale-proof) + minus-only span formula
      sh.getRange(r, COL_START).setValue(timeFrac_(+sp[0], +sp[1])).setNumberFormat('HH:mm');
      sh.getRange(r, COL_END).setValue(timeFrac_(+ep[0], +ep[1])).setNumberFormat('HH:mm');
      sh.getRange(r, COL_SPAN).setFormula('=C' + r + '-B' + r).setNumberFormat('[h]:mm');
    }

    sh.getRange('B2').setFormula('=SUM(D' + FIRST_ROW + ':D' + LAST_ROW + ')').setNumberFormat('[h]:mm');
    if (written > 0) sh.getRange('B3').setFormula('=B' + FIRST_ROW).setNumberFormat('HH:mm');

    // --- Day tab B1 -> this employee's month tab (back-link) ---------------
    // ';' argument separator on purpose: these sheets use a locale where a
    // comma in a formula is a syntax error. `id` is THIS person's spreadsheet
    // (from matchId_), so the link can never point into someone else's file.
    var month = findMonthTab_(sheets, y, mo);
    var monthLinked = setMonthBacklink_(sh, id, month);

    // --- Month tab B1 -> "🏠 Tổng quan" (overview) -------------------------
    // Wrapped: a sheet with no overview tab, or a protected B1, must not fail
    // the whole sync -- the day's data is already written by this point.
    var overviewLinked = false;
    try { overviewLinked = setOverviewLink_(ss, month); } catch (e2) { overviewLinked = false; }

    var gid = sh.getSheetId();
    var linked = false;
    if (month){
      var lr = month.getLastRow();
      var aVals = month.getRange(1, 1, lr, 1).getValues();
      // Start BELOW the "Ngày" header. A1 holds a real Date = the 1st of the
      // month (displayed as "Tháng 9 2026"), so scanning from row 1 matched
      // the title on yyyy-mm-01 and wrote "Mở->" into the month tab's B1.
      var startRow = monthDataStart_(aVals);
      for (var j = startRow; j < aVals.length; j++){
        var a = aVals[j][0], match = false;
        if (a instanceof Date) match = (a.getFullYear() === y && a.getMonth() + 1 === mo && a.getDate() === d);
        // Text dates in column A: accept either spelling. Some sheets hold
        // "2026-10-02", others "2026-10-2"; comparing dayKey_ instead of the
        // raw string means the Mở-> link still lands on the right row.
        else {
          var av = String(a).trim();
          match = (av === date) || (av === tabName) || (dayKey_(av) === dayKey_(date));
        }
        if (match){
          var url = 'https://docs.google.com/spreadsheets/d/' + id + '/edit?gid=' + gid + '#gid=' + gid;
          month.getRange(j + 1, 2).setFormula('=HYPERLINK("' + url + '";"Mở->")');
          linked = true;
          break;
        }
      }
    }

    return json_({
      ok: true, name: body.name, date: date, gid: gid,
      sessions_written: written, capped: sessions.length > capacity, linked: linked,
      month_linked: monthLinked, month_tab: month ? month.getName() : null,
      overview_linked: overviewLinked
    });
  } catch (err){
    return json_({ok: false, error: String(err)});
  }
}

function doGet(){
  return json_({ok: true, service: 'call-log sheets sync', methods: ['POST']});
}

// Index (0-based) of the first data row in a month tab's column-A values:
// the row after the "Ngày" header, else row 3 (index 2) per the fixed layout.
function monthDataStart_(aVals){
  for (var i = 0; i < Math.min(aVals.length, 10); i++){
    if (norm_(aVals[i][0]) === 'ngay') return i + 1;
  }
  return Math.min(2, aVals.length);
}

// Writes the month back-link into B1 of a day tab. Returns true if written.
function monthBacklinkFormula_(id, month){
  var mGid = month.getSheetId();
  var mUrl = 'https://docs.google.com/spreadsheets/d/' + id +
             '/edit?gid=' + mGid + '#gid=' + mGid;
  var mLabel = String(month.getName()).replace(/"/g, '');
  return '=HYPERLINK("' + mUrl + '";"← ' + mLabel + '")';
}
function setMonthBacklink_(daySheet, id, month){
  if (!month) return false;
  daySheet.getRange('B1').setFormula(monthBacklinkFormula_(id, month));
  return true;
}

// ---- Month tab -> overview ("Tổng quan") link ------------------------------
//
// Matched on the accent-stripped name so "🏠 Tổng quan", "Tổng Quan" and
// "TỔNG QUAN" all hit. indexOf, not equality: every sheet prefixes an emoji.
function findOverview_(sheets){
  return sheets.filter(function(s){
    return norm_(s.getName()).indexOf('tong quan') !== -1;
  })[0];
}
// Writes the overview link into B1 of a MONTH tab. Safe to call on every sync:
// month tabs keep their title in A1 and the "Ngày" header in row 2, and the
// column-A scan starts below that header (monthDataStart_), so B1 is unused.
// ';' separator on purpose -- these sheets' locale rejects ',' in formulas.
function setOverviewLink_(ss, monthSheet){
  var ov = findOverview_(ss.getSheets());
  if (!ov || !monthSheet) return false;
  var g = ov.getSheetId();
  var u = 'https://docs.google.com/spreadsheets/d/' + ss.getId() +
          '/edit?gid=' + g + '#gid=' + g;
  monthSheet.getRange('B1').setFormula('=HYPERLINK("' + u + '";"← Tổng quan")');
  return true;
}

// ---- Maintenance: back-fill month-tab B1 -> overview links -----------------
//
// Run backfillOverviewLinksDryRun() first, same discipline as the month-link
// backfill above. Only tabs that monthKey_ recognises as month tabs are
// touched; day tabs are skipped explicitly via dayKey_ so an unpadded
// "2026-10-2" can never be mistaken for a month. No redeploy needed to run.
function backfillOverviewLinksDryRun(){ return backfillOverviewLinks_(true); }
function backfillOverviewLinks(){ return backfillOverviewLinks_(false); }

function backfillOverviewLinks_(dryRun){
  var seen = {}, out = [];
  for (var key in NAME_TO_ID){
    var id = NAME_TO_ID[key];
    if (seen[id]) continue;
    seen[id] = true;
    if (BACKFILL_SKIP_IDS.indexOf(id) !== -1) continue;
    var ss = SpreadsheetApp.openById(id);
    var sheets = ss.getSheets();
    var ov = findOverview_(sheets);
    if (!ov){ out.push(ss.getName() + ': NO OVERVIEW TAB -- skipped'); continue; }
    var done = 0, already = 0;
    for (var i = 0; i < sheets.length; i++){
      var s = sheets[i], n = s.getName();
      if (dayKey_(n)) continue;                 // day tab, not a month tab
      if (!monthKey_(n)) continue;              // not a month tab either
      var cur = String(s.getRange('B1').getFormula() || '');
      if (cur.indexOf('gid=' + ov.getSheetId()) !== -1){ already++; continue; }
      if (!dryRun) setOverviewLink_(ss, s);
      done++;
    }
    out.push(ss.getName() + ': ' + (dryRun ? 'would link ' : 'linked ') + done +
             ', already ok ' + already);
  }
  var msg = (dryRun ? '[DRY RUN]\n' : '[APPLIED]\n') + out.join('\n');
  Logger.log(msg);
  return msg;
}

// ---- Maintenance: back-fill day-tab B1 -> month-tab links -----------------
//
// Run backfillMonthLinksDryRun() FIRST from the editor: it writes nothing and
// logs, per spreadsheet, how many day tabs would get a link, which would be
// skipped (no month tab) and which already have a non-empty B1 that is not
// one of our links (those are NEVER overwritten).
//
// Then run backfillMonthLinks(). Idempotent and resumable: a tab whose B1
// already holds exactly the right formula is skipped, so if it hits the
// 6-minute limit just run it again. No redeploy needed to run these.
// Parked/inactive employees listed in BACKFILL_SKIP_IDS (Employees.gs) are
// never touched.
function backfillMonthLinksDryRun(){ return backfillMonthLinks_(true); }
function backfillMonthLinks(){ return backfillMonthLinks_(false); }

function backfillMonthLinks_(dryRun){
  var started = Date.now();
  var seen = {}, out = [];
  for (var key in NAME_TO_ID){
    var id = NAME_TO_ID[key];
    if (seen[id]) continue;                 // several aliases share one file
    seen[id] = true;
    if (BACKFILL_SKIP_IDS.indexOf(id) !== -1) continue;
    if (Date.now() - started > 5 * 60 * 1000){
      out.push('TIME BUDGET HIT before ' + key + ' -- run again to continue');
      break;
    }
    try {
      var ss = SpreadsheetApp.openById(id);
      var sheets = ss.getSheets();
      var todo = 0, done = 0, noMonth = [], foreign = [];
      sheets.forEach(function(s){
        var k = dayKey_(s.getName());          // null for non-day tabs
        if (k === null) return;
        var y = Math.floor(k / 10000), mo = Math.floor(k / 100) % 100;
        var m = findMonthTab_(sheets, y, mo);
        if (!m){ noMonth.push(s.getName()); return; }
        var want = monthBacklinkFormula_(id, m);
        var cell = s.getRange('B1');
        var have = cell.getFormula();
        if (have === want){ done++; return; }
        // Never clobber something a human put there. Our own (stale) link is
        // fine to replace; an empty cell is fine; anything else is reported.
        var ours = have && have.indexOf('=HYPERLINK("https://docs.google.com/spreadsheets/d/' + id) === 0;
        if (!ours && (have || String(cell.getDisplayValue()).trim() !== '')){
          foreign.push(s.getName()); return;
        }
        todo++;
        if (!dryRun) cell.setFormula(want);
      });
      if (!dryRun) SpreadsheetApp.flush();
      out.push(ss.getName() + (dryRun ? ' [DRY RUN]' : '') + ': ' +
               (dryRun ? 'would link ' : 'linked ') + todo +
               ', already ok ' + done +
               (noMonth.length ? ', NO MONTH TAB: ' + noMonth.join(' ') : '') +
               (foreign.length ? ', B1 NOT EMPTY (left alone): ' + foreign.join(' ') : ''));
    } catch (err){ out.push(key + ' -> ERROR: ' + err); }
  }
  var msg = out.join('\n');
  Logger.log(msg);
  return msg;
}

/**
 * One-off repair: scans every yyyy-mm-dd tab in all 8 sheets, converts any
 * leftover "=TIME(h,m,s)" / "=TIME(h;m;s)" formulas in B7:C22 into numeric
 * values so the #ERROR! cells resolve. Safe to run multiple times.
 * Run from the editor (no redeploy needed).
 */
function repairTimes(){
  var out = [];
  for (var key in NAME_TO_ID){
    var ss = SpreadsheetApp.openById(NAME_TO_ID[key]);
    ss.getSheets().forEach(function(sh){
      if (!/^\d{4}-\d{2}-\d{2}$/.test(sh.getName())) return;
      var fs = sh.getRange(FIRST_ROW, COL_START, LAST_ROW - FIRST_ROW + 1, 2).getFormulas();
      var changed = 0;
      for (var i = 0; i < fs.length; i++){
        for (var c = 0; c < 2; c++){
          var mt = /=TIME\((\d+)[,;](\d+)[,;]\d+\)/i.exec(fs[i][c]);
          if (mt){
            sh.getRange(FIRST_ROW + i, COL_START + c)
              .setValue(timeFrac_(+mt[1], +mt[2])).setNumberFormat('HH:mm');
            changed++;
          }
        }
      }
      if (changed) out.push(ss.getName() + ' / ' + sh.getName() + ': ' + changed + ' cell(s)');
    });
  }
  Logger.log(JSON.stringify(out, null, 1));
}

// ---- Maintenance: put existing day tabs back in chronological order -------
//
// One-off repair for spreadsheets whose tabs were created by the old
// append-after-the-last-synced-day logic. Run `sortDayTabsAll` from the Apps
// Script editor (Run). Nothing is deployed and no cell is touched -- this ONLY
// changes the left-to-right order of the day tabs.
//
// Two things make this less trivial than a sort():
//   1. Tab names use BOTH "2026-07-13" and unpadded "2026-07-6", so a plain
//      string sort puts 2026-07-1 before 2026-07-10. We sort on the parsed
//      (y, m, d) instead.
//   2. Day tabs sit in separate runs, each under its own month header. We sort
//      only WITHIN each contiguous run, so no day tab can ever jump across a
//      month header into the wrong month.

// Tab names mix ASCII and FULL-WIDTH digits: "\uff12\uff10\uff12\uff16\u5e74\uff14\u6708" (April). JavaScript's
// \d is ASCII-only, so those months were invisible to the matcher and would
// have survived a purge. Fold U+FF10..U+FF19 down to 0-9 first.
function asciiDigits_(s){
  return String(s).replace(/[\uFF10-\uFF19]/g, function(c){
    return String.fromCharCode(c.charCodeAt(0) - 0xFF10 + 48);
  });
}

function dayKey_(name){
  var m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(asciiDigits_(name).trim());
  return m ? (+m[1]) * 10000 + (+m[2]) * 100 + (+m[3]) : null;
}
function isDayTabName_(n){ return dayKey_(n) !== null; }

function sortDayTabsIn_(id){
  var ss = SpreadsheetApp.openById(id);
  var readOrder = function(){
    return ss.getSheets().map(function(s){ return s.getName(); });
  };

  var cur = readOrder();

  // Desired order: within every contiguous run of day tabs, sort by date.
  // Non-day tabs keep their exact slots.
  var want = cur.slice();
  var i = 0;
  while (i < cur.length){
    if (!isDayTabName_(cur[i])){ i++; continue; }
    var j = i;
    while (j < cur.length && isDayTabName_(cur[j])) j++;
    var run = cur.slice(i, j).sort(function(a, b){ return dayKey_(a) - dayKey_(b); });
    for (var k = 0; k < run.length; k++) want[i + k] = run[k];
    i = j;
  }

  // Apply. IMPORTANT: re-read the real sheet order after every move instead of
  // simulating it. moveActiveSheet() is batched, so a locally-simulated array
  // goes stale after the first move and every later position is computed
  // against the wrong layout -- which showed up as only the first few tabs
  // after a month header being repositioned. flush() forces each move to land
  // before the next position is calculated.
  var moves = 0;
  for (var p = 0; p < want.length; p++){
    if (cur[p] === want[p]) continue;
    var sh = ss.getSheetByName(want[p]);
    if (!sh) continue;                       // defensive: should not happen
    ss.setActiveSheet(sh);
    ss.moveActiveSheet(p + 1);               // moveActiveSheet is 1-based
    SpreadsheetApp.flush();
    cur = readOrder();                       // authoritative, post-move
    moves++;
  }

  var total = cur.filter(isDayTabName_).length;
  var ok = true;
  for (var q = 0; q < want.length; q++) if (cur[q] !== want[q]) ok = false;
  return ss.getName() + ': ' + total + ' day tabs, ' + moves +
         ' moved, verified=' + ok;
}

function sortDayTabsAll(){
  var seen = {}, out = [];
  for (var key in NAME_TO_ID){
    var id = NAME_TO_ID[key];
    if (seen[id]) continue;                 // several aliases share one file
    seen[id] = true;
    try { out.push(sortDayTabsIn_(id)); }
    catch (err){ out.push(key + ' -> ERROR: ' + err); }
  }
  var msg = out.join('\n');
  Logger.log(msg);
  return msg;
}

// ---- Maintenance: strip pre-August tabs from a newly-copied spreadsheet ----
//
// New employees get a spreadsheet copied from an existing one, so it arrives
// carrying the ORIGINAL person's history. This removes everything before
// 2026-08-01: every yyyy-mm-dd day tab earlier than that, plus the month tabs
// Tháng 1..7. Kept: 📄 Mẫu, 🏠 Tổng quan, Tháng 8 onward, and any August day
// tabs.
//
// SAFETY
//  * Only touches the IDs listed in PURGE_SHEET_IDS. It can never reach the
//    established employees, whatever NAME_TO_ID contains.
//  * The template tab is protected explicitly, even if it were somehow named
//    like a date.
//  * Run purgePreAugustDryRun() FIRST -- it deletes nothing and logs exactly
//    what the real run would remove.
//  * Deleting a month tab breaks any formula in 🏠 Tổng quan that referenced
//    it (those cells become #REF!). Expected, and easy to clear afterwards.

// PURGE_SHEET_IDS is defined in Employees.gs.
var PURGE_BEFORE_KEY = 20260801;   // delete strictly earlier than this

// "📅 Tháng 6 2026" / "6月" -> {y, m}. Year defaults to 2026 when absent.
function monthKey_(name){
  var n = asciiDigits_(name);
  var m = /Tháng\s*0?(\d{1,2})/.exec(n) || /(?:^|[^0-9])0?(\d{1,2})\s*月/.exec(n);
  if (!m) return null;
  var mo = +m[1];
  if (mo < 1 || mo > 12) return null;
  var yr = /(\d{4})/.exec(n);
  return { y: yr ? +yr[1] : 2026, m: mo };
}

function purgeOne_(id, dryRun){
  var ss = SpreadsheetApp.openById(id);
  var sheets = ss.getSheets();
  var tmpl = findTemplate_(sheets);
  var tmplName = tmpl ? tmpl.getName() : null;

  var doomed = [];
  sheets.forEach(function(s){
    var n = s.getName();
    if (n === tmplName) return;                       // never the template
    var dk = dayKey_(n);
    if (dk !== null){
      if (dk < PURGE_BEFORE_KEY) doomed.push(n);
      return;
    }
    var mk = monthKey_(n);
    if (mk && (mk.y < 2026 || (mk.y === 2026 && mk.m < 8))) doomed.push(n);
  });

  if (doomed.length >= sheets.length){
    return ss.getName() + ': ABORTED, would delete every tab';
  }
  if (!dryRun){
    doomed.forEach(function(n){
      var sh = ss.getSheetByName(n);
      if (sh) ss.deleteSheet(sh);
    });
    SpreadsheetApp.flush();
  }
  var kept;
  if (dryRun){
    kept = sheets.map(function(s){ return s.getName(); })
                 .filter(function(n){ return doomed.indexOf(n) === -1; });
  } else {
    kept = ss.getSheets().map(function(s){ return s.getName(); });
  }
  return ss.getName() + (dryRun ? ' [DRY RUN]' : ' [DELETED]') +
         ': ' + doomed.length + ' tab(s) -> ' + doomed.join(', ') +
         '\n    remaining (' + kept.length + '): ' + kept.join(', ');
}

function purgePreAugustDryRun(){ return purgePreAugust_(true); }
function purgePreAugust(){ return purgePreAugust_(false); }

function purgePreAugust_(dryRun){
  if (!PURGE_SHEET_IDS.length) {
    var msg = 'PURGE_SHEET_IDS is empty - nothing to do.';
    Logger.log(msg); return msg;
  }
  var out = [];
  PURGE_SHEET_IDS.forEach(function(id){
    try { out.push(purgeOne_(id, dryRun)); }
    catch (err){ out.push(id + ' -> ERROR: ' + err); }
  });
  var msg = out.join('\n');
  Logger.log(msg);
  return msg;
}
