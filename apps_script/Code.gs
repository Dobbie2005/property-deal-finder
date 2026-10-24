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

// Where the map lives (used for the "Open the map" link in deal alert emails)
const MAP_URL = 'https://dobbie2005.github.io/property-deal-finder/';

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
  let added = 0, alreadyThere = 0, checked = 0;

  try {
    const threads = GmailApp.search(`after:${afterSeconds} -in:sent -in:drafts -in:trash -in:spam`, 0, MAX_THREADS);
    threads.forEach(thread => copyThread_(thread, cutoff, r => r ? added++ : alreadyThere++, () => checked++));
    props.setProperty('lastSyncMs', String(startedAt));
    console.log(`Checked ${checked} recent email(s): ${added} new, ${alreadyThere} already copied.`);
    if (added > 0) startGithubJob_();
    heartbeat_(checked, added, null);
    sendDealAlerts_();
  } catch (err) {
    heartbeat_(checked, added, String(err && err.message || err));
    throw err;
  }
}

/** Records that the script ran (so the GitHub summary and the map can show it's alive). */
function heartbeat_(checked, added, error) {
  try {
    UrlFetchApp.fetch(`${SUPABASE_URL}/rest/v1/rpc/sync_heartbeat`, {
      method: 'post',
      contentType: 'application/json',
      headers: { apikey: SUPABASE_PUBLISHABLE_KEY },
      payload: JSON.stringify({ p_token: INGEST_TOKEN, p_checked: checked, p_added: added, p_error: error }),
      muteHttpExceptions: true,
    });
  } catch (e) {
    console.error(`Heartbeat failed: ${e}`);
  }
}

/** Sends each recent message in a Gmail thread to Supabase. */
function copyThread_(thread, cutoff, onResult, onChecked) {
  thread.getMessages().forEach(m => {
      if (m.getDate().getTime() < cutoff) return;
      onChecked();
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
      onResult(res.getContentText().trim() === 'true');
  });
}

/** Run this to see whether the 15-minute schedule is installed. */
function checkSchedule() {
  const triggers = ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'syncListingEmails');
  console.log(triggers.length
    ? `Schedule is installed (${triggers.length} trigger). Last sync: ${new Date(Number(PropertiesService.getScriptProperties().getProperty('lastSyncMs') || 0))}`
    : 'No schedule installed! Run setup again.');
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

// ---------------------------------------------------------------------------------------------
// Deal alerts: the GitHub job queues new listings / price drops that look like deals; this emails
// them to the recipients set up on the map (Settings -> Deal alerts), each with their own minimum.
// ---------------------------------------------------------------------------------------------
function rpc_(fn, args) {
  const res = UrlFetchApp.fetch(`${SUPABASE_URL}/rest/v1/rpc/${fn}`, {
    method: 'post', contentType: 'application/json', headers: { apikey: SUPABASE_PUBLISHABLE_KEY },
    payload: JSON.stringify(Object.assign({ p_token: INGEST_TOKEN }, args || {})), muteHttpExceptions: true,
  });
  if (res.getResponseCode() >= 300) throw new Error(`${fn} failed (${res.getResponseCode()}): ${res.getContentText().slice(0, 300)}`);
  const text = res.getContentText();
  return text ? JSON.parse(text) : null;
}

const rand_ = v => v ? 'R ' + Math.round(v).toLocaleString('en-ZA').replace(/[,\u00a0]/g, ' ') : 'POA';
const esc_ = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function sendDealAlerts_() {
  try {
    const data = rpc_('pending_alerts');
    const alerts = (data && data.alerts) || [];
    if (!alerts.length) return;
    (data.recipients || []).forEach(rcp => {
      const picks = alerts.filter(a => {
        if (a.reason === 'price_drop' && !rcp.include_drops) return false;
        const score = rcp.include_nearby ? Math.max(a.deal_score ?? -999, a.wide_score ?? -999) : (a.deal_score ?? -999);
        return score >= Number(rcp.min_score);
      });
      if (!picks.length) return;
      MailApp.sendEmail({ to: rcp.email, name: 'Deal Finder', subject: alertSubject_(picks), htmlBody: alertHtml_(picks, rcp) });
    });
    rpc_('mark_alerts_sent', { p_ids: alerts.map(a => a.id) });
    console.log(`Deal alerts: ${alerts.length} checked, sent to ${(data.recipients || []).length} recipient(s) where they matched.`);
  } catch (e) {
    console.error(`Deal alerts not sent: ${e}`);  // the email sync itself still counts as done
  }
}

function scoreOf_(a) {
  if (a.deal_score != null) return { s: a.deal_score, n: a.comps_count, where: 'in ' + a.suburb };
  return { s: a.wide_score, n: a.wide_comps, where: 'within 3 km' };
}

function alertSubject_(picks) {
  const best = picks.map(a => ({ a, s: scoreOf_(a).s })).sort((x, y) => y.s - x.s)[0];
  const n = picks.length;
  return `${n} property deal${n === 1 ? '' : 's'}: best ${Math.round(best.s)}% below similar in ${best.a.suburb}`;
}

function alertHtml_(picks, rcp) {
  const cards = picks.map(a => {
    const sc = scoreOf_(a);
    const drop = a.reason === 'price_drop' && a.old_price ? `<div style="color:#1a9850;font-weight:600">Price dropped from ${rand_(a.old_price)}</div>` : '';
    const img = a.image_url ? `<img src="${esc_(a.image_url)}" width="160" style="border-radius:8px;display:block" alt="">` : '';
    return `<tr><td style="padding:10px 12px 10px 0;vertical-align:top">${img}</td>
      <td style="padding:10px 0;vertical-align:top;font-family:Arial,sans-serif;font-size:14px">
        <div style="font-size:17px;font-weight:700">${rand_(a.price)}</div>${drop}
        <div>${esc_(a.title || ((a.bedrooms ? a.bedrooms + ' bed ' : '') + (a.property_type || 'property') + ' in ' + a.suburb))}</div>
        <div style="color:#666">${esc_(a.suburb)}, ${esc_(a.city || '')}</div>
        <div style="margin-top:4px"><b>${Math.round(sc.s)}% below</b> similar homes ${esc_(sc.where)} (${sc.n} compared)</div>
        <div style="margin-top:6px"><a href="${esc_(a.url)}">Open listing</a> &nbsp;·&nbsp; <a href="${esc_(MAP_URL)}">Open the map</a></div>
      </td></tr>`;
  }).join('');
  return `<div style="font-family:Arial,sans-serif">
    <p>New deals at least ${esc_(rcp.min_score)}% below similar homes:</p>
    <table cellspacing="0" cellpadding="0">${cards}</table>
    <p style="color:#888;font-size:12px">Scores compare asking prices with similar listings, not sold prices. Check the area's sold prices before offering.
    Change these alerts on the map under Settings.</p></div>`;
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
