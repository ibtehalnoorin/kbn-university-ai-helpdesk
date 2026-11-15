import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CheckCircle2, Loader2, Phone, X } from "lucide-react";
import { ApiError, api } from "../api/client";
import type { EnquiryTopic } from "../api/types";
import { readActiveSession } from "../lib/storage";

const TOPICS: { value: EnquiryTopic; label: string }[] = [
  { value: "admissions", label: "Admissions" },
  { value: "fees", label: "Fees and scholarships" },
  { value: "exams", label: "Exams and results" },
  { value: "hostel", label: "Hostel and campus life" },
  { value: "other", label: "Something else" },
];

const field =
  "mt-1 w-full rounded-lg border border-sand-200 bg-white px-3 py-2 text-sm text-kbn-950 outline-none transition focus:border-kbn-400 focus:ring-2 focus:ring-kbn-200";

export function EnquiryDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [topic, setTopic] = useState<EnquiryTopic>("admissions");
  const [message, setMessage] = useState("");
  const [consent, setConsent] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reference, setReference] = useState<string | null>(null);
  const firstField = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    window.setTimeout(() => firstField.current?.focus(), 50);
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !sending) onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, sending, onClose]);

  if (!open) return null;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (sending) return;
    setSending(true);
    setError(null);
    try {
      const receipt = await api.sendEnquiry({
        name: name.trim(),
        contact: contact.trim(),
        topic,
        message: message.trim(),
        consent,
        session_id: readActiveSession(),
      });
      setReference(receipt.id);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 422) {
        setError("Please check your details: add your name, a valid email or phone number, and a message of at least 10 characters, and tick the consent box.");
      } else if (failure instanceof ApiError && failure.status === 429) {
        setError("You have sent several enquiries recently. Please try again later or call the admissions office.");
      } else {
        setError("We could not send your enquiry right now. Please try again, or call the admissions office.");
      }
    } finally {
      setSending(false);
    }
  }

  function close() {
    if (sending) return;
    if (reference) {
      setReference(null);
      setMessage("");
      setConsent(false);
    }
    onClose();
  }

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-kbn-950/45 p-4 backdrop-blur-sm"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="enquiry-title"
        className="max-h-[92dvh] w-full max-w-lg animate-fade-up overflow-y-auto rounded-2xl border border-sand-200 bg-white p-5 shadow-2xl shadow-kbn-950/20"
      >
        <div className="flex items-start gap-3">
          <img src="/brand/kbn-mark.png" alt="" width={40} height={40} className="h-10 w-10 shrink-0 object-contain" />
          <div className="min-w-0 flex-1">
            <h2 id="enquiry-title" className="font-display text-lg font-semibold text-kbn-600">
              Talk to the admissions office
            </h2>
            <p className="mt-0.5 text-xs text-stone-500">Leave your details and the university will get back to you.</p>
          </div>
          <button type="button" onClick={close} className="rounded-lg p-1.5 text-stone-400 hover:bg-sand-100 hover:text-stone-600" aria-label="Close">
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {reference ? (
          <div className="mt-5 text-center" role="status">
            <CheckCircle2 className="mx-auto h-10 w-10 text-kbn-500" aria-hidden="true" />
            <p className="mt-3 font-display text-base font-semibold text-kbn-600">Thank you, your enquiry was sent.</p>
            <p className="mt-1 text-sm text-stone-600">
              Reference <span className="font-mono text-kbn-700">{reference}</span>. The university will contact you using the details you gave.
            </p>
            <p className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-kbn-50 px-3 py-1 text-xs text-kbn-700">
              <Phone className="h-3.5 w-3.5" aria-hidden="true" />
              Admission Cell: +91 8472 236041 (Extn. 128, 136, 137)
            </p>
            <button type="button" onClick={close} className="mt-5 block w-full rounded-xl bg-kbn-600 px-3 py-2.5 text-sm font-semibold text-white hover:bg-kbn-700">
              Close
            </button>
          </div>
        ) : (
          <form onSubmit={submit} className="mt-4 space-y-3">
            <label className="block text-xs font-semibold text-stone-600">
              Your name
              <input ref={firstField} value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required autoComplete="name" className={field} />
            </label>
            <label className="block text-xs font-semibold text-stone-600">
              Email or phone number
              <input value={contact} onChange={(e) => setContact(e.target.value)} maxLength={120} required autoComplete="email" placeholder="you@example.com or +91 98450 12345" className={field} />
            </label>
            <label className="block text-xs font-semibold text-stone-600">
              Topic
              <select value={topic} onChange={(e) => setTopic(e.target.value as EnquiryTopic)} className={field}>
                {TOPICS.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </label>
            <label className="block text-xs font-semibold text-stone-600">
              Your question
              <textarea value={message} onChange={(e) => setMessage(e.target.value)} maxLength={1000} minLength={10} required rows={4} className={field} />
              <span className="mt-0.5 block text-right text-[0.62rem] font-normal text-stone-400">{message.length}/1000</span>
            </label>
            <label className="flex items-start gap-2 text-xs leading-relaxed text-stone-600">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5 h-4 w-4 rounded border-sand-200 accent-[var(--color-kbn-600)]" />
              <span>I agree that Khaja Bandanawaz University may use these details to contact me about my enquiry. They are kept for up to 6 months.</span>
            </label>
            {error ? (
              <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</p>
            ) : null}
            <button
              type="submit"
              disabled={sending || !consent}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-kbn-600 px-3 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-kbn-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {sending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
              {sending ? "Sending…" : "Send enquiry"}
            </button>
          </form>
        )}
      </div>
    </div>,
    document.body,
  );
}
