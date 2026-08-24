import { readFileSync } from "node:fs";

const tokens = readFileSync(new URL("../src/styles/tokens.css", import.meta.url), "utf8");

function colorTokens(name) {
  return [...tokens.matchAll(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`, "g"))].map(
    (match) => match[1],
  );
}

function relativeLuminance(hex) {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)
    .map((value) => Number.parseInt(value, 16) / 255)
    .map((value) => (value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4)));
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(left, right) {
  const luminances = [relativeLuminance(left), relativeLuminance(right)].sort((a, b) => b - a);
  return (luminances[0] + 0.05) / (luminances[1] + 0.05);
}

const backgrounds = colorTokens("chat-user-bg");
const foregrounds = colorTokens("chat-user-text");
const themes = ["白天模式", "黑夜模式"];

if (backgrounds.length !== themes.length || foregrounds.length !== themes.length) {
  throw new Error("对话气泡必须为白天和黑夜模式各定义一组颜色令牌");
}

themes.forEach((theme, index) => {
  const ratio = contrast(backgrounds[index], foregrounds[index]);
  if (ratio < 4.5) {
    throw new Error(`${theme}用户消息对比度 ${ratio.toFixed(2)}:1，低于 WCAG AA 4.5:1`);
  }
  process.stdout.write(`✓ ${theme}用户消息对比度 ${ratio.toFixed(2)}:1\n`);
});
