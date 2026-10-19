"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { API_URL, api } from "@/lib/api";

type Contact = {
  id: string;
  workspace_id: string;
  name: string | null;
  email: string;
  company: string | null;
  job_title: string | null;
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
  tags: string;
};

const emptyForm: ContactForm = { name: "", email: "", company: "", job_title: "", tags: "" };

export default function ContactsPanel({ connected, workspaceId }: { connected: boolean; workspaceId: string | null }) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [form, setForm] = useState<ContactForm>(emptyForm);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
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
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const visibleContacts = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    if (!needle) return contacts;
    return contacts.filter((contact) =>
      [contact.name, contact.email, contact.company, contact.job_title, ...contact.tags]
        .filter(Boolean)
        .some((value) => value!.toLocaleLowerCase().includes(needle)),
    );
  }, [contacts, search]);

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
    const csv = "name,email,company,job_title,tags\nAda Lovelace,ada@example.com,Analytical Engines,Founder,VIP;Technical\n";
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
          <label>Tags<input value={form.tags} onChange={(event) => updateField("tags", event.target.value)} placeholder="Founder, VIP" /></label>
          <div className="contact-form-actions">
            {editingId && <button className="text-button dark-text" type="button" onClick={resetForm}>Cancel edit</button>}
            <button className="button button-primary" disabled={loading} type="submit">{loading ? "Saving…" : editingId ? "Save changes" : "Save contact"}</button>
          </div>
        </form>

        <section className="contacts-card">
          <div className="contacts-card-head">
            <div><p className="eyebrow dark">PEOPLE</p><h3>All contacts</h3></div>
            <input aria-label="Search contacts" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search…" />
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
                    {contact.tags.length > 0 && <div className="tag-row">{contact.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
                  </div>
                  <div className="contact-row-actions">
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
