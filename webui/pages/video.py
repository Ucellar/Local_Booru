"""A video played by the program inside the page (webui/video_relay.py): its frames and its state."""
from __future__ import annotations

from webui.server import Request, Response
from webui.video_relay import RELAY

BOUNDARY = "lbframe"
KEEP_ALIVE = 5.0           # a paused video sends its last frame again this often: the stream stays open


def register(router) -> None:
    router.add("GET", r"/video/live/(?P<token>[A-Za-z0-9_-]{6,64})", live)
    router.add("GET", r"/api/video/state", state)


def live(req: Request) -> Response:
    """The frames of one play as an endless ``multipart/x-mixed-replace`` answer: the page's picture shows every
    new frame.  It ends when the play does (another video, another page, the program closed it)."""
    token = req.params["token"]
    if not RELAY.active(token):
        return Response.not_found("Это видео уже не играет")

    def frames():
        seq, idle = 0, 0.0
        while True:
            got = RELAY.next_frame(token, seq, timeout=1.0)
            if got is None:
                if not RELAY.active(token):
                    return
                idle += 1.0
                if idle < KEEP_ALIVE or seq == 0:
                    continue
                got = RELAY.next_frame(token, seq - 1, timeout=0)      # the last frame once more
                if got is None:
                    continue
            idle = 0.0
            seq, jpeg = got
            yield (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n").encode("ascii") \
                + jpeg + b"\r\n"

    return Response(200, b"", f"multipart/x-mixed-replace; boundary={BOUNDARY}", stream=frames())


def state(req: Request) -> Response:
    return Response.json(RELAY.state(req.arg("token")))
