/* eslint-disable no-restricted-globals */

// Service Worker for Web Push notifications.
// Receives push events from the server and displays native notifications.

self.addEventListener("push", function (event) {
  if (!event.data) return;

  let data;
  try {
    data = event.data.json();
  } catch {
    data = { title: "ADA", body: event.data.text() };
  }

  const title = data.title || "ADA";
  const options = {
    body: data.body || "",
    icon: data.icon || "/favicon.ico",
    badge: data.badge || "/favicon.ico",
    tag: data.tag || "ada-push-" + Date.now(),
    renotify: true,
    requireInteraction: false,
    silent: false,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clientList) {
      // If an ADA tab is already open, focus it.
      for (let i = 0; i < clientList.length; i++) {
        const client = clientList[i];
        if ("focus" in client) {
          return client.focus();
        }
      }
      // Otherwise open a new tab.
      if (clients.openWindow) {
        return clients.openWindow("/");
      }
    })
  );
});
