export type CameraLocation = {
  road_name: string;
  intersection?: string | null;
  gps_lat?: number | null;
  gps_lng?: number | null;
};

export type CameraInfo = {
  camera_id: string;
  camera_type: "roadside" | "overhead" | "intersection";
  location: CameraLocation;
  monitored_lanes: number[];
};

export type LanePolygon = {
  lane_id: number;
  polygon: Array<[number, number]>;
};

export type LanesResponse = {
  camera_id: string;
  frame_width?: number | null;
  frame_height?: number | null;
  lanes: LanePolygon[];
};

export type TrackVehicle = {
  vehicle_id: number;
  vehicle_type: string;
  lane_id?: number | null;
  bbox: { x1: number; y1: number; x2: number; y2: number };
};

export type TrackMessage = {
  type: "track";
  camera_id: string;
  timestamp: string;
  vehicles: TrackVehicle[];
};

export type ViolationEvent = {
  camera_id: string;
  location: CameraLocation;
  vehicle_id: number;
  vehicle_type: string;
  lane_id: number;
  violation: string;
  timestamp: string;
};

export type ViolationMessage = {
  type: "violation";
  event: ViolationEvent;
};

export type StatsRow = {
  camera_id?: string | null;
  road_name?: string | null;
  intersection?: string | null;
  vehicle_type: string;
  violation: string;
  count: number;
};

