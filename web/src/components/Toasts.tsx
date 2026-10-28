// Short messages that come and go (an action's result, an error): one Toaster for the
// app, at the top under the top bar (the bottom has the tab bar and the bulk action bar).
import {
  Toast,
  Toaster,
  ToastTitle,
  useToastController,
  type ToastIntent,
} from "@fluentui/react-components";

import { PHONE, useMediaQuery } from "../useMediaQuery";

const TOASTER = "app";

export function AppToaster() {
  const phone = useMediaQuery(PHONE);
  return (
    <Toaster
      toasterId={TOASTER}
      position={phone ? "top" : "top-end"}
      offset={{ vertical: 64 }}
      limit={3}
      pauseOnHover
      pauseOnWindowBlur
    />
  );
}

/** `toast.success("Saved")`, `toast.error(e)` (an Error or its message). */
export function useToast() {
  const { dispatchToast } = useToastController(TOASTER);
  const show = (intent: ToastIntent, text: string) =>
    dispatchToast(
      <Toast>
        <ToastTitle>{text}</ToastTitle>
      </Toast>,
      { intent, timeout: intent === "error" ? 8000 : 3000 },
    );
  return {
    success: (text: string) => show("success", text),
    info: (text: string) => show("info", text),
    error: (e: unknown) => show("error", e instanceof Error ? e.message : String(e)),
  };
}
