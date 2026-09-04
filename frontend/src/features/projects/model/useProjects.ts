import { useCallback, useRef, useState } from "react";
import { projectsApi } from "../api/projectsApi";
import { errorMessage } from "@/shared/lib/format";
import { notify } from "@/shared/lib/notify";
import type { Project } from "../types";

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loadState, setLoadState] = useState<"idle" | "loading" | "loaded" | "failed">("idle");
  const requestGeneration = useRef(0);

  const loadProjects = useCallback(async () => {
    const generation = ++requestGeneration.current;
    setLoadState("loading");
    try {
      const value = await projectsApi.list();
      if (generation !== requestGeneration.current) return value.projects;
      setProjects(value.projects);
      setLoadState("loaded");
      return value.projects;
    } catch (error) {
      if (generation !== requestGeneration.current) return [] as Project[];
      setLoadState("failed");
      notify(errorMessage(error), true);
      return [] as Project[];
    }
  }, []);

  const resetProjects = useCallback(() => {
    requestGeneration.current += 1;
    setProjects([]);
    setLoadState("idle");
  }, []);

  return { projects, setProjects, loadProjects, loadState, resetProjects };
}
