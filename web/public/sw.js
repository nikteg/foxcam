// Foxcam's service worker: only for web push notifications (server/src/foxcam/webpush.py).
// It has no fetch handler on purpose, so it never caches or touches page loads.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

// Every push must show a notification: iOS stops delivering to apps that don't.
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Foxcam", {
      body: data.body || "",
      tag: data.tag,
      icon: "/icon-192.png",
      data: { url: data.url || "/" },
    }),
  );
});

// A tap opens the event: in the open app when there is one, else in a new window.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const open = windows.find((c) => new URL(c.url).origin === self.location.origin);
      if (open) {
        await open.focus();
        // oxlint-disable-next-line unicorn/require-post-message-target-origin -- a Client, not a window
        open.postMessage({ type: "foxcam:open", url });
        return;
      }
      await self.clients.openWindow(url);
    })(),
  );
});
