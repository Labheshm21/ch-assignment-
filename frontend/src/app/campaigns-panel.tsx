"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";

type Contact = {
  id: string;
  name: string | null;
  email: string;
  company: string | null;
  job_title: string | null;
  tags: string[];
};

type Recipient = {
  id: string;
  contact_id: string | null;
  scheduled_email_id: string;
  recipient_name: string | null;
  recipient_email: string;
  position: number;
  subject: string;
  body: string;
  scheduled_at: string;
  status: "pending" | "processing" | "sent" | "failed" | "cancelled";
  retry_count: number;
  last_error: string | null;
  gmail_message_id: string | null;
  sent_at: string | null;
  sequence_status: "active" | "replied" | "completed" | "failed" | "cancelled";
  gmail_thread_id: string | null;
  replied_at: string | null;
  deliveries: Delivery[];
};

type Delivery = {
  id: string;
  step_id: string;
  step_position: number;
  scheduled_email_id: string;
  subject: string;
  body: string;
  scheduled_at: string;
  status: "pending" | "processing" | "sent" | "failed" | "cancelled";
  retry_count: number;
  last_error: string | null;
  gmail_message_id: string | null;
  sent_at: string | null;
};

type CampaignStep = {
  id: string;
  position: number;
  subject: string;
  body: string;
  delay_minutes: number;
};

type StepDraft = Omit<CampaignStep, "id" | "position">;

type Campaign = {
  id: string;
  workspace_id: string;
  name: string;
  subject: string;
  body: string;
  scheduled_at: string;
  original_timezone: string;
  interval_minutes: number;
  status: "scheduled" | "in_progress" | "completed" | "partial_failure" | "cancelled";
  recipient_count: number;
  status_counts: Record<string, number>;
  enrollment_counts: Record<string, number>;
  steps: CampaignStep[];
  recipients: Recipient[];
  created_at: string;
};

function localDateTimeValue(date: Date): string {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function initialSchedule(): string {
  const date = new Date(Date.now() + 60 * 60_000);
  date.setMinutes(Math.ceil(date.getMinutes() / 5) * 5, 0, 0);
  return localDateTimeValue(date);
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function statusLabel(status: Campaign["status"]): string {
  return status.replaceAll("_", " ");
}

const templateVariables = ["first_name", "name", "email", "company", "job_title"] as const;
const emptyStep = (): StepDraft => ({ subject: "", body: "", delay_minutes: 0 });

function contactValues(contact: Contact): Record<string, string> {
  return {
    first_name: contact.name?.trim().split(/\s+/)[0] || "",
    name: contact.name || "",
    email: contact.email,
    company: contact.company || "",
    job_title: contact.job_title || "",
  };
}

function renderContactTemplate(template: string, contact: Contact): string {
  const values = contactValues(contact);
  return template.replace(/\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g, (token, name: string) => values[name] || token);
}

export default function CampaignsPanel({ connected, workspaceId, replyMonitoringEnabled }: { connected: boolean; workspaceId: string | null; replyMonitoringEnabled: boolean }) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [steps, setSteps] = useState<StepDraft[]>([emptyStep()]);
  const [scheduledAt, setScheduledAt] = useState(initialSchedule);
  const [intervalMinutes, setIntervalMinutes] = useState(0);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const timezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", []);

  const refresh = useCallback(async () => {
    if (!connected || !workspaceId) {
      setContacts([]);
      setCampaigns([]);
      return;
    }
    try {
      const [availableContacts, workspaceCampaigns] = await Promise.all([
        api<Contact[]>("/api/contacts"),
        api<Campaign[]>("/api/campaigns"),
      ]);
      setContacts(availableContacts);
      setCampaigns(workspaceCampaigns);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load campaigns");
    }
  }, [connected, workspaceId]);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 10_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [refresh]);

  const visibleContacts = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    if (!needle) return contacts;
    return contacts.filter((contact) =>
      [contact.name, contact.email, contact.company, ...contact.tags]
        .filter(Boolean)
        .some((value) => value!.toLocaleLowerCase().includes(needle)),
    );
  }, [contacts, search]);

  const previewContact = useMemo(
    () => contacts.find((contact) => selected.has(contact.id)) || null,
    [contacts, selected],
  );
  const previewWarnings = useMemo(() => {
    if (!previewContact) return [];
    const values = contactValues(previewContact);
    const variables = Array.from(steps.map((step) => `${step.subject}\n${step.body}`).join("\n").matchAll(/\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g), (match) => match[1]);
    return Array.from(new Set(variables.filter((name) => !templateVariables.includes(name as typeof templateVariables[number]) || !values[name])));
  }, [previewContact, steps]);

  function updateStep(index: number, updates: Partial<StepDraft>) {
    setSteps((current) => current.map((step, stepIndex) => stepIndex === index ? { ...step, ...updates } : step));
  }

  function addStep() {
    setSteps((current) => current.length >= 10 ? current : [...current, { ...emptyStep(), delay_minutes: 1440 }]);
  }

  function removeStep(index: number) {
    setSteps((current) => current.filter((_, stepIndex) => stepIndex !== index));
  }

  function toggleContact(contactId: string) {
    if (!selected.has(contactId) && selected.size >= 100) {
      setError("A campaign can include up to 100 recipients.");
      return;
    }
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(contactId)) next.delete(contactId);
      else next.add(contactId);
      return next;
    });
  }

  function toggleVisible() {
    setSelected((current) => {
      const next = new Set(current);
      const allVisibleSelected = visibleContacts.length > 0 && visibleContacts.every((contact) => next.has(contact.id));
      if (allVisibleSelected) {
        visibleContacts.forEach((contact) => next.delete(contact.id));
      } else {
        visibleContacts
          .filter((contact) => !next.has(contact.id))
          .slice(0, Math.max(0, 100 - next.size))
          .forEach((contact) => next.add(contact.id));
      }
      return next;
    });
  }

  async function submitCampaign(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || steps.some((step) => !step.subject.trim() || !step.body.trim()) || !scheduledAt || selected.size === 0) return;
    setLoading(true);
    setError(null);
    setNotice(null);
    try {
      const campaign = await api<Campaign>("/api/campaigns", {
        method: "POST",
        body: JSON.stringify({
          name,
          contact_ids: contacts.filter((contact) => selected.has(contact.id)).map((contact) => contact.id),
          steps,
          scheduled_at: scheduledAt,
          timezone,
          interval_minutes: intervalMinutes,
        }),
      });
      setName("");
      setSteps([emptyStep()]);
      setScheduledAt(initialSchedule());
      setIntervalMinutes(0);
      setSelected(new Set());
      setExpandedId(campaign.id);
      setNotice(`Scheduled ${campaign.recipient_count} ${campaign.recipient_count === 1 ? "recipient" : "recipients"}.`);
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not schedule the campaign");
    } finally {
      setLoading(false);
    }
  }

  async function stopCampaign(campaignId: string) {
    setError(null);
    setNotice(null);
    try {
      await api<Campaign>(`/api/campaigns/${campaignId}`, { method: "DELETE" });
      setNotice("Pending campaign recipients were cancelled.");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not stop the campaign");
    }
  }

  if (!connected) {
    return <div className="empty-state contact-empty"><div>↗</div><h3>Connect Gmail to create campaigns</h3><p>Campaigns send from your connected inbox and stay isolated to the active workspace.</p></div>;
  }

  return (
    <>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}
      {!replyMonitoringEnabled && <div className="scope-banner"><span><strong>Reconnect Gmail to enable stop-on-reply.</strong> Existing access can send email but cannot check campaign threads.</span><a href={`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/auth/google`}>Reconnect Gmail</a></div>}

      <section className="campaign-builder">
        <form className="campaign-form" onSubmit={submitCampaign}>
          <div><p className="eyebrow dark">NEW SEQUENCE</p><h3>Compose the journey</h3><p>Relay sends each step automatically and stops future follow-ups when the recipient replies.</p></div>
          <label>Campaign name<input value={name} maxLength={200} placeholder="October follow-up" onChange={(event) => setName(event.target.value)} /></label>
          <div className="sequence-steps">
            {steps.map((step, index) => (
              <section className="sequence-step" key={index}>
                <div className="sequence-step-head">
                  <span className="step-number">{index + 1}</span>
                  <div><strong>{index === 0 ? "Opening email" : `Follow-up ${index}`}</strong><small>{index === 0 ? "Sent at the campaign start time" : `Wait ${step.delay_minutes} minutes after the previous step`}</small></div>
                  {index > 0 && <button type="button" className="remove-step" onClick={() => removeStep(index)}>Remove</button>}
                </div>
                {index > 0 && <label>Wait after previous step<input type="number" min={1} max={43200} value={step.delay_minutes} onChange={(event) => updateStep(index, { delay_minutes: Number(event.target.value) })} /><small className="field-hint">Minutes — 1440 is one day</small></label>}
                <label>Subject<input value={step.subject} maxLength={998} placeholder={index === 0 ? "A quick introduction" : "Following up"} onChange={(event) => updateStep(index, { subject: event.target.value })} /></label>
                <label>Message<textarea value={step.body} maxLength={100000} rows={5} placeholder={index === 0 ? "Hi {{first_name}} — thanks for your time…" : "Hi {{first_name}}, just following up…"} onChange={(event) => updateStep(index, { body: event.target.value })} /></label>
                <div className="variable-tools">
                  <span>Insert into this message</span>
                  <div>{templateVariables.map((variable) => <button type="button" key={variable} onClick={() => updateStep(index, { body: `${step.body}${step.body && !step.body.endsWith(" ") ? " " : ""}{{${variable}}}` })}>{`{{${variable}}}`}</button>)}</div>
                </div>
                {previewContact && (step.subject || step.body) && (
                  <div className="campaign-preview">
                    <div><span>STEP {index + 1} PREVIEW</span><strong>{previewContact.name || previewContact.email}</strong></div>
                    <strong>{renderContactTemplate(step.subject, previewContact) || "No subject"}</strong>
                    <p>{renderContactTemplate(step.body, previewContact) || "No message"}</p>
                  </div>
                )}
              </section>
            ))}
          </div>
          {previewWarnings.length > 0 && <div className="template-warning">Missing or unsupported: {previewWarnings.map((variable) => `{{${variable}}}`).join(", ")}. Relay will not schedule until these are fixed.</div>}
          {steps.length < 10 && <button className="add-step" type="button" onClick={addStep}>+ Add follow-up step</button>}
          <div className="form-split">
            <label>First send<input type="datetime-local" min={localDateTimeValue(new Date())} value={scheduledAt} onChange={(event) => setScheduledAt(event.target.value)} /></label>
            <label>Minutes apart<input type="number" min={0} max={43200} value={intervalMinutes} onChange={(event) => setIntervalMinutes(Number(event.target.value))} /></label>
          </div>
          <div className="campaign-confirm">
            <span>{selected.size}/100 selected · {timezone.replaceAll("_", " ")}</span>
            <button className="button button-primary" type="submit" disabled={loading || selected.size === 0 || !name.trim() || steps.some((step) => !step.subject.trim() || !step.body.trim()) || !scheduledAt || previewWarnings.length > 0}>
              {loading ? "Scheduling…" : `Launch for ${selected.size || ""} ${selected.size === 1 ? "contact" : "contacts"}`}
            </button>
          </div>
        </form>

        <section className="campaign-contacts">
          <div className="campaign-contacts-head">
            <div><p className="eyebrow dark">RECIPIENTS</p><h3>Select contacts</h3></div>
            <button type="button" onClick={toggleVisible}>{visibleContacts.length > 0 && visibleContacts.every((contact) => selected.has(contact.id)) ? "Clear visible" : "Select visible"}</button>
          </div>
          <input className="campaign-search" aria-label="Search campaign contacts" value={search} placeholder="Search contacts…" onChange={(event) => setSearch(event.target.value)} />
          <div className="campaign-contact-list">
            {visibleContacts.length === 0 ? <div className="small-empty"><strong>No contacts found</strong><span>Add contacts in this workspace first.</span></div> : visibleContacts.map((contact) => (
              <label className={`campaign-contact ${selected.has(contact.id) ? "selected" : ""}`} key={contact.id}>
                <input type="checkbox" checked={selected.has(contact.id)} disabled={!selected.has(contact.id) && selected.size >= 100} onChange={() => toggleContact(contact.id)} />
                <span className="contact-initial">{(contact.name || contact.email).charAt(0).toUpperCase()}</span>
                <span><strong>{contact.name || "Unnamed contact"}</strong><small>{contact.email}{contact.company ? ` · ${contact.company}` : ""}</small></span>
              </label>
            ))}
          </div>
        </section>
      </section>

      <section className="campaign-history">
        <div className="section-title"><div><p className="eyebrow dark">OUTREACH</p><h2>Campaign activity</h2></div><button className="refresh-button" onClick={() => void refresh()}>Refresh</button></div>
        <div className="campaign-list">
          {campaigns.length === 0 ? <div className="empty-state"><div>⌁</div><h3>No campaigns yet</h3><p>Select contacts, build a sequence, and choose when delivery should begin.</p></div> : campaigns.map((campaign) => {
            const expanded = expandedId === campaign.id;
            const remaining = (campaign.status_counts.pending || 0);
            return (
              <article className="campaign-card" key={campaign.id}>
                <button className="campaign-summary" type="button" onClick={() => setExpandedId(expanded ? null : campaign.id)} aria-expanded={expanded}>
                  <span className={`queue-icon ${campaign.status === "completed" ? "sent" : campaign.status === "partial_failure" ? "failed" : ""}`}>{campaign.status === "completed" ? "✓" : "↗"}</span>
                  <span className="campaign-title"><strong>{campaign.name}</strong><small>{campaign.steps.length || 1} {(campaign.steps.length || 1) === 1 ? "step" : "steps"} · {campaign.recipient_count} recipients · {formatTime(campaign.scheduled_at)}</small></span>
                  <span className={`pill ${campaign.status === "completed" ? "sent" : campaign.status === "partial_failure" ? "failed" : ""}`}>{statusLabel(campaign.status)}</span>
                  <span className="disclosure">{expanded ? "−" : "+"}</span>
                </button>
                {expanded && (
                  <div className="recipient-table">
                    <div className="campaign-stats">
                      <span><strong>{campaign.status_counts.sent || 0}</strong> sent</span>
                      <span><strong>{campaign.status_counts.pending || 0}</strong> pending</span>
                      <span><strong>{campaign.status_counts.failed || 0}</strong> failed</span>
                      <span><strong>{campaign.status_counts.cancelled || 0}</strong> cancelled</span>
                      <span><strong>{campaign.enrollment_counts.replied || 0}</strong> replied</span>
                      {remaining > 0 && <button className="cancel-button" type="button" onClick={() => void stopCampaign(campaign.id)}>Cancel pending</button>}
                    </div>
                    {campaign.recipients.map((recipient) => {
                      const deliveries = recipient.deliveries.length > 0 ? recipient.deliveries : [{
                        id: recipient.id,
                        step_id: "legacy",
                        step_position: 0,
                        scheduled_email_id: recipient.scheduled_email_id,
                        subject: recipient.subject,
                        body: recipient.body,
                        scheduled_at: recipient.scheduled_at,
                        status: recipient.status,
                        retry_count: recipient.retry_count,
                        last_error: recipient.last_error,
                        gmail_message_id: recipient.gmail_message_id,
                        sent_at: recipient.sent_at,
                      }];
                      return (
                        <div className="recipient-sequence" key={recipient.id}>
                          <div className="recipient-row">
                            <span><strong>{recipient.recipient_name || "Unnamed contact"}</strong><small>{recipient.recipient_email}</small></span>
                            <small>{recipient.replied_at ? `Replied ${formatTime(recipient.replied_at)}` : `${deliveries.length} ${deliveries.length === 1 ? "step" : "steps"}`}</small>
                            <span className={`pill ${recipient.sequence_status}`}>{recipient.sequence_status}</span>
                          </div>
                          <div className="delivery-timeline">
                            {deliveries.map((delivery) => (
                              <div className="delivery-item" key={delivery.id}>
                                <span className={`delivery-dot ${delivery.status}`}>{delivery.status === "sent" ? "✓" : delivery.step_position + 1}</span>
                                <span><strong>Step {delivery.step_position + 1}: {delivery.subject}</strong><small>{formatTime(delivery.sent_at || delivery.scheduled_at)}{delivery.last_error ? ` · ${delivery.last_error}` : ""}</small></span>
                                <span className={`pill ${delivery.status}`}>{delivery.status}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      </section>
    </>
  );
}
