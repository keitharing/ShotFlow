import { useEffect, useState } from "react";

export interface Route {
  page: string;
  id: number | null;
  query: URLSearchParams;
}

export function parseHash(hash: string): Route {
  const [path, queryString = ""] = hash.replace(/^#\/?/, "").split("?");
  const [page = "", rawId] = path.split("/");
  const id = rawId !== undefined && /^\d+$/.test(rawId) ? Number(rawId) : null;
  return { page: page || "dashboard", id, query: new URLSearchParams(queryString) };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export const link = {
  dashboard: () => "#/",
  sequence: (id: number) => `#/seq/${id}`,
  shot: (id: number, version?: number) => `#/shot/${id}${version ? `?v=${version}` : ""}`,
  review: (a: number, b: number) => `#/review?a=${a}&b=${b}`,
  edit: (id: number) => `#/edit/${id}`,
  generate: (shot?: number, source?: number, frame?: number) => {
    const q = new URLSearchParams();
    if (shot) q.set("shot", String(shot));
    if (source) q.set("source", String(source));
    if (frame) q.set("frame", String(frame));
    const s = q.toString();
    return `#/generate${s ? `?${s}` : ""}`;
  },
  jobs: () => "#/jobs",
  job: (id: number) => `#/job/${id}`,
  recipes: (id?: number) => `#/recipes${id ? `/${id}` : ""}`,
  prompts: (sequenceId?: number) => `#/prompts${sequenceId ? `/${sequenceId}` : ""}`,
  deliveries: (id?: number) => `#/deliveries${id ? `/${id}` : ""}`,
  scene3d: (tab?: "keyframe" | "layer" | "external") => `#/scene3d${tab ? `?tab=${tab}` : ""}`,
};
