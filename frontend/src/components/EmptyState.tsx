import {
  Building2,
  GraduationCap,
  Mail,
  MapPin,
  Trophy,
  Users,
} from "lucide-react";
import { QuickLinks } from "./QuickLinks";

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

      <img
        src="/brand/kbn-logo.png"
        alt="Khaja Bandanawaz University: Educating Humanity"
        className="relative mx-auto h-28 w-auto sm:h-32"
      />

      <h2 className="relative mt-6 max-w-xl font-display text-2xl font-semibold tracking-tight text-kbn-600 sm:text-3xl">
        How can we help you today?
      </h2>
      <p className="relative mt-2 max-w-lg text-sm leading-relaxed text-stone-600">
        Ask about admissions, programmes, faculties, people and official contacts.
        Answers come from the university&apos;s published information; if a fact
        isn&apos;t there, we&apos;ll say so and point you to the right office.
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

      <QuickLinks className="relative mt-7 max-w-2xl" />

      <p className="relative mt-6 flex items-center gap-1.5 text-[0.68rem] text-stone-400">
        <span className="h-1.5 w-1.5 rounded-full bg-gold-500" />
        Please don&apos;t share personal details. Official information: kbn.university
      </p>
    </div>
  );
}
