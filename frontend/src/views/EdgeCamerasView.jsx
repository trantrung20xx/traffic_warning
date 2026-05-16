import React, { useCallback, useEffect, useMemo, useState } from "react";
import AppIcon from "../components/AppIcon";
import {
	cycleEdgeCameraImageTuning,
	fetchEdgeCamera,
	fetchEdgeCameras,
	restartEdgeCameraStream,
	rescanEdgeCameras,
	startEdgeCameraStream,
	stopEdgeCameraStream,
} from "../api";

const IMAGE_TUNING_SHORT_LABELS = Object.freeze({
	normal: "Thường",
	low_light: "Tối",
	bright_scene: "Sáng",
	sharpness_safe: "Nét",
	disabled: "Tắt",
});

function formatLastSeen(value) {
	if (!value) return "-";
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return String(value);
	return date.toLocaleString("vi-VN");
}

function formatPercent(value) {
	const parsed = Number(value);
	return Number.isFinite(parsed) ? `${parsed.toFixed(1)}%` : "-";
}

function formatTemperature(value) {
	const parsed = Number(value);
	return Number.isFinite(parsed) ? `${parsed.toFixed(1)}C` : "-";
}

function formatFps(value) {
	const parsed = Number(value);
	return Number.isFinite(parsed) ? `${parsed.toFixed(1)} fps` : "-";
}

function formatBoolean(value) {
	if (value === true) return "Có";
	if (value === false) return "Không";
	return "-";
}

function formatUptimeSeconds(value) {
	const total = Number(value);
	if (!Number.isFinite(total) || total < 0) return "-";
	const seconds = Math.floor(total % 60);
	const minutes = Math.floor((total / 60) % 60);
	const hours = Math.floor(total / 3600);
	if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
	if (minutes > 0) return `${minutes}m ${seconds}s`;
	return `${seconds}s`;
}

function getStatusBadgeClass(status) {
	const normalized = String(status || "").toLowerCase();
	if (normalized === "online") return "badge success";
	if (normalized === "offline") return "badge warning";
	return "badge subtle";
}

function normalizeStreamState(row) {
	if (row?.stream_running === true) return "running";
	if (row?.stream_enabled === true) return "starting";
	if (row?.stream_enabled === false) return "stopped";
	return "unknown";
}

function isStreamStarted(row) {
	return row?.stream_running === true;
}

function isEdgeOnline(row) {
	return String(row?.status || "").toLowerCase() === "online";
}

function getImageTuningButtonLabel(profile) {
	const key = String(profile || "")
		.trim()
		.toLowerCase();
	const shortLabel = IMAGE_TUNING_SHORT_LABELS[key] || "Mặc định";
	return `Ảnh: ${shortLabel}`;
}

export default function EdgeCamerasView() {
	const [rows, setRows] = useState([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState("");
	const [busyAction, setBusyAction] = useState("");
	const [hardwarePopupRow, setHardwarePopupRow] = useState(null);
	const [hardwareLoading, setHardwareLoading] = useState(false);

	const loadRows = useCallback(async ({ silent = false } = {}) => {
		if (!silent) {
			setLoading(true);
			setError("");
		}
		try {
			const data = await fetchEdgeCameras();
			setRows(Array.isArray(data) ? data : []);
		} catch (loadError) {
			if (!silent) {
				setError(loadError?.message || "Không thể tải danh sách edge camera.");
				setRows([]);
			}
		} finally {
			if (!silent) {
				setLoading(false);
			}
		}
	}, []);

	useEffect(() => {
		loadRows();
	}, [loadRows]);

	useEffect(() => {
		const timer = window.setInterval(() => {
			// Poll trạng thái ngắn chu kỳ để khi Pi tắt/mất kết nối, UI đổi offline sớm.
			loadRows({ silent: true });
		}, 2500);
		return () => window.clearInterval(timer);
	}, [loadRows]);

	const hasRows = rows.length > 0;
	const sortedRows = useMemo(
		() =>
			[...rows].sort((left, right) =>
				String(left.camera_id || "").localeCompare(
					String(right.camera_id || ""),
					"vi",
				),
			),
		[rows],
	);

	const handleRescan = useCallback(async () => {
		setBusyAction("rescan");
		setError("");
		try {
			await rescanEdgeCameras();
			await loadRows();
		} catch (rescanError) {
			setError(rescanError?.message || "Không thể quét lại edge camera.");
		} finally {
			setBusyAction("");
		}
	}, [loadRows]);

	const handleAction = useCallback(
		async (row, action) => {
			const cameraId = String(row?.camera_id || "");
			if (!cameraId) return;
			const key =
				action === "restart"
					? `${cameraId}:restart`
					: action === "tuning"
						? `${cameraId}:tuning`
						: `${cameraId}:toggle`;
			setBusyAction(key);
			setError("");
			try {
				let response = null;
				if (action === "start") response = await startEdgeCameraStream(cameraId);
				if (action === "stop") response = await stopEdgeCameraStream(cameraId);
				if (action === "restart")
					response = await restartEdgeCameraStream(cameraId);
				if (action === "tuning")
					response = await cycleEdgeCameraImageTuning(cameraId);

				const cameraPayload = response?.camera;
				if (cameraPayload && cameraPayload.camera_id) {
					setRows((prevRows) =>
						prevRows.map((item) =>
							String(item.camera_id || "") === cameraId
								? { ...item, ...cameraPayload }
								: item,
						),
					);
				} else {
					try {
						const latest = await fetchEdgeCamera(cameraId);
						if (latest && latest.camera_id) {
							setRows((prevRows) =>
								prevRows.map((item) =>
									String(item.camera_id || "") === cameraId
										? { ...item, ...latest }
										: item,
								),
							);
						}
					} catch {
						// Nếu sync từng camera lỗi thì vẫn fallback reload toàn bộ danh sách.
					}
				}

				await loadRows({ silent: true });
			} catch (actionError) {
				if (action === "tuning") {
					setError(
						actionError?.message ||
							"Không thể đổi chế độ ảnh của edge camera.",
					);
				} else {
					setError(
						actionError?.message || "Không thể gửi lệnh điều khiển stream.",
					);
				}
			} finally {
				setBusyAction("");
			}
		},
		[loadRows],
	);

	const openHardwarePopup = useCallback(async (row) => {
		const cameraId = String(row?.camera_id || "");
		if (!cameraId) return;
		setHardwarePopupRow({ ...row });
		setHardwareLoading(true);
		try {
			const latest = await fetchEdgeCamera(cameraId);
			if (latest && latest.camera_id) {
				setHardwarePopupRow((current) => {
					if (!current || String(current.camera_id || "") !== cameraId)
						return current;
					return { ...current, ...latest };
				});
			}
		} catch (popupError) {
			setError(
				popupError?.message ||
					"Không thể đồng bộ thông tin phần cứng edge camera.",
			);
		} finally {
			setHardwareLoading(false);
		}
	}, []);

	const closeHardwarePopup = useCallback(() => {
		setHardwarePopupRow(null);
		setHardwareLoading(false);
	}, []);

	useEffect(() => {
		if (!hardwarePopupRow) return undefined;

		const handleKeyDown = (event) => {
			if (event.key === "Escape") {
				closeHardwarePopup();
			}
		};

		const previousOverflow = document.body.style.overflow;
		document.body.style.overflow = "hidden";
		document.addEventListener("keydown", handleKeyDown);

		return () => {
			document.body.style.overflow = previousOverflow;
			document.removeEventListener("keydown", handleKeyDown);
		};
	}, [hardwarePopupRow, closeHardwarePopup]);

	return (
		<div className="edge-cameras-layout">
			<section className="panel edge-cameras-panel">
				<div className="panel-header">
					<div>
						<div className="panel-kicker">Edge Discovery</div>
						<div className="title-with-icon">
							<span className="panel-title-icon">
								<AppIcon name="server" size={20} />
							</span>
							<h2>Edge Cameras</h2>
						</div>
					</div>
					<button
						className="button secondary"
						onClick={handleRescan}
						disabled={loading || busyAction === "rescan"}>
						<AppIcon name="redo" />
						{busyAction === "rescan" ? "Đang quét..." : "Rescan"}
					</button>
				</div>

				{loading && !hasRows ? (
					<div className="empty-state">Đang tải danh sách edge camera...</div>
				) : null}
				{error ? <div className="message-bar warning">{error}</div> : null}

				{!loading && !hasRows ? (
					<div className="empty-state">
						Không tìm thấy edge camera. Hãy kiểm tra Raspberry Pi, avahi/mDNS,
						cùng mạng LAN, firewall, và service traffic_camera_node.
					</div>
				) : null}

				{hasRows ? (
					<div className="edge-camera-table-wrap">
						<table className="edge-camera-table">
							<thead>
								<tr>
									<th>camera_id</th>
									<th>host</th>
									<th>api_port</th>
									<th>rtsp_url</th>
									<th>status</th>
									<th>stream</th>
									<th>last_seen</th>
									<th>Actions</th>
								</tr>
							</thead>
							<tbody>
								{sortedRows.map((row) => (
									<tr
										key={row.camera_id}
										className="edge-camera-row-button"
										role="button"
										tabIndex={0}
										onClick={() => openHardwarePopup(row)}
										onKeyDown={(event) => {
											if (
												event.key === "Enter" ||
												event.key === " "
											) {
												event.preventDefault();
												openHardwarePopup(row);
											}
										}}>
										<td>{row.camera_id || "-"}</td>
										<td>{row.host || "-"}</td>
										<td>{row.api_port ?? "-"}</td>
										<td className="edge-camera-rtsp-cell">
											{row.rtsp_url || "-"}
										</td>
										<td>
											<span
												className={getStatusBadgeClass(
													row.status,
												)}>
												{row.status || "unknown"}
											</span>
											{row.node_status ? (
												<div className="edge-camera-node-status">
													{String(row.node_status)}
												</div>
											) : null}
										</td>
										<td>{normalizeStreamState(row)}</td>
										<td>{formatLastSeen(row.last_seen)}</td>
										<td className="edge-camera-actions">
											<button
												className="button secondary compact-button edge-profile-button"
												onClick={(event) => {
													event.stopPropagation();
													handleAction(row, "tuning");
												}}
												disabled={
													!isEdgeOnline(row) ||
													busyAction.startsWith(
														`${row.camera_id}:`,
													)
												}>
												{busyAction === `${row.camera_id}:tuning`
													? "Đang đổi"
													: getImageTuningButtonLabel(
															row.image_tuning_profile,
														)}
											</button>
											<button
												className={`button compact-button ${isStreamStarted(row) ? "warning-action" : "secondary"}`}
												onClick={(event) => {
													event.stopPropagation();
													handleAction(
														row,
														isStreamStarted(row)
															? "stop"
															: "start",
													);
												}}
												disabled={
													!isEdgeOnline(row) ||
													busyAction.startsWith(
														`${row.camera_id}:`,
													)
												}>
												{busyAction === `${row.camera_id}:toggle`
													? "Đang gửi..."
													: isStreamStarted(row)
														? "Stop Stream"
														: "Start Stream"}
											</button>
											<button
												className="button danger compact-button"
												onClick={(event) => {
													event.stopPropagation();
													handleAction(row, "restart");
												}}
												disabled={
													!isEdgeOnline(row) ||
													busyAction.startsWith(
														`${row.camera_id}:`,
													)
												}>
												Restart Stream
											</button>
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				) : null}
			</section>

			{hardwarePopupRow ? (
				<div className="modal-backdrop" onClick={closeHardwarePopup}>
					<div
						className="confirm-dialog edge-hardware-modal"
						role="dialog"
						aria-modal="true"
						aria-labelledby="edge-hardware-title"
						onClick={(event) => event.stopPropagation()}>
						<div className="edge-hardware-modal-header">
							<div className="confirm-dialog-header">
								<span className="confirm-dialog-icon">
									<AppIcon name="server" size={20} />
								</span>
								<div>
									<div className="panel-kicker">Edge Hardware</div>
									<h3 id="edge-hardware-title">
										{hardwarePopupRow.camera_id || "Edge Camera"}
									</h3>
								</div>
							</div>
							<button
								className="edge-hardware-close-button"
								type="button"
								onClick={closeHardwarePopup}
								aria-label="Đóng cửa sổ thông tin phần cứng">
								<AppIcon name="x" size={16} />
							</button>
						</div>

						<div className="edge-hardware-content">
							<div className="edge-hardware-meta">
								<span
									className={getStatusBadgeClass(
										hardwarePopupRow.status,
									)}>
									{hardwarePopupRow.status || "unknown"}
								</span>
								<span>{`Lần cuối: ${formatLastSeen(hardwarePopupRow.last_seen)}`}</span>
								<span>{`Node: ${hardwarePopupRow.node_status || "-"}`}</span>
							</div>

							{hardwareLoading ? (
								<div className="empty-state slim">
									Đang đồng bộ thông tin phần cứng...
								</div>
							) : null}

							<div className="edge-hardware-grid">
								<div className="edge-hardware-item">
									<div className="edge-hardware-label">
										Nhiệt độ CPU
									</div>
									<div className="edge-hardware-value">
										{formatTemperature(
											hardwarePopupRow.temperature_c,
										)}
									</div>
								</div>
								<div className="edge-hardware-item">
									<div className="edge-hardware-label">CPU</div>
									<div className="edge-hardware-value">
										{formatPercent(hardwarePopupRow.cpu_percent)}
									</div>
								</div>
								<div className="edge-hardware-item">
									<div className="edge-hardware-label">RAM</div>
									<div className="edge-hardware-value">
										{formatPercent(hardwarePopupRow.ram_percent)}
									</div>
								</div>
								<div className="edge-hardware-item">
									<div className="edge-hardware-label">Disk</div>
									<div className="edge-hardware-value">
										{formatPercent(hardwarePopupRow.disk_percent)}
									</div>
								</div>
								<div className="edge-hardware-item">
									<div className="edge-hardware-label">FPS stream</div>
									<div className="edge-hardware-value">
										{formatFps(hardwarePopupRow.edge_fps)}
									</div>
								</div>
								<div className="edge-hardware-item">
									<div className="edge-hardware-label">Uptime</div>
									<div className="edge-hardware-value">
										{formatUptimeSeconds(hardwarePopupRow.uptime_s)}
									</div>
								</div>
								<div className="edge-hardware-item">
									<div className="edge-hardware-label">
										Undervoltage
									</div>
									<div className="edge-hardware-value">
										{formatBoolean(hardwarePopupRow.undervoltage)}
									</div>
								</div>
								<div className="edge-hardware-item">
									<div className="edge-hardware-label">
										Watchdog latched
									</div>
									<div className="edge-hardware-value">
										{formatBoolean(hardwarePopupRow.watchdog_latched)}
									</div>
								</div>
								<div className="edge-hardware-item">
									<div className="edge-hardware-label">
										Restart count
									</div>
									<div className="edge-hardware-value">
										{hardwarePopupRow.restart_count ?? "-"}
									</div>
								</div>
								<div className="edge-hardware-item">
									<div className="edge-hardware-label">Interface</div>
									<div className="edge-hardware-value">
										{hardwarePopupRow.active_interface || "-"}
									</div>
								</div>
								<div className="edge-hardware-item edge-hardware-item-wide">
									<div className="edge-hardware-label">
										Throttled raw
									</div>
									<div className="edge-hardware-value">
										{hardwarePopupRow.throttled_raw || "-"}
									</div>
								</div>
								<div className="edge-hardware-item edge-hardware-item-wide">
									<div className="edge-hardware-label">
										Lỗi gần nhất
									</div>
									<div className="edge-hardware-value">
										{hardwarePopupRow.last_error || "-"}
									</div>
								</div>
							</div>
						</div>
					</div>
				</div>
			) : null}
		</div>
	);
}
