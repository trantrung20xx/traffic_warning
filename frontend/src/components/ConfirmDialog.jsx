import React, { useEffect } from "react";
import { createPortal } from "react-dom";
import AppIcon from "./AppIcon";

const ICON_BY_TONE = {
  danger: "trash",
  warning: "alert",
  info: "info",
};

export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Xác nhận",
  cancelLabel = "Hủy",
  tone = "info",
  icon,
  confirmIcon,
  loading = false,
  onCancel,
  onConfirm,
}) {
  useEffect(() => {
    if (!open) return undefined;

    const handleKeyDown = (event) => {
      if (event.key === "Escape" && !loading) {
        onCancel?.();
      }
    };

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [loading, onCancel, open]);

  if (!open || typeof document === "undefined") return null;

  const normalizedTone = ICON_BY_TONE[tone] ? tone : "info";
  const resolvedIcon = icon || ICON_BY_TONE[normalizedTone];
  const resolvedConfirmIcon = confirmIcon || (normalizedTone === "danger" ? "trash" : "check");
  const confirmClassName = normalizedTone === "danger" ? "button danger confirm-danger" : "button primary";

  return createPortal(
    <div
      className="modal-backdrop confirmation-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !loading) {
          onCancel?.();
        }
      }}
    >
      <div className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title">
        <div className="confirm-dialog-header">
          <div className={`confirm-dialog-icon ${normalizedTone}`}>
            <AppIcon name={resolvedIcon} size={22} />
          </div>
          <div>
            <div className="panel-kicker">Xác nhận thao tác</div>
            <h3 id="confirm-dialog-title">{title}</h3>
          </div>
        </div>

        {description ? <p className="confirm-dialog-description">{description}</p> : null}

        <div className="confirm-dialog-actions">
          <button className="button dark" type="button" onClick={() => onCancel?.()} disabled={loading}>
            <AppIcon name="x" />
            {cancelLabel}
          </button>
          <button className={confirmClassName} type="button" onClick={() => onConfirm?.()} disabled={loading}>
            <AppIcon name={resolvedConfirmIcon} />
            {loading ? "Đang xử lý..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
