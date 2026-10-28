/** Web push on this device: the service worker (public/sw.js) and its push subscription. */

/** iPhone or iPad, also an iPad that says it is a Mac. */
export const isIos = () =>
  /iPhone|iPad|iPod/.test(navigator.userAgent) ||
  (navigator.userAgent.includes("Macintosh") && navigator.maxTouchPoints > 1);

const standalone = () =>
  window.matchMedia("(display-mode: standalone)").matches ||
  (navigator as { standalone?: boolean }).standalone === true;

/**
 * "ok", or why this device can't: iOS only lets home screen apps have push, other browsers
 * may not support it at all.
 */
export function pushSupport(): "ok" | "ios-browser" | "unsupported" {
  if ("serviceWorker" in navigator && "PushManager" in window && "Notification" in window) {
    return "ok";
  }
  return isIos() && !standalone() ? "ios-browser" : "unsupported";
}

/** "iPhone", "iPad", or the browser and system ("Chrome – Mac"), to tell devices apart. */
export function deviceName(): string {
  const ua = navigator.userAgent;
  if (/iPhone|iPod/.test(ua)) return "iPhone";
  if (isIos()) return "iPad";
  const os = /Android/.test(ua)
    ? "Android"
    : /Mac/.test(ua)
      ? "Mac"
      : /Windows/.test(ua)
        ? "Windows"
        : /Linux/.test(ua)
          ? "Linux"
          : null;
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /Firefox\//.test(ua)
      ? "Firefox"
      : /Chrome\//.test(ua)
        ? "Chrome"
        : /Safari\//.test(ua)
          ? "Safari"
          : null;
  return [browser, os].filter(Boolean).join(" – ") || "Browser";
}

const fromB64url = (text: string) => {
  const b64 = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
};

/** Same as the server's notify.endpoint_id: who we are without sending the endpoint around. */
async function endpointId(endpoint: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return Array.from(new Uint8Array(digest).slice(0, 8), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}

/** This device's subscription id, or null when it has none (or can't have one). */
export async function currentEndpointId(): Promise<string | null> {
  if (pushSupport() !== "ok") return null;
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = await reg?.pushManager.getSubscription();
  return sub ? endpointId(sub.endpoint) : null;
}

export class PushDenied extends Error {}

/** Ask for permission (must run from a tap), subscribe, and return what the server needs. */
export async function subscribe(serverKey: string): Promise<PushSubscriptionJSON> {
  if ((await Notification.requestPermission()) !== "granted") throw new PushDenied();
  const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  await navigator.serviceWorker.ready;
  const key = fromB64url(serverKey);
  let sub = await reg.pushManager.getSubscription();
  const same =
    sub?.options.applicationServerKey &&
    new Uint8Array(sub.options.applicationServerKey).every((b, i) => b === key[i]);
  if (sub && !same) {
    await sub.unsubscribe(); // made with another server key: it can't receive ours
    sub = null;
  }
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  return sub.toJSON();
}

/** A tapped notification asks the open app to show its event (see sw.js). */
export function onOpenFromNotification(open: (path: string) => void): void {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.addEventListener("message", (e: MessageEvent) => {
    const data = e.data as { type?: string; url?: string } | null;
    if (data?.type !== "foxcam:open" || !data.url) return;
    const url = new URL(data.url);
    if (url.origin === location.origin) open(url.pathname + url.search);
  });
}
