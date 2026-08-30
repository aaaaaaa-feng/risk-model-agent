import { useCallback, useState } from "react";
import { projectsApi } from "../api/projectsApi";
import { errorMessage } from "@/shared/lib/format";
import { notify } from "@/shared/lib/notify";
import type { Project } from "../types";

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([]);

  const loadProjects = useCallback(async () => {
    try {
      const value = await projectsApi.list();
      setProjects(value.projects);
      return value.projects;
    } catch (error) {
      notify(errorMessage(error), true);
      return [] as Project[];
    }
  }, []);

  return { projects, setProjects, loadProjects };
}
