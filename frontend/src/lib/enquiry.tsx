import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { EnquiryDialog } from "../components/EnquiryDialog";

const EnquiryContext = createContext<{ openEnquiry: () => void }>({ openEnquiry: () => undefined });

/** Lets any component open the "Talk to the admissions office" form. */
export function EnquiryProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const openEnquiry = useCallback(() => setOpen(true), []);
  const value = useMemo(() => ({ openEnquiry }), [openEnquiry]);
  return (
    <EnquiryContext.Provider value={value}>
      {children}
      <EnquiryDialog open={open} onClose={() => setOpen(false)} />
    </EnquiryContext.Provider>
  );
}

export function useEnquiry() {
  return useContext(EnquiryContext);
}
