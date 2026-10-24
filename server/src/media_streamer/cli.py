from __future__ import annotations

import argparse
import logging
import os


def _backends() -> tuple[str, ...]:
    from .hwaccel import BACKENDS

    return BACKENDS


def _hw_command(args: argparse.Namespace) -> int:
    from . import hwaccel

    if args.cmd == "hwaccel":
        print(f"auto picks, in order: {', '.join(hwaccel.auto_order())}")
        for backend, codec, ok, err in hwaccel.report(args.ffmpeg, args.device):
            name = hwaccel.encoder_name(backend, codec)
            print(f"  {'ok  ' if ok else 'no  '} {backend:<13} {name:<18} {err[:90]}")
        best = hwaccel.select("auto", ffmpeg=args.ffmpeg, device=args.device, refresh=True)
        print(f"auto -> {best} ({hwaccel.encoder_name(best, 'h264')})")
        return 0
    masks = [[float(v) for v in m.split(",")] for m in args.mask]
    if any(len(m) != 4 for m in masks):
        raise SystemExit("--mask needs X,Y,W,H")
    enc = hwaccel.Encode(codec=args.codec, max_height=args.max_height, fps=args.fps,
                         bitrate_kbit=args.bitrate, masks=masks, audio=args.audio,
                         live=not args.archive)
    return hwaccel.run(enc, args.input, args.output, preference=args.hwaccel,
                       ffmpeg=args.ffmpeg, device=args.device)


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
    serve.add_argument("--reload", action="store_true",
                       help="restart when the server's Python code changes (development)")

    sub.add_parser("ingest", help="publish non-RTSP sources (tapo_cloud, test, file) to MediaMTX")
    sub.add_parser("tapo-login", help="log in to the Tapo cloud and store a session")
    check = sub.add_parser("tapo-check", help="test the Tapo cloud relay with the saved session "
                                              "(prints no secrets)")
    check.add_argument("--camera", help="camera name or device id (default: first)")
    check.add_argument("--seconds", type=float, default=10)
    check.add_argument("--resolution", choices=["HD", "VGA"], default="HD")
    sub.add_parser("tapo-logout", help="delete the saved Tapo session")
    tok = sub.add_parser("worker-token", help="create a worker access token and print it")
    tok.add_argument("name")
    tok.add_argument("--tags", default="", help="comma separated capability tags")
    tok.add_argument("--save", metavar="FILE",
                     help="keep the token in FILE and reuse it while it is valid (dev tooling)")
    reset = sub.add_parser("reset", help="delete one kind of data, keep the rest "
                                         "(stop the server first)")
    reset.add_argument("scopes", nargs="+", metavar="SCOPE",
                       choices=["recordings", "events", "detections", "queue", "workers",
                                "cameras", "profiles", "tapo"],
                       help="recordings | events | detections | queue | workers | cameras | "
                            "profiles | tapo")
    reset.add_argument("-y", "--yes", action="store_true", help="don't ask")
    rm = sub.add_parser("remove-cameras",
                        help="remove cameras and their recordings, events and settings "
                             "(stop the server first)")
    rm.add_argument("ids", nargs="*", help="camera ids")
    rm.add_argument("--test", action="store_true",
                    help="all test cameras (looping sample files, test patterns)")
    rm.add_argument("--orphaned", action="store_true",
                    help="data of cameras that are no longer defined")
    rm.add_argument("-y", "--yes", action="store_true", help="don't ask")

    hw_help = "auto | none | " + " | ".join(b for b in _backends() if b != "software")
    tc = sub.add_parser("transcode", help="RTSP -> masked / scaled / re-encoded RTSP with the "
                                          "best hardware encoder (run by MediaMTX)")
    tc.add_argument("--input", required=True)
    tc.add_argument("--output", required=True)
    tc.add_argument("--hwaccel", default="auto", help=hw_help)
    tc.add_argument("--device", help="Linux render node (default /dev/dri/renderD128)")
    tc.add_argument("--ffmpeg", default="ffmpeg")
    tc.add_argument("--codec", choices=["h264", "h265"], default="h264")
    tc.add_argument("--max-height", type=int)
    tc.add_argument("--fps", type=int)
    tc.add_argument("--bitrate", type=int, help="kbit/s (default: constant quality)")
    tc.add_argument("--mask", action="append", default=[], metavar="X,Y,W,H",
                    help="normalized rectangle to black out (repeatable)")
    tc.add_argument("--audio", choices=["copy", "opus", "none"], default="copy")
    tc.add_argument("--archive", action="store_true", help="B-frames allowed (not for live)")
    hw = sub.add_parser("hwaccel", help="show which hardware encoders work on this machine")
    hw.add_argument("--ffmpeg", default="ffmpeg")
    hw.add_argument("--device", help="Linux render node (default /dev/dri/renderD128)")

    args = parser.parse_args()
    if args.cmd in ("transcode", "hwaccel"):  # no config needed (runs inside MediaMTX)
        logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        raise SystemExit(_hw_command(args))
    if args.config:
        os.environ["MEDIA_STREAMER_CONFIG"] = args.config
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)  # --reload: one line per save

    from .config import get_settings

    settings = get_settings()

    if args.cmd == "serve":
        import uvicorn

        if args.no_background:
            os.environ["MEDIA_STREAMER_NO_BACKGROUND"] = "1"
        from pathlib import Path

        # Only watch the package: watching the repo would restart on every new recording.
        uvicorn.run("media_streamer.web.main:app", host=args.host, port=args.port,
                    reload=args.reload, reload_dirs=[str(Path(__file__).parent)],
                    log_level="info")
    elif args.cmd == "ingest":
        import signal

        from .ingest.supervisor import run_ingest

        # Stop publishers cleanly on Ctrl+C / SIGTERM, also when started in the background
        # (which leaves SIGINT ignored) - e.g. scripts/dev.sh restarting it on code changes.
        def stop(*_: object) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, stop)
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
    elif args.cmd == "worker-token":
        from pathlib import Path

        from . import worker_tokens
        from .db import init_db

        init_db(settings)
        tags = [t for t in args.tags.split(",") if t.strip()]
        try:
            if args.save:
                print(worker_tokens.ensure(args.name, Path(args.save), tags))
            else:
                print(worker_tokens.create(args.name, tags=tags)[1])
        except worker_tokens.TokenError as e:
            raise SystemExit(f"error: {e}") from None
    elif args.cmd == "reset":
        from . import reset as reset_mod
        from .db import init_db

        if not args.yes:
            lines = [f"  {s}: {reset_mod.SCOPES[s].__doc__.splitlines()[0]}" for s in args.scopes]
            print("This deletes:\n" + "\n".join(lines))
            if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
                raise SystemExit("aborted")
        init_db(settings)
        for scope in args.scopes:
            print(f"{scope}: {reset_mod.SCOPES[scope](settings)}")
    elif args.cmd == "remove-cameras":
        from . import reset as reset_mod
        from .db import init_db

        init_db(settings)
        targets = list(dict.fromkeys(args.ids))
        in_config: list[str] = []
        if args.test:
            ui_ids, in_config = reset_mod.test_cameras(settings)
            targets += [i for i in ui_ids if i not in targets]
        if args.orphaned:
            targets += [i for i in reset_mod.orphaned_cameras(settings) if i not in targets]
        config_ids = {c.id for c in settings.cameras}
        blocked = sorted(set(in_config) | (set(targets) & config_ids))
        targets = [i for i in targets if i not in blocked]
        for cam_id in blocked:
            print(f"{cam_id}: defined in config.yaml - remove it there (and restart), then "
                  "run this again with --orphaned to delete its data")
        if not targets:
            print("nothing to remove")
            return
        if not args.yes:
            print("This deletes these cameras with all their recordings, events and settings:")
            print("  " + ", ".join(targets))
            if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
                raise SystemExit("aborted")
        for cam_id in targets:
            r = reset_mod.purge_camera(settings, cam_id)
            print(f"{cam_id}: {r['segments']} recordings and {r['events']} events deleted"
                  f"{', camera removed' if r['camera_removed'] else ''}")
    elif args.cmd == "tapo-logout":
        from .tapo_account import logout

        logout(settings)
        print(f"Removed {settings.tapo_session_file}")


if __name__ == "__main__":
    main()
