import { createDarkTheme, type BrandVariants, type Theme } from "@fluentui/react-components";

// Fox orange brand ramp (10 = darkest, 160 = lightest), built around #ff7a1a.
const fox: BrandVariants = {
  10: "#080401",
  20: "#24130a",
  30: "#3c1c0c",
  40: "#52240d",
  50: "#692c0d",
  60: "#80350c",
  70: "#983e0a",
  80: "#b14807",
  90: "#ca5203",
  100: "#e45f05",
  110: "#ff7a1a",
  120: "#ff8f42",
  130: "#ffa466",
  140: "#ffb98a",
  150: "#ffcdad",
  160: "#ffe2d1",
};

export const theme: Theme = {
  ...createDarkTheme(fox),
  // Dark themes read the brand from lighter shades so it stays legible.
  colorBrandForeground1: fox[110],
  colorBrandForeground2: fox[120],
  colorBrandForegroundLink: fox[120],
  colorBrandForegroundLinkHover: fox[130],
  colorBrandBackground: fox[100],
  colorBrandBackgroundHover: fox[110],
  colorBrandBackgroundPressed: fox[90],
  colorCompoundBrandStroke: fox[110],
  colorCompoundBrandBackground: fox[110],
};
