/**
 * Deal Finder: weekly summary email
 *
 * Add this as a SECOND file in the same Apps Script project as Code.gs (it uses the settings at the
 * top of Code.gs, so no tokens to fill in):
 *   1. In script.google.com, open the Deal Finder project.
 *   2. Click + next to "Files" -> Script, name it Digest, delete what's in it, paste this file.
 *   3. Save, choose `setupWeekly` in the function dropdown and click Run.
 *
 * The listing check makes one summary a week (Monday morning); this checks every hour and emails it to
 * everyone with "weekly summary" ticked on the map under Settings.
 */

function sendWeeklyDigest() {
  const d = rpc_('pending_digest');
  if (!d || !d.id) return;                       // nothing waiting
  const to = d.recipients || [];
  if (!to.length) {
    console.log('A weekly summary is ready, but no one has "weekly summary" ticked under Settings on the map.');
    return;
  }
  to.forEach(email => MailApp.sendEmail({ to: email, name: 'Deal Finder', subject: d.subject, htmlBody: d.html }));
  rpc_('mark_digest_sent', { p_id: d.id });
  console.log(`Weekly summary sent to ${to.join(', ')}.`);
}

/** Run this once: checks for a new weekly summary every hour (and sends one now if it's waiting). */
function setupWeekly() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'sendWeeklyDigest')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('sendWeeklyDigest').timeBased().everyHours(1).create();
  sendWeeklyDigest();
  console.log('Done. Weekly summaries will be emailed within an hour of being made.');
}
