/**
 * Google Apps Script Web App: receives leads from the Python app and appends
 * them to this spreadsheet, skipping duplicates by email.
 *
 * Setup (about 3 minutes):
 *  1. Open the Google Sheet -> Extensions -> Apps Script, paste this file.
 *  2. Project Settings -> Script properties -> add SHARED_SECRET (any long random string).
 *  3. Deploy -> New deployment -> type "Web app":
 *       Execute as: Me      Who has access: Anyone
 *     Copy the Web app URL into SHEETS_WEBAPP_URL, and the secret into SHEETS_SHARED_SECRET.
 *
 * Request body:  {"action": "append", "token": "<secret>", "lead": {received_at, name, email, ...}}
 * Responses:     {"status": "added", "row": 12} | {"status": "duplicate"} | {"status": "error", "message": "..."}
 */

var SHEET_NAME = 'Leads';
var COLUMNS = ['received_at', 'name', 'email', 'phone', 'service', 'message', 'source',
               'score', 'intent', 'urgency', 'budget', 'summary', 'scored_by'];
var HEADERS = ['Received', 'Name', 'Email', 'Phone', 'Service', 'Message', 'Source',
               'Score', 'Intent', 'Urgency', 'Budget', 'Summary', 'Scored by'];

function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);
    var secret = PropertiesService.getScriptProperties().getProperty('SHARED_SECRET');
    if (!secret || body.token !== secret) {
      return json_({ status: 'error', message: 'unauthorized' });
    }
    if (body.action !== 'append' || !body.lead) {
      return json_({ status: 'error', message: 'unsupported action' });
    }
    return json_(appendLead_(body.lead));
  } catch (err) {
    return json_({ status: 'error', message: String(err) });
  }
}

function appendLead_(lead) {
  // Lock so two simultaneous submissions cannot both pass the duplicate check.
  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    var sheet = getSheet_();
    var email = String(lead.email || '').toLowerCase().trim();
    var emailCol = COLUMNS.indexOf('email') + 1;
    var lastRow = sheet.getLastRow();
    if (lastRow > 1) {
      var emails = sheet.getRange(2, emailCol, lastRow - 1, 1).getValues();
      for (var i = 0; i < emails.length; i++) {
        if (String(emails[i][0]).toLowerCase().trim() === email) {
          return { status: 'duplicate' };
        }
      }
    }
    // Prefix values starting with = + - @ so they are stored as text, not formulas.
    var row = COLUMNS.map(function (key) {
      var value = String(lead[key] || '');
      return /^[=+\-@]/.test(value) ? "'" + value : value;
    });
    sheet.appendRow(row);
    return { status: 'added', row: sheet.getLastRow() };
  } finally {
    lock.releaseLock();
  }
}

function getSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
