"""Run Dreachy from this checkout on the laptop, with the robot as its body
(docs/live-checks-r3.md). Test infrastructure, not part of the app.

Dreachy asks for the "local" media backend, which is right on the robot,
where the microphone and speaker are attached. From a laptop the robot's
audio comes over WebRTC instead, so this launcher switches that one setting
and leaves everything else as the app has it. The SDK connects to
reachy-mini.local over the network on its own when no daemon runs locally.

Usage (from dreachy-app/):  uv run python scripts/live/run_on_laptop.py
Settings come from ~/.local/share/dreachy/.env, as on the robot.
"""

from dreachy.main import Dreachy

Dreachy.request_media_backend = "webrtc"

if __name__ == "__main__":
    app = Dreachy()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
