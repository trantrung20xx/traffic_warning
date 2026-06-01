function parseIceServersFromLinkHeader(linkHeader) {
	if (!linkHeader) return [];
	return linkHeader
		.split(",")
		.map((item) => item.trim())
		.map((item) => {
			const urlMatch = item.match(/^<([^>]+)>/);
			if (!urlMatch?.[1]) return null;
			const server = { urls: [urlMatch[1]] };
			const usernameMatch = item.match(/username="([^"]*)"/i);
			const credentialMatch = item.match(/credential="([^"]*)"/i);
			if (usernameMatch?.[1]) server.username = usernameMatch[1];
			if (credentialMatch?.[1]) server.credential = credentialMatch[1];
			return server;
		})
		.filter(Boolean);
}

function parseOfferMeta(sdp) {
	const result = { iceUfrag: "", icePwd: "", medias: [] };
	(sdp || "").split("\r\n").forEach((line) => {
		if (line.startsWith("m=")) {
			result.medias.push(line.slice(2));
			return;
		}
		if (!result.iceUfrag && line.startsWith("a=ice-ufrag:")) {
			result.iceUfrag = line.slice("a=ice-ufrag:".length);
			return;
		}
		if (!result.icePwd && line.startsWith("a=ice-pwd:")) {
			result.icePwd = line.slice("a=ice-pwd:".length);
		}
	});
	return result;
}

function buildTrickleIceSdpFragment(offerMeta, candidates) {
	const byMid = new Map();
	(candidates || []).forEach((candidate) => {
		const mid = Number(candidate?.sdpMLineIndex);
		if (!Number.isFinite(mid)) return;
		if (!byMid.has(mid)) byMid.set(mid, []);
		byMid.get(mid).push(candidate);
	});

	let fragment = `a=ice-ufrag:${offerMeta.iceUfrag}\r\n`;
	fragment += `a=ice-pwd:${offerMeta.icePwd}\r\n`;
	for (let mid = 0; mid < offerMeta.medias.length; mid += 1) {
		const mediaCandidates = byMid.get(mid);
		if (!mediaCandidates?.length) continue;
		fragment += `m=${offerMeta.medias[mid]}\r\n`;
		fragment += `a=mid:${mid}\r\n`;
		mediaCandidates.forEach((candidate) => {
			fragment += `a=${candidate.candidate}\r\n`;
		});
	}
	return fragment;
}

function createBasicAuthHeader(user, pass) {
	if (!user) return {};
	return { Authorization: `Basic ${window.btoa(`${user}:${pass || ""}`)}` };
}

function createBearerHeader(token) {
	if (!token) return {};
	return { Authorization: `Bearer ${token}` };
}

export class WhepReader {
	constructor({
		url,
		user = "",
		pass = "",
		token = "",
		onTrack,
		onError,
		retryPauseMs = 2000,
	}) {
		this.url = String(url || "");
		this.user = String(user || "");
		this.pass = String(pass || "");
		this.token = String(token || "");
		this.onTrack = typeof onTrack === "function" ? onTrack : null;
		this.onError = typeof onError === "function" ? onError : null;
		this.retryPauseMs = Math.max(Number(retryPauseMs) || 2000, 500);

		this._pc = null;
		this._sessionUrl = null;
		this._offerMeta = null;
		this._queuedCandidates = [];
		this._closed = false;
		this._retryTimer = null;
		this._state = "starting";

		this._start();
	}

	close() {
		this._closed = true;
		if (this._retryTimer) {
			window.clearTimeout(this._retryTimer);
			this._retryTimer = null;
		}
		this._cleanupPeer();
		this._deleteSession();
	}

	_authHeaders() {
		return {
			...createBasicAuthHeader(this.user, this.pass),
			...createBearerHeader(this.token),
		};
	}

	_start() {
		if (this._closed) return;
		this._connect().catch((error) => this._handleError(error));
	}

	async _connect() {
		if (this._closed) return;
		const iceServers = await this._requestIceServers();
		await this._setupPeerConnection(iceServers);
		const answerSdp = await this._sendOffer();
		if (this._closed || !this._pc) return;
		await this._pc.setRemoteDescription(
			new RTCSessionDescription({ type: "answer", sdp: answerSdp }),
		);
		if (this._queuedCandidates.length) {
			const buffered = [...this._queuedCandidates];
			this._queuedCandidates = [];
			await this._sendCandidates(buffered);
		}
		this._state = "running";
	}

	async _requestIceServers() {
		const response = await fetch(this.url, {
			method: "OPTIONS",
			headers: { ...this._authHeaders() },
		});
		if (!response.ok) return [];
		return parseIceServersFromLinkHeader(response.headers.get("Link"));
	}

	async _setupPeerConnection(iceServers) {
		if (this._closed) return;
		this._cleanupPeer();

		const pc = new RTCPeerConnection({
			iceServers,
			sdpSemantics: "unified-plan",
		});
		this._pc = pc;
		pc.addTransceiver("video", { direction: "recvonly" });
		pc.addTransceiver("audio", { direction: "recvonly" });

		pc.ontrack = (event) => {
			if (this._closed) return;
			this.onTrack?.(event);
		};
		pc.onicecandidate = (event) => {
			if (this._closed || !event.candidate) return;
			if (!this._sessionUrl) {
				this._queuedCandidates.push(event.candidate);
				return;
			}
			this._sendCandidates([event.candidate]).catch((error) =>
				this._handleError(error),
			);
		};
		pc.onconnectionstatechange = () => {
			if (this._closed) return;
			if (
				pc.connectionState === "failed" ||
				pc.connectionState === "closed" ||
				pc.connectionState === "disconnected"
			) {
				this._handleError(new Error(`peer connection ${pc.connectionState}`));
			}
		};

		const offer = await pc.createOffer();
		await pc.setLocalDescription(offer);
		this._offerMeta = parseOfferMeta(offer.sdp || "");
	}

	async _sendOffer() {
		if (this._closed || !this._pc?.localDescription?.sdp) {
			throw new Error("local SDP offer is not available");
		}
		const response = await fetch(this.url, {
			method: "POST",
			headers: {
				...this._authHeaders(),
				"Content-Type": "application/sdp",
			},
			body: this._pc.localDescription.sdp,
		});
		if (response.status !== 201) {
			if (response.status === 404) {
				throw new Error("stream not found");
			}
			let detail = `bad status ${response.status}`;
			try {
				const body = await response.json();
				detail = body?.error || body?.message || detail;
			} catch {
				// Keep fallback detail.
			}
			throw new Error(detail);
		}

		const location = response.headers.get("location");
		if (!location) {
			throw new Error("WHEP response missing session location");
		}
		this._sessionUrl = new URL(location, this.url).toString();
		return await response.text();
	}

	async _sendCandidates(candidates) {
		if (
			this._closed ||
			!this._sessionUrl ||
			!this._offerMeta ||
			!Array.isArray(candidates) ||
			candidates.length === 0
		) {
			return;
		}
		const response = await fetch(this._sessionUrl, {
			method: "PATCH",
			headers: {
				"Content-Type": "application/trickle-ice-sdpfrag",
				"If-Match": "*",
			},
			body: buildTrickleIceSdpFragment(this._offerMeta, candidates),
		});
		if (response.status !== 204) {
			throw new Error(`candidate patch failed (${response.status})`);
		}
	}

	_cleanupPeer() {
		if (this._pc) {
			try {
				this._pc.close();
			} catch {
				// Ignore close errors.
			}
			this._pc = null;
		}
		this._offerMeta = null;
		this._queuedCandidates = [];
	}

	_deleteSession() {
		const currentSessionUrl = this._sessionUrl;
		this._sessionUrl = null;
		if (!currentSessionUrl) return;
		fetch(currentSessionUrl, { method: "DELETE" }).catch(() => {
			// Ignore delete failures on shutdown.
		});
	}

	_handleError(error) {
		if (this._closed) return;
		const text =
			error instanceof Error ? error.message : String(error || "unknown error");
		this.onError?.(text);
		this._cleanupPeer();
		this._deleteSession();
		this._state = "restarting";
		if (this._retryTimer) {
			window.clearTimeout(this._retryTimer);
		}
		this._retryTimer = window.setTimeout(() => {
			this._retryTimer = null;
			if (this._closed) return;
			this._state = "starting";
			this._start();
		}, this.retryPauseMs);
	}
}

