from __future__ import annotations

import argparse
import logging
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="media-streamer")
    parser.add_argument("-c", "--config", help="config file (default: $MEDIA_STREAMER_CONFIG "
                                               "or ./config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="web UI, API and processing pipeline")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-background", action="store_true",
                       help="don't run scanner/preprocess/queue loops (UI/API only)")
    serve.add_argument("--reload", action="store_true")

    sub.add_parser("ingest", help="publish non-RTSP sources (tapo_cloud, test, file) to MediaMTX")
    sub.add_parser("tapo-login", help="log in to the Tapo cloud and store a session")
    check = sub.add_parser("tapo-check", help="test the Tapo cloud relay with the saved session "
                                              "(prints no secrets)")
    check.add_argument("--camera", help="camera name or device id (default: first)")
    check.add_argument("--seconds", type=float, default=10)
    check.add_argument("--resolution", choices=["HD", "VGA"], default="HD")
    sub.add_parser("tapo-logout", help="delete the saved Tapo session")

    args = parser.parse_args()
    if args.config:
        os.environ["MEDIA_STREAMER_CONFIG"] = args.config
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from .config import get_settings

    settings = get_settings()

    if args.cmd == "serve":
        import uvicorn

        if args.no_background:
            os.environ["MEDIA_STREAMER_NO_BACKGROUND"] = "1"
        uvicorn.run("media_streamer.web.main:app", host=args.host, port=args.port,
                    reload=args.reload, log_level="info")
    elif args.cmd == "ingest":
        from .ingest.supervisor import run_ingest

        settings.ensure_dirs()
        run_ingest(settings)
    elif args.cmd == "tapo-login":
        from .ingest.tapo_cloud import interactive_login

        settings.ensure_dirs()
        interactive_login(settings)
    elif args.cmd == "tapo-check":
        from .ingest.tapo_cloud import check

        try:
            raise SystemExit(check(settings, args.camera, args.seconds, args.resolution))
        except RuntimeError as e:  # e.g. not signed in / ontapo not installed
            raise SystemExit(f"error: {e}") from None
    elif args.cmd == "tapo-logout":
        settings.tapo_session_file.unlink(missing_ok=True)
        print(f"Removed {settings.tapo_session_file}")


if __name__ == "__main__":
    main()
