"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

type Toast = { id: number; text: string; tone: "info" | "error" };
type ToastApi = { show: (text: string, tone?: Toast["tone"]) => void };

const ToastContext = createContext<ToastApi>({ show: () => {} });
const TOAST_MS = 4_000;

export function ToastHost({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const show = useCallback((text: string, tone: Toast["tone"] = "info") => {
    const id = next.current++;
    setToasts((current) => [...current.slice(-3), { id, text, tone }]);
    setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), TOAST_MS);
  }, []);
  const api = useMemo(() => ({ show }), [show]);
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex max-w-sm flex-col gap-2"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role={toast.tone === "error" ? "alert" : "status"}
            className={`item-enter pointer-events-auto rounded-md border px-3 py-2 text-sm shadow-lg ${
              toast.tone === "error"
                ? "border-red-500/50 bg-red-950 text-red-100"
                : "border-line bg-panel text-ink"
            }`}
          >
            {toast.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(ToastContext);
}
