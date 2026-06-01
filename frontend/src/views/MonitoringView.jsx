import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Hls from "hls.js";
import AppIcon from "../components/AppIcon";
import CameraCanvas from "../components/CameraCanvas";
import StatPill from "../components/StatPill";
import ViolationDetailModal from "../components/ViolationDetailModal";
import {
	connectTracks,
	connectViolations,
	fetchCameraDetail,
	fetchEdgeCamera,
	fetchCameraStreamEndpoints,
	fetchViolationDetail,
	getCameraPreviewUrl,
} from "../api";
import { WhepReader } from "../lib/whepReader";
import {
	formatLicensePlateValue,
	formatTimestamp,
	getCameraTypeLabel,
	getDirectionStatusLabel,
	getVehicleTypeLabel,
	getViolationLabel,
} from "../utils";
import { sanitizeViolationPlateForDisplay } from "../violationDetails";

const DEFAULT_MONITORING_UI_CONFIG = {
	trajectory: {
		default_limit: 30,
		min_limit: 10,
		max_limit: 80,
		max_points_per_vehicle: 48,
		stale_ms: 1500,
		min_point_distance_px: 1.5,
	},
	violation: {
		list_max_rows: 80,
		highlight_duration_ms: 15000,
	},
	processing_fps: {
		stale_after_ms: 1000,
		poll_interval_ms: 500,
	},
};

const EDGE_PROFILE_LABELS = Object.freeze({
	normal: "Normal",
	low_light: "Low Light",
	bright_scene: "Bright Scene",
	sharpness_safe: "Sharpness Safe",
	disabled: "Disabled",
});

function normalizeEdgeRuntime(raw) {
	if (!raw || typeof raw !== "object") return null;
	const streamState = String(raw.stream_state || "")
		.trim()
		.toLowerCase();
	return {
		...raw,
		stream_state: streamState || "unknown",
		profile_change_pending: raw.profile_change_pending === true,
	};
}

function formatProfileLabel(profile) {
	const key = String(profile || "")
		.trim()
		.toLowerCase();
	return EDGE_PROFILE_LABELS[key] || key || "Unknown";
}

function toFiniteNumber(value, fallback) {
	// Chuẩn hóa input số từ API/UI, fallback khi NaN/Infinity.
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizeMonitoringUiConfig(rawConfig) {
	// Chuẩn hóa toàn bộ tham số UI từ backend để tránh state lỗi do config ngoài biên.
	const fallback = DEFAULT_MONITORING_UI_CONFIG;
	const rawTrajectory = rawConfig?.trajectory || {};
	const rawViolation = rawConfig?.violation || {};
	const rawProcessingFps = rawConfig?.processing_fps || {};

	const trajectoryMinLimit = Math.max(
		1,
		Math.round(
			toFiniteNumber(rawTrajectory.min_limit, fallback.trajectory.min_limit),
		),
	);
	// max_limit luôn >= min_limit để input số lượng quỹ đạo không mâu thuẫn.
	const trajectoryMaxLimit = Math.max(
		trajectoryMinLimit,
		Math.round(
			toFiniteNumber(rawTrajectory.max_limit, fallback.trajectory.max_limit),
		),
	);
	const trajectoryDefaultLimit = Math.min(
		Math.max(
			Math.round(
				toFiniteNumber(
					rawTrajectory.default_limit,
					fallback.trajectory.default_limit,
				),
			),
			trajectoryMinLimit,
		),
		trajectoryMaxLimit,
	);

	return {
		trajectory: {
			default_limit: trajectoryDefaultLimit,
			min_limit: trajectoryMinLimit,
			max_limit: trajectoryMaxLimit,
			max_points_per_vehicle: Math.max(
				2,
				Math.round(
					toFiniteNumber(
						rawTrajectory.max_points_per_vehicle,
						fallback.trajectory.max_points_per_vehicle,
					),
				),
			),
			stale_ms: Math.max(
				0,
				Math.round(
					toFiniteNumber(rawTrajectory.stale_ms, fallback.trajectory.stale_ms),
				),
			),
			min_point_distance_px: Math.max(
				0,
				toFiniteNumber(
					rawTrajectory.min_point_distance_px,
					fallback.trajectory.min_point_distance_px,
				),
			),
		},
		violation: {
			list_max_rows: Math.max(
				1,
				Math.round(
					toFiniteNumber(
						rawViolation.list_max_rows,
						fallback.violation.list_max_rows,
					),
				),
			),
			highlight_duration_ms: Math.max(
				0,
				Math.round(
					toFiniteNumber(
						rawViolation.highlight_duration_ms,
						fallback.violation.highlight_duration_ms,
					),
				),
			),
		},
		processing_fps: {
			stale_after_ms: Math.max(
				0,
				Math.round(
					toFiniteNumber(
						rawProcessingFps.stale_after_ms,
						fallback.processing_fps.stale_after_ms,
					),
				),
			),
			poll_interval_ms: Math.max(
				100,
				Math.round(
					toFiniteNumber(
						rawProcessingFps.poll_interval_ms,
						fallback.processing_fps.poll_interval_ms,
					),
				),
			),
		},
	};
}

function clampTrajectoryLimit(value, trajectoryConfig) {
	const next = Math.round(toFiniteNumber(value, trajectoryConfig.default_limit));
	// Kẹp limit theo biên cấu hình UI để tránh render quá tải.
	return Math.min(
		Math.max(next, trajectoryConfig.min_limit),
		trajectoryConfig.max_limit,
	);
}

function getVehicleTrajectoryPoint(vehicle) {
	// Dùng điểm đáy trung tâm bbox làm đại diện vị trí xe khi vẽ quỹ đạo.
	const bbox = vehicle?.bbox;
	if (!bbox) return null;

	const x1 = Number(bbox.x1);
	const y2 = Number(bbox.y2);
	const x2 = Number(bbox.x2);
	if (!Number.isFinite(x1) || !Number.isFinite(x2) || !Number.isFinite(y2)) {
		return null;
	}
	return [(x1 + x2) / 2, y2];
}

function pointDistance(left, right) {
	return Math.hypot(left[0] - right[0], left[1] - right[1]);
}

function violationRowKey(violation) {
	const id = Number(violation?.id);
	if (Number.isFinite(id) && id > 0) {
		return `id:${id}`;
	}
	return `fallback:${String(violation?.camera_id || "")}:${String(violation?.vehicle_id || "")}:${String(violation?.violation || "")}:${String(violation?.timestamp || "")}`;
}

function upsertViolationRows(prevRows, nextRow, maxRows) {
	const safeMaxRows = Math.max(Number(maxRows) || 1, 1);
	const nextKey = violationRowKey(nextRow);
	const merged = [nextRow, ...prevRows.filter((row) => violationRowKey(row) !== nextKey)];
	return merged.slice(0, safeMaxRows);
}

export default function MonitoringView({
	cameras,
	selectedCameraId,
	onSelectCamera,
	loading,
	configRevision,
}) {
	const [detail, setDetail] = useState(null);
	const [vehicles, setVehicles] = useState([]);
	const [violations, setViolations] = useState([]);
	const [processingFps, setProcessingFps] = useState(null);
	const [streamFps, setStreamFps] = useState(null);
	const [cameraDetailError, setCameraDetailError] = useState("");
	const [previewError, setPreviewError] = useState("");
	const [streamEndpoints, setStreamEndpoints] = useState(null);
	const [streamEndpointsLoaded, setStreamEndpointsLoaded] = useState(false);
	const [edgeRuntime, setEdgeRuntime] = useState(null);
	const [activePreviewTransport, setActivePreviewTransport] = useState("none");
	const [previewReady, setPreviewReady] = useState(false);
	const [realtimeError, setRealtimeError] = useState("");
	const [selectedViolation, setSelectedViolation] = useState(null);
	const [showTrajectoryOverlay, setShowTrajectoryOverlay] = useState(true);
	const [trajectoryLimit, setTrajectoryLimit] = useState(
		DEFAULT_MONITORING_UI_CONFIG.trajectory.default_limit,
	);
	const [trajectoryRows, setTrajectoryRows] = useState([]);
	const vehicleSeenOrderRef = useRef(new Map());
	const nextVehicleOrderRef = useRef(0);
	const violatingVehicleIdsRef = useRef(new Map());
	const lastTrackUpdateRef = useRef(0);
	const liveTrajectoriesRef = useRef(new Map());
	const [previewSessionToken, setPreviewSessionToken] = useState(0);
	const videoRef = useRef(null);
	const whepReaderRef = useRef(null);
	const hlsRef = useRef(null);
	const webrtcFallbackAttemptedRef = useRef(false);
	const showTrajectoryOverlayRef = useRef(true);
	const trajectoryLimitRef = useRef(
		DEFAULT_MONITORING_UI_CONFIG.trajectory.default_limit,
	);
	const previewErrorRef = useRef("");
	const monitoringUiConfig = useMemo(
		() => normalizeMonitoringUiConfig(detail?.ui?.monitoring),
		[detail],
	);

	useEffect(() => {
		previewErrorRef.current = previewError || "";
	}, [previewError]);

	useEffect(() => {
		const normalizedLimit = clampTrajectoryLimit(
			trajectoryLimit,
			monitoringUiConfig.trajectory,
		);
		if (normalizedLimit !== trajectoryLimit) {
			// Đồng bộ lại state khi người dùng nhập ngoài biên.
			setTrajectoryLimit(normalizedLimit);
			return;
		}
		trajectoryLimitRef.current = normalizedLimit;
		setTrajectoryRows((rows) => rows.slice(0, normalizedLimit));
	}, [monitoringUiConfig.trajectory, trajectoryLimit]);

	useEffect(() => {
		showTrajectoryOverlayRef.current = showTrajectoryOverlay;
		if (!showTrajectoryOverlay) {
			liveTrajectoriesRef.current = new Map();
			setTrajectoryRows((rows) => (rows.length ? [] : rows));
		}
	}, [showTrajectoryOverlay]);

	useEffect(() => {
		setPreviewSessionToken((value) => value + 1);
	}, [selectedCameraId]);

	const previewUrl = useMemo(() => {
		if (!selectedCameraId) return "";
		return getCameraPreviewUrl(
			selectedCameraId,
			`${selectedCameraId}-${previewSessionToken}`,
		);
	}, [previewSessionToken, selectedCameraId]);

	const webrtcWhepUrl = useMemo(() => {
		return String(streamEndpoints?.webrtc?.whep_url || "");
	}, [streamEndpoints]);

	const hlsM3u8Url = useMemo(() => {
		return String(streamEndpoints?.hls?.m3u8_url || "");
	}, [streamEndpoints]);

	useEffect(() => {
		if (!selectedCameraId) {
			setDetail(null);
			setCameraDetailError("");
			setPreviewError("");
			setStreamEndpoints(null);
			setStreamEndpointsLoaded(false);
			setEdgeRuntime(null);
			setActivePreviewTransport("none");
			setPreviewReady(false);
			setRealtimeError("");
			setSelectedViolation(null);
			setTrajectoryRows([]);
			setTrajectoryLimit(DEFAULT_MONITORING_UI_CONFIG.trajectory.default_limit);
			liveTrajectoriesRef.current = new Map();
			webrtcFallbackAttemptedRef.current = false;
			return;
		}
		let active = true;
		// Xóa detail cũ ngay khi đổi camera để tránh hiển thị chéo dữ liệu camera trước.
		setDetail(null);
		setCameraDetailError("");
		setPreviewError("");
		setStreamEndpoints(null);
		setStreamEndpointsLoaded(false);
		setEdgeRuntime(null);
		setActivePreviewTransport("none");
		setPreviewReady(false);
		setRealtimeError("");
		Promise.all([
			fetchCameraDetail(selectedCameraId),
			fetchCameraStreamEndpoints(selectedCameraId).catch(() => null),
		])
			.then(([nextDetail, nextStreamEndpoints]) => {
				if (!active) return;
				setDetail(nextDetail);
				setStreamEndpoints(nextStreamEndpoints);
				setStreamEndpointsLoaded(true);
				setEdgeRuntime(
					normalizeEdgeRuntime(
						nextDetail?.edge_runtime || nextStreamEndpoints?.edge_runtime || null,
					),
				);
				const uiConfig = normalizeMonitoringUiConfig(nextDetail?.ui?.monitoring);
				setTrajectoryLimit(uiConfig.trajectory.default_limit);
			})
			.catch((error) => {
				if (!active) return;
				setDetail(null);
				setStreamEndpoints(null);
				setStreamEndpointsLoaded(true);
				setEdgeRuntime(null);
				setCameraDetailError(
					error?.message || "Không thể tải cấu hình camera hoặc backend đang offline.",
				);
				setTrajectoryLimit(DEFAULT_MONITORING_UI_CONFIG.trajectory.default_limit);
			});
		setVehicles([]);
		setViolations([]);
		setProcessingFps(null);
		setStreamFps(null);
		setTrajectoryRows([]);
		setSelectedViolation(null);
		vehicleSeenOrderRef.current = new Map();
		nextVehicleOrderRef.current = 0;
		violatingVehicleIdsRef.current = new Map();
		lastTrackUpdateRef.current = 0;
		liveTrajectoriesRef.current = new Map();
		return () => {
			active = false;
		};
	}, [configRevision, selectedCameraId]);

	useEffect(() => {
		if (!selectedCameraId) return undefined;
		let active = true;

		const syncEdgeRuntime = async () => {
			try {
				const snapshot = await fetchEdgeCamera(selectedCameraId);
				if (!active) return;
				setEdgeRuntime(normalizeEdgeRuntime(snapshot));
			} catch {
				if (!active) return;
				// Camera có thể không phải edge source hoặc edge tạm mất mạng: giữ trạng thái gần nhất.
			}
		};

		syncEdgeRuntime();
		const timer = window.setInterval(syncEdgeRuntime, 1800);
		return () => {
			active = false;
			window.clearInterval(timer);
		};
	}, [selectedCameraId]);

	const updateLiveTrajectories = (nextVehicles) => {
		if (!showTrajectoryOverlayRef.current) {
			if (liveTrajectoriesRef.current.size) {
				liveTrajectoriesRef.current = new Map();
			}
			setTrajectoryRows((rows) => (rows.length ? [] : rows));
			return;
		}

		const now = Date.now();
		const nextMap = new Map(liveTrajectoriesRef.current);
		const trajectoryUi = monitoringUiConfig.trajectory;
		const activeVehicleIds = new Set();

		nextVehicles.forEach((vehicle) => {
			const vehicleId = vehicle.vehicle_id;
			if (vehicleId == null) return;
			activeVehicleIds.add(vehicleId);

			const point = getVehicleTrajectoryPoint(vehicle);
			if (!point) return;

			const current = nextMap.get(vehicleId) || {
				vehicle_id: vehicleId,
				vehicle_type: vehicle.vehicle_type,
				lane_id: vehicle.lane_id,
				points: [],
				lastSeenMs: now,
			};
			const points = [...current.points];
			const lastPoint = points[points.length - 1];
			if (!lastPoint) {
				points.push(point);
			} else if (
				// Bỏ nhiễu dao động nhỏ bằng ngưỡng dịch chuyển tối thiểu.
				pointDistance(lastPoint, point) >= trajectoryUi.min_point_distance_px
			) {
				points.push(point);
			} else {
				// Nếu chưa vượt ngưỡng thì cập nhật điểm cuối để bám theo vị trí mới nhất.
				points[points.length - 1] = point;
			}

			nextMap.set(vehicleId, {
				...current,
				vehicle_type: vehicle.vehicle_type,
				lane_id: vehicle.lane_id,
				// Giới hạn số điểm mỗi xe để giữ hiệu năng render canvas.
				points: points.slice(-trajectoryUi.max_points_per_vehicle),
				lastSeenMs: now,
			});
		});

		nextMap.forEach((row, vehicleId) => {
			// Loại track stale để quỹ đạo phản ánh scene hiện tại.
			if (now - row.lastSeenMs > trajectoryUi.stale_ms) {
				nextMap.delete(vehicleId);
			}
		});

		liveTrajectoriesRef.current = nextMap;
		setTrajectoryRows(
			[...nextMap.values()]
				.filter((row) => activeVehicleIds.has(row.vehicle_id))
				.filter((row) => row.points.length >= 1)
				.sort((left, right) => right.lastSeenMs - left.lastSeenMs)
				.slice(0, trajectoryLimitRef.current)
				// lastSeenMs chỉ dùng cho sắp xếp nội bộ, không cần đẩy xuống canvas.
				.map(({ lastSeenMs, ...row }) => row),
		);
	};

	useEffect(() => {
		if (!selectedCameraId) return undefined;
		const violationUi = monitoringUiConfig.violation;
		let active = true;
		const trackWs = connectTracks(
			selectedCameraId,
			(message) => {
				if (!active || message?.camera_id !== selectedCameraId) return;
				lastTrackUpdateRef.current = Date.now();
				const now = Date.now();
				violatingVehicleIdsRef.current.forEach((expiresAt, vehicleId) => {
					if (expiresAt <= now) {
						violatingVehicleIdsRef.current.delete(vehicleId);
					}
				});
				setVehicles(
					(message.vehicles || []).map((vehicle) => ({
						...vehicle,
						// isViolating chỉ là cờ hiển thị tạm theo highlight window.
						isViolating:
							(violatingVehicleIdsRef.current.get(vehicle.vehicle_id) || 0) >
							now,
					})),
				);
				updateLiveTrajectories(message.vehicles || []);
				setProcessingFps(
					Number.isFinite(message.processing_fps)
						? message.processing_fps
						: null,
				);
				setStreamFps(
					Number.isFinite(message.stream_fps) ? message.stream_fps : null,
				);
				setRealtimeError("");
			},
			{
				onError: () => {
					if (!active) return;
					setRealtimeError(
						"Mất kết nối luồng tracks realtime. Hệ thống sẽ tự thử kết nối lại khi đổi camera hoặc tải lại trang.",
					);
				},
				onInvalidMessage: () => {
					if (!active) return;
					setRealtimeError("Dữ liệu tracks realtime không hợp lệ từ backend.");
				},
			},
		);
		const violationWs = connectViolations(
			selectedCameraId,
			(event) => {
				if (!active || event?.camera_id !== selectedCameraId) return;
				const normalizedEvent = sanitizeViolationPlateForDisplay(event);
				// Đánh dấu "xe đang vi phạm" trong một khoảng thời gian highlight ngắn.
				violatingVehicleIdsRef.current.set(
					normalizedEvent.vehicle_id,
					Date.now() + violationUi.highlight_duration_ms,
				);
				setViolations((prev) => upsertViolationRows(prev, normalizedEvent, violationUi.list_max_rows));
				setSelectedViolation((current) => {
					if (!current) return current;
					return violationRowKey(current) === violationRowKey(normalizedEvent)
						? normalizedEvent
						: current;
				});
			},
			{
				onError: () => {
					if (!active) return;
					setRealtimeError(
						"Mất kết nối luồng violations realtime. Dữ liệu vi phạm mới có thể bị chậm.",
					);
				},
				onInvalidMessage: () => {
					if (!active) return;
					setRealtimeError("Dữ liệu violations realtime không hợp lệ từ backend.");
				},
			},
		);
		return () => {
			active = false;
			trackWs.close();
			violationWs.close();
		};
	}, [
		monitoringUiConfig.violation.highlight_duration_ms,
		monitoringUiConfig.violation.list_max_rows,
		selectedCameraId,
	]);

	useEffect(() => {
		if (!selectedCameraId) return undefined;
		const processingFpsUi = monitoringUiConfig.processing_fps;
		const timer = window.setInterval(() => {
			const lastUpdate = lastTrackUpdateRef.current;
			if (lastUpdate && Date.now() - lastUpdate > processingFpsUi.stale_after_ms) {
				// Nếu không còn track mới thì xem FPS là stale.
				setProcessingFps(null);
				setStreamFps(null);
			}
		}, processingFpsUi.poll_interval_ms);
		return () => window.clearInterval(timer);
	}, [monitoringUiConfig.processing_fps, selectedCameraId]);

	const stopActivePreviewStream = useCallback(() => {
		const reader = whepReaderRef.current;
		if (reader) {
			try {
				reader.close();
			} catch {
				// Ignore close errors during transport switch.
			}
			whepReaderRef.current = null;
		}

		const hls = hlsRef.current;
		if (hls) {
			try {
				hls.destroy();
			} catch {
				// Ignore destroy errors.
			}
			hlsRef.current = null;
		}

		const video = videoRef.current;
		if (video) {
			try {
				video.pause();
			} catch {
				// Ignore pause errors.
			}
			video.removeAttribute("src");
			video.srcObject = null;
			video.load();
		}
	}, []);

	useEffect(() => {
		if (!selectedCameraId) {
			stopActivePreviewStream();
			setActivePreviewTransport("none");
			setPreviewReady(false);
			setPreviewError("");
			return;
		}

		stopActivePreviewStream();
		webrtcFallbackAttemptedRef.current = false;
		setActivePreviewTransport("none");
		setPreviewReady(false);
		setPreviewError("");
		const video = videoRef.current;
		if (!video) return;
		video.muted = true;
		video.autoplay = true;
		video.playsInline = true;

		const startMjpegFallback = () => {
			stopActivePreviewStream();
			setActivePreviewTransport("mjpeg");
			setPreviewReady(false);
		};

		const startHlsPlayback = () => {
			if (!hlsM3u8Url) return false;
			stopActivePreviewStream();
			setActivePreviewTransport("hls");
			setPreviewReady(false);
			if (Hls.isSupported()) {
				const hls = new Hls({
					lowLatencyMode: true,
					liveSyncDurationCount: 2,
					liveMaxLatencyDurationCount: 4,
					maxLiveSyncPlaybackRate: 1.2,
				});
				hlsRef.current = hls;
				hls.on(Hls.Events.MEDIA_ATTACHED, () => {
					hls.loadSource(hlsM3u8Url);
				});
				hls.on(Hls.Events.MANIFEST_PARSED, () => {
					video.play().catch(() => {
						// Autoplay may be blocked in some browsers despite mute.
					});
				});
				hls.on(Hls.Events.ERROR, (_, data) => {
					if (!data?.fatal) return;
					startMjpegFallback();
					setPreviewError(
						"Không phát được HLS realtime. Đang fallback về MJPEG preview.",
					);
				});
				hls.attachMedia(video);
				return true;
			}
			if (video.canPlayType("application/vnd.apple.mpegurl")) {
				video.src = hlsM3u8Url;
				video.play().catch(() => {
					// Autoplay may be blocked in some browsers despite mute.
				});
				return true;
			}
			return false;
		};

		const startWebRtcPlayback = () => {
			if (!webrtcWhepUrl || typeof RTCPeerConnection === "undefined") {
				return false;
			}
			stopActivePreviewStream();
			setActivePreviewTransport("webrtc");
			setPreviewReady(false);
			whepReaderRef.current = new WhepReader({
				url: webrtcWhepUrl,
				onTrack: (event) => {
					const stream = event?.streams?.[0];
					if (!stream) return;
					if (video.srcObject !== stream) {
						video.srcObject = stream;
					}
					video.play().catch(() => {
						// Autoplay may be blocked in some browsers despite mute.
					});
					setPreviewReady(true);
					if (previewErrorRef.current) {
						setPreviewError("");
					}
				},
				onError: (reason) => {
					if (!webrtcFallbackAttemptedRef.current) {
						webrtcFallbackAttemptedRef.current = true;
						if (startHlsPlayback()) {
							setPreviewError(
								`WebRTC lỗi (${reason}). Đang chuyển sang HLS realtime.`,
							);
							return;
						}
					}
					startMjpegFallback();
					setPreviewError(
						`WebRTC lỗi (${reason}). Đang fallback về MJPEG preview.`,
					);
				},
			});
			return true;
		};

		if (startWebRtcPlayback()) {
			return () => {
				stopActivePreviewStream();
			};
		}
		if (startHlsPlayback()) {
			return () => {
				stopActivePreviewStream();
			};
		}

		startMjpegFallback();
		if (streamEndpointsLoaded) {
			setPreviewError(
				"Camera chưa có endpoint WebRTC/HLS khả dụng. Đang dùng MJPEG preview.",
			);
		}
		return () => {
			stopActivePreviewStream();
		};
	}, [
		hlsM3u8Url,
		previewUrl,
		selectedCameraId,
		stopActivePreviewStream,
		streamEndpointsLoaded,
		webrtcWhepUrl,
	]);

	useEffect(() => {
		return () => {
			stopActivePreviewStream();
		};
	}, [stopActivePreviewStream]);

	const handlePreviewReady = useCallback(() => {
		setPreviewReady(true);
		if (previewErrorRef.current) {
			setPreviewError("");
		}
	}, []);

	const detailCameraId = detail?.camera?.camera_id || null;
	const isDetailMatchedSelectedCamera =
		Boolean(selectedCameraId) && detailCameraId === selectedCameraId;
	const camera = isDetailMatchedSelectedCamera ? detail?.camera || null : null;
	const laneConfig = isDetailMatchedSelectedCamera
		? detail?.lane_config || null
		: null;
	const cameraLocationRoadName = camera?.location?.road_name || "-";
	const cameraLocationIntersection = camera?.location?.intersection_name || "";
	const edgeRuntimeSnapshot = normalizeEdgeRuntime(
		edgeRuntime || detail?.edge_runtime || streamEndpoints?.edge_runtime || null,
	);
	const previewTransportBaseLabel =
		activePreviewTransport === "webrtc"
			? "Video realtime · WebRTC"
			: activePreviewTransport === "hls"
				? "Video realtime · HLS"
				: activePreviewTransport === "mjpeg"
					? "Preview fallback · MJPEG"
					: "Đang chuẩn bị luồng video";
	const profileSwitchPending = edgeRuntimeSnapshot?.profile_change_pending === true;
	const previewTransportLabel = profileSwitchPending
		? `Đang áp dụng profile ${formatProfileLabel(edgeRuntimeSnapshot?.profile_change_target_profile)} · ${previewTransportBaseLabel}`
		: previewTransportBaseLabel;
	const previewTransportBadgeClass = profileSwitchPending
		? "badge warning monitor-trajectory-status"
		: activePreviewTransport === "webrtc"
			? "badge success monitor-trajectory-status"
			: activePreviewTransport === "hls"
				? "badge warning monitor-trajectory-status"
				: "badge subtle monitor-trajectory-status";
	const edgeRuntimeStatusMessage = profileSwitchPending
		? `Pi 5 đang đổi profile (${formatProfileLabel(edgeRuntimeSnapshot?.profile_change_previous_profile)} -> ${formatProfileLabel(edgeRuntimeSnapshot?.profile_change_target_profile)}), stream sẽ tự đồng bộ lại.`
		: edgeRuntimeSnapshot?.stream_state
			? `Trạng thái stream edge: ${String(edgeRuntimeSnapshot.stream_state).toUpperCase()}`
			: "";
	const orderedVehicles = [...vehicles]
		.map((vehicle) => {
			if (!vehicleSeenOrderRef.current.has(vehicle.vehicle_id)) {
				vehicleSeenOrderRef.current.set(
					vehicle.vehicle_id,
					nextVehicleOrderRef.current,
				);
				nextVehicleOrderRef.current += 1;
			}
			return {
				...vehicle,
				// seenOrder giúp giữ thứ tự ổn định giữa các lần render.
				seenOrder: vehicleSeenOrderRef.current.get(vehicle.vehicle_id) ?? 0,
			};
		})
		.sort((left, right) => right.seenOrder - left.seenOrder);

	const activeVehicleIds = new Set(vehicles.map((vehicle) => vehicle.vehicle_id));
	vehicleSeenOrderRef.current.forEach((_, vehicleId) => {
		if (!activeVehicleIds.has(vehicleId)) {
			vehicleSeenOrderRef.current.delete(vehicleId);
		}
	});

	const handleViolationKeyDown = (event, violation) => {
		if (event.key === "Enter" || event.key === " ") {
			event.preventDefault();
			setSelectedViolation(violation);
		}
	};

	const loadViolationDetail = useCallback(async (violationId) => {
		return await fetchViolationDetail(violationId);
	}, []);

	return (
		<>
			<div className="monitor-layout">
				<section className="panel hero-panel">
					<div className="panel-header">
						<div>
							<div className="panel-kicker">
								Luồng hình và vi phạm thời gian thực
							</div>
							<div className="title-with-icon">
								<span className="panel-title-icon">
									<AppIcon name="video" size={20} />
								</span>
								<h2>Màn hình giám sát camera</h2>
							</div>
						</div>
						<label className="field field-inline monitor-camera-picker">
							<span className="field-label-with-icon">
								<AppIcon name="camera" />
								Camera
							</span>
							<select
								className="monitor-camera-select"
								value={selectedCameraId || ""}
								onChange={(event) =>
									onSelectCamera(event.target.value || null)
								}>
								{cameras.map((cameraRow) => (
									<option
										key={cameraRow.camera_id}
										value={cameraRow.camera_id}>
										{cameraRow.camera_id} -{" "}
										{cameraRow.location?.road_name || "Chưa có tên đường"}
									</option>
								))}
							</select>
						</label>
					</div>

					{loading && cameras.length === 0 ? (
						<div className="empty-state">Đang tải danh sách camera...</div>
					) : null}
					{selectedCameraId && !cameraDetailError && laneConfig && !previewReady ? (
						<div className="empty-state slim">
							Đang kết nối luồng video realtime...
						</div>
					) : null}
					{!selectedCameraId ? (
						<div className="empty-state">Chưa có camera được cấu hình.</div>
					) : null}
					{selectedCameraId && cameraDetailError ? (
						<div className="message-bar warning">{cameraDetailError}</div>
					) : null}
					{selectedCameraId && realtimeError ? (
						<div className="message-bar warning">{realtimeError}</div>
					) : null}
					{selectedCameraId && !cameraDetailError && !laneConfig ? (
						<div className="empty-state">
							Đang chờ cấu hình camera từ backend...
						</div>
					) : null}

					{selectedCameraId && laneConfig ? (
						<>
							<div className="camera-meta-grid">
								<StatPill
									label="Camera ID"
									value={camera?.camera_id || selectedCameraId}
									icon="camera"
								/>
								<StatPill
									label="Loại camera"
									value={getCameraTypeLabel(camera?.camera_type)}
									icon="video"
								/>
								<StatPill
									label="Hướng quan sát"
									value={camera?.view_direction || "-"}
									icon="navigation"
								/>
								<StatPill
									label="Vị trí"
									value={`${cameraLocationRoadName}${cameraLocationIntersection ? ` · ${cameraLocationIntersection}` : ""}`}
									icon="map-pin"
								/>
							</div>
							<div className="monitor-overlay-toolbar">
								<div className="monitor-trajectory-controls">
									<label className="field monitor-trajectory-limit">
										<span className="field-label-with-icon">
											<AppIcon name="route" />
											Số quỹ đạo hiển thị
										</span>
										<input
											type="number"
											disabled={!showTrajectoryOverlay}
											min={monitoringUiConfig.trajectory.min_limit}
											max={monitoringUiConfig.trajectory.max_limit}
											value={trajectoryLimit}
											onChange={(event) =>
												setTrajectoryLimit(
													clampTrajectoryLimit(
														event.target.value,
														monitoringUiConfig.trajectory,
													),
												)
											}
										/>
									</label>
									<button
										type="button"
										className={`button monitor-trajectory-toggle ${showTrajectoryOverlay ? "secondary" : "ghost"}`}
										aria-pressed={showTrajectoryOverlay}
										onClick={() =>
											setShowTrajectoryOverlay((value) => !value)
										}>
										<AppIcon name={showTrajectoryOverlay ? "eye-off" : "eye"} />
										{showTrajectoryOverlay
											? "Ẩn quỹ đạo"
											: "Hiện quỹ đạo"}
									</button>
								</div>
								<div
									className={
										showTrajectoryOverlay
											? "badge success monitor-trajectory-status"
											: "badge subtle monitor-trajectory-status"
									}>
									<AppIcon name={showTrajectoryOverlay ? "route" : "eye-off"} />
									{showTrajectoryOverlay
										? `Quỹ đạo đang theo dõi · ${trajectoryRows.length}`
										: "Theo dõi quỹ đạo đang tắt"}
								</div>
								<div
									className={previewTransportBadgeClass}>
									<AppIcon name="video" />
									{previewTransportLabel}
								</div>
							</div>
							<div className="video-stage">
								<video
									ref={videoRef}
									className="video-preview"
									style={{
										display:
											activePreviewTransport === "mjpeg" ? "none" : "block",
									}}
									autoPlay
									muted
									playsInline
									onLoadedData={handlePreviewReady}
									onCanPlay={handlePreviewReady}
								/>
								{activePreviewTransport === "mjpeg" ? (
									<img
										className="video-preview"
										key={previewUrl || selectedCameraId}
										alt="Xem trước camera"
										src={previewUrl}
										onLoad={handlePreviewReady}
										onError={() =>
											setPreviewError(
												"Không thể tải preview MJPEG. Hãy kiểm tra backend hoặc camera stream.",
											)
										}
									/>
								) : null}
								{activePreviewTransport !== "mjpeg" && !previewReady ? (
									<div className="video-preview-placeholder">
										Đang khởi tạo video realtime...
									</div>
								) : null}
								<CameraCanvas
									overlay
									frameWidth={laneConfig.frame_width}
									frameHeight={laneConfig.frame_height}
									lanes={laneConfig.lanes}
									vehicles={vehicles}
									trajectoryOverlays={
										showTrajectoryOverlay ? trajectoryRows : []
									}
									processingFps={processingFps}
									streamFps={streamFps}
								/>
							</div>
							{previewError ? (
								<div className="message-bar warning">{previewError}</div>
							) : null}
							{!previewError && edgeRuntimeStatusMessage ? (
								<div className={profileSwitchPending ? "message-bar warning" : "message-bar"}>
									{edgeRuntimeStatusMessage}
								</div>
							) : null}
						</>
					) : null}
				</section>

				<aside className="stack-column monitor-sidebar">
					<section className="panel monitor-realtime-panel">
						<div className="panel-header compact">
							<div>
								<div className="panel-kicker">Thời gian thực</div>
								<div className="title-with-icon">
									<span className="panel-title-icon">
										<AppIcon name="car" size={18} />
									</span>
									<h3>Xe đang được theo dõi</h3>
								</div>
							</div>
							<div className="badge">
								<AppIcon name="car" />
								{vehicles.length} xe
							</div>
						</div>
						<div className="entity-list tracked-vehicle-list">
							{orderedVehicles.length === 0 ? (
								<div className="empty-state slim">
									Chưa có phương tiện đang hoạt động.
								</div>
							) : null}
							{orderedVehicles.map((vehicle) => (
								<article
									className="list-row tracked-vehicle-row"
									key={vehicle.vehicle_id}>
									<div className="tracked-vehicle-main">
										<div className="row-title icon-label tracked-vehicle-title">
											<AppIcon name="car" />
											{`${getVehicleTypeLabel(vehicle.vehicle_type)} · ID xe: #${vehicle.vehicle_id}`}
										</div>
										<div className="row-sub tracked-vehicle-meta">
											<div className="tracked-vehicle-meta-line">
												{`Làn ổn định: ${vehicle.lane_id != null ? vehicle.lane_id : "Chưa ổn định"}`}
											</div>
											<div className="tracked-vehicle-meta-line">
												{`Biển số: ${formatLicensePlateValue(
													vehicle.license_plate,
													vehicle.license_plate_status,
												)}`}
											</div>
											{vehicle.direction_status === "wrong_direction" ? (
												<div className="tracked-vehicle-meta-line">
													{`Hướng: ${getDirectionStatusLabel(vehicle.direction_status)}`}
												</div>
											) : null}
										</div>
									</div>
									<div
										className={
											vehicle.direction_status === "wrong_direction"
												? "badge danger"
												: vehicle.bbox
													? "badge success"
													: "badge subtle"
										}>
										<AppIcon
											name={
												vehicle.direction_status === "wrong_direction"
													? "shield-alert"
													: vehicle.bbox
														? "check-circle"
														: "clock"
											}
										/>
										{vehicle.direction_status === "wrong_direction"
											? "Ngược chiều"
											: vehicle.bbox
												? "Đang theo dõi"
												: "Chờ xử lý"}
									</div>
								</article>
							))}
						</div>
					</section>
				</aside>

				<section className="panel monitor-full-width">
					<div className="panel-header compact">
						<div>
							<div className="panel-kicker">Luồng vi phạm</div>
							<div className="title-with-icon">
								<span className="panel-title-icon danger">
									<AppIcon name="shield-alert" size={18} />
								</span>
								<h3>Danh sách vi phạm của camera đang xem</h3>
							</div>
						</div>
						<div className="badge danger">
							<AppIcon name="shield-alert" />
							{violations.length}
						</div>
					</div>
					<div className="entity-list violation-list">
						{violations.length === 0 ? (
							<div className="empty-state slim">
								Chưa có vi phạm thời gian thực.
							</div>
						) : null}
						{violations.map((event) => (
							<article
								className="list-row violation-row violation-trigger"
								key={violationRowKey(event)}
								onClick={() => setSelectedViolation(event)}
								onKeyDown={(keyEvent) =>
									handleViolationKeyDown(keyEvent, event)
								}
								role="button"
								tabIndex={0}>
								<div>
									<div className="row-title icon-label">
										<AppIcon name="alert" />
										{getViolationLabel(event.violation)} · làn{" "}
										{event.lane_id}
									</div>
									<div className="row-sub">
										{getVehicleTypeLabel(event.vehicle_type)} · ID xe: #
										{event.vehicle_id}
										{` · Biển số: ${formatLicensePlateValue(
											event.license_plate,
											event.license_plate_status,
										)}`}
									</div>
								</div>
								<div className="row-meta icon-label">
									<AppIcon name="clock" />
									{formatTimestamp(event.timestamp)}
								</div>
							</article>
						))}
					</div>
				</section>
			</div>

			<ViolationDetailModal
				open={Boolean(selectedViolation)}
				violation={selectedViolation}
				onClose={() => setSelectedViolation(null)}
				loadViolationDetail={loadViolationDetail}
			/>
		</>
	);
}
