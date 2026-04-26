import React, { useEffect } from "react";
import { createPortal } from "react-dom";
import AppIcon from "./AppIcon";

const TOAST_ICON_BY_TONE = {
  success: "check-circle",
  warning: "alert",
  danger: "shield-alert",
  info: "info",
};

export default function Toast({ message, tone = "info", duration = 3000, onClose }) {
  useEffect(() => {
    if (!message) return undefined;

    const timer = window.setTimeout(() => {
      onClose?.();
    }, duration);

    return () => window.clearTimeout(timer);
  }, [duration, message, onClose]);

  if (!message || typeof document === "undefined") return null;

  const normalizedTone = TOAST_ICON_BY_TONE[tone] ? tone : "info";

  return createPortal(
    <div className="toast-viewport" aria-live="polite" aria-atomic="true">
      <div className={`app-toast ${normalizedTone}`} role="status">
        <div className="toast-icon">
          <AppIcon name={TOAST_ICON_BY_TONE[normalizedTone]} size={20} />
        </div>
        <div className="toast-content">{message}</div>
        <button className="toast-close-button" type="button" onClick={() => onClose?.()} aria-label="Tắt thông báo">
          <AppIcon name="x" size={16} />
        </button>
      </div>
    </div>,
    document.body,
  );
}
