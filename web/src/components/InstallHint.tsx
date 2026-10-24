// iPhone / iPad: Safari never offers to install a web app, so say how (once, dismissible).
import { InfoBanner } from "./ui";
import { useI18n } from "../i18n";

function iosSafariNotInstalled(): boolean {
  const ua = navigator.userAgent;
  // iPadOS reports itself as a Mac; touch gives it away.
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  const installed =
    (navigator as Navigator & { standalone?: boolean }).standalone === true ||
    window.matchMedia("(display-mode: standalone)").matches;
  // Other iOS browsers (Chrome, Firefox) name themselves; only Safari can add it.
  return ios && !installed && !/CriOS|FxiOS|EdgiOS/.test(ua);
}

export function InstallHint() {
  const { t, rich } = useI18n();
  if (!iosSafariNotInstalled()) return null;
  return (
    <InfoBanner id="ios-install">
      {rich("install.ios", {
        share: <b>{t("install.share")}</b>,
        add: <b>{t("install.addToHomeScreen")}</b>,
      })}
    </InfoBanner>
  );
}
