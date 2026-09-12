import { api, type ApiContext } from "./api";

const deviceKey = (userId: string) => `nexus.inbox.push-device.${userId}`;

function decodeVapid(value: string) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const raw = atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map(character => character.charCodeAt(0)));
}

export async function ensureInboxServiceWorker() {
  if (!("serviceWorker" in navigator)) throw new Error("Desktop notifications are not supported by this browser.");
  return navigator.serviceWorker.register("/nexus-service-worker.js", { scope: "/" });
}

export async function enableDesktopNotifications(ctx: ApiContext, userId: string, vapidPublicKey: string) {
  if (!("Notification" in window) || !("PushManager" in window)) throw new Error("Desktop notifications are not supported by this browser.");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Browser notification permission was not granted. You can restore it in site settings.");
  const registration = await ensureInboxServiceWorker();
  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: decodeVapid(vapidPublicKey) });
  }
  const json = subscription.toJSON();
  if (!json.endpoint || !json.keys?.p256dh || !json.keys.auth) throw new Error("The browser returned an incomplete push subscription.");
  const device = await api.subscribePush(ctx, {
    endpoint: json.endpoint, keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
    device_name: `${navigator.platform || "Browser"} · ${new Date().toLocaleDateString()}`
  });
  localStorage.setItem(deviceKey(userId), device.id);
  return device;
}

export async function disableCurrentPushDevice(ctx: ApiContext, userId: string) {
  const id = localStorage.getItem(deviceKey(userId));
  if (id) await api.unsubscribePush(ctx, id).catch(() => undefined);
  localStorage.removeItem(deviceKey(userId));
  if ("serviceWorker" in navigator) {
    const registration = await navigator.serviceWorker.getRegistration("/");
    const subscription = await registration?.pushManager.getSubscription();
    await subscription?.unsubscribe().catch(() => false);
  }
}
