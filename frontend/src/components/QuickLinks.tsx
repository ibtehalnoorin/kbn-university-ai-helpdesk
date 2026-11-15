import { ExternalLink } from "lucide-react";

const SITE = "https://www.kbn.university";

/** Official pages on the university website (paths taken from its sitemap). */
export const OFFICIAL_LINKS = [
  { label: "Admissions", href: `${SITE}/admissions` },
  { label: "Fee structure", href: `${SITE}/fee-structure` },
  { label: "Programmes", href: `${SITE}/courses` },
  { label: "Notifications", href: `${SITE}/notifications-circulars` },
  { label: "Exam schedule", href: `${SITE}/exam-schedule` },
  { label: "Events calendar", href: `${SITE}/calendar-of-events` },
  { label: "Grievance redressal", href: `${SITE}/student-grievance-redressal-committe` },
  { label: "Anti-ragging", href: `${SITE}/anti-ragging` },
  { label: "Contact", href: `${SITE}/contact` },
] as const;

export function QuickLinks({ className = "" }: { className?: string }) {
  return (
    <nav aria-label="Official university pages" className={className}>
      <p className="mb-2 text-[0.62rem] font-bold uppercase tracking-[0.16em] text-gold-600">
        Official pages
      </p>
      <ul className="flex flex-wrap justify-center gap-1.5">
        {OFFICIAL_LINKS.map((link) => (
          <li key={link.href}>
            <a
              href={link.href}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 rounded-full border border-kbn-200 bg-white/80 px-2.5 py-1 text-[0.7rem] font-medium text-kbn-700 transition-colors hover:border-gold-300 hover:bg-gold-200/40 hover:text-gold-600"
            >
              {link.label}
              <ExternalLink className="h-3 w-3 opacity-60" aria-hidden="true" />
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
