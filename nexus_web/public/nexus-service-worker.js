self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = {}; }
  const title = data.title === "Nexilume AI" ? data.title : "Nexilume AI";
  const allowedBodies = new Set([
    "A task needs your attention",
    "A background task failed",
    "Your background task completed",
    "Desktop notifications are ready"
  ]);
  const body = allowedBodies.has(data.body) ? data.body : "Work Inbox has an update";
  const url = typeof data.url === "string" && /^\/inbox(?:\?|$)/.test(data.url) ? data.url : "/inbox";
  event.waitUntil(self.registration.showNotification(title, {
    body, tag: typeof data.tag === "string" ? data.tag : "nexus-inbox",
    data: { url }, renotify: false
  }));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const target = event.notification.data?.url || "/inbox";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(clients => {
    const existing = clients.find(client => new URL(client.url).origin === self.location.origin);
    if (existing) {
      existing.postMessage({ type: "nexus-inbox-open", url: target });
      return existing.focus();
    }
    return self.clients.openWindow(target);
  }));
});

self.addEventListener("message", event => {
  if (event.data?.type === "nexus-inbox-invalidate") {
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(clients =>
      clients.forEach(client => client.postMessage({ type: "nexus-inbox-invalidate" }))
    );
  }
});
