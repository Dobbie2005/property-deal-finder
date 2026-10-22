/**
 * Deal Finder: Gmail -> Supabase email sync
 *
 * Runs inside the bot Gmail account (script.google.com), so no app password is needed.
 * Every 15 minutes it copies new emails into the Supabase `raw_emails` table.
 * When new emails arrived (and GITHUB_TOKEN is filled in) it immediately starts the
 * GitHub job, which pulls out the listings, scores them and updates the map.
 *
 * Setup (once): paste this file into a new Apps Script project while signed in
 * as the bot Gmail, fill in INGEST_TOKEN (and GITHUB_TOKEN), pick `setup` and click Run.
 */

const SUPABASE_URL = 'https://oqctqrhxeuqlhopnmsmt.supabase.co';
const SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_n3s6e7fY50SPVjm6eZgFeA_Lb0262BE';
const INGEST_TOKEN = 'PASTE_TOKEN_HERE';

// Optional but recommended: a GitHub fine-grained token for this one repo with
// "Contents: Read and write". Lets the script start the GitHub job as soon as alerts arrive.
const GITHUB_TOKEN = '';
const GITHUB_REPO = 'Dobbie2005/property-deal-finder';

const EVERY_MINUTES = 15;     // Apps Script allows 1, 5, 10, 15 or 30
const LOOKBACK_DAYS = 3;      // how far back the very first run looks
const MAX_THREADS = 200;      // safety cap per run

/** Copies new emails to Supabase. Emails already copied are skipped by the database. */
function syncListingEmails() {
  const props = PropertiesService.getScriptProperties();
  const lastSync = Number(props.getProperty('lastSyncMs') || 0);
  const startedAt = Date.now();
  // Look back to the previous run with a 1-hour overlap (first run: LOOKBACK_DAYS)
  const cutoff = Math.max(lastSync - 60 * 60 * 1000, startedAt - LOOKBACK_DAYS * 24 * 3600 * 1000);
  const afterSeconds = Math.floor(cutoff / 1000);

  const threads = GmailApp.search(`after:${afterSeconds} -in:sent -in:drafts -in:trash -in:spam`, 0, MAX_THREADS);
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
  if (added > 0) startGithubJob_();
}

/** Asks GitHub to run the listing check now (only when GITHUB_TOKEN is filled in). */
function startGithubJob_() {
  if (!GITHUB_TOKEN) return;
  const res = UrlFetchApp.fetch(`https://api.github.com/repos/${GITHUB_REPO}/dispatches`, {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: `Bearer ${GITHUB_TOKEN}`, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ event_type: 'new-emails' }),
    muteHttpExceptions: true,
  });
  if (res.getResponseCode() === 204) {
    console.log('Started the GitHub listing check.');
  } else {
    // Don't stop the email sync; the daily scheduled run will still pick the emails up.
    console.error(`Could not start the GitHub job (${res.getResponseCode()}): ${res.getContentText().slice(0, 300)}`);
  }
}

/** Run this once: installs the 15-minute schedule and does a first sync. */
function setup() {
  if (INGEST_TOKEN === 'PASTE_TOKEN_HERE') {
    throw new Error('Fill in INGEST_TOKEN at the top of the script first.');
  }
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'syncListingEmails')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('syncListingEmails').timeBased().everyMinutes(EVERY_MINUTES).create();
  PropertiesService.getScriptProperties().deleteProperty('lastSyncMs');
  syncListingEmails();
  console.log(`Done. Emails will now be copied every ${EVERY_MINUTES} minutes.`);
}

/** Optional: run this to check the GitHub token works (starts one listing check). */
function testGithubToken() {
  if (!GITHUB_TOKEN) throw new Error('GITHUB_TOKEN is empty.');
  startGithubJob_();
}
