import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { getViolationEvidenceUrl } from "../api";
import { buildViolationSections, getViolationLocationText, hasViolationCoreDetails } from "../violationDetails";
import { getVehicleTypeLabel, getViolationLabel } from "../utils";

function ViolationEvidence({ imageSrc }) {
  const [hasImageError, setHasImageError] = useState(false);

  useEffect(() => {
    setHasImageError(false);
  }, [imageSrc]);

  return (
    <section className="violation-detail-card violation-media-card">
      <div className="panel-kicker">Ảnh phương tiện vi phạm</div>
      <div className="violation-media-frame">
        {imageSrc && !hasImageError ? (
          <img
            className="violation-media-image"
            alt="Ảnh phương tiện vi phạm"
            src={imageSrc}
            onError={() => setHasImageError(true)}
          />
        ) : (
          <div className="violation-media-empty">
            <strong>{hasImageError ? "Không tải được ảnh vi phạm" : "Chưa có ảnh vi phạm"}</strong>
            <span>{hasImageError ? "Ảnh evidence đã lưu nhưng hiện không tải được." : "Bản ghi này chưa có ảnh evidence lưu sẵn."}</span>
          </div>
        )}
      </div>
    </section>
  );
}

function ViolationMetadata({ sections }) {
  return (
    <div className="violation-info-stack">
      {sections.map((section) => (
        <section className="violation-detail-card" key={section.id}>
          <div className="panel-kicker">{section.kicker}</div>
          <div className="violation-meta-grid">
            {section.items
              .filter((item) => !item.hidden)
              .map((item) => (
                <div className="violation-meta-item" key={`${section.id}-${item.label}`}>
                  <span className="violation-meta-label">{item.label}</span>
                  <strong className="violation-meta-value">{item.value}</strong>
                </div>
              ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export default function ViolationDetailModal({ open, violation, imageSrc = null, onClose, loadViolationDetail }) {
  const [detail, setDetail] = useState(violation);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) {
      setDetail(violation || null);
      setLoading(false);
      setError("");
      return undefined;
    }

    const inlineDetail = hasViolationCoreDetails(violation) ? violation : null;
    const violationId = violation?.id ?? null;

    if (inlineDetail || !loadViolationDetail || violationId == null) {
      setDetail(violation || null);
      setLoading(false);
      setError("");
      return undefined;
    }

    let cancelled = false;
    setDetail(null);
    setLoading(true);
    setError("");

    loadViolationDetail(violationId)
      .then((nextDetail) => {
        if (cancelled) return;
        setDetail(nextDetail || null);
      })
      .catch((loadError) => {
        if (cancelled) return;
        setError(loadError?.message || "Không thể tải chi tiết vi phạm.");
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [open, violation, loadViolationDetail]);

  useEffect(() => {
    if (!open) return undefined;

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        onClose?.();
      }
    };

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, onClose]);

  if (!open || typeof document === "undefined") return null;

  const resolvedDetail = detail || violation || null;
  const sections = resolvedDetail ? buildViolationSections(resolvedDetail) : [];
  const locationLabel = resolvedDetail ? getViolationLocationText(resolvedDetail.location) : "-";
  const vehicleLabel = resolvedDetail?.vehicle_type ? getVehicleTypeLabel(resolvedDetail.vehicle_type) : "-";
  const violationLabel = resolvedDetail?.violation ? getViolationLabel(resolvedDetail.violation) : "Chi tiết vi phạm";
  const resolvedImageSrc = getViolationEvidenceUrl(imageSrc || resolvedDetail?.image_url || resolvedDetail?.image_path || resolvedDetail?.imageSrc || null);

  return createPortal(
    <div
      className="modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          onClose?.();
        }
      }}
    >
      <div className="violation-modal" role="dialog" aria-modal="true" aria-labelledby="violation-detail-title">
        <div className="violation-modal-header">
          <div className="violation-modal-title-block">
            <div className="panel-kicker">Chi tiết vi phạm</div>
            <h3 id="violation-detail-title">{violationLabel}</h3>
            <div className="violation-modal-subtitle">
              <span className="badge danger">{vehicleLabel}</span>
              <span className="row-meta">{locationLabel}</span>
            </div>
          </div>
          <button className="button ghost compact-button" type="button" onClick={() => onClose?.()}>
            Đóng
          </button>
        </div>

        {loading ? <div className="empty-state">Đang tải chi tiết vi phạm...</div> : null}
        {!loading && error ? <div className="message-bar warning">Không thể tải chi tiết vi phạm: {error}</div> : null}
        {!loading && !error && !resolvedDetail ? <div className="empty-state">Không có dữ liệu chi tiết để hiển thị.</div> : null}

        {!loading && !error && resolvedDetail ? (
          <div className="violation-modal-grid">
            <ViolationEvidence imageSrc={resolvedImageSrc} />
            <ViolationMetadata sections={sections} />
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
