import { describe, expect, it } from "vitest";

const featureSources = import.meta.glob("./features/**/*.{ts,tsx}", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

const sharedSources = import.meta.glob("./shared/**/*.{ts,tsx}", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

const appSources = import.meta.glob("./app/**/*.{ts,tsx}", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

const featureImport = /(?:from\s+|import\s*\()["']@\/features\/([^/"']+)([^"']*)["']/g;
const legacyRootImport = /["']@\/(?:api|types|components|hooks|lib|runState)(?:[/'"])/;

describe("前端 feature-slice 边界", () => {
  it("shared 不反向依赖 feature", () => {
    const violations = Object.entries(sharedSources)
      .filter(([, source]) => /@\/features\/|(?:\.\.\/)+features\//.test(source))
      .map(([file]) => file);
    expect(violations).toEqual([]);
  });

  it("feature 之间只通过对方公开 index 交互", () => {
    const violations: string[] = [];
    for (const [file, source] of Object.entries(featureSources)) {
      const owner = file.match(/^\.\/features\/([^/]+)\//)?.[1];
      for (const match of source.matchAll(featureImport)) {
        const [, target, suffix] = match;
        if (target !== owner && suffix !== "") violations.push(`${file} -> ${target}${suffix}`);
      }
    }
    expect(violations).toEqual([]);
  });

  it("feature 不回退到旧的根级杂项目录", () => {
    const violations = Object.entries(featureSources)
      .filter(([, source]) => legacyRootImport.test(source))
      .map(([file]) => file);
    expect(violations).toEqual([]);
  });

  it("AppShell 只组合布局，把项目和 Run 编排委托给 ProjectSession", () => {
    const shell = appSources["./app/AppShell.tsx"];
    const session = appSources["./app/model/useProjectSession.ts"];

    expect(shell).toContain("useProjectSession");
    expect(shell).not.toMatch(
      /\b(?:projectsApi|runsApi|useProjectData|useRunData|useGlobalPolling)\b/,
    );
    expect(session).toMatch(/\buseProjectData\b/);
    expect(session).toMatch(/\buseRunData\b/);
    expect(session).toMatch(/\buseGlobalPolling\b/);
  });
});
