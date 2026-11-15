import {
  AlertTriangle,
  BadgeCheck,
  Globe2,
  Info,
  ShieldQuestion,
} from "lucide-react";
import type { Verification, VerificationStatus } from "../api/types";

const styles: Record<
  VerificationStatus,
  { className: string; Icon: typeof BadgeCheck }
> = {
  verified_kb: {
    className:
      "border-kbn-200 bg-kbn-50 text-kbn-800 [&>svg]:text-kbn-600",
    Icon: BadgeCheck,
  },
  general_unverified: {
    className:
      "border-amber-200 bg-amber-50 text-amber-800 [&>svg]:text-amber-600",
    Icon: Info,
  },
  unavailable: {
    className:
      "border-rose-200 bg-rose-50 text-rose-700 [&>svg]:text-rose-500",
    Icon: ShieldQuestion,
  },
  web_unverified: {
    className:
      "border-sky-200 bg-sky-50 text-sky-800 [&>svg]:text-sky-600",
    Icon: Globe2,
  },
};

export function VerificationBadge({
  verification,
  compact = false,
}: {
  verification: Verification;
  compact?: boolean;
}) {
  const config = styles[verification.status] ?? styles.unavailable;
  const { Icon } = config;
  const label =
    compact && verification.status === "verified_kb"
      ? "Verified"
      : verification.label;

  return (
    <span
      className={`inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-[0.68rem] font-semibold tracking-wide ${config.className}`}
      title={verification.reason ?? verification.label}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span className="truncate">{label}</span>
    </span>
  );
}

export function VerificationNote({
  verification,
}: {
  verification: Verification;
}) {
  if (verification.status === "verified_kb" || !verification.reason) {
    return null;
  }
  if (verification.status === "unavailable") {
    return (
      <p className="mt-2 flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs leading-relaxed text-rose-700">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{verification.reason}</span>
      </p>
    );
  }
  return (
    <p className="mt-2 flex items-start gap-2 rounded-lg bg-sand-100 px-3 py-2 text-xs leading-relaxed text-stone-600">
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{verification.reason}</span>
    </p>
  );
}
