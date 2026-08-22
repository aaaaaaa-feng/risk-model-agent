import { useCallback, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../lib/format";
import type { Project, ProjectsResponse } from "../types";

export function useProjects(notify: (message: string, error?: boolean) => void) {
  const [projects, setProjects] = useState<Project[]>([]);

  const loadProjects = useCallback(async () => {
    try {
      const value = await api.get<ProjectsResponse>("/projects");
      setProjects(value.projects);
      return value.projects;
    } catch (error) {
      notify(errorMessage(error), true);
      return [] as Project[];
    }
  }, [notify]);

  return { projects, setProjects, loadProjects };
}
