import {
  BookOpenText,
  Building2,
  GraduationCap,
  Mail,
  MapPin,
  Trophy,
  Users,
} from "lucide-react";

const suggestions = [
  {
    icon: Users,
    label: "Leadership",
    prompt: "Who is the Vice Chancellor of KBN University?",
  },
  {
    icon: Mail,
    label: "Contact",
    prompt: "What is the email address of the Vice Chancellor of KBN University?",
  },
  {
    icon: Building2,
    label: "Faculties",
    prompt: "Which faculties and departments does KBN University have?",
  },
  {
    icon: GraduationCap,
    label: "Courses",
    prompt: "What courses are offered at KBN University?",
  },
  {
    icon: MapPin,
    label: "Location",
    prompt: "Where is KBN University located and what are its contact details?",
  },
  {
    icon: Trophy,
    label: "About",
    prompt: "When was KBN University established and how is it ranked?",
  },
];

export function EmptyState({
  onPick,
  disabled,
}: {
  onPick: (prompt: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="relative flex flex-1 flex-col items-center justify-center px-4 py-10 text-center sm:py-16">
      <div className="hero-grid pointer-events-none absolute inset-0 opacity-60" aria-hidden="true" />

      <div className="relative">
        <span className="mx-auto grid h-16 w-16 place-items-center rounded-2xl border border-gold-300/60 bg-gradient-to-br from-kbn-800 to-kbn-950 text-gold-300 shadow-lg shadow-kbn-950/20">
          <BookOpenText className="h-8 w-8" aria-hidden="true" />
        </span>
        <span className="absolute -right-2 -top-2 rounded-full border border-gold-200 bg-gold-300 px-2 py-0.5 text-[0.6rem] font-bold uppercase tracking-wider text-kbn-950">
          Verified
        </span>
      </div>

      <h2 className="relative mt-6 max-w-xl font-display text-2xl font-semibold tracking-tight text-kbn-950 sm:text-3xl">
        How can the KBN University Helpdesk assist you?
      </h2>
      <p className="relative mt-2 max-w-lg text-sm leading-relaxed text-stone-600">
        Ask about people, faculties, courses, admissions, location and official
        contacts. Answers come only from the university&apos;s indexed records —
        if a fact is missing, the helpdesk will tell you.
      </p>

      <div className="relative mt-7 grid w-full max-w-2xl grid-cols-1 gap-2.5 sm:grid-cols-2">
        {suggestions.map(({ icon: Icon, label, prompt }) => (
          <button
            key={prompt}
            type="button"
            disabled={disabled}
            onClick={() => onPick(prompt)}
            className="group flex items-start gap-3 rounded-xl border border-sand-200 bg-white/85 p-3 text-left shadow-sm shadow-stone-200/40 transition-all hover:-translate-y-0.5 hover:border-kbn-200 hover:bg-white hover:shadow-md hover:shadow-kbn-900/5 disabled:pointer-events-none disabled:opacity-50"
          >
            <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-kbn-50 text-kbn-700 transition-colors group-hover:bg-kbn-100">
              <Icon className="h-4 w-4" aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="block text-[0.62rem] font-bold uppercase tracking-[0.14em] text-gold-600">
                {label}
              </span>
              <span className="mt-0.5 block text-xs leading-relaxed text-stone-700">
                {prompt}
              </span>
            </span>
          </button>
        ))}
      </div>

      <p className="relative mt-6 flex items-center gap-1.5 text-[0.68rem] text-stone-400">
        <span className="h-1.5 w-1.5 rounded-full bg-kbn-400" />
        Private and on-device · Qwen3:8B · 38 indexed KBN records
      </p>
    </div>
  );
}
