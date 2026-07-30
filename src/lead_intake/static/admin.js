"use strict";

function setVerificationState(channel, status, message, verified) {
  const toggle = channel.querySelector("[data-channel-toggle]");
  const toggleLabel = toggle.closest(".toggle-field");
  const marker = channel.querySelector(".verification");

  toggle.disabled = !verified;
  toggleLabel.classList.toggle("toggle-field-disabled", !verified);
  marker.className = `verification verification-${status}`;
  marker.textContent = message;
}

function markVerificationRequired(element) {
  const channel = element.closest("[data-channel-type]");
  if (channel === null) {
    return;
  }
  channel.querySelector("[data-channel-toggle]").checked = false;
  setVerificationState(
    channel,
    "required",
    "Настройки изменены. Выполните проверочную отправку.",
    false,
  );
}

document.addEventListener("input", (event) => {
  if (event.target.matches("[data-verification-field]")) {
    markVerificationRequired(event.target);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-verification-field]")) {
    markVerificationRequired(event.target);
  }
});

document.body.addEventListener("channelVerificationUpdated", (event) => {
  const channel = document.querySelector(
    `[data-channel-type="${event.detail.channelType}"]`,
  );
  if (channel === null) {
    return;
  }
  setVerificationState(
    channel,
    event.detail.status,
    event.detail.message,
    event.detail.verified,
  );
});

document.body.addEventListener("channelEnabledUpdated", (event) => {
  const channel = document.querySelector(
    `[data-channel-type="${event.detail.channelType}"]`,
  );
  if (channel !== null) {
    channel.querySelector("[data-channel-toggle]").checked = event.detail.enabled;
  }
});
