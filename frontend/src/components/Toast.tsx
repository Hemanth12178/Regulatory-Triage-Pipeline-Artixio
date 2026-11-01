import { useCallback, useState } from "react";

export interface ToastMessage {
  id: number;
  kind: "error" | "success";
  text: string;
}

let nextId = 1;

/** Minimal toast queue - errors from the API must never be swallowed. */
export function useToasts() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (kind: ToastMessage["kind"], text: string) => {
      const id = nextId++;
      setToasts((current) => [...current, { id, kind, text }]);
      // Successes self-dismiss; errors stay until the user acknowledges them.
      if (kind === "success") setTimeout(() => dismiss(id), 2600);
    },
    [dismiss],
  );

  return { toasts, push, dismiss };
}

export function Toasts({
  toasts,
  onDismiss,
}: {
  toasts: ToastMessage[];
  onDismiss: (id: number) => void;
}) {
  if (toasts.length === 0) return null;
  return (
    <div className="toasts">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast ${toast.kind}`} role="status">
          <span className="label">{toast.kind === "error" ? "Rejected" : "Saved"}</span>
          <span style={{ flex: 1 }}>{toast.text}</span>
          <button onClick={() => onDismiss(toast.id)} aria-label="Dismiss">
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
