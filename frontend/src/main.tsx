import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { EnquiryProvider } from "./lib/enquiry";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <EnquiryProvider>
      <App />
    </EnquiryProvider>
  </StrictMode>,
);
