/**
 * Web Push subscription manager.
 *
 * Registers the service worker, requests notification permission,
 * subscribes to push, and sends the subscription to the backend.
 */

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

function getToken(): string | null {
  try {
    return localStorage.getItem("docsearch-token");
  } catch {
    return null;
  }
}

async function fetchVapidPublicKey(): Promise<string> {
  const res = await fetch("/api/push/key");
  if (!res.ok) throw new Error("VAPID key unavailable");
  const data = await res.json();
  return data.public_key;
}

async function registerServiceWorker(): Promise<ServiceWorkerRegistration> {
  if (!("serviceWorker" in navigator)) throw new Error("Service workers not supported");
  return navigator.serviceWorker.register("/sw.js", { scope: "/" });
}

export async function subscribeToPush(): Promise<boolean> {
  try {
    // Check support
    if (!("PushManager" in window) || !("Notification" in window)) return false;

    // Request permission if needed
    if (Notification.permission === "default") {
      const result = await Notification.requestPermission();
      if (result !== "granted") return false;
    }
    if (Notification.permission !== "granted") return false;

    // Register service worker
    const reg = await registerServiceWorker();

    // Check existing subscription
    let subscription = await reg.pushManager.getSubscription();
    if (subscription) {
      // Already subscribed — just ensure backend knows about it
      await sendSubscriptionToBackend(subscription);
      return true;
    }

    // Get VAPID key
    const vapidKey = await fetchVapidPublicKey();
    const applicationServerKey = urlBase64ToUint8Array(vapidKey);

    // Subscribe
    subscription = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: applicationServerKey.buffer as ArrayBuffer,
    });

    // Send to backend
    await sendSubscriptionToBackend(subscription);
    return true;
  } catch (err) {
    console.warn("Push subscription failed:", err);
    return false;
  }
}

export async function unsubscribeFromPush(): Promise<void> {
  try {
    if (!("serviceWorker" in navigator)) return;
    const reg = await navigator.serviceWorker.ready;
    const subscription = await reg.pushManager.getSubscription();
    if (!subscription) return;

    // Tell backend to remove it
    const endpoint = subscription.endpoint;
    const token = getToken();
    if (token) {
      await fetch("/api/push/unsubscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ endpoint }),
      }).catch(() => {});
    }

    // Unsubscribe locally
    await subscription.unsubscribe();
  } catch {
    /* best-effort */
  }
}

async function sendSubscriptionToBackend(subscription: PushSubscription): Promise<void> {
  const token = getToken();
  if (!token) return;

  const json = subscription.toJSON();
  const keys = json.keys as { p256dh: string; auth: string } | undefined;
  if (!keys) return;

  await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      endpoint: subscription.endpoint,
      p256dh: keys.p256dh,
      auth: keys.auth,
    }),
  });
}

export function isPushSupported(): boolean {
  return "PushManager" in window && "Notification" in window && "serviceWorker" in navigator;
}

export function getNotificationPermission(): NotificationPermission {
  if (!("Notification" in window)) return "denied";
  return Notification.permission;
}
