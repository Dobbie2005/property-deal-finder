/**
 * Deal Finder: Gmail -> Supabase email sync
 *
 * Runs inside the bot Gmail account (script.google.com), so no app password is needed.
 * Every 4 hours it copies new emails into the Supabase `raw_emails` table.
 * The daily GitHub job then reads them, pulls out the listings and scores them.
 *
 * Setup (once): paste this file into a new Apps Script project while signed in
 * as the bot Gmail, fill in INGEST_TOKEN, pick `setup` and click Run.
 */

const SUPABASE_URL = 'https://oqctqrhxeuqlhopnmsmt.supabase.co';
const SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_n3s6e7fY50SPVjm6eZgFeA_Lb0262BE';
const INGEST_TOKEN = 'PASTE_TOKEN_HERE';

const LOOKBACK_DAYS = 3;      // how far back to look on each run
const MAX_THREADS = 200;      // safety cap per run

/** Copies recent emails to Supabase. Emails already copied are skipped by the database. */
function syncListingEmails() {
  const props = PropertiesService.getScriptProperties();
  const lastSync = Number(props.getProperty('lastSyncMs') || 0);
  const startedAt = Date.now();
  const cutoff = Math.max(lastSync - 60 * 60 * 1000, startedAt - LOOKBACK_DAYS * 24 * 3600 * 1000);

  const threads = GmailApp.search(`newer_than:${LOOKBACK_DAYS}d -in:sent -in:drafts -in:trash -in:spam`, 0, MAX_THREADS);
  let added = 0, alreadyThere = 0, checked = 0;

  threads.forEach(thread => {
    thread.getMessages().forEach(m => {
      if (m.getDate().getTime() < cutoff) return;
      checked++;
      const payload = {
        p_token: INGEST_TOKEN,
        p_id: m.getId(),
        p_received_at: m.getDate().toISOString(),
        p_sender: m.getFrom(),
        p_subject: m.getSubject(),
        p_html: m.getBody(),
        p_plain: m.getPlainBody(),
      };
      const res = UrlFetchApp.fetch(`${SUPABASE_URL}/rest/v1/rpc/submit_email`, {
        method: 'post',
        contentType: 'application/json',
        headers: { apikey: SUPABASE_PUBLISHABLE_KEY },
        payload: JSON.stringify(payload),
        muteHttpExceptions: true,
      });
      const code = res.getResponseCode();
      if (code >= 300) {
        throw new Error(`Supabase rejected the email "${m.getSubject()}" (${code}): ${res.getContentText().slice(0, 300)}`);
      }
      res.getContentText().trim() === 'true' ? added++ : alreadyThere++;
    });
  });

  props.setProperty('lastSyncMs', String(startedAt));
  console.log(`Checked ${checked} recent email(s): ${added} new, ${alreadyThere} already copied.`);
}

/** Run this once: installs the 4-hourly schedule and does a first sync. */
function setup() {
  if (INGEST_TOKEN === 'PASTE_TOKEN_HERE') {
    throw new Error('Fill in INGEST_TOKEN at the top of the script first.');
  }
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'syncListingEmails')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('syncListingEmails').timeBased().everyHours(4).create();
  PropertiesService.getScriptProperties().deleteProperty('lastSyncMs');
  syncListingEmails();
  console.log('Done. Emails will now be copied every 4 hours.');
}
