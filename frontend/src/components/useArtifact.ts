import { useEffect, useState } from "react";
import { type Artifact, getArtifact } from "../api";

// One artifact's text and people, fetched once per id (api.ts caches) when `enabled` turns on.
export function useArtifact(id: string, enabled = true) {
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    getArtifact(id).then(
      (a) => live && setArtifact(a),
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
  }, [id, enabled]);
  return { artifact, error };
}

export function authors(a: Artifact): string {
  const names = a.people.filter((p) => p.relation === "AUTHORED").map((p) => p.name);
  return [...new Set(names)].join(", ");
}
