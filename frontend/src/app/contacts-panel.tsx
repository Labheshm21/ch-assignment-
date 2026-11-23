"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { API_URL, api } from "@/lib/api";

type ContactStage = "new" | "qualified" | "rejected" | "contacted" | "replied" | "interested" | "not_interested" | "meeting" | "won" | "lost" | "unsubscribed";
type UseCase = "unknown" | "generative_video" | "gaming" | "robotics" | "media" | "avatars" | "other";
type TechnicalFit = "unknown" | "low" | "medium" | "high";
type BuyingSignal = "none" | "weak" | "medium" | "strong";
type FitPriority = "low" | "medium" | "high";

type Contact = {
  id: string;
  workspace_id: string;
  name: string | null;
  email: string;
  company: string | null;
  job_title: string | null;
  website: string | null;
  industry: string | null;
  company_size: string | null;
  lead_source: string | null;
  use_case: UseCase;
  technical_fit: TechnicalFit;
  buying_signal: BuyingSignal;
  research_notes: string | null;
  fit_score: number;
  fit_priority: FitPriority;
  fit_reason: string;
  stage: ContactStage;
  tags: string[];
  created_at: string;
  updated_at: string;
};

type ContactMutation = { contact: Contact; created: boolean };
type ImportResult = {
  added: number;
  updated: number;
  rejected: number;
  errors: { row: number; message: string }[];
};

type ContactForm = {
  name: string;
  email: string;
  company: string;
  job_title: string;
  website: string;
  industry: string;
  company_size: string;
  lead_source: string;
  use_case: string;
  technical_fit: string;
  buying_signal: string;
  research_notes: string;
  tags: string;
};

const emptyForm: ContactForm = {
  name: "", email: "", company: "", job_title: "", website: "", industry: "",
  company_size: "", lead_source: "", use_case: "unknown", technical_fit: "unknown",
  buying_signal: "none", research_notes: "", tags: "",
};
const stageOptions: ContactStage[] = ["new", "qualified", "rejected", "contacted", "replied", "interested", "not_interested", "meeting", "won", "lost", "unsubscribed"];
const stageLabel = (stage: ContactStage) => stage === "new" ? "needs review" : stage.replaceAll("_", " ");

export default function ContactsPanel({ connected, workspaceId }: { connected: boolean; workspaceId: string | null }) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [form, setForm] = useState<ContactForm>(emptyForm);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [priorityFilter, setPriorityFilter] = useState<"all" | FitPriority>("all");
  const [reviewFilter, setReviewFilter] = useState<"all" | "new" | "qualified" | "rejected">("all");
  const [loading, setLoading] = useState(false);
  const [stageUpdatingId, setStageUpdatingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    if (!connected || !workspaceId) return;
    try {
      setContacts(await api<Contact[]>("/api/contacts"));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load contacts");
    }
  }, [connected, workspaceId]);

  useEffect(() => {
    const refreshContacts = () => void refresh();
    const initial = window.setTimeout(refreshContacts, 0);
    const interval = window.setInterval(refreshContacts, 10_000);
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") refreshContacts();
    };
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [refresh]);

  const visibleContacts = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return contacts.filter((contact) =>
      (reviewFilter === "all" || contact.stage === reviewFilter) &&
      (priorityFilter === "all" || contact.fit_priority === priorityFilter) &&
      (!needle || [contact.name, contact.email, contact.company, contact.job_title, contact.website, contact.industry, contact.lead_source, contact.use_case, contact.fit_priority, ...contact.tags]
        .filter(Boolean)
        .some((value) => value!.toLocaleLowerCase().includes(needle))),
    );
  }, [contacts, priorityFilter, reviewFilter, search]);

  function updateField(field: keyof ContactForm, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function resetForm() {
    setForm(emptyForm);
    setEditingId(null);
  }

  async function submitContact(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setNotice(null);
    const payload = {
      email: form.email,
      name: form.name || null,
      company: form.company || null,
      job_title: form.job_title || null,
      website: form.website || null,
      industry: form.industry || null,
      company_size: form.company_size || null,
      lead_source: form.lead_source || null,
      use_case: form.use_case,
      technical_fit: form.technical_fit,
      buying_signal: form.buying_signal,
      research_notes: form.research_notes || null,
      tags: form.tags.split(",").map((tag) => tag.trim()).filter(Boolean),
    };
    try {
      if (editingId) {
        await api<Contact>(`/api/contacts/${editingId}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        setNotice("Contact updated.");
      } else {
        const result = await api<ContactMutation>("/api/contacts", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setNotice(result.created ? "Contact added." : "Existing contact updated by email.");
      }
      resetForm();
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save contact");
    } finally {
      setLoading(false);
    }
  }

  function startEditing(contact: Contact) {
    setEditingId(contact.id);
    setForm({
      name: contact.name ?? "",
      email: contact.email,
      company: contact.company ?? "",
      job_title: contact.job_title ?? "",
      website: contact.website ?? "",
      industry: contact.industry ?? "",
      company_size: contact.company_size ?? "",
      lead_source: contact.lead_source ?? "",
      use_case: contact.use_case,
      technical_fit: contact.technical_fit,
      buying_signal: contact.buying_signal,
      research_notes: contact.research_notes ?? "",
      tags: contact.tags.join(", "),
    });
    setNotice(null);
    setError(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function removeContact(contact: Contact) {
    if (!window.confirm(`Delete ${contact.name || contact.email}?`)) return;
    setError(null);
    try {
      await api<{ ok: boolean }>(`/api/contacts/${contact.id}`, { method: "DELETE" });
      setNotice("Contact deleted.");
      if (editingId === contact.id) resetForm();
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not delete contact");
    }
  }

  async function changeStage(contact: Contact, stage: ContactStage) {
    setStageUpdatingId(contact.id);
    setError(null);
    setNotice(null);
    try {
      const updated = await api<Contact>(`/api/contacts/${contact.id}/stage`, {
        method: "PATCH",
        body: JSON.stringify({ stage }),
      });
      setContacts((current) => current.map((item) => item.id === updated.id ? updated : item));
      setNotice(`${contact.name || contact.email} moved to ${stageLabel(stage)}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not update contact stage");
    } finally {
      setStageUpdatingId(null);
    }
  }

  async function importCsv(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setLoading(true);
    setError(null);
    setNotice(null);
    const body = new FormData();
    body.append("file", file);
    try {
      const result = await api<ImportResult>("/api/contacts/import", { method: "POST", body });
      setNotice(
        `Import complete: ${result.added} added, ${result.updated} updated, ${result.rejected} rejected.` +
          (result.errors.length ? ` First issue: row ${result.errors[0].row} — ${result.errors[0].message}` : ""),
      );
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not import CSV");
    } finally {
      setLoading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  function downloadTemplate() {
    const csv = "name,email,company,job_title,website,industry,company_size,lead_source,use_case,technical_fit,buying_signal,research_notes,tags\nAda Lovelace,ada@example.com,Analytical Engines,Founder,https://example.com,Artificial Intelligence,51-200,Apollo,generative_video,high,strong,Hiring video AI engineers,VIP;Technical\n";
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "relay-contacts-template.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function exportContacts() {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_URL}/api/contacts/export`, { credentials: "include" });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({ detail: "Export failed" }));
        throw new Error(payload.detail ?? "Export failed");
      }
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "relay-contacts.csv";
      anchor.click();
      URL.revokeObjectURL(url);
      setNotice(`Exported ${contacts.length} ${contacts.length === 1 ? "contact" : "contacts"}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not export contacts");
    } finally {
      setLoading(false);
    }
  }

  if (!connected) {
    return (
      <div className="empty-state contact-empty">
        <div>◎</div>
        <h3>Connect Gmail to manage contacts</h3>
        <p>Your personal workspace is created automatically when you connect your account.</p>
      </div>
    );
  }

  return (
    <>
      <section className="contact-toolbar">
        <div>
          <p className="eyebrow dark">WORKSPACE ADDRESS BOOK</p>
          <h2>{contacts.length} {contacts.length === 1 ? "contact" : "contacts"}</h2>
        </div>
        <div className="import-actions">
          <button className="refresh-button" type="button" onClick={downloadTemplate}>CSV template</button>
          <button className="refresh-button" type="button" disabled={loading || contacts.length === 0} onClick={() => void exportContacts()}>Export CSV</button>
          <label className={`button button-primary ${loading ? "disabled-label" : ""}`}>
            Import CSV
            <input ref={fileInput} type="file" accept=".csv,text/csv" disabled={loading} onChange={importCsv} hidden />
          </label>
        </div>
      </section>

      {(error || notice) && <div className={error ? "error-banner" : "success-banner"}>{error ?? notice}</div>}

      <section className="contact-grid">
        <form className="contact-form" onSubmit={submitContact}>
          <div>
            <p className="eyebrow dark">{editingId ? "EDIT CONTACT" : "ADD OR UPDATE"}</p>
            <h3>{editingId ? "Update this person" : "Save a person"}</h3>
            <p>Using an existing email updates that contact instead of creating a duplicate.</p>
          </div>
          <label>Name<input value={form.name} maxLength={200} onChange={(event) => updateField("name", event.target.value)} placeholder="Ada Lovelace" /></label>
          <label>Email *<input type="email" required value={form.email} maxLength={320} onChange={(event) => updateField("email", event.target.value)} placeholder="ada@example.com" /></label>
          <div className="form-split">
            <label>Company<input value={form.company} maxLength={200} onChange={(event) => updateField("company", event.target.value)} placeholder="Acme" /></label>
            <label>Job title<input value={form.job_title} maxLength={200} onChange={(event) => updateField("job_title", event.target.value)} placeholder="Founder" /></label>
          </div>
          <label>Website<input type="url" value={form.website} maxLength={500} onChange={(event) => updateField("website", event.target.value)} placeholder="https://example.com" /></label>
          <div className="form-split">
            <label>Industry<input value={form.industry} maxLength={100} onChange={(event) => updateField("industry", event.target.value)} placeholder="Gaming" /></label>
            <label>Company size<input value={form.company_size} maxLength={50} onChange={(event) => updateField("company_size", event.target.value)} placeholder="51-200" /></label>
          </div>
          <div className="form-split">
            <label>Lead source<input value={form.lead_source} maxLength={100} onChange={(event) => updateField("lead_source", event.target.value)} placeholder="Apollo" /></label>
            <label>Use case<select value={form.use_case} onChange={(event) => updateField("use_case", event.target.value)}><option value="unknown">Unknown</option><option value="generative_video">Generative video</option><option value="gaming">Gaming</option><option value="robotics">Robotics</option><option value="media">Media</option><option value="avatars">Avatars</option><option value="other">Other</option></select></label>
          </div>
          <div className="form-split">
            <label>Technical fit<select value={form.technical_fit} onChange={(event) => updateField("technical_fit", event.target.value)}><option value="unknown">Unknown</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
            <label>Buying signal<select value={form.buying_signal} onChange={(event) => updateField("buying_signal", event.target.value)}><option value="none">None</option><option value="weak">Weak</option><option value="medium">Medium</option><option value="strong">Strong</option></select></label>
          </div>
          <label>Research notes<textarea value={form.research_notes} maxLength={2000} rows={3} onChange={(event) => updateField("research_notes", event.target.value)} placeholder="Why this account may need Reactor…" /></label>
          <label>Tags<input value={form.tags} onChange={(event) => updateField("tags", event.target.value)} placeholder="Founder, VIP" /></label>
          <div className="contact-form-actions">
            {editingId && <button className="text-button dark-text" type="button" onClick={resetForm}>Cancel edit</button>}
            <button className="button button-primary" disabled={loading} type="submit">{loading ? "Saving…" : editingId ? "Save changes" : "Save contact"}</button>
          </div>
        </form>

        <section className="contacts-card">
          <div className="contacts-card-head">
            <div><p className="eyebrow dark">PEOPLE</p><h3>All contacts</h3></div>
            <div className="contact-filters"><select aria-label="Filter by review status" value={reviewFilter} onChange={(event) => setReviewFilter(event.target.value as "all" | "new" | "qualified" | "rejected")}><option value="all">All status</option><option value="new">Review queue</option><option value="qualified">Qualified</option><option value="rejected">Rejected</option></select><select aria-label="Filter by fit priority" value={priorityFilter} onChange={(event) => setPriorityFilter(event.target.value as "all" | FitPriority)}><option value="all">All fit</option><option value="high">High fit</option><option value="medium">Medium fit</option><option value="low">Low fit</option></select><input aria-label="Search contacts" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search…" /></div>
          </div>
          {visibleContacts.length === 0 ? (
            <div className="small-empty"><strong>No contacts found</strong><span>Add someone manually or import a CSV.</span></div>
          ) : (
            <div className="contacts-list">
              {visibleContacts.map((contact) => (
                <article className="contact-row" key={contact.id}>
                  <div className="contact-initial">{(contact.name || contact.email).charAt(0).toUpperCase()}</div>
                  <div className="contact-details">
                    <strong>{contact.name || "Unnamed contact"}</strong>
                    <a href={`mailto:${contact.email}`}>{contact.email}</a>
                    <span>{[contact.job_title, contact.company].filter(Boolean).join(" at ") || "No company details"}</span>
                    <span>{[contact.industry, contact.use_case !== "unknown" ? contact.use_case.replaceAll("_", " ") : null, contact.lead_source].filter(Boolean).join(" · ") || "No account intelligence"}</span>
                    <div className="fit-row"><span className={`fit-badge ${contact.fit_priority}`}>{contact.fit_score} · {contact.fit_priority} fit</span><small>{contact.fit_reason}</small></div>
                    {contact.tags.length > 0 && <div className="tag-row">{contact.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
                  </div>
                  <div className="contact-row-actions">
                    {contact.stage === "new" && <><button className="approve" type="button" disabled={stageUpdatingId === contact.id} onClick={() => void changeStage(contact, "qualified")}>Approve</button><button className="danger" type="button" disabled={stageUpdatingId === contact.id} onClick={() => void changeStage(contact, "rejected")}>Reject</button></>}
                    <select aria-label={`Stage for ${contact.name || contact.email}`} value={contact.stage} disabled={stageUpdatingId === contact.id} onChange={(event) => void changeStage(contact, event.target.value as ContactStage)}>
                      {stageOptions.map((stage) => <option value={stage} key={stage}>{stageLabel(stage)}</option>)}
                    </select>
                    <button type="button" onClick={() => startEditing(contact)}>Edit</button>
                    <button className="danger" type="button" onClick={() => void removeContact(contact)}>Delete</button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </section>
    </>
  );
}
