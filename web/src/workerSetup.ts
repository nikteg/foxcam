import type { MessageKey } from "./i18n";

export type DetectorChoice = "speciesnet" | "ultralytics" | "dummy";
export type Platform = "wsl" | "windows" | "linux" | "docker";

export const PLATFORMS: Platform[] = ["wsl", "windows", "linux", "docker"];

/** "gpu, Winhack" -> ["gpu", "winhack"] */
export const splitTags = (text: string) =>
  text
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);

export interface SetupStep {
  text: MessageKey;
  code?: string;
}

/** Best guess for the machine the user is on (usually also the GPU machine). */
export function guessPlatform(): Platform {
  return /Windows/i.test(navigator.userAgent) ? "wsl" : "linux";
}

/** One "--flag value" per entry, so commands can be shown one option per line. */
function detectorArgs(detector: DetectorChoice): string[] {
  switch (detector) {
    case "speciesnet":
      return ["--detector speciesnet"];
    case "ultralytics":
      return ["--detector ultralytics", "--model yolov8s-worldv2.pt", "--classes fox"];
    case "dummy":
      return ["--detector dummy", "--dummy-label fox"];
  }
}

/** A command split over lines with the shell's continuation character. */
function lines(platform: Platform, command: string, args: string[]): string {
  const cont = platform === "windows" ? " `" : " \\";
  return [command, ...args].join(`${cont}\n  `);
}

function installCommand(platform: Platform, detector: DetectorChoice, folder: string): string {
  const extra = detector === "dummy" ? "" : `[${detector}]`;
  const args = [
    ...(detector === "speciesnet" ? ["--python 3.12"] : []),
    // PyPI's PyTorch is CPU-only on Windows; auto picks the CUDA build for the driver.
    ...(detector === "dummy" ? [] : ["--torch-backend auto"]),
    `"media-worker${extra} @ ${folder}"`,
  ];
  return lines(platform, "uv tool install", args);
}

/** Copy-paste setup steps for a worker on the given platform (see docs/WORKER_SETUP.md). */
export function setupSteps(
  platform: Platform,
  detector: DetectorChoice,
  server: string,
  token: string,
): SetupStep[] {
  const conn = [`--server ${server}`, `--token ${token}`];
  const folder = platform === "windows" ? ".\\worker" : "./worker";
  const common: SetupStep[] = [
    { text: "workers.step.install", code: installCommand(platform, detector, folder) },
    { text: "workers.step.doctor", code: lines(platform, "media-worker doctor", conn) },
    {
      text: "workers.step.run",
      code: lines(platform, "media-worker", [...conn, ...detectorArgs(detector)]),
    },
  ];
  switch (platform) {
    case "wsl":
      return [
        { text: "workers.step.wslDriver", code: "wsl --install -d Ubuntu-24.04" },
        {
          text: "workers.step.wslTools",
          code: "sudo apt update && sudo apt install -y ffmpeg git\ncurl -LsSf https://astral.sh/uv/install.sh | sh && exec $SHELL",
        },
        { text: "workers.step.codeWsl" },
        ...common,
      ];
    case "windows":
      return [
        {
          text: "workers.step.winget",
          code: "winget install astral-sh.uv\nwinget install Gyan.FFmpeg",
        },
        { text: "workers.step.code" },
        ...common,
      ];
    case "linux":
      return [
        {
          text: "workers.step.linuxTools",
          code: "sudo apt install -y ffmpeg   # macOS: brew install ffmpeg\ncurl -LsSf https://astral.sh/uv/install.sh | sh",
        },
        { text: "workers.step.code" },
        ...common,
      ];
    case "docker":
      return [
        { text: "workers.step.dockerGpu" },
        { text: "workers.step.code" },
        {
          text: "workers.step.dockerEnv",
          code: [
            `MEDIA_SERVER=${server}`,
            `MEDIA_WORKER_TOKEN=${token}`,
            `DETECTOR=${detector}`,
            `WORKER_ARGS=${detectorArgs(detector).join(" ")}`,
          ].join("\n"),
        },
        {
          text: "workers.step.dockerUp",
          code: "docker compose -f worker/compose.yaml up --build -d\ndocker compose -f worker/compose.yaml run --rm worker doctor",
        },
      ];
  }
}
