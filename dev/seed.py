#!/usr/bin/env python3
"""Seed ReportPortal with realistic test data for rp-fetch CLI evaluation.

Creates a project, two launches (one passed, one failed), with suites,
test cases, log entries at various levels, and binary attachments
(fake screenshots and PCAP files).

Usage:
    cd dev/
    uv run python seed.py                         # defaults
    uv run python seed.py --base-url http://localhost:8080   # custom URL

After seeding:
    uv run rp-fetch config init   # use http://localhost:8080, key from output
    uv run rp-fetch launch list
    uv run rp-fetch download <uuid>
"""

from __future__ import annotations

import argparse
import io
import json
import struct
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

BASE_URL = "http://localhost:8080"
ADMIN_USER = "superadmin"
ADMIN_PASS = "erebus"
PROJECT_NAME = "tessa_vonr"

# ── Fake binary content generators ──────────────────────────────

def make_fake_png(width: int = 100, height: int = 50, label: str = "test") -> bytes:
    """Generate a minimal valid PNG (1x1 red pixel repeated, with label in metadata)."""
    # Minimal valid PNG: 8-byte signature + IHDR + IDAT + IEND
    # We'll create a small but valid PNG
    import zlib

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        raw = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(raw) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + raw + crc

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    ihdr = _chunk(b"IHDR", ihdr_data)

    # Simple image data: red pixels
    raw_rows = b""
    for _ in range(height):
        raw_rows += b"\x00" + (b"\xff\x00\x00") * width  # filter=none, RGB red

    compressed = zlib.compress(raw_rows)
    idat = _chunk(b"IDAT", compressed)

    # Add a tEXt chunk with the label
    text_data = b"Comment\x00" + label.encode("utf-8")
    text = _chunk(b"tEXt", text_data)

    iend = _chunk(b"IEND", b"")
    return sig + ihdr + text + idat + iend


def make_fake_pcap(packet_count: int = 5) -> bytes:
    """Generate a minimal valid PCAP file with fake Ethernet/IP/UDP packets."""
    buf = io.BytesIO()
    # Global header
    buf.write(struct.pack("<IHHiIII",
        0xA1B2C3D4,  # magic
        2, 4,         # version
        0,            # timezone
        0,            # sigfigs
        65535,        # snaplen
        1,            # link type: Ethernet
    ))
    base_ts = int(time.time())
    for i in range(packet_count):
        # Minimal Ethernet + IP + UDP packet (42 bytes)
        eth = b"\xff" * 6 + b"\x00" * 6 + b"\x08\x00"  # dst, src, type=IPv4
        ip = (
            b"\x45\x00\x00\x1c"  # ver/ihl, tos, total_len=28
            b"\x00\x00\x00\x00"  # id, flags/frag
            b"\x40\x11\x00\x00"  # ttl=64, proto=UDP, checksum=0
            b"\xc0\xa8\x01\x01"  # src 192.168.1.1
            b"\xc0\xa8\x01\x02"  # dst 192.168.1.2
        )
        udp = struct.pack(">HHHH", 5060, 5060, 8, 0)  # SIP ports, len=8, checksum=0
        packet = eth + ip + udp
        ts = base_ts + i
        buf.write(struct.pack("<IIII", ts, 0, len(packet), len(packet)))
        buf.write(packet)
    return buf.getvalue()


def make_fake_appium_log() -> bytes:
    """Generate a fake Appium session log."""
    lines = [
        "[2026-03-18 14:22:01] [INFO] Starting Appium session abc-123",
        "[2026-03-18 14:22:02] [INFO] Device: Pixel 7, Android 15",
        "[2026-03-18 14:22:03] [INFO] Launching com.example.vonr.app",
        "[2026-03-18 14:22:05] [DEBUG] Touch event at (540, 1200)",
        "[2026-03-18 14:22:06] [DEBUG] Element found: registration_button",
        "[2026-03-18 14:22:07] [INFO] Navigating to registration screen",
        "[2026-03-18 14:22:10] [WARN] Slow network response: 3200ms",
        "[2026-03-18 14:22:12] [INFO] Registration form submitted",
        "[2026-03-18 14:22:15] [ERROR] Unexpected dialog: 'Network unavailable'",
        "[2026-03-18 14:22:16] [INFO] Session ended",
    ]
    return "\n".join(lines).encode("utf-8")


# ── API helpers ──────────────────────────────────────────────────

class RPSeeder:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token: str = ""
        self.client = httpx.Client(timeout=30.0)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def authenticate(self) -> str:
        """Get an OAuth token for superadmin."""
        print("  Authenticating as superadmin...")
        resp = self.client.post(
            f"{self.base_url}/uat/sso/oauth/token",
            data={
                "grant_type": "password",
                "username": ADMIN_USER,
                "password": ADMIN_PASS,
            },
            headers={"Authorization": "Basic dWk6dWltYW4="},  # base64(ui:uiman)
        )
        resp.raise_for_status()
        self.token = resp.json()["access_token"]
        print(f"  Token acquired: {self.token[:12]}...")
        return self.token

    def create_project(self) -> None:
        """Create the test project (ignore if exists)."""
        print(f"  Creating project '{PROJECT_NAME}'...")
        resp = self.client.post(
            f"{self.base_url}/api/v1/project",
            json={"projectName": PROJECT_NAME, "entryType": "INTERNAL"},
            headers=self._headers(),
        )
        if resp.status_code == 409:
            print(f"  Project '{PROJECT_NAME}' already exists — skipping")
        else:
            resp.raise_for_status()
            print(f"  Project created (id={resp.json().get('id', '?')})")

    def assign_admin_to_project(self) -> None:
        """Assign superadmin to the project so we can push data."""
        print(f"  Assigning superadmin to project...")
        resp = self.client.put(
            f"{self.base_url}/api/v1/project/{PROJECT_NAME}/assign",
            json={"userNames": {"superadmin": "PROJECT_MANAGER"}},
            headers=self._headers(),
        )
        if resp.status_code in (200, 409):
            print(f"  Assignment OK")
        else:
            # Some versions return 400 if already assigned — that's fine
            print(f"  Assignment response: {resp.status_code} (may already be assigned)")

    def generate_api_key(self) -> str:
        """Generate an API key for superadmin and return it."""
        print("  Generating API key...")
        # RP 5.15+: POST /api/users/{numericUserId}/api-keys
        # First, look up numeric user ID
        user_resp = self.client.get(
            f"{self.base_url}/api/v1/user",
            headers=self._headers(),
        )
        user_id = None
        if user_resp.is_success:
            user_id = user_resp.json().get("id") or user_resp.json().get("userId")

        if user_id:
            resp = self.client.post(
                f"{self.base_url}/api/users/{user_id}/api-keys",
                json={"name": f"rp-fetch-dev-{int(time.time())}"},
                headers=self._headers(),
            )
        else:
            resp = None

        if resp is None or not resp.is_success:
            if resp is not None:
                print(f"  API key response: {resp.status_code} — {resp.text[:200]}")
            # Fallback: use the bearer token directly
            print("  Using bearer token as API key fallback")
            return self.token
        data = resp.json()
        key = data.get("api_key") or data.get("apiKey") or data.get("key", "")
        if not key:
            # Fallback: use the bearer token directly
            print("  Could not generate API key — using bearer token as fallback")
            return self.token
        print(f"  API key: {key[:12]}...")
        return key

    def _ts(self, dt: datetime) -> str:
        """Format datetime as epoch millis string (RP v5 format)."""
        return str(int(dt.timestamp() * 1000))

    def start_launch(self, name: str, start: datetime, **extra: str) -> str:
        """Start a launch, return its UUID."""
        body: dict = {
            "name": name,
            "startTime": self._ts(start),
            "mode": "DEFAULT",
            "attributes": [
                {"key": "campaign", "value": "VoNR March 2026"},
                {"key": "env", "value": "staging"},
            ],
            **extra,
        }
        resp = self.client.post(
            f"{self.base_url}/api/v1/{PROJECT_NAME}/launch",
            json=body,
            headers=self._headers(),
        )
        resp.raise_for_status()
        uuid = resp.json()["id"]
        print(f"    Launch started: {name} → {uuid}")
        return uuid

    def finish_launch(self, uuid: str, end: datetime, status: str = "PASSED") -> None:
        resp = self.client.put(
            f"{self.base_url}/api/v1/{PROJECT_NAME}/launch/{uuid}/finish",
            json={"endTime": self._ts(end), "status": status},
            headers=self._headers(),
        )
        resp.raise_for_status()

    def start_item(
        self, name: str, item_type: str, launch_uuid: str, start: datetime,
        parent_uuid: str | None = None, description: str = "",
    ) -> str:
        """Start a test item, return its UUID."""
        body = {
            "name": name,
            "type": item_type,
            "launchUuid": launch_uuid,
            "startTime": self._ts(start),
            "description": description,
        }
        path = f"/api/v1/{PROJECT_NAME}/item"
        if parent_uuid:
            path += f"/{parent_uuid}"
        resp = self.client.post(
            f"{self.base_url}{path}",
            json=body,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()["id"]

    def finish_item(
        self, uuid: str, launch_uuid: str, end: datetime,
        status: str = "PASSED", issue: dict | None = None,
    ) -> None:
        body: dict = {
            "endTime": self._ts(end),
            "status": status,
            "launchUuid": launch_uuid,
        }
        if issue:
            body["issue"] = issue
        resp = self.client.put(
            f"{self.base_url}/api/v1/{PROJECT_NAME}/item/{uuid}",
            json=body,
            headers=self._headers(),
        )
        resp.raise_for_status()

    def create_log(
        self, item_uuid: str, launch_uuid: str, ts: datetime,
        message: str, level: str = "info",
    ) -> None:
        resp = self.client.post(
            f"{self.base_url}/api/v1/{PROJECT_NAME}/log",
            json={
                "itemUuid": item_uuid,
                "launchUuid": launch_uuid,
                "time": self._ts(ts),
                "message": message,
                "level": level,
            },
            headers=self._headers(),
        )
        resp.raise_for_status()

    def create_log_with_attachment(
        self, item_uuid: str, launch_uuid: str, ts: datetime,
        message: str, level: str,
        filename: str, content_type: str, file_data: bytes,
    ) -> None:
        """Create a log entry with a binary attachment (multipart upload)."""
        log_json = json.dumps([{
            "itemUuid": item_uuid,
            "launchUuid": launch_uuid,
            "time": self._ts(ts),
            "message": message,
            "level": level,
            "file": {"name": filename},
        }])
        resp = self.client.post(
            f"{self.base_url}/api/v1/{PROJECT_NAME}/log",
            files={
                "json_request_part": (None, log_json, "application/json"),
                "file": (filename, file_data, content_type),
            },
            headers=self._headers(),
        )
        resp.raise_for_status()


# ── Test data definitions ────────────────────────────────────────

SUITES = [
    {
        "name": "VoNR_Registration",
        "description": "VoNR IMS Registration test scenarios",
        "tests": [
            {
                "name": "TC001_InitialRegistration",
                "description": "Verify initial IMS registration on VoNR",
                "status": "PASSED",
                "logs": [
                    ("info", "Starting IMS registration sequence"),
                    ("info", "UE sending REGISTER to P-CSCF via NR"),
                    ("debug", "SIP REGISTER sip:ims.operator.com SIP/2.0"),
                    ("info", "401 Unauthorized received — performing AKA auth"),
                    ("debug", "Computing AKA response with USIM credentials"),
                    ("info", "Re-REGISTER with authorization header sent"),
                    ("info", "200 OK received — registration successful"),
                    ("info", "Registration binding: 3600s"),
                ],
                "attachments": [("capture_registration.pcap", "application/vnd.tcpdump.pcap", "pcap")],
            },
            {
                "name": "TC002_ReRegistration",
                "description": "Verify periodic re-registration",
                "status": "PASSED",
                "logs": [
                    ("info", "Waiting for re-registration timer (expiry - 600s)"),
                    ("info", "Re-REGISTER triggered automatically"),
                    ("info", "200 OK received — re-registration successful"),
                ],
                "attachments": [],
            },
            {
                "name": "TC003_DeRegistration",
                "description": "Verify clean de-registration on power off",
                "status": "PASSED",
                "logs": [
                    ("info", "UE initiating de-registration"),
                    ("info", "REGISTER with Expires: 0 sent"),
                    ("info", "200 OK — de-registration confirmed"),
                ],
                "attachments": [],
            },
        ],
    },
    {
        "name": "VoNR_BasicCall",
        "description": "VoNR basic voice call establishment and teardown",
        "tests": [
            {
                "name": "TC010_MOCall_Success",
                "description": "Mobile-originated call setup and teardown",
                "status": "PASSED",
                "logs": [
                    ("info", "Originating call from UE-A to UE-B"),
                    ("info", "INVITE sent to P-CSCF"),
                    ("debug", "SDP offer: AMR-WB, EVS codec negotiation"),
                    ("info", "100 Trying received"),
                    ("info", "183 Session Progress with SDP answer"),
                    ("info", "Early media path established (QoS bearer active)"),
                    ("info", "200 OK received from UE-B"),
                    ("info", "ACK sent — call established"),
                    ("info", "Call duration: 30s"),
                    ("info", "BYE sent — call terminated normally"),
                    ("info", "200 OK for BYE received"),
                ],
                "attachments": [
                    ("call_setup.pcap", "application/vnd.tcpdump.pcap", "pcap"),
                    ("screenshot_call_active.png", "image/png", "png"),
                ],
            },
            {
                "name": "TC011_MTCall_Success",
                "description": "Mobile-terminated call setup and teardown",
                "status": "PASSED",
                "logs": [
                    ("info", "Incoming INVITE received on UE-B"),
                    ("info", "180 Ringing sent"),
                    ("info", "User answered — 200 OK sent"),
                    ("info", "ACK received — call established"),
                    ("info", "Call duration: 15s — BYE received"),
                ],
                "attachments": [],
            },
        ],
    },
    {
        "name": "VoNR_Handover",
        "description": "VoNR to VoLTE handover (EPS fallback) scenarios",
        "tests": [
            {
                "name": "TC020_SRVCC_DuringCall",
                "description": "SRVCC handover from NR to LTE during active call",
                "status": "PASSED",
                "logs": [
                    ("info", "Active VoNR call established"),
                    ("info", "NR signal degrading — RSRP below threshold"),
                    ("warn", "SRVCC triggered by network"),
                    ("info", "Handover to LTE completed — call preserved"),
                    ("info", "Audio gap: 180ms (within 300ms target)"),
                ],
                "attachments": [("srvcc_handover.pcap", "application/vnd.tcpdump.pcap", "pcap")],
            },
            {
                "name": "TC021_EPSFallback_CallSetup",
                "description": "EPS Fallback for call setup when NR coverage is weak",
                "status": "PASSED",
                "logs": [
                    ("info", "MO call initiated on NR"),
                    ("warn", "NR coverage insufficient for VoNR — EPS Fallback triggered"),
                    ("info", "UE redirected to LTE"),
                    ("info", "VoLTE call established successfully"),
                    ("info", "Call setup time: 4.2s"),
                ],
                "attachments": [],
            },
        ],
    },
]

# Second launch: same suites but some failures
FAILED_TESTS = {
    "TC001_InitialRegistration": {
        "status": "FAILED",
        "extra_logs": [
            ("error", "Registration FAILED — 403 Forbidden from S-CSCF"),
            ("error", "Possible cause: USIM profile mismatch on HSS"),
        ],
        "issue": {"issueType": "pb001", "comment": "HSS configuration error in staging"},
        "attachments": [
            ("screenshot_registration_fail.png", "image/png", "png"),
            ("appium_session.log", "text/plain", "appium"),
        ],
    },
    "TC010_MOCall_Success": {
        "status": "FAILED",
        "extra_logs": [
            ("warn", "SDP negotiation taking longer than expected"),
            ("error", "408 Request Timeout from S-CSCF after 32s"),
            ("error", "Call setup FAILED — network timeout"),
        ],
        "issue": {"issueType": "si001", "comment": "S-CSCF timeout under load"},
        "attachments": [
            ("call_failure.pcap", "application/vnd.tcpdump.pcap", "pcap"),
            ("screenshot_call_fail.png", "image/png", "png"),
        ],
    },
    "TC020_SRVCC_DuringCall": {
        "status": "FAILED",
        "extra_logs": [
            ("error", "SRVCC handover FAILED — call dropped"),
            ("error", "Audio gap exceeded 2000ms — unacceptable"),
        ],
        "issue": {"issueType": "pb001", "comment": "eNodeB handover config issue"},
        "attachments": [
            ("srvcc_fail.pcap", "application/vnd.tcpdump.pcap", "pcap"),
        ],
    },
}


# ── BDD / Cucumber-style test data (mirrors customer setup) ───
# Hierarchy: Folder → Folder → Feature → Scenario
# This matches the structure seen in the customer's RP instance:
#   Launch > Folder: 0_VoLTE_E2E > Folder: 01_Basic_Calls > Feature: VoLTE_VoLTE > Scenario: VoLTE_VoLTE

BDD_STRUCTURE = {
    "launch_name": "clab_come-playground",
    "description": "VoLTE E2E regression — Cucumber/BDD structure with Folders, Features, Scenarios",
    "folders": [
        {
            "name": "0_VoLTE_E2E",
            "description": "VoLTE end-to-end test scenarios",
            "subfolders": [
                {
                    "name": "01_Basic_Calls",
                    "description": "Basic VoLTE call scenarios",
                    "features": [
                        {
                            "name": "VoLTE_VoLTE",
                            "description": "VoLTE to VoLTE call scenarios",
                            "scenarios": [
                                {
                                    "name": "VoLTE_VoLTE",
                                    "description": "Basic VoLTE to VoLTE voice call",
                                    "status": "FAILED",
                                    "issue": {"issueType": "ab001", "comment": "Alerting State Not Reached"},
                                    "logs": [
                                        ("info", "-- And the following parties: --\n"
                                                 "| name    | type  | RAT | VoLTE | 4G | 2G | NLAN |\n"
                                                 "| A-party | Probe | 4G  | on    | on | on | on   |\n"
                                                 "| B-party | Probe | 4G  | on    | on | on | on   |"),
                                        ("info", "JobID: 10f9aeaea98a726f\n"
                                                 "https://grafana01.its-telekom.eu/d/_p3ZawU2k/appium-environment-logs"
                                                 "?orgId=1&var-env_id=10f9aeaea98a726f\n"
                                                 "GridURL: selenium.its:4446"),
                                        ("info", "A-party is initialized as Probe9"),
                                        ("info", "B-party is initialized as Probe12"),
                                        ("debug", "Dialing B-party number: +491511234567"),
                                        ("info", "A-party call state: DIALING"),
                                        ("info", "B-party incoming call detected"),
                                        ("info", "B-party answering call"),
                                        ("error", "Alerting state NOT reached within 30s timeout"),
                                        ("error", "Expected: ALERTING, Got: DIALING after 30000ms"),
                                    ],
                                    "attachments": [
                                        ("screenshot_call_fail.png", "image/png", "png"),
                                        ("appium_a_party.log", "text/plain", "appium"),
                                    ],
                                },
                                {
                                    "name": "VoLTE_VoLTE_hold_resume",
                                    "description": "VoLTE call with hold and resume",
                                    "status": "PASSED",
                                    "logs": [
                                        ("info", "-- And the following parties: --\n"
                                                 "| name    | type  | RAT | VoLTE | 4G |\n"
                                                 "| A-party | Probe | 4G  | on    | on |\n"
                                                 "| B-party | Probe | 4G  | on    | on |"),
                                        ("info", "A-party is initialized as Probe3"),
                                        ("info", "B-party is initialized as Probe7"),
                                        ("info", "Call established between A and B"),
                                        ("info", "A-party puts call on hold"),
                                        ("info", "Hold confirmed — media stream paused"),
                                        ("info", "A-party resumes call"),
                                        ("info", "Call resumed — media stream active"),
                                        ("info", "Call terminated normally"),
                                    ],
                                    "attachments": [
                                        ("call_hold_resume.pcap", "application/vnd.tcpdump.pcap", "pcap"),
                                    ],
                                },
                            ],
                        },
                        {
                            "name": "VoLTE_PSTN",
                            "description": "VoLTE to PSTN breakout scenarios",
                            "scenarios": [
                                {
                                    "name": "VoLTE_to_PSTN_basic",
                                    "description": "VoLTE origination to PSTN termination",
                                    "status": "PASSED",
                                    "logs": [
                                        ("info", "A-party (VoLTE) calling B-party (PSTN)"),
                                        ("info", "MGCF interworking — SIP to ISUP conversion"),
                                        ("info", "Call established via PSTN gateway"),
                                        ("info", "Audio quality: MOS 4.1"),
                                        ("info", "Call terminated by A-party"),
                                    ],
                                    "attachments": [],
                                },
                            ],
                        },
                    ],
                },
                {
                    "name": "02_Emergency_Calls",
                    "description": "Emergency call scenarios",
                    "features": [
                        {
                            "name": "Emergency_112",
                            "description": "112 emergency call handling",
                            "scenarios": [
                                {
                                    "name": "Emergency_112_basic",
                                    "description": "Basic 112 emergency call",
                                    "status": "PASSED",
                                    "logs": [
                                        ("info", "UE dialing 112"),
                                        ("info", "Emergency bearer established — QCI 1"),
                                        ("info", "Location info attached: Cell ID 0x1A2B"),
                                        ("info", "Call routed to PSAP"),
                                        ("info", "Call established with emergency center"),
                                        ("info", "Call duration: 45s"),
                                    ],
                                    "attachments": [
                                        ("emergency_setup.pcap", "application/vnd.tcpdump.pcap", "pcap"),
                                        ("screenshot_emergency.png", "image/png", "png"),
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        },
        {
            "name": "1_SMS_over_IMS",
            "description": "SMS over IMS (SMSoIP) test scenarios",
            "subfolders": [
                {
                    "name": "01_MO_SMS",
                    "description": "Mobile-originated SMS scenarios",
                    "features": [
                        {
                            "name": "SMS_Send",
                            "description": "Send SMS via IMS",
                            "scenarios": [
                                {
                                    "name": "MO_SMS_basic",
                                    "description": "Send a basic SMS over IMS",
                                    "status": "PASSED",
                                    "logs": [
                                        ("info", "A-party sending SMS to B-party"),
                                        ("debug", "SIP MESSAGE sip:+491517654321@ims.operator.com"),
                                        ("info", "202 Accepted from SC"),
                                        ("info", "Delivery report received: DELIVERED"),
                                    ],
                                    "attachments": [],
                                },
                                {
                                    "name": "MO_SMS_long",
                                    "description": "Send a concatenated (long) SMS over IMS",
                                    "status": "FAILED",
                                    "issue": {"issueType": "pb001", "comment": "Concatenation header missing"},
                                    "logs": [
                                        ("info", "Sending 300-char SMS (requires concatenation)"),
                                        ("debug", "UDH: concat ref=42, total=2, seq=1"),
                                        ("info", "Part 1/2 sent successfully"),
                                        ("debug", "UDH: concat ref=42, total=2, seq=2"),
                                        ("error", "Part 2/2 FAILED — 500 Server Error from SC"),
                                        ("error", "Delivery report: FAILED — partial delivery"),
                                    ],
                                    "attachments": [
                                        ("sms_failure.pcap", "application/vnd.tcpdump.pcap", "pcap"),
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    ],
}


def seed_bdd_launch(seeder: RPSeeder, base_time: datetime) -> str:
    """Seed a BDD/Cucumber-style launch with Folders, Features, and Scenarios."""
    t = base_time
    cfg = BDD_STRUCTURE
    launch_uuid = seeder.start_launch(
        cfg["launch_name"], t, description=cfg["description"],
    )
    overall_status = "PASSED"

    for folder in cfg["folders"]:
        t += timedelta(seconds=2)
        folder_uuid = seeder.start_item(
            folder["name"], "suite", launch_uuid, t,
            description=folder["description"],
        )
        folder_status = "PASSED"

        for subfolder in folder.get("subfolders", []):
            t += timedelta(seconds=1)
            sub_uuid = seeder.start_item(
                subfolder["name"], "suite", launch_uuid, t,
                parent_uuid=folder_uuid,
                description=subfolder["description"],
            )
            sub_status = "PASSED"

            for feature in subfolder.get("features", []):
                t += timedelta(seconds=1)
                feat_uuid = seeder.start_item(
                    feature["name"], "test", launch_uuid, t,
                    parent_uuid=sub_uuid,
                    description=feature["description"],
                )
                feat_status = "PASSED"

                for scenario in feature.get("scenarios", []):
                    t += timedelta(seconds=1)
                    scen_uuid = seeder.start_item(
                        scenario["name"], "step", launch_uuid, t,
                        parent_uuid=feat_uuid,
                        description=scenario["description"],
                    )
                    status = scenario["status"]
                    issue = scenario.get("issue")

                    for level, msg in scenario["logs"]:
                        t += timedelta(milliseconds=500)
                        seeder.create_log(scen_uuid, launch_uuid, t, msg, level)

                    for filename, content_type, att_type in scenario.get("attachments", []):
                        t += timedelta(milliseconds=100)
                        data = generate_attachment_data(att_type)
                        seeder.create_log_with_attachment(
                            scen_uuid, launch_uuid, t,
                            f"Attachment: {filename}", "info",
                            filename, content_type, data,
                        )
                        print(f"        📎 {filename} ({len(data)} bytes)")

                    t += timedelta(seconds=1)
                    seeder.finish_item(scen_uuid, launch_uuid, t, status=status, issue=issue)
                    if status == "FAILED":
                        feat_status = "FAILED"
                    symbol = "✅" if status == "PASSED" else "❌"
                    print(f"        {symbol} {scenario['name']} — {status}")

                t += timedelta(seconds=1)
                seeder.finish_item(feat_uuid, launch_uuid, t, status=feat_status)
                if feat_status == "FAILED":
                    sub_status = "FAILED"

            t += timedelta(seconds=1)
            seeder.finish_item(sub_uuid, launch_uuid, t, status=sub_status)
            if sub_status == "FAILED":
                folder_status = "FAILED"

        t += timedelta(seconds=1)
        seeder.finish_item(folder_uuid, launch_uuid, t, status=folder_status)
        if folder_status == "FAILED":
            overall_status = "FAILED"

    t += timedelta(seconds=5)
    seeder.finish_launch(launch_uuid, t, status=overall_status)
    print(f"    Launch finished: {overall_status}")
    return launch_uuid


def generate_attachment_data(att_type: str) -> bytes:
    if att_type == "pcap":
        return make_fake_pcap(packet_count=10)
    elif att_type == "png":
        return make_fake_png(width=640, height=480, label="rp-fetch test screenshot")
    elif att_type == "appium":
        return make_fake_appium_log()
    return b"unknown attachment type"


def seed_launch(
    seeder: RPSeeder,
    launch_name: str,
    base_time: datetime,
    apply_failures: bool = False,
) -> str:
    """Seed a complete launch with suites, tests, logs, and attachments."""
    t = base_time
    launch_uuid = seeder.start_launch(
        launch_name, t,
        description=f"{'Failed' if apply_failures else 'Passed'} run of VoNR regression suite",
    )

    for suite_def in SUITES:
        t += timedelta(seconds=2)
        suite_uuid = seeder.start_item(
            suite_def["name"], "suite", launch_uuid, t,
            description=suite_def["description"],
        )
        suite_status = "PASSED"

        for test_def in suite_def["tests"]:
            t += timedelta(seconds=1)
            test_uuid = seeder.start_item(
                test_def["name"], "test", launch_uuid, t,
                parent_uuid=suite_uuid,
                description=test_def["description"],
            )

            # Determine if this test should fail
            failure = FAILED_TESTS.get(test_def["name"]) if apply_failures else None
            status = failure["status"] if failure else test_def["status"]
            issue = failure.get("issue") if failure else None

            # Write standard logs
            for level, msg in test_def["logs"]:
                t += timedelta(milliseconds=500)
                seeder.create_log(test_uuid, launch_uuid, t, msg, level)

            # Write failure-specific logs
            if failure:
                for level, msg in failure.get("extra_logs", []):
                    t += timedelta(milliseconds=200)
                    seeder.create_log(test_uuid, launch_uuid, t, msg, level)

            # Upload attachments
            attachments = failure["attachments"] if failure else test_def["attachments"]
            for filename, content_type, att_type in attachments:
                t += timedelta(milliseconds=100)
                data = generate_attachment_data(att_type)
                seeder.create_log_with_attachment(
                    test_uuid, launch_uuid, t,
                    f"Attachment: {filename}", "info",
                    filename, content_type, data,
                )
                print(f"      📎 {filename} ({len(data)} bytes)")

            t += timedelta(seconds=2)
            seeder.finish_item(test_uuid, launch_uuid, t, status=status, issue=issue)

            if status == "FAILED":
                suite_status = "FAILED"
            print(f"      {'✅' if status == 'PASSED' else '❌'} {test_def['name']} — {status}")

        t += timedelta(seconds=1)
        seeder.finish_item(suite_uuid, launch_uuid, t, status=suite_status)

    t += timedelta(seconds=5)
    overall = "FAILED" if apply_failures else "PASSED"
    seeder.finish_launch(launch_uuid, t, status=overall)
    print(f"    Launch finished: {overall}")
    return launch_uuid


# ── Main ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed ReportPortal with test data")
    parser.add_argument("--base-url", default=BASE_URL, help=f"RP base URL (default: {BASE_URL})")
    parser.add_argument("--wait", type=int, default=0, help="Seconds to wait for RP to be ready")
    args = parser.parse_args()

    seeder = RPSeeder(args.base_url)

    # Wait for RP to be ready
    if args.wait > 0:
        print(f"Waiting up to {args.wait}s for ReportPortal to be ready...")
        deadline = time.time() + args.wait
        while time.time() < deadline:
            try:
                resp = httpx.get(f"{args.base_url}/api/health", timeout=5)
                if resp.status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            time.sleep(5)
            print("  Still waiting...")
        else:
            print("WARNING: RP may not be fully ready — proceeding anyway")

    print("\n🔧 ReportPortal Seed Script")
    print("=" * 50)

    # Step 1: Authenticate
    print("\n[1/5] Authentication")
    seeder.authenticate()

    # Step 2: Create project
    print("\n[2/5] Project setup")
    seeder.create_project()
    seeder.assign_admin_to_project()

    # Step 3: Generate API key
    print("\n[3/5] API key generation")
    api_key = seeder.generate_api_key()

    # Step 4: Seed passed launch (flat suite/test structure)
    base_time = datetime(2026, 3, 15, 9, 10, 0, tzinfo=timezone.utc)
    print("\n[4/6] Seeding PASSED launch (suite/test)")
    uuid1 = seed_launch(seeder, "VoNR_Regression_v2.0", base_time, apply_failures=False)

    # Step 5: Seed failed launch (flat suite/test structure)
    base_time = datetime(2026, 3, 18, 14, 22, 0, tzinfo=timezone.utc)
    print("\n[5/6] Seeding FAILED launch (suite/test)")
    uuid2 = seed_launch(seeder, "VoNR_Regression_v2.1", base_time, apply_failures=True)

    # Step 6: Seed BDD/Cucumber-style launch (Folder → Folder → Feature → Scenario)
    base_time = datetime(2026, 3, 3, 6, 48, 0, tzinfo=timezone.utc)
    print("\n[6/6] Seeding BDD/Cucumber launch (folder/feature/scenario)")
    uuid3 = seed_bdd_launch(seeder, base_time)

    # Summary
    print("\n" + "=" * 50)
    print("✅ Seeding complete!\n")
    print("Launches created:")
    print(f"  1. VoNR_Regression_v2.0 (PASSED) → {uuid1}   [suite/test]")
    print(f"  2. VoNR_Regression_v2.1 (FAILED) → {uuid2}   [suite/test]")
    print(f"  3. clab_come-playground (FAILED)  → {uuid3}   [folder/feature/scenario]")
    print(f"\nProject: {PROJECT_NAME}")
    print(f"API Key: {api_key}")
    print(f"\n📋 Quick start with rp-fetch:")
    print(f"   uv run rp-fetch config init")
    print(f"   # Use: base_url={args.base_url}  project={PROJECT_NAME}  api_key=<above>")
    print(f"   uv run rp-fetch launch list")
    print(f"   uv run rp-fetch download {uuid3}              # BDD launch")
    print(f"   uv run rp-fetch download {uuid3} --flat       # BDD launch (flat mode)")
    print(f"   uv run rp-fetch download {uuid2}              # suite/test launch")
    print(f"   uv run rp-fetch download {uuid2} --dry-run")


if __name__ == "__main__":
    main()
