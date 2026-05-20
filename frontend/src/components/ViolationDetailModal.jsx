import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import AppIcon from "./AppIcon";
import { getViolationEvidenceUrl } from "../api";
import {
  buildViolationSections,
  getViolationLocationText,
  hasViolationCoreDetails,
  sanitizeViolationPlateForDisplay,
} from "../violationDetails";
import { getVehicleTypeLabel, getViolationLabel } from "../utils";

function ViolationEvidence({ imageSrc, licensePlateImageSrc }) {
  const [hasImageError, setHasImageError] = useState(false);
  const [hasPlateImageError, setHasPlateImageError] = useState(false);

  useEffect(() => {
    setHasImageError(false);
    setHasPlateImageError(false);
  }, [imageSrc, licensePlateImageSrc]);

  return (
    <section className="violation-detail-card violation-media-card">
      <div className="panel-kicker icon-label">
        <AppIcon name="image" />
        Ảnh phương tiện vi phạm
      </div>
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
            <AppIcon name="image-off" size={32} />
            <strong>{hasImageError ? "Không tải được ảnh vi phạm" : "Chưa có ảnh vi phạm"}</strong>
            <span>{hasImageError ? "Ảnh evidence đã lưu nhưng hiện không tải được." : "Bản ghi này chưa có ảnh evidence lưu sẵn."}</span>
          </div>
        )}
      </div>
      {licensePlateImageSrc ? (
        <div className="violation-media-frame violation-media-frame-plate">
          {hasPlateImageError ? (
            <div className="violation-media-empty">
              <AppIcon name="image-off" size={28} />
              <strong>Không tải được ảnh biển số</strong>
              <span>Ảnh crop biển số đã lưu nhưng hiện không tải được.</span>
            </div>
          ) : (
            <img
              className="violation-media-image violation-media-image-plate"
              alt="Ảnh crop biển số"
              src={licensePlateImageSrc}
              onError={() => setHasPlateImageError(true)}
            />
          )}
        </div>
      ) : null}
    </section>
  );
}

function ViolationMetadata({ sections }) {
  return (
    <div className="violation-info-stack">
      {sections.map((section) => (
        <section className="violation-detail-card" key={section.id}>
          <div className="panel-kicker icon-label">
            <AppIcon name={section.id === "vehicle" ? "car" : section.id === "location" ? "map-pin" : "info"} />
            {section.kicker}
          </div>
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

    const violationId = violation?.id ?? null;

    if (!loadViolationDetail || violationId == null) {
      setDetail(violation || null);
      setLoading(false);
      setError("");
      return undefined;
    }

    let cancelled = false;
    const hasInlineDetail = hasViolationCoreDetails(violation);
    setDetail(violation || null);
    setLoading(!hasInlineDetail);
    setError("");

    const fetchLatestDetail = async ({ silent }) => {
      try {
        const nextDetail = await loadViolationDetail(violationId);
        if (cancelled) return;
        if (nextDetail) {
          setDetail(nextDetail);
        }
      } catch (loadError) {
        if (cancelled || silent) return;
        setError(loadError?.message || "Không thể tải chi tiết vi phạm.");
      } finally {
        if (!cancelled && !silent) {
          setLoading(false);
        }
      }
    };

    fetchLatestDetail({ silent: false });
    const refreshIntervalMs = 1000;
    const timer = window.setInterval(() => {
      fetchLatestDetail({ silent: true });
    }, refreshIntervalMs);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
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
  const safeDetail = sanitizeViolationPlateForDisplay(resolvedDetail);
  const sections = safeDetail ? buildViolationSections(safeDetail) : [];
  const locationLabel = safeDetail ? getViolationLocationText(safeDetail.location) : "-";
  const vehicleLabel = safeDetail?.vehicle_type ? getVehicleTypeLabel(safeDetail.vehicle_type) : "-";
  const violationLabel = safeDetail?.violation ? getViolationLabel(safeDetail.violation) : "Chi tiết vi phạm";
  const resolvedImageSrc = getViolationEvidenceUrl(
    imageSrc || safeDetail?.image_url || safeDetail?.image_path || safeDetail?.imageSrc || null,
  );
  const resolvedLicensePlateImageSrc = getViolationEvidenceUrl(
    safeDetail?.license_plate_image_url || safeDetail?.license_plate_image_path || null,
  );

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
            <div className="title-with-icon">
              <span className="panel-title-icon danger">
                <AppIcon name="shield-alert" size={18} />
              </span>
              <h3 id="violation-detail-title">{violationLabel}</h3>
            </div>
            <div className="violation-modal-subtitle">
              <span className="badge danger">
                <AppIcon name="car" />
                {vehicleLabel}
              </span>
              <span className="row-meta icon-label">
                <AppIcon name="map-pin" />
                {locationLabel}
              </span>
            </div>
          </div>
          <button className="button ghost compact-button" type="button" onClick={() => onClose?.()}>
            <AppIcon name="x" />
            Đóng
          </button>
        </div>

        {loading ? <div className="empty-state">Đang tải chi tiết vi phạm...</div> : null}
        {!loading && error ? <div className="message-bar warning">Không thể tải chi tiết vi phạm: {error}</div> : null}
        {!loading && !error && !resolvedDetail ? <div className="empty-state">Không có dữ liệu chi tiết để hiển thị.</div> : null}

        {!loading && !error && safeDetail ? (
          <div className="violation-modal-grid">
            <ViolationEvidence imageSrc={resolvedImageSrc} licensePlateImageSrc={resolvedLicensePlateImageSrc} />
            <ViolationMetadata sections={sections} />
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
