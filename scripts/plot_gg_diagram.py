"""Plot G-G traction circle diagram for a vehicle dynamics telemetry session.

Fetches the G-G friction circle plot from the ApexTelemetry API (or renders locally)
and saves it as a high-resolution PNG image for performance and race engineers.
"""

import argparse
import sys
import uuid
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and save G-G traction circle plot for a test session."
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8000", help="ApexTelemetry API base URL"
    )
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session UUID to plot (defaults to latest available session)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="gg_diagram.png",
        help="Output PNG image path (default: gg_diagram.png)",
    )
    parser.add_argument(
        "--points",
        type=int,
        default=3000,
        help="Maximum points to render on the friction circle",
    )
    args = parser.parse_args()

    client = httpx.Client(base_url=args.base_url, timeout=30.0, follow_redirects=True)

    # 1. Health Probe
    try:
        health = client.get("/health")
        health.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"[Error] Failed to connect to server at {args.base_url}: {exc}", file=sys.stderr)
        sys.exit(1)

    # 2. Resolve Session ID
    session_id_str = args.session_id
    if not session_id_str:
        print("[Lookup] No session ID provided. Fetching latest session...")
        list_resp = client.get("/api/v1/sessions")
        if list_resp.status_code != 200:
            print(f"[Error] Failed to list sessions: {list_resp.text}", file=sys.stderr)
            sys.exit(1)
        items = list_resp.json().get("items", [])
        if not items:
            print(
                "[Error] No sessions found in the database. Run seed_data.py or ingest_f1_session.py first.",
                file=sys.stderr,
            )
            sys.exit(1)
        session_id_str = items[0]["id"]
        session_name = items[0]["session_slug"]
        print(f"[Lookup] Selected latest session: {session_name} ({session_id_str})")
    else:
        try:
            uuid.UUID(session_id_str)
        except ValueError:
            print(f"[Error] Invalid session UUID format: {session_id_str}", file=sys.stderr)
            sys.exit(1)

    # 3. Fetch Summary Metrics
    summary_resp = client.get(f"/api/v1/sessions/{session_id_str}/analysis/traction-circle")
    if summary_resp.status_code == 200:
        data = summary_resp.json()
        print("\n--- G-G Friction Circle Summary ---")
        print(f"  Sample Points:        {data['sample_count']}")
        print(f"  Peak Lateral Grip:    {data['peak_lateral_g']:.3f} g")
        print(f"  Peak Braking Decel:   {data['peak_braking_g']:.3f} g")
        print(f"  Peak Combined Grip:   {data['peak_combined_g']:.3f} g")
        print(f"  p95 Sustained Grip:   {data['p95_combined_g']:.3f} g")
        print(f"  High Grip Util:       {data['high_utilization_pct']:.1f}%")
        print("----------------------------------\n")

    # 4. Request Rendered Diagram from API
    print(f"[Render] Generating G-G diagram for session {session_id_str}...")
    plot_resp = client.get(
        f"/api/v1/sessions/{session_id_str}/analysis/traction-circle/plot",
        params={"max_points": args.points},
    )

    if plot_resp.status_code != 200:
        print(
            f"[Error] Failed to generate plot: {plot_resp.status_code} - {plot_resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 5. Save Output
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(plot_resp.content)

    print(
        f"[Success] G-G Traction Circle diagram saved to: {output_path} ({len(plot_resp.content)} bytes)"
    )


if __name__ == "__main__":
    main()
